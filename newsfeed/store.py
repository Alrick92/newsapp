"""SQLite-backed item store.

Stories are kept for RETENTION_DAYS (90 by default) unless starred, which are
kept forever; a nightly prune deletes the rest and returns the space to the
disk. The UI shows the last DISPLAY_HOURS (72) unless the reader picks a
longer window. Read/star state, saved summaries, feed polling state, og:image
progress and the trending snapshot all live in this one file, and a full-text
index (FTS5) over titles and feed text backs search.

Schema changes are applied in order by `_migrate`, tracked with SQLite's
`user_version`, so an existing database is upgraded in place once.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from .items import Item, normalize_title

log = logging.getLogger(__name__)

DISPLAY_HOURS = 72
RETENTION_DAYS = int(os.environ.get("NEWSFEED_RETENTION_DAYS", "90"))
# The nightly prune runs on the first check after this hour (server local time).
PRUNE_HOUR = int(os.environ.get("NEWSFEED_PRUNE_HOUR", "3"))
# Identical headlines this close together are one story; further apart they are
# treated as distinct (recurring titles like "Week in review").
TITLE_DEDUPE_HOURS = 48

# Columns for list views; the full text is loaded only when a single item needs it.
_LIST_COLUMNS = ("id", "title", "description", "url", "image", "source_id", "source", "category",
                 "source_class", "published", "guid", "read_at", "starred_at", "summary", "also")
_SELECT_LIST = ", ".join(f"items.{c}" for c in _LIST_COLUMNS) + ", length(items.content) AS content_len"

_BASE_SCHEMA = """
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

# Each entry upgrades the schema by one version; never edit a released step.
_MIGRATIONS = [
    # 1: reader state, GUIDs, full text, merged sources, saved summaries, FTS5 search.
    """
    ALTER TABLE items ADD COLUMN guid TEXT;
    ALTER TABLE items ADD COLUMN content TEXT NOT NULL DEFAULT '';
    ALTER TABLE items ADD COLUMN read_at REAL;
    ALTER TABLE items ADD COLUMN starred_at REAL;
    ALTER TABLE items ADD COLUMN summary TEXT;
    ALTER TABLE items ADD COLUMN summary_model TEXT;
    ALTER TABLE items ADD COLUMN also TEXT NOT NULL DEFAULT '[]';
    UPDATE items SET content = description WHERE content = '';
    CREATE INDEX items_guid ON items (source_id, guid);
    CREATE INDEX items_starred ON items (starred_at) WHERE starred_at IS NOT NULL;
    CREATE VIRTUAL TABLE items_fts USING fts5(
        title, content, content='items', content_rowid='rowid',
        tokenize='porter unicode61 remove_diacritics 2');
    CREATE TRIGGER items_fts_insert AFTER INSERT ON items BEGIN
        INSERT INTO items_fts (rowid, title, content) VALUES (new.rowid, new.title, new.content);
    END;
    CREATE TRIGGER items_fts_delete AFTER DELETE ON items BEGIN
        INSERT INTO items_fts (items_fts, rowid, title, content) VALUES ('delete', old.rowid, old.title, old.content);
    END;
    CREATE TRIGGER items_fts_update AFTER UPDATE OF title, content ON items BEGIN
        INSERT INTO items_fts (items_fts, rowid, title, content) VALUES ('delete', old.rowid, old.title, old.content);
        INSERT INTO items_fts (rowid, title, content) VALUES (new.rowid, new.title, new.content);
    END;
    INSERT INTO items_fts (items_fts) VALUES ('rebuild');
    """,
]
SCHEMA_VERSION = len(_MIGRATIONS)

_WORD = re.compile(r"\w+", re.UNICODE)


def fts_query(text: str) -> str | None:
    """Turn what a person types into a safe FTS5 query: every word must match,
    the last one as a prefix (so results appear while typing)."""
    words = _WORD.findall(text or "")
    if not words:
        return None
    quoted = [f'"{w}"' for w in words]
    quoted[-1] += "*"
    return " ".join(quoted)


