"""SQLite-backed item store.

Stories are kept for RETENTION_DAYS (30 by default) and purged after that; the
UI shows the last DISPLAY_HOURS (72) unless the viewer picks a longer window.
Nothing is held in memory beyond the rows a request asks for, and feed polling
state, og:image progress and the trending snapshot live in the same file, so a
restart picks up exactly where it left off.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

from .items import Item, normalize_title

log = logging.getLogger(__name__)

DISPLAY_HOURS = 72
RETENTION_DAYS = int(os.environ.get("NEWSFEED_RETENTION_DAYS", "30"))
# Identical headlines this close together are one story; further apart they are
# treated as distinct (recurring titles like "Week in review").
TITLE_DEDUPE_HOURS = 48

_ITEM_COLUMNS = ("id", "title", "description", "url", "image", "source_id", "source",
                 "category", "source_class", "published")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    norm_title   TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    url          TEXT NOT NULL,
    image        TEXT,
    source_id    TEXT NOT NULL,
    source       TEXT NOT NULL,
    category     TEXT NOT NULL,
    source_class TEXT NOT NULL,
    published    REAL NOT NULL,
    first_seen   REAL NOT NULL,
    og_checked   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS items_published ON items (published DESC);
CREATE INDEX IF NOT EXISTS items_category ON items (category, published DESC);
CREATE INDEX IF NOT EXISTS items_norm_title ON items (norm_title);
CREATE TABLE IF NOT EXISTS feed_state (
    feed_id       TEXT PRIMARY KEY,
    etag          TEXT,
    last_modified TEXT,
    last_polled   REAL NOT NULL DEFAULT 0,
    last_ok       REAL,
    last_error    TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class Store:
    def __init__(self, path: Path | str = ":memory:", retention_days: int = RETENTION_DAYS):
        self.retention = retention_days * 86400
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # One connection shared across the event loop and FastAPI's worker
        # threads; the lock serializes access.
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.execute("PRAGMA auto_vacuum = INCREMENTAL")  # only takes effect on a new file
            if self.path != ":memory:":
                self._db.execute("PRAGMA journal_mode = WAL")
            self._db.executescript(_SCHEMA)
        self.version = 0  # bumps whenever the item set changes (in this process)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- writes ------------------------------------------------------------

    def add(self, items: list[Item], now: float | None = None) -> int:
        """Insert items inside the retention window; returns how many were new stories."""
        now = now or time.time()
        cutoff = now - self.retention
        added = changed = 0
        with self._lock, self._db:
            for item in items:
                if item.published < cutoff:
                    continue
                if self._db.execute("SELECT 1 FROM items WHERE id = ?", (item.id,)).fetchone():
                    continue
                norm = normalize_title(item.title)
                dup = self._db.execute(
                    "SELECT id, published FROM items WHERE norm_title = ? AND published BETWEEN ? AND ?",
                    (norm, item.published - TITLE_DEDUPE_HOURS * 3600, item.published + TITLE_DEDUPE_HOURS * 3600),
                ).fetchone()
                if dup:
                    # Same headline from another feed (or a re-posted URL): keep the earliest.
                    if dup["published"] <= item.published:
                        continue
                    self._db.execute("DELETE FROM items WHERE id = ?", (dup["id"],))
                else:
                    added += 1
                self._db.execute(
                    "INSERT INTO items (id, title, norm_title, description, url, image, source_id, source,"
                    " category, source_class, published, first_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (item.id, item.title, norm, item.description, item.url, item.image, item.source_id,
                     item.source, item.category, item.source_class, item.published, now),
                )
                changed += 1
        removed = self.purge(now)
        if changed or removed:
            self.version += 1
        return added

    def purge(self, now: float | None = None) -> int:
        """Delete stories older than the retention window and reclaim the space."""
        cutoff = (now or time.time()) - self.retention
        with self._lock, self._db:
            removed = self._db.execute("DELETE FROM items WHERE published < ?", (cutoff,)).rowcount
        if removed:
            with self._lock:
                self._db.execute("PRAGMA incremental_vacuum")
            log.info("purged %d stories older than %d days", removed, self.retention // 86400)
            self.version += 1
        return removed

    def set_image(self, item_id: str, image: str | None) -> None:
        """Record an og:image lookup (image may be None when the page had none)."""
        with self._lock, self._db:
            self._db.execute("UPDATE items SET og_checked = 1, image = COALESCE(?, image) WHERE id = ?",
                             (image, item_id))

    # -- reads -------------------------------------------------------------

    def _where(self, *, category=None, sources=None, q=None, hours=None, has_image=False, now=None):
        now = now or time.time()
        hours = min(hours or DISPLAY_HOURS, self.retention / 3600)
        clauses, params = ["published >= ?"], [now - hours * 3600]
        if category:
            clauses.append("category = ?")
            params.append(category)
        if sources:
            clauses.append(f"source_id IN ({','.join('?' * len(sources))})")
            params.extend(sorted(sources))
        if has_image:
            clauses.append("image IS NOT NULL")
        for term in (q or "").split():
            clauses.append("(title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' OR source LIKE ? ESCAPE '\\')")
            params.extend([_like(term)] * 3)
        return " AND ".join(clauses), params

    def search(self, *, sort: str = "newest", limit: int | None = None, offset: int = 0,
               **filters) -> tuple[int, list[Item]]:
        """Matching items, newest first (or by source); returns (total, page)."""
        where, params = self._where(**filters)
        order = "source COLLATE NOCASE, published DESC" if sort == "source" else "published DESC"
        page = f" LIMIT {int(limit)} OFFSET {int(offset)}" if limit is not None else ""
        with self._lock:
            total = self._db.execute(f"SELECT COUNT(*) FROM items WHERE {where}", params).fetchone()[0]
            rows = self._db.execute(
                f"SELECT {', '.join(_ITEM_COLUMNS)} FROM items WHERE {where} ORDER BY {order}{page}", params
            ).fetchall()
        return total, [Item(**dict(r)) for r in rows]

    def query(self, **kwargs) -> list[Item]:
        return self.search(**kwargs)[1]

    def category_counts(self, **filters) -> dict[str, int]:
        filters.pop("category", None)
        where, params = self._where(**filters)
        with self._lock:
            rows = self._db.execute(
                f"SELECT category, COUNT(*) FROM items WHERE {where} GROUP BY category", params).fetchall()
        return {r[0]: r[1] for r in rows}

    def count(self, hours: float | None = None, now: float | None = None) -> int:
        """Stories in the last `hours` (default: the whole retention window)."""
        cutoff = (now or time.time()) - (hours * 3600 if hours else self.retention)
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM items WHERE published >= ?", (cutoff,)).fetchone()[0]

    def source_counts(self, hours: float = DISPLAY_HOURS) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT source_id, COUNT(*) FROM items WHERE published >= ? GROUP BY source_id",
                                    (time.time() - hours * 3600,)).fetchall()
        return {r[0]: r[1] for r in rows}

    def missing_images(self, limit: int) -> list[Item]:
        """Newest image-less items that have not had an og:image lookup yet."""
        with self._lock:
            rows = self._db.execute(
                f"SELECT {', '.join(_ITEM_COLUMNS)} FROM items WHERE image IS NULL AND og_checked = 0"
                " ORDER BY published DESC LIMIT ?", (limit,)).fetchall()
        return [Item(**dict(r)) for r in rows]

    # -- feed state and misc -----------------------------------------------

    def feed_states(self) -> dict[str, dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM feed_state").fetchall()
        return {r["feed_id"]: {k: r[k] for k in r.keys() if k != "feed_id"} for r in rows}

    def save_feed_state(self, feed_id: str, **state) -> None:
        cols = ["feed_id", *state]
        with self._lock, self._db:
            self._db.execute(
                f"INSERT INTO feed_state ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
                f" ON CONFLICT(feed_id) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in state)}",
                [feed_id, *state.values()])

    def get_meta(self, key: str, default=None):
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key: str, value) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO meta (key, value) VALUES (?, ?)"
                             " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))

    @property
    def last_refresh(self) -> float | None:
        return self.get_meta("last_refresh")

    @last_refresh.setter
    def last_refresh(self, value: float | None) -> None:
        self.set_meta("last_refresh", value)

    def import_json_snapshot(self, path: Path) -> int:
        """One-time migration from the old items.json snapshot; renames it afterwards."""
        if not path.exists():
            return 0
        try:
            payload = json.loads(path.read_text())
            added = self.add([Item(**raw) for raw in payload.get("items", [])])
            if payload.get("last_refresh") and not self.last_refresh:
                self.last_refresh = payload["last_refresh"]
        except (ValueError, TypeError) as exc:
            log.warning("ignoring unreadable snapshot %s: %s", path, exc)
            return 0
        path.rename(path.with_suffix(".json.imported"))
        log.info("imported %d stories from %s", added, path)
        return added
