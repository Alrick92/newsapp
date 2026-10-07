"""Polite, concurrent feed polling.

Honors ETag / Last-Modified (a 304 counts as success), polls each feed on its
own cadence, and backfills missing thumbnails from the article's og:image.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field

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
    item_count: int = 0


@dataclass
class Fetcher:
    store: Store
    feeds: tuple[Feed, ...] = FEEDS
    concurrency: int = 8
    og_image_budget: int = int(os.environ.get("NEWSFEED_OG_IMAGE_BUDGET", "40"))
    state: dict[str, FeedState] = field(default_factory=dict)
    _og_tried: set[str] = field(default_factory=set)

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
        self.store.last_refresh = time.time()
        self.store.save()
        log.info("refresh: polled %d feeds, %d new items, %d in window", len(due), added, len(self.store.items))
        return {"polled": len(due), "added": added, "total": len(self.store.items)}

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
        st.last_ok, st.last_error, st.item_count = time.time(), None, len(items)
        return items

    async def _backfill_images(self, client: httpx.AsyncClient) -> None:
        """Fetch og:image for the newest image-less items, within a per-refresh budget."""
        if self.og_image_budget <= 0:
            return
        todo = [i for i in sorted(self.store.items.values(), key=lambda i: -i.published)
                if not i.image and i.id not in self._og_tried][: self.og_image_budget]
        sem = asyncio.Semaphore(6)

        async def one(item: Item) -> None:
            self._og_tried.add(item.id)
            async with sem:
                try:
                    async with client.stream("GET", item.url, headers={"Accept": "text/html"}) as resp:
                        if resp.status_code != 200:
                            return
                        head = b""
                        async for chunk in resp.aiter_bytes():
                            head += chunk
                            if len(head) > 96_000 or b"</head>" in head:
                                break
                except httpx.HTTPError:
                    return
            match = _OG_IMAGE.search(head.decode("utf-8", "ignore"))
            if match:
                url = match.group(1) or match.group(2)
                if url.startswith("//"):
                    url = "https:" + url
                if url.startswith("http"):
                    item.image = url

        await asyncio.gather(*(one(i) for i in todo))

    def status(self) -> list[dict]:
        out = []
        for feed in self.feeds:
            st = self.state.get(feed.id, FeedState())
            out.append({
                "id": feed.id, "name": feed.name, "category": feed.category,
                "source_class": feed.source_class, "url": feed.url,
                "last_ok": st.last_ok, "last_error": st.last_error,
                "items_in_window": sum(1 for i in self.store.items.values() if i.source_id == feed.id),
            })
        return out
