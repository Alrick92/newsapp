import time

from fastapi.testclient import TestClient

from newsfeed.app import create_app
from newsfeed.bias import blindspot, reading_profile, story_coverage, tally
from newsfeed.feeds import DEFAULT
from newsfeed.model import Feed
from newsfeed.opml import merge, read_opml, write_opml

FEEDS = {f.id: f for f in [
    Feed("l", "L", "https://l.example/rss", "world", bias="left"),
    Feed("ll", "LL", "https://ll.example/rss", "world", bias="lean-left"),
    Feed("c", "C", "https://c.example/rss", "world", bias="center"),
    Feed("lr", "LR", "https://lr.example/rss", "world", bias="lean-right"),
    Feed("r", "R", "https://r.example/rss", "world", bias="right"),
    Feed("u", "U", "https://u.example/rss", "world"),
]}


def test_opml_reads_writes_and_merges_ratings():
    text = """<opml version="2.0" xmlns:nf="https://github.com/alrick92/newsapp/ns/opml"><body>
      <outline text="World">
        <outline type="rss" text="A" xmlUrl="https://a.example/rss" nf:bias="Lean-Right" nf:factuality="high"/>
        <outline type="rss" text="B" xmlUrl="https://b.example/rss" nf:bias="far-out" nf:factuality="??"/>
      </outline></body></opml>"""
    a, b = read_opml(text).feeds
    assert (a.bias, a.factuality) == ("lean-right", "high")
    assert (b.bias, b.factuality) == (None, None)  # unknown values read as unrated
    back = read_opml(write_opml(read_opml(text))).feeds
    assert back == (a, b)
    merged, added = merge(DEFAULT, read_opml(text))
    assert added == 2 and merged.by_id[a.id].bias == "lean-right"


def test_bundled_list_has_both_sides_in_politics():
    sides = tally((f.id for f in DEFAULT.feeds if f.category == "politics"), DEFAULT.by_id)["sides"]
    assert sides["left"] and sides["right"] and sides["center"]
    assert abs(sides["left"] - sides["right"]) <= 2


def test_tally_counts_each_source_once():
    counts = tally(["l", "l", "ll", "c", "r", "u", "gone"], FEEDS)
    assert counts["sides"] == {"left": 2, "center": 1, "right": 1}
    assert counts["by_bias"]["left"] == 1 and counts["rated"] == 4 and counts["unrated"] == 2


def test_blindspot_needs_enough_rated_sources_and_a_lopsided_split():
    assert blindspot({"left": 2, "center": 0, "right": 0}) is None  # too few
    assert blindspot({"left": 3, "center": 1, "right": 0}) == "right"
    assert blindspot({"left": 0, "center": 1, "right": 3}) == "left"
    assert blindspot({"left": 2, "center": 1, "right": 1}) is None
    assert blindspot({"left": 0, "center": 4, "right": 0}) is None  # center-only hides nothing


def test_story_coverage_from_links():
    story = {"items": [{"source_id": "r"}, {"source_id": "lr"}, {"source_id": "r"}, {"source_id": "u"},
                       {"source_id": "r"}, {"source_id": "c"}]}
    cov = story_coverage(story, FEEDS)
    assert cov["sides"] == {"left": 0, "center": 1, "right": 2} and cov["blindspot"] is None
    story["items"].append({"source_id": "lr"})  # same source again changes nothing
    assert story_coverage(story, FEEDS) == cov


def test_reading_profile_groups_by_side():
    profile = reading_profile({"l": 3, "r": 1, "u": 2}, {"ll": 10, "c": 5, "lr": 7}, FEEDS)
    assert profile["read"] == {"left": 3, "center": 0, "right": 1, "unrated": 2}
    assert profile["published"] == {"left": 10, "center": 5, "right": 7, "unrated": 0}


def test_api_trending_coverage_and_bias(monkeypatch):
    monkeypatch.setenv("NEWSFEED_DISABLE_AI", "1")
    with TestClient(create_app(demo=True, poll=False)) as client:
        meta = client.get("/api/meta").json()
        fox = next(s for s in meta["sources"] if s["id"] == "fox-politics")
        assert fox["bias"] == "right" and "factuality" in fox

        for _ in range(50):
            data = client.get("/api/trending").json()
            if data["generated_at"]:
                break
            time.sleep(0.05)
        spots = {s["coverage"]["blindspot"] for s in data["stories"]}
        assert {"left", "right"} <= spots  # the demo has one of each
        assert all("sides" in s["coverage"] for s in data["stories"])

        empty = client.get("/api/bias?days=30").json()
        assert empty["days"] == 30 and sum(empty["read"].values()) == 0
        assert empty["published"]["right"] > 0

        items = client.get("/api/items?sources=fox-politics,nypost-politics").json()["items"]
        client.post("/api/items/read", json={"ids": [i["id"] for i in items], "read": True})
        read = client.get("/api/bias?days=7").json()["read"]
        assert read["right"] == len(items) and read["left"] == 0
        assert client.get("/api/bias?days=0").status_code == 422