def _row_to_item(row: sqlite3.Row) -> Item:
    data = dict(row)
    data["also"] = json.loads(data.get("also") or "[]")
    return Item(**data)


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
            self._db.executescript(_BASE_SCHEMA)
        self._migrate()
        self.version = 0  # bumps whenever the item set changes (in this process)

    def _migrate(self) -> None:
        with self._lock:
            current = self._db.execute("PRAGMA user_version").fetchone()[0]
            for number in range(current, SCHEMA_VERSION):
                # executescript commits first; wrap each step so it applies fully or not at all.
                self._db.executescript(f"BEGIN; {_MIGRATIONS[number]}; PRAGMA user_version = {number + 1}; COMMIT;")
                log.info("database upgraded to schema version %d", number + 1)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- writes ------------------------------------------------------------

    def add(self, items: list[Item], now: float | None = None) -> int:
        """Insert items inside the retention window; returns how many were new stories.

        Duplicates are detected by the feed's GUID, then by link, then by an
        identical headline within 48 hours. A headline duplicate from another
        feed isn't stored twice: it's recorded as another source of the story
        already kept, so the story keeps one read/starred state.
        """
        now = now or time.time()
        cutoff = now - self.retention
        added = changed = 0
        with self._lock, self._db:
            for item in items:
                if item.published < cutoff:
                    continue
                if item.guid and self._db.execute(
                        "SELECT 1 FROM items WHERE source_id = ? AND guid = ?", (item.source_id, item.guid)).fetchone():
                    continue
                if self._db.execute("SELECT 1 FROM items WHERE id = ?", (item.id,)).fetchone():
                    continue
                norm = normalize_title(item.title)
                dup = self._db.execute(
                    "SELECT id, source_id, also FROM items WHERE norm_title = ? AND published BETWEEN ? AND ?",
                    (norm, item.published - TITLE_DEDUPE_HOURS * 3600, item.published + TITLE_DEDUPE_HOURS * 3600),
                ).fetchone()
                if dup:
                    also = json.loads(dup["also"])
                    if item.source_id != dup["source_id"] and all(a["source_id"] != item.source_id for a in also):
                        also.append({"source_id": item.source_id, "source": item.source, "url": item.url,
                                     "source_class": item.source_class, "published": item.published})
                        self._db.execute("UPDATE items SET also = ? WHERE id = ?", (json.dumps(also), dup["id"]))
                        changed += 1
                    continue
                self._db.execute(
                    "INSERT INTO items (id, title, norm_title, description, url, image, source_id, source,"
                    " category, source_class, published, first_seen, guid, content)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (item.id, item.title, norm, item.description, item.url, item.image, item.source_id,
                     item.source, item.category, item.source_class, item.published, now, item.guid,
                     item.content or item.description),
                )
                added += 1
                changed += 1
        if changed:
            self.version += 1
        return added

    def purge(self, now: float | None = None) -> int:
        """Delete unstarred stories older than the retention window and reclaim the space."""
        now = now or time.time()
        cutoff = now - self.retention
        with self._lock, self._db:
            removed = self._db.execute(
                "DELETE FROM items WHERE published < ? AND starred_at IS NULL", (cutoff,)).rowcount
        with self._lock:
            self._db.execute("PRAGMA incremental_vacuum")
        self.set_meta("last_purge", now)
        if removed:
            log.info("pruned %d stories older than %d days (starred kept)", removed, self.retention // 86400)
            self.version += 1
        return removed

    def maybe_purge(self, now: float | None = None) -> int:
        """Run the prune once a day, on the first call after PRUNE_HOUR."""
        now = now or time.time()
        last = self.get_meta("last_purge")
        today = datetime.fromtimestamp(now)
        due_at = today.replace(hour=PRUNE_HOUR, minute=0, second=0, microsecond=0).timestamp()
        if now < due_at or (last and last >= due_at):
            return 0
        return self.purge(now)

    def set_image(self, item_id: str, image: str | None) -> None:
        """Record an og:image lookup (image may be None when the page had none)."""
        with self._lock, self._db:
            self._db.execute("UPDATE items SET og_checked = 1, image = COALESCE(?, image) WHERE id = ?",
                             (image, item_id))

    def set_read(self, ids: list[str], read: bool, now: float | None = None) -> list[str]:
        """Mark items read or unread; returns the ids whose state changed."""
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        state = "read_at IS NULL" if read else "read_at IS NOT NULL"
        with self._lock, self._db:
            changed = [r[0] for r in self._db.execute(
                f"SELECT id FROM items WHERE id IN ({marks}) AND {state}", ids).fetchall()]
            if changed:
                self._db.execute(f"UPDATE items SET read_at = ? WHERE id IN ({','.join('?' * len(changed))})",
                                 [(now or time.time()) if read else None, *changed])
        return changed

    def set_starred(self, item_id: str, starred: bool, now: float | None = None) -> bool:
        with self._lock, self._db:
            return self._db.execute("UPDATE items SET starred_at = ? WHERE id = ?",
                                    ((now or time.time()) if starred else None, item_id)).rowcount > 0

    def save_summary(self, item_id: str, summary: str, model: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE items SET summary = ?, summary_model = ? WHERE id = ?", (summary, model, item_id))

    # -- reads -------------------------------------------------------------

    def _where(self, *, category=None, sources=None, hours=None, has_image=False, unread=False,
               starred=False, now=None):
        clauses, params = [], []
        if not starred:  # starred items show regardless of age
            now = now or time.time()
            hours = min(hours or DISPLAY_HOURS, self.retention / 3600)
            clauses.append("items.published >= ?")
            params.append(now - hours * 3600)
        if category:
            clauses.append("items.category = ?")
            params.append(category)
        if sources:
            clauses.append(f"items.source_id IN ({','.join('?' * len(sources))})")
            params.extend(sorted(sources))
        if has_image:
            clauses.append("items.image IS NOT NULL")
        if unread:
            clauses.append("items.read_at IS NULL")
        if starred:
            clauses.append("items.starred_at IS NOT NULL")
        return " AND ".join(clauses) or "1", params

    def search(self, *, q: str | None = None, sort: str = "newest", limit: int | None = None,
               offset: int = 0, **filters) -> tuple[int, list[Item]]:
        """Matching items; returns (total, page).

        With `q`, results come from the full-text index over titles and feed
        text, best match first (sort="relevance", the default when searching)
        or newest first, each with a snippet around the matching words.
        """
        where, params = self._where(**filters)
        match = fts_query(q) if q else None
        page = f" LIMIT {int(limit)} OFFSET {int(offset)}" if limit is not None else ""
        if match:
            source = "items JOIN items_fts ON items_fts.rowid = items.rowid"
            where = f"items_fts MATCH ? AND {where}"
            params = [match, *params]
            # Title matches weigh more than body matches.
            order = {"newest": "items.published DESC",
                     "source": "items.source COLLATE NOCASE, items.published DESC"}.get(sort, "bm25(items_fts, 8.0, 1.0)")
            columns = f"{_SELECT_LIST}, snippet(items_fts, 1, char(2), char(3), '…', 24) AS snippet"
        else:
            source = "items"
            order = "items.source COLLATE NOCASE, items.published DESC" if sort == "source" else "items.published DESC"
            columns = _SELECT_LIST
        with self._lock:
            total = self._db.execute(f"SELECT COUNT(*) FROM {source} WHERE {where}", params).fetchone()[0]
            rows = self._db.execute(f"SELECT {columns} FROM {source} WHERE {where} ORDER BY {order}{page}",
                                    params).fetchall()
        return total, [_row_to_item(r) for r in rows]

    def query(self, **kwargs) -> list[Item]:
        return self.search(**kwargs)[1]

    def ids(self, *, q: str | None = None, **filters) -> list[str]:
        """Ids of every matching item (used by "mark all read")."""
        return [i.id for i in self.query(q=q, **filters)]

    def category_counts(self, *, q: str | None = None, **filters) -> tuple[dict[str, int], dict[str, int]]:
        """(total, unread) per category for the given filters, ignoring any category filter."""
        filters.pop("category", None)
        where, params = self._where(**filters)
        source = "items"
        match = fts_query(q) if q else None
        if match:
            source = "items JOIN items_fts ON items_fts.rowid = items.rowid"
            where, params = f"items_fts MATCH ? AND {where}", [match, *params]
        with self._lock:
            rows = self._db.execute(
                f"SELECT items.category, COUNT(*), SUM(items.read_at IS NULL) FROM {source} WHERE {where}"
                " GROUP BY items.category", params).fetchall()
        return {r[0]: r[1] for r in rows}, {r[0]: r[2] for r in rows}

    def get(self, item_id: str) -> Item | None:
        """One item including its full text."""
        with self._lock:
            row = self._db.execute(f"SELECT {_SELECT_LIST}, items.content FROM items WHERE id = ?",
                                   (item_id,)).fetchone()
        return _row_to_item(row) if row else None

    def count(self, hours: float | None = None, now: float | None = None) -> int:
        """Stories in the last `hours` (default: everything stored)."""
        with self._lock:
            if hours is None:
                return self._db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            return self._db.execute("SELECT COUNT(*) FROM items WHERE published >= ?",
                                    ((now or time.time()) - hours * 3600,)).fetchone()[0]

    def starred_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM items WHERE starred_at IS NOT NULL").fetchone()[0]

    def source_counts(self, hours: float = DISPLAY_HOURS) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT source_id, COUNT(*) FROM items WHERE published >= ? GROUP BY source_id",
                                    (time.time() - hours * 3600,)).fetchall()
        return {r[0]: r[1] for r in rows}

    def missing_images(self, limit: int) -> list[Item]:
        """Newest image-less items that have not had an og:image lookup yet."""
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_SELECT_LIST} FROM items WHERE image IS NULL AND og_checked = 0"
                " ORDER BY published DESC LIMIT ?", (limit,)).fetchall()
        return [_row_to_item(r) for r in rows]

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
