"""The feed list.

Feeds live in an OPML file (see opml.py). The app ships `feeds.opml` next to
this module: the curated World, Politics, AI, Technology, Security, Economy,
Local (by state) and Blogs feeds. At runtime the editable copy is
`<data dir>/feeds.opml`:

- first run: the bundled list is copied there, and a reader export placed at
  `<data dir>/import.opml` (e.g. from Feedly) is merged in, then renamed
  `import.opml.imported` so it's applied once;
- edits to the file are picked up within a minute, no restart needed.

FEEDS, CATEGORIES, REGIONS and REGION_ALL_LABELS below are the bundled list
(used by demo mode and tests); the running app reads FeedList.catalog.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .model import (COMMUNITY, GOVERNMENT, INSTITUTIONAL, OFFICIAL_LAB,  # noqa: F401 (re-exported)
                    PUBLISHER, VENDOR_SECURITY, Feed)
from .opml import Catalog, OPMLError, merge, read_opml, write_opml

log = logging.getLogger(__name__)

BUNDLED_OPML = Path(__file__).with_name("feeds.opml")

DEFAULT = read_opml(BUNDLED_OPML.read_bytes())
FEEDS: tuple[Feed, ...] = DEFAULT.feeds
FEEDS_BY_ID = DEFAULT.by_id
CATEGORIES = DEFAULT.categories
REGIONS = DEFAULT.regions
REGION_ALL_LABELS = DEFAULT.region_all_labels


class FeedList:
    """The editable OPML in the data dir, reloaded when it changes on disk."""

    def __init__(self, path: Path, import_path: Path | None = None):
        self.path = path
        self.error: str | None = None
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(write_opml(DEFAULT))
            log.info("created %s with the bundled feed list", path)
        self.catalog = self._read() or DEFAULT
        self._mtime = path.stat().st_mtime
        if import_path and import_path.exists():
            self.import_file(import_path)

    def _read(self) -> Catalog | None:
        try:
            cat = read_opml(self.path.read_bytes())
        except (OSError, OPMLError) as exc:
            self.error = f"{self.path.name}: {exc}"
            log.error("can't read %s, keeping the previous feed list: %s", self.path, exc)
            return None
        if not cat.feeds:
            self.error = f"{self.path.name} has no feeds"
            log.error("%s has no feeds, keeping the previous feed list", self.path)
            return None
        self.error = None
        return cat

    def reload_if_changed(self) -> bool:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return False
        if mtime == self._mtime:
            return False
        self._mtime = mtime
        cat = self._read()
        if cat is None:
            return False
        self.catalog = cat
        log.info("reloaded %s: %d feeds", self.path, len(cat.feeds))
        return True

    def import_file(self, import_path: Path) -> int:
        """Merge another reader's OPML export into the list, once; returns feeds added."""
        try:
            extra = read_opml(import_path.read_bytes())
        except (OSError, OPMLError) as exc:
            log.error("can't import %s: %s", import_path, exc)
            return 0
        merged, added = merge(self.catalog, extra)
        self.path.write_text(write_opml(merged))
        self.catalog, self._mtime = merged, self.path.stat().st_mtime
        import_path.rename(import_path.with_name(import_path.name + ".imported"))
        log.info("imported %d new feeds from %s", added, import_path)
        return added


def default_opml_path(data_dir: Path) -> Path:
    return Path(os.environ.get("NEWSFEED_OPML") or data_dir / "feeds.opml")
