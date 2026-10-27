import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from newsfeed import trending
from newsfeed.ai import AIError
from newsfeed.app import create_app
from newsfeed.compare import CompareError, Comparer, CoverageComparison, render, sides_of
from newsfeed.demo import demo_items
from newsfeed.feeds import FEEDS_BY_ID
from newsfeed.items import Item
from newsfeed.store import Store
from newsfeed.stories import cluster, title_tokens

NOW = time.time()


def item(n, feed_id, title, hours=1.0):
    feed = FEEDS_BY_ID[feed_id]
    return Item(id=f"{n:016x}", title=title, description=f"snippet {n}", url=f"https://example.com/{n}", image=None,
                source_id=feed.id, source=feed.name, category=feed.category, source_class=feed.source_class,
                published=NOW - hours * 3600)


def stories_of(store):
    return {lead.title: sorted(o.title for o in others) for lead, others in store.search_stories(limit=100, hours=72)[1]}


def test_title_tokens_stem_and_drop_stopwords():
    assert title_tokens("Gateways exploited after the exploitation report") == {"gateway", "exploit"}


def test_cluster_groups_related_headlines_and_keeps_assignments():
    store = Store()
    store.add([item(1, "bbc-world", "Port strike halts container shipping in Rotterdam", 5),
               item(2, "fox-world", "Rotterdam port strike halts shipping for second day", 3),
               item(3, "dw-world", "Glacier melt accelerates in the Alps, study finds", 4)], now=NOW)
    assert cluster(store, NOW) == 3 and cluster(store, NOW) == 0  # nothing new the second time
    groups = stories_of(store)
    assert groups["Rotterdam port strike halts shipping for second day"] == ["Port strike halts container shipping in Rotterdam"]
    assert groups["Glacier melt accelerates in the Alps, study finds"] == []

    # A later article joins the existing story; the story keeps its first item's id.
    store.add([item(4, "nypost-world", "Shipping strike at Rotterdam port spreads to Antwerp", 1)], now=NOW)
    cluster(store, NOW)
    total, page = store.search_stories(limit=100, hours=72)
    assert total == 2
    lead, others = page[0]
    assert lead.id == f"{4:016x}" and len(others) == 2


def test_grouped_filters_match_any_member_but_return_the_whole_story():
    store = Store()
    store.add([item(1, "bbc-world", "Port strike halts container shipping in Rotterdam", 5),
               item(2, "fox-politics", "Rotterdam port strike halts shipping, lawmakers react", 3)], now=NOW)
    cluster(store, NOW)
    total, page = store.search_stories(limit=10, category="world", hours=72)
    assert total == 1
    lead, others = page[0]
    assert lead.source_id == "bbc-world" and [o.source_id for o in others] == ["fox-politics"]
    store.set_read([lead.id], True)
    assert store.search_stories(limit=10, unread=True, hours=72)[0] == 1  # the other article is unread
    store.set_read([others[0].id], True)
    assert store.search_stories(limit=10, unread=True, hours=72)[0] == 0


class FakeProvider:
    name, model, max_items = "openai", "test-model", 150

    def __init__(self, fail=False):
        self.calls, self.fail, self.user = 0, fail, None

    async def generate(self, system, user, schema):
        if schema is not CoverageComparison:
            raise AIError("trending isn't under test")
        self.calls += 1
        self.user = user
        if self.fail:
            raise AIError("boom")
        return CoverageComparison(common="Both report the strike.", left="Workers' pay.", center="Disruption.",
                                  right="Costs to business.", differences=["Left quotes unions."])


def strike_store():
    store = Store()
    store.add([item(1, "guardian-world", "Port strike halts shipping in Rotterdam", 5),
               item(2, "bbc-world", "Rotterdam port strike halts shipping", 4),
               item(3, "dw-world", "Glacier melt accelerates in the Alps", 4)], now=NOW)
    return store


def test_sides_and_render_leave_out_unrated_sources():
    store = strike_store()
    store.add([item(9, "bleepingcomputer", "Port systems hit during Rotterdam strike", 1)], now=NOW)
    sides = sides_of(store.items_by_id([f"{1:016x}", f"{2:016x}", f"{9:016x}"]), FEEDS_BY_ID)
    assert [r["source"] for r in sides["left"]] == ["The Guardian"]
    assert [r["source"] for r in sides["center"]] == ["BBC News"] and sides["right"] == []
    text = render(sides)
    assert "RIGHT (0 articles)\n(none)" in text and "BleepingComputer" not in text


