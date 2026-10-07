import asyncio
import time

from fastapi.testclient import TestClient

from newsfeed import trending
from newsfeed.ai import AIError
from newsfeed.app import create_app
from newsfeed.demo import demo_items
from newsfeed.items import Item
from newsfeed.store import Store
from newsfeed.trending import TrendingEngine, TrendingResult, TrendingStory

HOUR = 3600


class CountingProvider:
    name = "openai"
    max_items = 150

    def __init__(self, model="m", fail=False):
        self.model, self.fail, self.calls = model, fail, 0

    async def generate(self, system, user, schema):
        self.calls += 1
        if self.fail:
            raise AIError("boom")
        return TrendingResult(stories=[TrendingStory(headline="h", summary="s", why_trending="w",
                                                     category="security", item_ids=[0, 1], momentum=50)])


def store_with_items():
    store = Store()
    store.add(demo_items(time.time()))
    return store


def add_story(store, n):
    now = time.time()
    store.add([Item(id=f"new{n}", title=f"Brand new story {n}", description="", url=f"https://example.com/n{n}",
                    image=None, source_id="bbc-world", source="BBC News", category="world",
                    source_class="publisher", published=now)], now=now)


def age_last_request(engine, store, hours):
    engine._last_ai["at"] -= hours * HOUR
    store.set_meta("trending_ai_last_request", engine._last_ai)


def test_one_ai_request_per_window_whatever_triggers_it():
    store, provider = store_with_items(), CountingProvider()
    engine = TrendingEngine(store, provider=provider)

    assert asyncio.run(engine.update(force=True)) is True
    assert provider.calls == 1
    assert engine.snapshot()["next_ai_refresh_at"] > time.time() + 3.9 * HOUR

    add_story(store, 1)  # new stories alone don't make it due
    assert not engine.stale()
    assert asyncio.run(engine.update()) is False
    assert asyncio.run(engine.update(force=True)) is False  # Regenerate button is throttled too
    assert provider.calls == 1

    age_last_request(engine, store, 4)  # window over
    assert engine.stale()
    assert asyncio.run(engine.update()) is True
    assert provider.calls == 2


def test_throttle_survives_restart():
    store, provider = store_with_items(), CountingProvider()
    asyncio.run(TrendingEngine(store, provider=provider).update(force=True))
    restarted = TrendingEngine(store, provider=provider)
    add_story(store, 1)
    assert asyncio.run(restarted.update(force=True)) is False
    assert provider.calls == 1 and restarted.snapshot()["next_ai_refresh_at"]


def test_failed_request_counts_but_changing_model_does_not_wait():
    store = store_with_items()
    failing = CountingProvider(fail=True)
    engine = TrendingEngine(store, provider=failing)
    asyncio.run(engine.update(force=True))
    assert failing.calls == 1 and engine.mode == "heuristic"
    assert asyncio.run(engine.update(force=True)) is False  # a failed request still used the window

    fixed = CountingProvider(model="other-model")
    engine = TrendingEngine(store, provider=fixed)
    assert engine.next_ai_at() is None
    assert asyncio.run(engine.update(force=True)) is True and fixed.calls == 1


def test_without_ai_keyword_clustering_keeps_its_short_cycle(monkeypatch):
    monkeypatch.setenv("NEWSFEED_DISABLE_AI", "1")
    store = store_with_items()
    engine = TrendingEngine(store)
    assert asyncio.run(engine.update(force=True)) is True
    assert engine.snapshot()["next_ai_refresh_at"] is None and engine.snapshot()["ai_enabled"] is False
    assert asyncio.run(engine.update(force=True)) is True  # forced heuristic runs are free


def test_regenerate_endpoint_reports_throttle(monkeypatch):
    provider = CountingProvider()
    monkeypatch.setattr(trending, "provider_from_env", lambda: provider)
    with TestClient(create_app(demo=True)) as client:
        first = client.post("/api/trending/refresh").json()
        second = client.post("/api/trending/refresh").json()
    assert provider.calls == 1
    assert second["throttled"] is True and second["next_ai_refresh_at"] and second["stories"] == first["stories"]
    assert second["ai_refresh_hours"] == 4
