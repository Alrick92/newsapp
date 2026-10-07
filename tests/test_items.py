from conftest import NOW

from newsfeed.feeds import FEEDS_BY_ID
from newsfeed.items import canonicalize_url, clean_text, parse_feed

WORLD = FEEDS_BY_ID["bbc-world"]
SECURITY = FEEDS_BY_ID["google-security"]


def test_canonicalize_strips_tracking_and_normalizes():
    a = canonicalize_url("http://www.Example.com/a/b/?utm_source=x&id=3&fbclid=y#frag")
    b = canonicalize_url("https://example.com/a/b?id=3")
    assert a == b == "https://example.com/a/b?id=3"


def test_clean_text_strips_html_and_truncates_on_word_boundary():
    assert clean_text("<p>Hello&nbsp;<b>world</b></p>") == "Hello world"
    out = clean_text("word " * 200, limit=50)
    assert len(out) <= 51 and out.endswith("…") and not out.endswith(" …")


def test_parse_rss(rss_bytes):
    items = parse_feed(rss_bytes, WORLD, now=NOW)
    by_title = {i.title: i for i in items}

    assert "" not in by_title  # untitled entries dropped
    climate = by_title["Leaders meet for climate talks"]
    assert climate.description == "Delegates gathered on Monday for the summit."
    assert climate.image == "https://img.example.org/large.jpg"  # widest media wins
    assert climate.published == NOW - 2 * 3600
    assert climate.source == "BBC News" and climate.category == "world"

    # relative <img> in the description resolves against the article URL
    assert by_title["Inline image story"].image == "https://news.example.org/images/inline.png"
    assert by_title["Inline image story"].description == "Text after image."
    assert by_title["Enclosure story"].image == "https://img.example.org/enc.jpg"


def test_same_article_with_tracking_params_gets_same_id(rss_bytes):
    first = parse_feed(rss_bytes, WORLD, now=NOW)[0]
    again = parse_feed(rss_bytes.replace(b"utm_source=rss", b"utm_source=other"), WORLD, now=NOW)[0]
    assert first.id == again.id


def test_parse_atom_uses_content_and_updated(atom_bytes):
    [item] = parse_feed(atom_bytes, SECURITY, now=NOW)
    assert item.url == "https://security.example.org/2026/10/zero-day.html"
    assert item.description == "A long and detailed write-up."
    assert item.published == NOW - 3 * 3600
    assert item.source_class == "vendor-security"
    assert item.image is None
