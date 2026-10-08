import json
from pathlib import Path

from fastapi.testclient import TestClient

from newsfeed.app import STATIC, create_app, static_version


def client(monkeypatch):
    monkeypatch.setenv("NEWSFEED_DISABLE_AI", "1")
    return TestClient(create_app(demo=True))


def test_manifest_is_installable(monkeypatch):
    with client(monkeypatch) as c:
        resp = c.get("/manifest.webmanifest")
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("application/manifest+json")
        m = resp.json()
        assert m["name"] and m["start_url"] == "/" and m["display"] == "standalone"
        sizes = {(i["sizes"], i.get("purpose", "any")) for i in m["icons"]}
        assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= sizes
        for icon in m["icons"] + [i for s in m["shortcuts"] for i in s["icons"]]:
            assert c.get(icon["src"]).status_code == 200, icon["src"]


def test_service_worker_is_versioned_and_served_from_root(monkeypatch):
    with client(monkeypatch) as c:
        resp = c.get("/sw.js")
        assert resp.status_code == 200 and "javascript" in resp.headers["content-type"]
        assert resp.headers["cache-control"] == "no-cache" and resp.headers["service-worker-allowed"] == "/"
        assert f'const VERSION = "{static_version()}"' in resp.text and "__VERSION__" not in resp.text
        # Every file the worker pre-caches must exist, or installation fails.
        shell = json.loads(resp.text.split("const SHELL_FILES = ", 1)[1].split(";", 1)[0].replace(",\n]", "]"))
        for path in shell:
            assert c.get(path).status_code == 200, path


def test_version_changes_with_static_files(tmp_path, monkeypatch):
    before = static_version()
    probe = STATIC / "_probe.txt"
    try:
        probe.write_text("x")
        assert static_version() != before
    finally:
        probe.unlink()
    assert static_version() == before


def test_page_links_manifest_and_icons(monkeypatch):
    with client(monkeypatch) as c:
        html = c.get("/").text
        assert 'rel="manifest" href="/manifest.webmanifest" crossorigin="use-credentials"' in html
        assert 'rel="apple-touch-icon"' in html and 'name="theme-color"' in html
        assert "viewport-fit=cover" in html
