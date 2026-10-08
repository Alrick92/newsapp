"""OPML feed lists: read, write, and merge a Feedly (or any reader's) export.

Folders become categories and nested folders become regions (a state in
Local, US/International in Politics). Plain OPML from other readers works as
is; the app's own details ride along in an `nf:` namespace that those readers
ignore:

    <outline text="Local" nf:key="local" nf:allLabel="All states">
      <outline text="Georgia" nf:region="GA">
        <outline type="rss" text="Georgia Recorder" xmlUrl="https://…/feed/"
                 nf:id="georgia-recorder" nf:class="publisher" nf:poll="15"/>
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .model import PUBLISHER, Feed

NS = "https://github.com/alrick92/newsapp/ns/opml"
ET.register_namespace("nf", NS)
_NF = f"{{{NS}}}"

# Feeds from another reader's export have no polling hint; the spec's 30 minutes.
IMPORTED_POLL_MINUTES = 30
UNFILED = ("other", "Other")  # category for feeds that sit outside any folder


class OPMLError(ValueError):
    """The file isn't OPML we can read."""


@dataclass
class Catalog:
    feeds: tuple[Feed, ...] = ()
    categories: dict[str, str] = field(default_factory=dict)  # key -> label, in display order
    regions: dict[str, dict[str, str]] = field(default_factory=dict)  # category -> {code: label}
    region_all_labels: dict[str, str] = field(default_factory=dict)

    @property
    def by_id(self) -> dict[str, Feed]:
        return {f.id: f for f in self.feeds}


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "feed"


def _feed_id(name: str, url: str, taken: set[str]) -> str:
    base = slugify(name)[:40]
    return base if base not in taken else f"{base}-{hashlib.sha1(url.encode()).hexdigest()[:6]}"


def _norm_url(url: str) -> str:
    """Feed URLs compared loosely, so http/https or a trailing slash don't create a duplicate."""
    parts = urlsplit(url.strip())
    return f"{parts.netloc.lower().removeprefix('www.')}{parts.path.rstrip('/')}?{parts.query}"


def read_opml(text: str | bytes) -> Catalog:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise OPMLError(f"not valid XML: {exc}") from None
    body = root.find("body")
    if root.tag != "opml" or body is None:
        raise OPMLError("missing <opml><body>")

    cat = Catalog()
    feeds: list[Feed] = []
    taken: set[str] = set()
    seen_urls: set[str] = set()

    def add_category(el: ET.Element | None) -> str:
        if el is None:
            key, label = UNFILED
        else:
            label = el.get("text") or el.get("title") or "Untitled"
            key = el.get(_NF + "key") or slugify(label)
            if el.get(_NF + "allLabel"):
                cat.region_all_labels[key] = el.get(_NF + "allLabel")
        cat.categories.setdefault(key, label)
        return key

    def add_feed(el: ET.Element, category: str, region: str | None) -> None:
        url = (el.get("xmlUrl") or "").strip()
        if not url or _norm_url(url) in seen_urls:
            return
        seen_urls.add(_norm_url(url))
        name = el.get("text") or el.get("title") or urlsplit(url).netloc
        fid = el.get(_NF + "id") or _feed_id(name, url, taken)
        if fid in taken:
            fid = _feed_id(fid, url, taken)
        taken.add(fid)
        try:
            poll = int(el.get(_NF + "poll") or IMPORTED_POLL_MINUTES)
        except ValueError:
            poll = IMPORTED_POLL_MINUTES
        feeds.append(Feed(fid, name, url, category, el.get(_NF + "class") or PUBLISHER, max(5, poll), region))

    def walk(el: ET.Element, category: str | None, region: str | None) -> None:
        for child in el.findall("outline"):
            if child.get("xmlUrl"):
                add_feed(child, category or add_category(None), region)
            elif category is None:  # top-level folder: a category
                walk(child, add_category(child), None)
            else:  # nested folder: a region of that category
                label = child.get("text") or child.get("title") or "Untitled"
                code = child.get(_NF + "region") or slugify(label).upper()
                cat.regions.setdefault(category, {})[code] = label
                walk(child, category, code)

    walk(body, None, None)
    cat.feeds = tuple(feeds)
    # Drop folders that ended up empty (e.g. a Feedly folder of removed feeds).
    used = {f.category for f in feeds}
    cat.categories = {k: v for k, v in cat.categories.items() if k in used}
    return cat


def write_opml(cat: Catalog, title: str = "Newsfeed") -> str:
    root = ET.Element("opml", {"version": "2.0"})
    head = ET.SubElement(root, "head")
    ET.SubElement(head, "title").text = title
    body = ET.SubElement(root, "body")

    def feed_el(parent: ET.Element, f: Feed) -> None:
        site = urlsplit(f.url)
        ET.SubElement(parent, "outline", {
            "type": "rss", "text": f.name, "title": f.name, "xmlUrl": f.url,
            "htmlUrl": f"{site.scheme}://{site.netloc}",
            _NF + "id": f.id, _NF + "class": f.source_class, _NF + "poll": str(f.poll_minutes),
        })

    for key, label in cat.categories.items():
        attrs = {"text": label, "title": label, _NF + "key": key}
        if key in cat.region_all_labels:
            attrs[_NF + "allLabel"] = cat.region_all_labels[key]
        folder = ET.SubElement(body, "outline", attrs)
        in_cat = [f for f in cat.feeds if f.category == key]
        for f in in_cat:
            if not f.region:
                feed_el(folder, f)
        for code, region_label in cat.regions.get(key, {}).items():
            sub = ET.SubElement(folder, "outline", {"text": region_label, "title": region_label, _NF + "region": code})
            for f in in_cat:
                if f.region == code:
                    feed_el(sub, f)
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def merge(base: Catalog, extra: Catalog) -> tuple[Catalog, int]:
    """Add extra's feeds that base lacks (matched by feed URL); returns (merged, number added).

    A folder whose name matches an existing category (e.g. Feedly's "Tech" and
    our "tech") joins it; other folders become new categories at the end.
    """
    known = {_norm_url(f.url) for f in base.feeds}
    taken = {f.id for f in base.feeds}
    by_label = {label.lower(): key for key, label in base.categories.items()}
    categories, regions = dict(base.categories), {k: dict(v) for k, v in base.regions.items()}
    feeds = list(base.feeds)
    added = 0
    for f in extra.feeds:
        if _norm_url(f.url) in known:
            continue
        label = extra.categories.get(f.category, f.category)
        key = f.category if f.category in categories else by_label.get(label.lower(), f.category)
        categories.setdefault(key, label)
        if f.region:
            regions.setdefault(key, {}).setdefault(f.region, extra.regions.get(f.category, {}).get(f.region, f.region))
        fid = f.id if f.id not in taken else _feed_id(f.id, f.url, taken)
        taken.add(fid)
        known.add(_norm_url(f.url))
        feeds.append(Feed(fid, f.name, f.url, key, f.source_class, f.poll_minutes, f.region))
        added += 1
    all_labels = {**extra.region_all_labels, **base.region_all_labels}
    return Catalog(tuple(feeds), categories, regions, all_labels), added
