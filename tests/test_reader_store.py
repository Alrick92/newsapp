import sqlite3
from datetime import datetime

from conftest import NOW

from newsfeed.feeds import FEEDS_BY_ID
from newsfeed.items import Item
from newsfeed.store import SCHEMA_VERSION, Store, fts_query

DAY = 86400


def make(id, title, hours=1, source="bbc-world", content="", guid=None, desc=""):
    feed = FEEDS_BY_ID[source]
    return Item(id=id, title=title, description=desc, url=f"https://example.com/{id}", image=None,
                source_id=feed.id, source=feed.name, category=feed.category, source_class=feed.source_class,
                published=NOW - hours * 3600, guid=guid, content=content)


def test_existing_database_is_upgraded_in_place(tmp_path):
    db = tmp_path / "newsfeed.db"
    old = sqlite3.connect(db)  # the pre-reader schema, with one stored story
    old.executescript("""
        CREATE TABLE items (id TEXT PRIMARY KEY, title TEXT NOT NULL, norm_title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '', url TEXT NOT NULL, image TEXT, source_id TEXT NOT NULL,
            source TEXT NOT NULL, category TEXT NOT NULL, source_class TEXT NOT NULL, published REAL NOT NULL,
            first_seen REAL NOT NULL, og_checked INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);""")
    old.execute("INSERT INTO items VALUES ('a','Harbour strike ends','harbour strike ends','Dock workers return',"
                "'https://x/a',NULL,'bbc-world','BBC News','world','publisher',?,?,0)", (NOW, NOW))
    old.commit()
    old.close()

    store = Store(db)
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    [item] = store.query(now=NOW)
    assert item.id == "a" and item.read_at is None and item.also == []
    assert [i.id for i in store.query(q="dock workers", now=NOW)] == ["a"]  # old rows are searchable
    store.close()
    assert Store(db)._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION  # idempotent


def test_dedupe_by_guid_then_link():
    store = Store()
    assert store.add([make("a", "First title", guid="tag:feed,1")], now=NOW) == 1
    # Same GUID with an edited title and a new tracking-free link: still the same entry.
    renamed = make("b", "Edited title", guid="tag:feed,1")
    assert store.add([renamed], now=NOW) == 0
    # Same GUID from a different feed is a different entry.
    assert store.add([make("c", "Another", source="dw-world", guid="tag:feed,1")], now=NOW) == 1
    # No GUID: the link (id) decides.
    assert store.add([make("a", "Different words")], now=NOW) == 0


def test_full_text_search_ranks_and_snippets():
    store = Store()
    store.add([
        make("t", "Central bank raises rates", content="The central bank raised its policy rate by a quarter point."),
        make("b", "Weather update", content="Markets ignored the central bank rates decision entirely today."),
        make("n", "Unrelated", content="Nothing to see."),
    ], now=NOW)
    hits = store.query(q="central bank rates", now=NOW)
    assert [h.id for h in hits] == ["t", "b"]  # title match ranks first
    assert "\x02central\x03" in hits[1].snippet
    assert [h.id for h in store.query(q="rais", now=NOW)] == ["t"]  # last word matches as a prefix
    assert [h.id for h in store.query(q="raise", sort="newest", now=NOW)] == ["t"]
    assert fts_query('") OR "x') == '"OR" "x"*'  # quotes and operators can't escape
    assert fts_query("  ...  ") is None


def test_read_star_and_unread_filters():
    store = Store()
    store.add([make("a", "One"), make("b", "Two", 2), make("c", "Three", 3)], now=NOW)
    assert store.set_read(["a", "b"], True, now=NOW) == ["a", "b"]
    assert store.set_read(["a", "c"], True, now=NOW) == ["c"]  # only changes are reported
    assert store.set_read(["a"], False) == ["a"]
    assert [i.id for i in store.query(unread=True, now=NOW)] == ["a"]
    totals, unread = store.category_counts(now=NOW)
    assert totals == {"world": 3} and unread == {"world": 1}
    assert store.set_starred("b", True, now=NOW)
    [starred] = store.query(starred=True, now=NOW + 400 * DAY)  # starred shows regardless of age
    assert starred.id == "b" and starred.starred_at == NOW


def test_prune_keeps_starred_forever_and_runs_nightly():
    store = Store(retention_days=90)
    store.add([make("old", "Old", 24 * 89), make("kept", "Starred", 24 * 89), make("new", "New")], now=NOW)
    store.set_starred("kept", True)
    later = NOW + 2 * DAY  # "old" and "kept" are now past 90 days
    assert store.purge(now=later) == 1
    assert {i.id for i in store.query(hours=24 * 90, now=later)} == {"new"}
    assert [i.id for i in store.query(starred=True)] == ["kept"]

    # maybe_purge: once a day, after 03:00 local time.
    day = datetime.fromtimestamp(later).replace(hour=2, minute=0, second=0, microsecond=0).timestamp() + DAY
    store.set_meta("last_purge", None)
    assert store.maybe_purge(now=day) == 0 and store.get_meta("last_purge") is None  # before 03:00
    store.maybe_purge(now=day + 2 * 3600)  # 04:00: runs
    ran_at = store.get_meta("last_purge")
    store.maybe_purge(now=day + 5 * 3600)  # later the same day: doesn't run again
    assert store.get_meta("last_purge") == ran_at


def test_get_returns_full_text_and_summary():
    store = Store()
    store.add([make("a", "Title", content="Long body " * 100)], now=NOW)
    item = store.get("a")
    assert item.content.startswith("Long body") and item.to_dict()["content_len"] == len(item.content)
    assert "content" not in item.to_dict()
    store.save_summary("a", "Short version.", "m")
    assert store.get("a").summary == "Short version." and store.get("missing") is None
