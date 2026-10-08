import asyncio
import json

import httpx
from conftest import NOW

from newsfeed.feeds import FEEDS_BY_ID
from newsfeed.fetcher import Fetcher
from newsfeed.items import Item, parse_feed
from newsfeed.store import Store

DAY = 86400


def make(id, title, hours, source="bbc-world", desc="", image=None):
    feed = FEEDS_BY_ID[source]
    return Item(id=id, title=title, description=desc, url=f"https://example.com/{id}", image=image,
                source_id=feed.id, source=feed.name, category=feed.category,
                source_class=feed.source_class, published=NOW - hours * 3600)


def ids(store, **kw):
    kw.setdefault("now", NOW)
    return [i.id for i in store.query(**kw)]


def test_retention_window_and_purge():
    store = Store(retention_days=30)
    added = store.add([
        make("a", "Fresh story", 1),
        make("b", "Last week", 24 * 7),
        make("c", "Edge of retention", 24 * 29.99),
        make("d", "Too old", 24 * 31),
    ], now=NOW)
    assert added == 3
    assert store.count(now=NOW) == 3
    assert ids(store) == ["a"]  # default view is the last 72 hours
    assert ids(store, hours=24 * 30) == ["a", "b", "c"]
    v = store.version

    assert store.purge(now=NOW + 3600) == 1  # c passes 30 days and is deleted
    assert ids(store, hours=24 * 30, now=NOW + 3600) == ["a", "b"]
    assert store.version == v + 1
    assert store.add([make("a", "Fresh story", 1)], now=NOW + 3600) == 0


def test_headline_duplicates_become_extra_sources_of_one_story():
    store = Store()
    assert store.add([
        make("late", "Same headline", 1),
        make("early", "same headline!", 5, source="dw-world"),  # another feed: recorded as a source
        make("again", "Same Headline", 0.5, source="dw-world"),  # that feed again: ignored
        make("weekly", "Same headline", 24 * 5, source="guardian-world"),  # days apart: a distinct story
    ], now=NOW) == 2
    assert ids(store, hours=24 * 30) == ["late", "weekly"]
    [story] = store.query(now=NOW, sources={"bbc-world"})
    assert [a["source"] for a in story.also] == ["DW"]


def test_query_filters_and_paging():
    store = Store()
    store.add([
        make("w1", "Summit opens", 1, desc="talks on trade 100%"),
        make("w2", "Election recount", 30, image="https://img/x.jpg"),
        make("s1", "Ransomware hits hospital", 2, source="bleepingcomputer"),
    ], now=NOW)
    assert ids(store) == ["w1", "s1", "w2"]
    assert ids(store, category="security") == ["s1"]
    assert ids(store, hours=24) == ["w1", "s1"]
    assert ids(store, q="TRADE summit") == ["w1"]  # full-text search over title and text
    assert ids(store, q="100%") == ["w1"] and ids(store, q="_") == []  # punctuation can't break the query
    assert ids(store, sources={"bleepingcomputer"}) == ["s1"]
    assert ids(store, has_image=True) == ["w2"]
    assert ids(store, sort="source") == ["w1", "w2", "s1"]  # BBC News < BleepingComputer, then newest
    total, page = store.search(now=NOW, limit=1, offset=1)
    assert total == 3 and [i.id for i in page] == ["s1"]
    totals, unread = store.category_counts(now=NOW, category="world")
    assert totals == {"world": 2, "security": 1} and unread == totals


def test_everything_survives_restart(tmp_path):
    db = tmp_path / "newsfeed.db"
    store = Store(db)
    store.add([make("a", "Kept", 1)], now=NOW)
    store.last_refresh = NOW
    store.save_feed_state("bbc-world", etag='"v1"', last_modified=None, last_polled=NOW, last_ok=NOW, last_error=None)
    store.set_meta("trending", {"stories": [{"headline": "x"}], "mode": "heuristic", "generated_at": NOW})
    store.close()

    again = Store(db)
    assert ids(again) == ["a"]
    assert again.last_refresh == NOW
    assert again.feed_states()["bbc-world"]["etag"] == '"v1"'
    assert again.get_meta("trending")["stories"] == [{"headline": "x"}]


def test_imports_old_json_snapshot_once(tmp_path):
    snap = tmp_path / "items.json"
    snap.write_text(json.dumps({"last_refresh": NOW, "items": [make("a", "Old snapshot story", 1).to_dict()]}))
    # The fixture clock is in the past relative to the real one; widen retention so the item counts.
    store = Store(retention_days=3650)
    assert store.import_json_snapshot(snap) == 1
    assert not snap.exists() and (tmp_path / "items.json.imported").exists()
    assert store.import_json_snapshot(snap) == 0
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert store.import_json_snapshot(bad) == 0 and bad.exists()


def test_fetcher_conditional_get_and_og_image(rss_bytes, monkeypatch, tmp_path):
    import time as _time
    monkeypatch.setattr(_time, "time", lambda: NOW)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), request.headers.get("if-none-match")))
        if request.url.host == "feeds.bbci.co.uk":
            if request.headers.get("if-none-match") == '"v1"':
                return httpx.Response(304)
            return httpx.Response(200, content=rss_bytes, headers={"etag": '"v1"'})
        if "news.example.org" in request.url.host:
            return httpx.Response(200, html='<html><head><meta property="og:image" content="https://img.example.org/og.jpg"></head>')
        return httpx.Response(500)

    feeds = (FEEDS_BY_ID["bbc-world"], FEEDS_BY_ID["cisa"])
    store = Store(tmp_path / "newsfeed.db")
    fetcher = Fetcher(store, feeds=feeds)
    monkeypatch.setattr(fetcher, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    summary = asyncio.run(fetcher.refresh(force=True))
    assert summary["added"] == 4  # the week-old entry is kept now (30-day retention)
    assert fetcher.state["cisa"].last_error  # failure recorded, other feeds unaffected
    assert all(i.image for i in store.query(hours=24 * 30))  # og:image filled the gaps
    assert store.missing_images(10) == []

    # A new process reuses the saved ETag, so the next poll is a cheap 304.
    restarted = Fetcher(store, feeds=feeds)
    monkeypatch.setattr(restarted, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    asyncio.run(restarted.refresh(force=True))
    assert ("https://feeds.bbci.co.uk/news/world/rss.xml", '"v1"') in calls
    assert restarted.state["bbc-world"].last_error is None


def test_parse_feed_ignores_garbage():
    assert parse_feed(b"<html>not a feed</html>", FEEDS_BY_ID["bbc-world"], now=NOW) == []


def test_idle_check_makes_no_http_client(tmp_path):
    import time as _time
    from newsfeed.fetcher import FeedState
    store = Store(tmp_path / "newsfeed.db")
    fetcher = Fetcher(store, feeds=(FEEDS_BY_ID["bbc-world"],))
    fetcher.state["bbc-world"] = FeedState(last_polled=_time.time())  # polled just now: nothing due

    def no_client():
        raise AssertionError("an HTTP client was created although no feed was due")

    fetcher._client = no_client
    assert asyncio.run(fetcher.refresh()) == {"polled": 0, "added": 0, "total": 0}