def test_comparer_caches_validates_and_limits(monkeypatch):
    store = strike_store()
    provider = FakeProvider()
    comparer = Comparer(store, lambda: provider, lambda: FEEDS_BY_ID)
    ids = [f"{1:016x}", f"{2:016x}"]
    result, cached = asyncio.run(comparer.compare(ids))
    assert not cached and result["common"] == "Both report the strike."
    assert result["right"] == ""  # no right-leaning article, whatever the model said
    assert result["counts"] == {"left": 1, "center": 1, "right": 0}
    assert asyncio.run(comparer.compare(list(reversed(ids))))[1] is True and provider.calls == 1

    with pytest.raises(CompareError) as err:  # one side only
        asyncio.run(comparer.compare([f"{1:016x}"]))
    assert err.value.status == 422
    with pytest.raises(CompareError) as err:
        asyncio.run(comparer.compare(["nope"]))
    assert err.value.status == 404
    with pytest.raises(CompareError) as err:
        asyncio.run(Comparer(store, lambda: None, lambda: FEEDS_BY_ID).compare(ids))
    assert err.value.status == 404

    monkeypatch.setattr("newsfeed.compare.COMPARISONS_PER_HOUR", 1)
    store.add([item(4, "fox-world", "Rotterdam port strike costs mount", 1)], now=NOW)
    with pytest.raises(CompareError) as err:  # new coverage means a new comparison, over the limit
        asyncio.run(comparer.compare([*ids, f"{4:016x}"]))
    assert err.value.status == 429

    failing = Comparer(store, lambda: FakeProvider(fail=True), lambda: FEEDS_BY_ID)
    with pytest.raises(CompareError) as err:
        asyncio.run(failing.compare([*ids, f"{4:016x}"]))
    assert err.value.status == 502


def test_api_grouped_items_and_compare(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(trending, "provider_from_env", lambda: provider)
    with TestClient(create_app(demo=True, poll=False)) as client:
        assert client.get("/api/meta").json()["compare_enabled"] is True
        flat = client.get("/api/items", params={"limit": 200}).json()
        grouped = client.get("/api/items", params={"limit": 200, "group": "true"}).json()
        assert grouped["grouped"] and not flat["grouped"]
        assert grouped["total"] < flat["total"]
        assert sum(e["story"]["size"] for e in grouped["items"]) == flat["total"]
        stopgap = next(e for e in grouped["items"] if "stopgap" in e["title"].lower())
        assert stopgap["story"]["coverage"]["sides"] == {"left": 1, "center": 1, "right": 1}
        assert all("category" in m for m in stopgap["story"]["items"])

        # Searching or sorting by source lists articles.
        assert not client.get("/api/items", params={"group": "true", "q": "stopgap"}).json()["grouped"]
        assert not client.get("/api/items", params={"group": "true", "sort": "source"}).json()["grouped"]

        ids = [stopgap["id"], *(m["id"] for m in stopgap["story"]["items"])]
        res = client.post("/api/compare", json={"ids": ids}).json()
        assert res["cached"] is False and res["counts"] == {"left": 1, "center": 1, "right": 1}
        assert client.post("/api/compare", json={"ids": ids}).json()["cached"] is True
        assert client.post("/api/compare", json={"ids": []}).status_code == 422

        clinics = next(e for e in grouped["items"] if "clinics" in e["title"].lower())
        one_side = client.post("/api/compare", json={"ids": [clinics["id"], *(m["id"] for m in clinics["story"]["items"])]})
        assert one_side.status_code == 422


def test_purge_drops_old_comparisons():
    store = Store(retention_days=1)
    store.set_meta("compare:old", {"at": NOW - 3 * 86400, "result": {}})
    store.set_meta("compare:new", {"at": NOW, "result": {}})
    store.purge(NOW)
    assert store.get_meta("compare:old") is None and store.get_meta("compare:new")


def test_demo_stories_cluster():
    store = Store()
    store.add(demo_items(NOW), now=NOW)
    cluster(store, NOW)
    groups = stories_of(store)
    fisheries = next(v for k, v in groups.items() if "fisheries" in k.lower())
    assert len(fisheries) == 3
