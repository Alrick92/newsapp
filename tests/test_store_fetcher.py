import asyncio

import httpx
from conftest import NOW

from newsfeed.feeds import FEEDS_BY_ID
from newsfeed.fetcher import Fetcher
from newsfeed.items import Item, parse_feed
from newsfeed.store import Store


def make(id, title, hours, source="bbc-world", desc="", image=None):
    feed = FEEDS_BY_ID[source]
    return Item(id=id, title=title, description=desc, url=f"https://example.com/{id}", image=image,
                source_id=feed.id, source=feed.name, category=feed.category,
                source_class=feed.source_class, published=NOW - hours * 3600)


def test_window_dedupe_and_prune():
    store = Store()
    added = store.add([
        make("a", "Fresh story", 1),
        make("b", "Edge of window", 71.9),
        make("c", "Too old", 73),
        make("d", "Fresh Story!", 3, source="guardian-world"),  # same headline, published earlier: replaces a
        make("e", "fresh story", 0.5, source="dw-world"),  # same headline, published later: dropped
    ], now=NOW)
    assert added == 2
    assert set(store.items) == {"d", "b"}
    v = store.version

    store.prune(now=NOW + 3600)  # b falls out of the 72h window
    assert set(store.items) == {"d"}
    assert store.add([make("d", "Fresh Story!", 3, source="guardian-world")], now=NOW) == 0
    assert store.version == v  # unchanged by a no-op add


def test_earlier_duplicate_headline_replaces_later_one():
    store = Store()
    store.add([make("late", "Same headline", 1)], now=NOW)
    store.add([make("early", "same headline", 5, source="dw-world")], now=NOW)
    assert set(store.items) == {"early"}


def test_query_filters():
    store = Store()
    store.add([
        make("w1", "Summit opens", 1, desc="talks on trade"),
        make("w2", "Election recount", 30, image="https://img/x.jpg"),
        make("s1", "Ransomware hits hospital", 2, source="bleepingcomputer"),
    ], now=NOW)
    ids = lambda **kw: [i.id for i in store.query(now=NOW, **kw)]
    assert ids() == ["w1", "s1", "w2"]
    assert ids(category="security") == ["s1"]
    assert ids(hours=24) == ["w1", "s1"]
    assert ids(q="TRADE summit") == ["w1"]
    assert ids(sources={"bleepingcomputer"}) == ["s1"]
    assert ids(has_image=True) == ["w2"]
    assert ids(sort="source") == ["w1", "w2", "s1"]  # BBC News < BleepingComputer, then newest


def test_snapshot_roundtrip(tmp_path):
    store = Store(snapshot=tmp_path / "items.json")
    store.add([make("a", "Kept", 1)], now=NOW)
    store.last_refresh = NOW
    store.save()
    # Loading uses the real clock, so the fixture items are outside the window...
    fresh = Store(snapshot=tmp_path / "items.json")
    fresh.load()
    assert fresh.last_refresh == NOW
    # ...and a corrupt snapshot is ignored rather than crashing startup.
    (tmp_path / "items.json").write_text("{not json")
    Store(snapshot=tmp_path / "items.json").load()


def test_fetcher_conditional_get_and_og_image(rss_bytes, monkeypatch):
    import time as _time
    monkeypatch.setattr(_time, "time", lambda: NOW)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), request.headers.get("if-none-match")))
        if request.url.host == "feeds.bbci.co.uk":
            if request.headers.get("if-none-match") == '"v1"':
                return httpx.Response(304)
            return httpx.Response(200, content=rss_bytes, headers={"etag": '"v1"'})
        if request.url.path == "/world/old" or "news.example.org" in request.url.host:
            return httpx.Response(200, html='<html><head><meta property="og:image" content="https://img.example.org/og.jpg"></head>')
        return httpx.Response(500)

    store = Store()
    fetcher = Fetcher(store, feeds=(FEEDS_BY_ID["bbc-world"], FEEDS_BY_ID["cisa"]))
    monkeypatch.setattr(fetcher, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    summary = asyncio.run(fetcher.refresh(force=True))
    assert summary["added"] == 3  # the week-old entry is outside the window
    assert fetcher.state["cisa"].last_error  # failure recorded, other feeds unaffected
    assert all(i.image for i in store.items.values())  # no item lacks an image after og backfill

    asyncio.run(fetcher.refresh(force=True))
    assert ("https://feeds.bbci.co.uk/news/world/rss.xml", '"v1"') in calls
    assert fetcher.state["bbc-world"].last_error is None


def test_parse_feed_ignores_garbage():
    assert parse_feed(b"<html>not a feed</html>", FEEDS_BY_ID["bbc-world"], now=NOW) == []
