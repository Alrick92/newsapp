"""HTTP API and static front end."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .feeds import CATEGORIES, FEEDS
from .fetcher import Fetcher
from .store import DISPLAY_HOURS, RETENTION_DAYS, Store
from .trending import TrendingEngine

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
TICK_SECONDS = 60


def create_app(*, demo: bool | None = None, poll: bool | None = None, data_dir: Path | None = None) -> FastAPI:
    demo = os.environ.get("NEWSFEED_DEMO") == "1" if demo is None else demo
    poll = (not demo) if poll is None else poll
    data_dir = data_dir or Path(os.environ.get("NEWSFEED_DATA_DIR", "data"))

    store = Store(":memory:" if demo else data_dir / "newsfeed.db")
    fetcher = Fetcher(store)
    trending = TrendingEngine(store)

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
        return {
            "window_hours": DISPLAY_HOURS,
            "retention_days": RETENTION_DAYS,
            "categories": CATEGORIES,
            "sources": [{"id": f.id, "name": f.name, "category": f.category, "source_class": f.source_class}
                        for f in FEEDS],
            "total": store.count(hours=DISPLAY_HOURS),
            "stored": store.count(),
            "last_refresh": store.last_refresh,
            "demo": demo,
        }

    @app.get("/api/items")
    def items(
        category: str | None = None,
        sources: str | None = Query(None, description="Comma-separated feed ids"),
        q: str | None = None,
        hours: float = Query(DISPLAY_HOURS, gt=0, le=RETENTION_DAYS * 24),
        has_image: bool = False,
        sort: str = Query("newest", pattern="^(newest|source)$"),
        limit: int = Query(60, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict:
        source_set = {s for s in (sources or "").split(",") if s} or None
        filters = dict(sources=source_set, q=q, hours=hours, has_image=has_image)
        total, page = store.search(category=category or None, sort=sort, limit=limit, offset=offset, **filters)
        return {
            "total": total,
            "counts": store.category_counts(**filters),
            "items": [i.to_dict() for i in page],
        }

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
        await trending.update(force=True)
        return trending.snapshot()

    @app.get("/healthz")
    def healthz() -> dict:
        """Liveness for the load balancer and the container healthcheck."""
        return {"ok": True, "stored": store.count(), "last_refresh": store.last_refresh}

    @app.get("/api/feeds")
    def feeds() -> list[dict]:
        return fetcher.status()

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
