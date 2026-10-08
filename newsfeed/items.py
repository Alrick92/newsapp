"""Turn raw RSS/Atom bytes into normalized news items."""

from __future__ import annotations

import calendar
import hashlib
import html
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import feedparser

from .feeds import Feed

DESCRIPTION_LIMIT = 320
# Full feed text kept for search and summaries (never shown in full; links go to
# the publisher). Long-form feeds are cut here to keep the database small.
CONTENT_LIMIT = 20_000

_TRACKING_PARAMS = re.compile(r"^(utm_\w+|fbclid|gclid|mc_cid|mc_eid|ref|cmpid|ito|at_\w+)$", re.I)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,;:!?)\]])")
_IMG_SRC = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.I)
_IMAGE_EXT = re.compile(r"\.(jpe?g|png|webp|gif|avif)(\?|$)", re.I)


@dataclass
class Item:
    id: str
    title: str
    description: str
    url: str
    image: str | None
    source_id: str
    source: str
    category: str
    source_class: str
    published: float  # unix seconds, UTC
    guid: str | None = None  # the feed's own id for the entry, when it gives one
    content: str = ""  # full feed text, plain; not sent to the page
    content_len: int = 0  # length of content when it wasn't loaded
    read_at: float | None = None
    starred_at: float | None = None
    summary: str | None = None
    also: list[dict] = field(default_factory=list)  # the same story from other feeds
    snippet: str | None = None  # search hit context, with \x02...\x03 around matches

    def to_dict(self) -> dict:
        data = asdict(self)
        data["content_len"] = len(self.content) or self.content_len
        del data["content"]
        return data


def canonicalize_url(url: str) -> str:
    """Strip tracking params, fragments, and trailing slashes so reposts dedupe."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING_PARAMS.match(k)]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https" if parts.scheme in ("http", "https") else parts.scheme,
                       parts.netloc.lower().removeprefix("www."), path, urlencode(query), ""))


def normalize_title(title: str) -> str:
    return _SPACE.sub(" ", re.sub(r"[^\w\s]", "", title.lower())).strip()


def clean_text(raw: str, limit: int = DESCRIPTION_LIMIT) -> str:
    text = _SPACE.sub(" ", html.unescape(_TAG.sub(" ", raw or ""))).strip()
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:.-") + "…"


def _entry_image(entry, base: str) -> str | None:
    candidates: list[tuple[int, str]] = []

    for media in entry.get("media_content", []) or []:
        url = media.get("url")
        kind = (media.get("medium") or media.get("type") or "")
        if url and ("image" in kind or _IMAGE_EXT.search(url)):
            candidates.append((int(media.get("width") or 0), url))
    for thumb in entry.get("media_thumbnail", []) or []:
        if thumb.get("url"):
            candidates.append((int(thumb.get("width") or 0), thumb["url"]))
    if candidates:
        return urljoin(base, max(candidates, key=lambda c: c[0])[1])

    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure" and str(link.get("type", "")).startswith("image"):
            return urljoin(base, link["href"])
    for enc in entry.get("enclosures", []) or []:
        if str(enc.get("type", "")).startswith("image") and enc.get("href"):
            return urljoin(base, enc["href"])

    bodies = [c.get("value", "") for c in entry.get("content", []) or []]
    bodies.append(entry.get("summary", ""))
    for body in bodies:
        match = _IMG_SRC.search(body or "")
        if match and not match.group(1).startswith("data:"):
            return urljoin(base, html.unescape(match.group(1)))
    return None


def _entry_timestamp(entry) -> float | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        if parsed:
            return float(calendar.timegm(parsed))
    return None


def parse_feed(raw: bytes, feed: Feed, now: float) -> list[Item]:
    """Parse one feed document. Entries without a date are stamped `now` (first seen)."""
    parsed = feedparser.parse(raw)
    items: list[Item] = []
    for entry in parsed.entries:
        link = entry.get("link") or entry.get("id")
        title = clean_text(entry.get("title", ""), limit=300)
        if not link or not title or not link.startswith("http"):
            continue
        url = canonicalize_url(link)
        bodies = [c.get("value", "") for c in entry.get("content", []) or []]
        summary = entry.get("summary") or next(iter(bodies), "")
        full = max([*bodies, entry.get("summary") or ""], key=len)
        published = _entry_timestamp(entry) or now
        items.append(Item(
            id=hashlib.sha1(url.encode()).hexdigest()[:16],
            title=title,
            description=clean_text(summary),
            url=link.strip(),
            image=_entry_image(entry, link),
            source_id=feed.id,
            source=feed.name,
            category=feed.category,
            source_class=feed.source_class,
            published=min(published, now),  # clamp future-dated entries
            guid=(entry.get("id") or "").strip() or None,
            content=clean_text(full, limit=CONTENT_LIMIT),
        ))
    return items
