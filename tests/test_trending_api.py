import asyncio
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from newsfeed.ai import AnthropicProvider
from newsfeed.app import create_app
from newsfeed.demo import demo_items
from newsfeed.store import Store
from newsfeed.trending import TrendingEngine, TrendingResult, TrendingStory, ai_trending, heuristic_trending


def test_heuristic_clusters_multi_source_stories():
    now = time.time()
    stories = heuristic_trending(demo_items(now), now)
    headlines = " | ".join(s["headline"] for s in stories)
    assert "ExampleVPN" in headlines
    assert "fisheries" in headlines.lower()
    top = stories[0]
    assert top["source_count"] >= 2 and 1 <= top["momentum"] <= 100
    assert all(len({i["source"] for i in s["items"]}) >= 2 for s in stories)


class FakeMessages:
    def __init__(self, result, stop_reason="end_turn"):
        self.result, self.stop_reason, self.kwargs = result, stop_reason, None

    async def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(parsed_output=self.result, stop_reason=self.stop_reason)


def fake_client(result, stop_reason="end_turn"):
    messages = FakeMessages(result, stop_reason)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def test_ai_trending_maps_ids_and_validates():
    now = time.time()
    items = demo_items(now)
    result = TrendingResult(stories=[
        TrendingStory(headline="VPN flaw exploited", summary="s", why_trending="w", category="security",
                      item_ids=[17, 18, 18, 999], momentum=140),
        TrendingStory(headline="Hallucinated", summary="s", why_trending="w", category="ai", item_ids=[999], momentum=90),
    ])
    client, messages = fake_client(result)
    provider = AnthropicProvider(model="claude-opus-5-5", client=client)
    stories = asyncio.run(ai_trending(items, now, provider))

    assert [s["headline"] for s in stories] == ["VPN flaw exploited"]  # story with no valid ids dropped
    assert stories[0]["momentum"] == 100  # clamped
    assert [i["source"] for i in stories[0]["items"]] == ["BleepingComputer", "The Hacker News"]
    assert messages.kwargs["model"] == "claude-opus-5-5"
    assert messages.kwargs["output_format"] is TrendingResult
    assert messages.kwargs["fallbacks"] == "default"
    assert "[17]" in messages.kwargs["messages"][0]["content"]


def test_engine_falls_back_to_heuristic_on_refusal():
    store = Store()
    store.add(demo_items(time.time()))
    client, _ = fake_client(None, stop_reason="refusal")
    engine = TrendingEngine(store, provider=AnthropicProvider(client=client))
    asyncio.run(engine.update(force=True))
    snap = engine.snapshot()
    assert snap["mode"] == "heuristic" and snap["stories"] and "refusal" in snap["error"]
    assert snap["error"].startswith("anthropic:")
    assert not engine.stale()

    # A restarted process reuses the saved result instead of calling Claude again.
    restarted = TrendingEngine(store, provider=AnthropicProvider(client=client))
    assert restarted.snapshot()["stories"] == snap["stories"] and not restarted.stale()


def test_api_in_demo_mode(monkeypatch):
    monkeypatch.setenv("NEWSFEED_DISABLE_AI", "1")
    with TestClient(create_app(demo=True)) as client:
        meta = client.get("/api/meta").json()
        assert meta["demo"] and meta["total"] > 20 and set(meta["categories"]) == {"world", "ai", "tech", "security", "economy", "local", "blogs"}

        all_items = client.get("/api/items").json()
        assert all_items["total"] == meta["total"]
        assert sum(all_items["counts"].values()) == meta["total"]

        sec = client.get("/api/items", params={"category": "security", "hours": 24}).json()
        assert sec["total"] and all(i["category"] == "security" for i in sec["items"])
        assert all(time.time() - i["published"] <= 24 * 3600 for i in sec["items"])

        found = client.get("/api/items", params={"q": "examplevpn", "sources": "cisa,bleepingcomputer"}).json()
        assert {i["source"] for i in found["items"]} == {"CISA", "BleepingComputer"}

        page = client.get("/api/items", params={"limit": 5, "offset": 5}).json()
        assert len(page["items"]) == 5

        assert client.get("/api/items", params={"hours": 24 * 30}).status_code == 200
        assert client.get("/api/items", params={"hours": 24 * 31}).status_code == 422

        data = client.post("/api/trending/refresh").json()
        assert data["mode"] == "heuristic" and data["stories"]

        assert client.post("/api/refresh").json()["demo"] is True
        assert client.get("/healthz").json()["ok"] is True
        assert "Newsfeed" in client.get("/").text
        assert client.get("/static/app.js").status_code == 200


def test_local_feeds_are_tagged_by_state(monkeypatch):
    from newsfeed.feeds import FEEDS, REGIONS
    assert set(REGIONS) == {"GA", "MD", "PA", "VA", "WV"}
    for feed in FEEDS:
        assert (feed.region in REGIONS) == (feed.category == "local"), feed.id
    monkeypatch.setenv("NEWSFEED_DISABLE_AI", "1")
    with TestClient(create_app(demo=True)) as client:
        meta = client.get("/api/meta").json()
        assert meta["regions"]["WV"] == "West Virginia"
        md = [s["id"] for s in meta["sources"] if s["region"] == "MD"]
        found = client.get("/api/items", params={"category": "local", "sources": ",".join(md)}).json()
        assert found["total"] and {i["source"] for i in found["items"]} <= {"Maryland Matters", "The Baltimore Sun", "Baltimore Brew"}
