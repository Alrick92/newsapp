"""Polite, concurrent feed polling.

Honors ETag / Last-Modified (a 304 counts as success), polls each feed on its
own cadence, and backfills missing thumbnails from the article's og:image.
Per-feed state is persisted in the store so restarts keep conditional GETs.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field

import httpx

from .feeds import FEEDS, Feed
from .items import Item, parse_feed
from .store import Store

log = logging.getLogger(__name__)

USER_AGENT = os.environ.get(
    "NEWSFEED_USER_AGENT",
    "NewsfeedAggregator/1.0 (+https://github.com/alrick92/bot)",
)
_OG_IMAGE = re.compile(
    r"<meta[^>]+(?:property|name)=[\"'](?:og:image|twitter:image)(?::src)?[\"'][^>]*content=[\"']([^\"']+)[\"']"
    r"|<meta[^>]+content=[\"']([^\"']+)[\"'][^>]*(?:property|name)=[\"'](?:og:image|twitter:image)[\"']",
    re.I,
)


@dataclass
class FeedState:
    etag: str | None = None
    last_modified: str | None = None
    last_polled: float = 0.0
    last_ok: float | None = None
    last_error: str | None = None


@dataclass
class Fetcher:
    store: Store
    feeds: tuple[Feed, ...] = FEEDS
    concurrency: int = 8
    og_image_budget: int = int(os.environ.get("NEWSFEED_OG_IMAGE_BUDGET", "40"))
    state: dict[str, FeedState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for feed_id, saved in self.store.feed_states().items():
            self.state[feed_id] = FeedState(**saved)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"},
            timeout=httpx.Timeout(15.0, connect=8.0),
            follow_redirects=True,
        )

    async def refresh(self, force: bool = False) -> dict:
        """Poll every feed that is due; returns a small summary."""
        now = time.time()
        due = [f for f in self.feeds
               if force or now - self.state.setdefault(f.id, FeedState()).last_polled >= f.poll_minutes * 60]
        sem = asyncio.Semaphore(self.concurrency)
        async with self._client() as client:
            async def run(feed: Feed) -> list[Item]:
                async with sem:
                    return await self._poll(client, feed)
            batches = await asyncio.gather(*(run(f) for f in due))
            new_items = [i for batch in batches for i in batch]
            added = self.store.add(new_items, now=time.time())
            await self._backfill_images(client)
        for feed in due:
            self.store.save_feed_state(feed.id, **asdict(self.state[feed.id]))
        self.store.last_refresh = time.time()
        total = self.store.count()
        log.info("refresh: polled %d feeds, %d new items, %d stored", len(due), added, total)
        return {"polled": len(due), "added": added, "total": total}

    async def _poll(self, client: httpx.AsyncClient, feed: Feed) -> list[Item]:
        st = self.state.setdefault(feed.id, FeedState())
        st.last_polled = time.time()
        headers = {}
        if st.etag:
            headers["If-None-Match"] = st.etag
        if st.last_modified:
            headers["If-Modified-Since"] = st.last_modified
        try:
            resp = await client.get(feed.url, headers=headers)
            if resp.status_code == 304:
                st.last_ok, st.last_error = time.time(), None
                return []
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            st.last_error = f"{type(exc).__name__}: {exc}"[:200]
            log.warning("feed %s failed: %s", feed.id, st.last_error)
            return []
        st.etag = resp.headers.get("etag")
        st.last_modified = resp.headers.get("last-modified")
        items = parse_feed(resp.content, feed, now=time.time())
        st.last_ok, st.last_error = time.time(), None
        return items

    async def _backfill_images(self, client: httpx.AsyncClient) -> None:
        """Fetch og:image for the newest image-less items, within a per-refresh budget."""
        if self.og_image_budget <= 0:
            return
        todo = self.store.missing_images(self.og_image_budget)
        sem = asyncio.Semaphore(6)

        async def one(item: Item) -> str | None:
            async with sem:
                try:
                    async with client.stream("GET", item.url, headers={"Accept": "text/html"}) as resp:
                        if resp.status_code != 200:
                            return None
                        head = b""
                        async for chunk in resp.aiter_bytes():
                            head += chunk
                            if len(head) > 96_000 or b"</head>" in head:
                                break
                except httpx.HTTPError:
                    return None
            match = _OG_IMAGE.search(head.decode("utf-8", "ignore"))
            if not match:
                return None
            url = match.group(1) or match.group(2)
            if url.startswith("//"):
                url = "https:" + url
            return url if url.startswith("http") else None

        images = await asyncio.gather(*(one(i) for i in todo))
        for item, image in zip(todo, images):
            self.store.set_image(item.id, image)

    def status(self) -> list[dict]:
        counts = self.store.source_counts()
        out = []
        for feed in self.feeds:
            st = self.state.get(feed.id, FeedState())
            out.append({
                "id": feed.id, "name": feed.name, "category": feed.category,
                "source_class": feed.source_class, "url": feed.url,
                "last_ok": st.last_ok, "last_error": st.last_error,
                "items_last_72h": counts.get(feed.id, 0),
            })
        return out
