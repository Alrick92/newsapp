import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from newsfeed import trending
from newsfeed.app import create_app
from newsfeed.ai import AIError
from newsfeed.feeds import FEEDS_BY_ID
from newsfeed.items import Item
from newsfeed.store import Store
from newsfeed.summaries import ArticleSummary, SUMMARY_MIN_CHARS, Summarizer, SummaryError

LONG = "The council voted to expand the program after months of debate. " * 20


class FakeProvider:
    name, model, max_items = "openai", "test-model", 150

    def __init__(self, fail=False):
        self.calls, self.fail = 0, fail

    async def generate(self, system, user, schema):
        self.calls += 1
        if self.fail:
            raise AIError("boom")
        if schema is ArticleSummary:
            return ArticleSummary(summary="  The council expanded the program.  ")
        return schema(stories=[])


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("NEWSFEED_DISABLE_AI", "1")
    with TestClient(create_app(demo=True)) as c:
        yield c


def first_ids(client, n=3, **params):
    return [i["id"] for i in client.get("/api/items", params={"limit": n, **params}).json()["items"]]


def test_read_unread_and_counts(client):
    a, b, _ = first_ids(client)
    before = client.get("/api/items").json()
    assert sum(before["unread_counts"].values()) == before["total"]  # everything starts unread

    assert client.post(f"/api/items/{a}/read").json() == {"changed": [a]}
    assert client.post(f"/api/items/{a}/read").json() == {"changed": []}  # already read
    item = next(i for i in client.get("/api/items").json()["items"] if i["id"] == a)
    assert item["read_at"]
    assert a not in first_ids(client, 100, unread=True)

    client.post(f"/api/items/{a}/read", json={"read": False})
    assert a in first_ids(client, 100, unread=True)


def test_mark_all_read_and_undo(client):
    sec = client.get("/api/items", params={"category": "security"}).json()["total"]
    changed = client.post("/api/items/mark-all-read", params={"category": "security"}).json()["changed"]
    assert len(changed) == sec
    after = client.get("/api/items").json()
    assert after["unread_counts"].get("security", 0) == 0 and after["unread_counts"]["world"] > 0
    assert client.post("/api/items/read", json={"ids": changed, "read": False}).json()["changed"] == changed
    assert client.get("/api/items").json()["unread_counts"]["security"] == sec


def test_star_and_starred_view(client):
    a = first_ids(client, 1)[0]
    assert client.post(f"/api/items/{a}/star").json() == {"starred": True}
    starred = client.get("/api/items", params={"starred": True}).json()
    assert [i["id"] for i in starred["items"]] == [a] and client.get("/api/meta").json()["starred"] == 1
    client.post(f"/api/items/{a}/star", json={"starred": False})
    assert client.get("/api/items", params={"starred": True}).json()["total"] == 0
    assert client.post("/api/items/nope/star").status_code == 404


def test_search_endpoint_uses_full_text(client):
    res = client.get("/api/items", params={"q": "patch available", "sort": "relevance"}).json()
    assert res["total"] >= 1 and "\x02" in (res["items"][0]["snippet"] or "")
    assert client.get("/api/items", params={"q": '"); DROP TABLE items; --'}).status_code == 200


def test_summaries_hidden_without_provider(client):
    assert client.get("/api/meta").json()["summaries_enabled"] is False
    assert all(i["summarizable"] is False for i in client.get("/api/items").json()["items"])
    a = first_ids(client, 1)[0]
    assert client.post(f"/api/items/{a}/summary").status_code == 404


def make_store_with_long_item():
    store = Store()
    feed = FEEDS_BY_ID["bbc-world"]
    store.add([Item(id="long", title="Council vote", description="Short", url="https://x/long", image=None,
                    source_id=feed.id, source=feed.name, category=feed.category, source_class=feed.source_class,
                    published=time.time(), content=LONG),
               Item(id="short", title="Teaser only", description="Just a line.", url="https://x/short", image=None,
                    source_id=feed.id, source=feed.name, category=feed.category, source_class=feed.source_class,
                    published=time.time())])
    return store


def test_summary_is_saved_and_reused():
    store, provider = make_store_with_long_item(), FakeProvider()
    summarizer = Summarizer(store, lambda: provider)
    assert summarizer.can_summarize(len(LONG)) and not summarizer.can_summarize(SUMMARY_MIN_CHARS - 1)
    assert asyncio.run(summarizer.summarize("long")) == ("The council expanded the program.", False)
    assert asyncio.run(summarizer.summarize("long")) == ("The council expanded the program.", True)
    assert provider.calls == 1 and store.get("long").summary == "The council expanded the program."


@pytest.mark.parametrize("item_id,status", [("short", 422), ("missing", 404)])
def test_summary_refuses_teasers_and_unknown_items(item_id, status):
    summarizer = Summarizer(make_store_with_long_item(), lambda: FakeProvider())
    with pytest.raises(SummaryError) as err:
        asyncio.run(summarizer.summarize(item_id))
    assert err.value.status == status


def test_summary_rate_limit_and_provider_failure(monkeypatch):
    monkeypatch.setattr("newsfeed.summaries.SUMMARIES_PER_HOUR", 1)
    store = make_store_with_long_item()
    failing = Summarizer(store, lambda: FakeProvider(fail=True))
    with pytest.raises(SummaryError) as err:
        asyncio.run(failing.summarize("long"))
    assert err.value.status == 502
    with pytest.raises(SummaryError) as err:  # the failed request still used this hour's one slot
        asyncio.run(failing.summarize("long"))
    assert err.value.status == 429 and "per hour" in str(err.value)


def test_summary_endpoint_with_provider(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(trending, "provider_from_env", lambda: provider)
    with TestClient(create_app(demo=True)) as client:
        assert client.get("/api/meta").json()["summaries_enabled"] is True
        items = client.get("/api/items", params={"limit": 200}).json()["items"]
        # Only stories with real body text get a summary button.
        summarizable = [i for i in items if i["summarizable"]]
        assert [i["source"] for i in summarizable] == ["Federal Reserve"]
        teaser = next(i for i in items if not i["summarizable"])
        assert client.post(f"/api/items/{teaser['id']}/summary").status_code == 422
        res = client.post(f"/api/items/{summarizable[0]['id']}/summary").json()
        assert res == {"summary": "The council expanded the program.", "cached": False}
