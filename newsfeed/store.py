"""In-memory item store with a rolling window and an on-disk snapshot."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path

from .items import Item, normalize_title

log = logging.getLogger(__name__)

WINDOW_HOURS = 72


class Store:
    def __init__(self, window_hours: int = WINDOW_HOURS, snapshot: Path | None = None):
        self.window = window_hours * 3600
        self.snapshot = snapshot
        self.items: dict[str, Item] = {}
        self._titles: dict[str, str] = {}  # normalized title -> item id
        self.version = 0  # bumps whenever the item set changes
        self.last_refresh: float | None = None

    def add(self, items: list[Item], now: float | None = None) -> int:
        """Add items inside the window; returns how many were new."""
        now = now or time.time()
        cutoff = now - self.window
        added = 0
        changed = False
        for item in items:
            if item.published < cutoff or item.id in self.items:
                continue
            key = normalize_title(item.title)
            dup = self.items.get(self._titles.get(key, ""))
            if dup:
                # Same headline from another feed (or a re-posted URL): keep the earliest.
                if dup.published <= item.published:
                    continue
                del self.items[dup.id]
            else:
                added += 1
            self.items[item.id] = item
            self._titles[key] = item.id
            changed = True
        removed = self.prune(now)
        if changed or removed:
            self.version += 1
        return added

    def prune(self, now: float | None = None) -> int:
        cutoff = (now or time.time()) - self.window
        stale = [i for i, it in self.items.items() if it.published < cutoff]
        for item_id in stale:
            item = self.items.pop(item_id)
            self._titles.pop(normalize_title(item.title), None)
        return len(stale)

    def query(self, *, category: str | None = None, sources: set[str] | None = None,
              q: str | None = None, hours: float | None = None, has_image: bool = False,
              sort: str = "newest", now: float | None = None) -> list[Item]:
        now = now or time.time()
        cutoff = now - min(hours or WINDOW_HOURS, self.window / 3600) * 3600
        terms = [t for t in (q or "").lower().split() if t]
        out = []
        for item in self.items.values():
            if item.published < cutoff:
                continue
            if category and item.category != category:
                continue
            if sources and item.source_id not in sources:
                continue
            if has_image and not item.image:
                continue
            if terms:
                hay = f"{item.title} {item.description} {item.source}".lower()
                if not all(t in hay for t in terms):
                    continue
            out.append(item)
        if sort == "source":
            out.sort(key=lambda i: (i.source.lower(), -i.published))
        else:
            out.sort(key=lambda i: -i.published)
        return out

    # -- persistence -------------------------------------------------------

    def save(self) -> None:
        if not self.snapshot:
            return
        self.snapshot.parent.mkdir(parents=True, exist_ok=True)
        payload = {"last_refresh": self.last_refresh, "items": [i.to_dict() for i in self.items.values()]}
        fd, tmp = tempfile.mkstemp(dir=self.snapshot.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, self.snapshot)

    def load(self) -> None:
        if not self.snapshot or not self.snapshot.exists():
            return
        try:
            payload = json.loads(self.snapshot.read_text())
            self.add([Item(**raw) for raw in payload.get("items", [])])
            self.last_refresh = payload.get("last_refresh")
            log.info("loaded %d items from snapshot", len(self.items))
        except (ValueError, TypeError) as exc:
            log.warning("ignoring unreadable snapshot %s: %s", self.snapshot, exc)
