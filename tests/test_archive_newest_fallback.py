"""Tests for the archive.today /newest fast path and auto extractor comparison."""

import surf
from surf import ContentProcessor, Fetcher


class _FakeResponse:
    def __init__(self, url, body, status_code=200, json_data=None):
        self.url = url
        self.status_code = status_code
        self.content = body.encode("utf-8")
        self.headers = {"Content-Type": "text/html; charset=utf-8"}
        self._json_data = json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


def test_archive_snapshot_url_detection():
    assert Fetcher._is_archive_snapshot_url(
        "https://archive.ph/20261018202622/https://www.nytimes.com/2026/10/13/opinion/x.html"
    )
    assert Fetcher._is_archive_snapshot_url("https://archive.ph/WOQ7d")
    assert not Fetcher._is_archive_snapshot_url("https://archive.ph/rss")
    assert not Fetcher._is_archive_snapshot_url(
        "https://archive.ph/newest/https%3A%2F%2Fexample.com%2Farticle"
    )


def test_archive_newest_fast_path_uses_redirect(monkeypatch):
    body = (
        "<html><head><title>Archived Article</title></head><body>"
        + "<p>archived body text</p>" * 200
        + "</body></html>"
    )
    snapshot_url = "https://archive.ph/20261018202622/https://example.com/article"
    requested = []

    def fake_get(url, **kwargs):
        requested.append(url)
        return _FakeResponse(snapshot_url, body)

    monkeypatch.setattr(surf, "_requests_get_with_system_trust_interruptibly", fake_get)

    html, url = Fetcher._fetch_archive_newest_via_requests(
        "https://example.com/article", ("archive.ph",), {}
    )

    assert html == body
    assert url == snapshot_url
    assert requested[0].startswith("https://archive.ph/newest/")


def test_archive_newest_fast_path_rejects_listing_page(monkeypatch):
    listing_url = "https://archive.ph/newest/https%3A%2F%2Fexample.com%2Farticle"

    def fake_get(url, **kwargs):
        return _FakeResponse(listing_url, "<html><body>No results</body></html>")

    monkeypatch.setattr(surf, "_requests_get_with_system_trust_interruptibly", fake_get)

    html, url = Fetcher._fetch_archive_newest_via_requests(
        "https://example.com/article", ("archive.ph",), {}
    )

    assert html is None
    assert url is None


def test_auto_extractor_prefers_longer_trafilatura_result(monkeypatch):
    short_html = "<article><p>short teaser</p></article>"
    long_body = "<p>" + ("full article body " * 400) + "</p>"

    class _FakeDocument:
        def __init__(self, html):
            self.html = html

        def title(self):
            return "Example Title"

        def summary(self):
            return short_html

    class _FakeTrafilatura:
        def extract(self, html, output_format=None, include_images=None):
            return long_body

    monkeypatch.setattr(surf, "Document", _FakeDocument)
    monkeypatch.setattr(
        ContentProcessor, "_rescue_content", staticmethod(lambda soup, summary: summary)
    )
    monkeypatch.setattr(surf, "_get_trafilatura", lambda: _FakeTrafilatura())

    title, content_html = ContentProcessor.extract_content(
        "<html><head><title>Example Title</title></head><body></body></html>",
        extractor="auto",
    )

    assert title == "Example Title"
    assert "full article body" in content_html


def test_archive_lookup_variants_strip_tracking_and_fragment():
    variants = Fetcher._archive_lookup_variants(
        "https://www.nytimes.com/2026/10/13/opinion/x.html?utm_source=newsletter&id=1#frag"
    )

    assert variants[0].endswith("#frag")
    assert "https://www.nytimes.com/2026/10/13/opinion/x.html?utm_source=newsletter&id=1" in variants
    assert "https://www.nytimes.com/2026/10/13/opinion/x.html?id=1" in variants
    assert "https://www.nytimes.com/2026/10/13/opinion/x.html" in variants


def test_archive_newest_fast_path_retries_canonical_url(monkeypatch):
    body = (
        "<html><head><title>Archived Article</title></head><body>"
        + "<p>archived body text</p>" * 200
        + "</body></html>"
    )
    snapshot_url = "https://archive.ph/20261018202622/https://example.com/article"
    requested = []

    def fake_get(url, **kwargs):
        requested.append(url)
        if "utm_source" in url:
            return _FakeResponse(
                "https://archive.ph/newest/https%3A%2F%2Fexample.com%2Farticle",
                "<html><body>No results</body></html>",
                status_code=404,
            )
        return _FakeResponse(snapshot_url, body)

    monkeypatch.setattr(surf, "_requests_get_with_system_trust_interruptibly", fake_get)

    html, url = Fetcher._fetch_archive_newest_via_requests(
        "https://example.com/article?utm_source=feed", ("archive.ph",), {}
    )

    assert html == body
    assert url == snapshot_url
    assert any("utm_source" in item for item in requested)
    assert any("utm_source" not in item for item in requested)


def test_wayback_fallback_uses_id_document(monkeypatch):
    body = (
        "<html><head><title>Archived Article</title></head><body>"
        + "<article><p>archived body text</p></article>"
        + ("<p>more body text</p>" * 400)
        + "</body></html>"
    )
    availability = {
        "archived_snapshots": {
            "closest": {
                "available": True,
                "status": "200",
                "url": "http://web.archive.org/web/20261009161156/https://example.com/article",
            }
        }
    }
    requested = []

    def fake_get(url, **kwargs):
        requested.append(url)
        if "archive.org/wayback/available" in url:
            return _FakeResponse(url, "", json_data=availability)
        return _FakeResponse(url, body)

    monkeypatch.setattr(surf, "_requests_get_with_system_trust_interruptibly", fake_get)

    html, snapshot_url = Fetcher._fetch_wayback_snapshot_via_requests(
        "https://example.com/article", {}
    )

    assert html == body
    assert snapshot_url == (
        "https://web.archive.org/web/20261009161156/https://example.com/article"
    )
    assert any("/web/20261009161156id_/" in url for url in requested)
