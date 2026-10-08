import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from newsfeed.app import create_app
from newsfeed.feeds import DEFAULT, FeedList
from newsfeed.opml import OPMLError, merge, read_opml, write_opml

FEEDLY = Path(__file__).parent / "fixtures" / "feedly.opml"


def test_bundled_list_round_trips_exactly():
    back = read_opml(write_opml(DEFAULT))
    assert back.feeds == DEFAULT.feeds
    assert back.categories == DEFAULT.categories and back.regions == DEFAULT.regions
    assert back.region_all_labels == DEFAULT.region_all_labels
    assert len(DEFAULT.feeds) == 71 and DEFAULT.regions["local"]["WV"] == "West Virginia"


def test_reads_a_feedly_export():
    cat = read_opml(FEEDLY.read_bytes())
    assert cat.categories == {"tech": "Tech", "cooking": "Cooking", "other": "Other"}  # empty folder dropped
    by_name = {f.name: f for f in cat.feeds}
    assert by_name["xkcd"].category == "other"  # outside any folder
    assert by_name["Smitten Kitchen"].poll_minutes == 30  # imported feeds poll every 30 minutes
    assert by_name["Smitten Kitchen"].source_class == "publisher"
    assert len({f.id for f in cat.feeds}) == len(cat.feeds)


def test_merging_feedly_skips_feeds_already_tracked():
    merged, added = merge(DEFAULT, read_opml(FEEDLY.read_bytes()))
    # The Verge is already tracked (http vs https and a trailing slash don't matter).
    assert added == 4
    names = {f.name: f for f in merged.feeds}
    assert names["Ars Technica - All content"].category == "tech"  # "Tech" folder joins our Technology
    assert names["Smitten Kitchen"].category == "cooking"
    assert list(merged.categories)[-2:] == ["cooking", "other"]  # new folders go last
    assert merged.categories["tech"] == "Technology"  # existing labels kept
    assert sum(f.name == "The Verge" for f in merged.feeds) == 1


@pytest.mark.parametrize("text", [b"not xml", b"<html><body/></html>"])
def test_rejects_non_opml(text):
    with pytest.raises(OPMLError):
        read_opml(text)


def test_feed_list_first_run_import_and_reload(tmp_path):
    (tmp_path / "import.opml").write_bytes(FEEDLY.read_bytes())
    fl = FeedList(tmp_path / "feeds.opml", tmp_path / "import.opml")
    assert (tmp_path / "feeds.opml").exists()
    assert len(fl.catalog.feeds) == 75 and "cooking" in fl.catalog.categories
    assert not (tmp_path / "import.opml").exists() and (tmp_path / "import.opml.imported").exists()

    # Editing the file is picked up without a restart.
    text = (tmp_path / "feeds.opml").read_text().replace('text="Cooking" title="Cooking"', 'text="Food" title="Food"')
    (tmp_path / "feeds.opml").write_text(text)
    os.utime(tmp_path / "feeds.opml", (time.time() + 5, time.time() + 5))
    assert fl.reload_if_changed() and fl.catalog.categories["cooking"] == "Food"

    # A broken edit keeps the last good list and reports the problem.
    (tmp_path / "feeds.opml").write_text("<opml><body>")
    os.utime(tmp_path / "feeds.opml", (time.time() + 10, time.time() + 10))
    assert not fl.reload_if_changed()
    assert len(fl.catalog.feeds) == 75 and "feeds.opml" in fl.error


def test_export_endpoint_and_meta_follow_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWSFEED_DISABLE_AI", "1")
    (tmp_path / "import.opml").write_bytes(FEEDLY.read_bytes())
    with TestClient(create_app(data_dir=tmp_path, poll=False)) as client:
        meta = client.get("/api/meta").json()
        assert meta["categories"]["cooking"] == "Cooking" and len(meta["sources"]) == 75
        resp = client.get("/export.opml")
        assert resp.status_code == 200 and "attachment" in resp.headers["content-disposition"]
        assert len(read_opml(resp.content).feeds) == 75
