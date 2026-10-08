"""HTTP API and static front end."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from fastapi.staticfiles import StaticFiles

from .feeds import DEFAULT, FeedList, default_opml_path
from .fetcher import Fetcher
from .opml import write_opml
from .store import DISPLAY_HOURS, RETENTION_DAYS, Store
from .summaries import Summarizer, SummaryError
from .trending import TrendingEngine

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
TICK_SECONDS = 60


def static_version() -> str:
    """Hash of the app's static files; changes whenever a deploy changes any of them,
    which makes browsers install the new service worker and refresh their cache."""
    digest = hashlib.sha256()
    for path in sorted(p for p in STATIC.rglob("*") if p.is_file()):
        digest.update(path.relative_to(STATIC).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


class ReadState(BaseModel):
    read: bool = True


class StarState(BaseModel):
    starred: bool = True


class BulkRead(BaseModel):
    ids: list[str] = Field(max_length=5000)
    read: bool = True


def create_app(*, demo: bool | None = None, poll: bool | None = None, data_dir: Path | None = None) -> FastAPI:
    demo = os.environ.get("NEWSFEED_DEMO") == "1" if demo is None else demo
    poll = (not demo) if poll is None else poll
    data_dir = data_dir or Path(os.environ.get("NEWSFEED_DATA_DIR", "data"))

    store = Store(":memory:" if demo else data_dir / "newsfeed.db")
    # Demo mode uses the bundled list; otherwise the editable OPML in the data dir.
    feed_list = None if demo else FeedList(default_opml_path(data_dir), data_dir / "import.opml")

    def catalog():
        return feed_list.catalog if feed_list else DEFAULT

    fetcher = Fetcher(store, feed_list=feed_list) if feed_list else Fetcher(store)
    trending = TrendingEngine(store, categories=lambda: catalog().categories)
    summarizer = Summarizer(store, trending.provider)

    async def loop() -> None:
        while True:
            try:
                await fetcher.refresh()
                await trending.update()
            except Exception:  # keep the poller alive whatever one cycle does
                log.exception("refresh cycle failed")
            await asyncio.sleep(TICK_SECONDS)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        if demo:
            from .demo import demo_items
            store.add(demo_items(time.time()))
            store.last_refresh = time.time()
        else:
            store.import_json_snapshot(data_dir / "items.json")  # pre-SQLite installs
        task = asyncio.create_task(loop()) if poll else None
        if not poll:
            asyncio.create_task(trending.update(force=True))
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        store.close()

    app = FastAPI(title="Newsfeed", lifespan=lifespan)
    app.state.store, app.state.fetcher, app.state.trending = store, fetcher, trending

    @app.get("/api/meta")
    def meta() -> dict:
        cat = catalog()
        return {
            "window_hours": DISPLAY_HOURS,
            "retention_days": RETENTION_DAYS,
            "categories": cat.categories,
            "regions": cat.regions,
            "region_all_labels": cat.region_all_labels,
            "sources": [{"id": f.id, "name": f.name, "category": f.category, "source_class": f.source_class,
                         "region": f.region} for f in cat.feeds],
            "feed_list_error": feed_list.error if feed_list else None,
            "summaries_enabled": summarizer.enabled,
            "starred": store.starred_count(),
            "total": store.count(hours=DISPLAY_HOURS),
            "stored": store.count(),
            "last_refresh": store.last_refresh,
            "demo": demo,
        }

    def list_filters(category, sources, q, hours, has_image, unread, starred) -> dict:
        return dict(category=category or None, sources={s for s in (sources or "").split(",") if s} or None,
                    q=q or None, hours=hours, has_image=has_image, unread=unread, starred=starred)

    @app.get("/api/items")
    def items(
        category: str | None = None,
        sources: str | None = Query(None, description="Comma-separated feed ids"),
        q: str | None = Query(None, description="Full-text search over titles and feed text"),
        hours: float = Query(DISPLAY_HOURS, gt=0, le=RETENTION_DAYS * 24),
        has_image: bool = False,
        unread: bool = False,
        starred: bool = Query(False, description="Starred items of any age"),
        sort: str = Query("newest", pattern="^(newest|source|relevance)$"),
        limit: int = Query(60, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict:
        filters = list_filters(category, sources, q, hours, has_image, unread, starred)
        total, page = store.search(sort=sort, limit=limit, offset=offset, **filters)
        totals, unread_counts = store.category_counts(**filters)
        return {
            "total": total,
            "counts": totals,
            "unread_counts": unread_counts,
            "items": [{**i.to_dict(), "summarizable": summarizer.can_summarize(i.content_len)} for i in page],
        }

    @app.post("/api/items/{item_id}/read")
    def mark_read(item_id: str, body: ReadState = Body(default_factory=ReadState)) -> dict:
        return {"changed": store.set_read([item_id], body.read)}

    @app.post("/api/items/read")
    def bulk_read(body: BulkRead) -> dict:
        """Set read state for many items at once (also used to undo "mark all read")."""
        return {"changed": store.set_read(body.ids, body.read)}

    @app.post("/api/items/mark-all-read")
    def mark_all_read(
        category: str | None = None, sources: str | None = None, q: str | None = None,
        hours: float = Query(DISPLAY_HOURS, gt=0, le=RETENTION_DAYS * 24), has_image: bool = False,
        starred: bool = False,
    ) -> dict:
        """Mark everything matching the list's filters read; returns the ids changed, for undo."""
        filters = list_filters(category, sources, q, hours, has_image, True, starred)
        return {"changed": store.set_read(store.ids(**filters), True)}

    @app.post("/api/items/{item_id}/star")
    def star(item_id: str, body: StarState = Body(default_factory=StarState)) -> dict:
        if not store.set_starred(item_id, body.starred):
            raise HTTPException(404, "That story is no longer stored.")
        return {"starred": body.starred}

    @app.post("/api/items/{item_id}/summary")
    async def summarize(item_id: str) -> dict:
        try:
            summary, cached = await summarizer.summarize(item_id)
        except SummaryError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        return {"summary": summary, "cached": cached}

    @app.get("/api/trending")
    async def get_trending() -> dict:
        if trending.stale() and not trending.running:
            asyncio.create_task(trending.update())
        return trending.snapshot()

    @app.post("/api/refresh")
    async def refresh() -> dict:
        if demo:
            return {"polled": 0, "added": 0, "total": store.count(), "demo": True}
        result = await fetcher.refresh(force=True)
        asyncio.create_task(trending.update())
        return result

    @app.post("/api/trending/refresh")
    async def refresh_trending() -> dict:
        # Throttled like every other trigger: within the AI window this returns
        # the current stories with throttled=true and next_ai_refresh_at set.
        ran = await trending.update(force=True)
        return {**trending.snapshot(), "throttled": not ran and trending.next_ai_at() is not None}

    @app.get("/healthz")
    def healthz() -> dict:
        """Liveness for the load balancer and the container healthcheck."""
        return {"ok": True, "stored": store.count(), "last_refresh": store.last_refresh}

    @app.get("/api/feeds")
    def feeds() -> list[dict]:
        return fetcher.status()

    @app.get("/export.opml")
    def export_opml() -> Response:
        """The current feed list as OPML, for importing into another reader."""
        return Response(write_opml(catalog()), media_type="text/x-opml; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="newsfeed.opml"'})

    sw_source = (STATIC / "sw.js").read_text().replace("__VERSION__", static_version())

    @app.get("/sw.js")
    def service_worker() -> Response:
        # Served from the root so it controls the whole app; never cached by the
        # browser, so a new deploy is noticed on the next visit.
        return Response(sw_source, media_type="text/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    @app.get("/manifest.webmanifest")
    def manifest() -> FileResponse:
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
