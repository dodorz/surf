from pathlib import Path
import sys
import types

import surf
import surf_web
from surf import Fetcher, OutputHandler


class _FakeConfig:
    llm_provider = ""

    def get(self, section, key, fallback=None):
        if section == "Output" and key == "target_language":
            return "zh-cn"
        return fallback

    def get_path(self, section, key, fallback=None):
        return fallback or "."

    def get_llm_config(self, provider=None):
        return {"model": "fake-model"}


def test_archive_domain_rotation_uses_next_domain_after_a_failed_lookup(monkeypatch):
    attempted_domains = []

    class _FakeTimeoutError(Exception):
        pass

    class _FakeLocator:
        @property
        def first(self):
            return self

        def count(self):
            return 0

    class _FakePage:
        def __init__(self, archive_base_url):
            self.archive_base_url = archive_base_url
            self.url = archive_base_url

        def set_default_timeout(self, timeout):
            pass

        def goto(self, url, **kwargs):
            if "?run=1" in url:
                self.url = (
                    self.archive_base_url + "ABc12"
                    if self.archive_base_url == "https://archive.ph/"
                    else url
                )
            else:
                self.url = url

        def wait_for_load_state(self, *args, **kwargs):
            pass

        def wait_for_timeout(self, timeout):
            pass

        def locator(self, selector):
            return _FakeLocator()

        def content(self):
            return "<html><body><article>" + ("snapshot content " * 50) + "</article></body></html>"

    class _FakeContext:
        def __init__(self, archive_base_url):
            self.archive_base_url = archive_base_url

        def new_page(self):
            return _FakePage(self.archive_base_url)

    class _FakeBrowser:
        def close(self):
            pass

    class _FakeChromium:
        def launch(self, **kwargs):
            return _FakeBrowser()

    class _FakePlaywright:
        chromium = _FakeChromium()

    class _FakePlaywrightContextManager:
        def __enter__(self):
            return _FakePlaywright()

        def __exit__(self, *args):
            pass

    fake_playwright = types.ModuleType("playwright")
    fake_sync_api = types.ModuleType("playwright.sync_api")
    fake_sync_api.TimeoutError = _FakeTimeoutError
    fake_sync_api.sync_playwright = _FakePlaywrightContextManager
    fake_playwright.sync_api = fake_sync_api
    monkeypatch.setitem(sys.modules, "playwright", fake_playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)
    monkeypatch.setattr(Fetcher, "_get_proxies", staticmethod(lambda *args, **kwargs: ({}, None)))

    def _create_context(browser, archive_base_url):
        attempted_domains.append(archive_base_url)
        return _FakeContext(archive_base_url)

    monkeypatch.setattr(Fetcher, "_create_stealth_context", staticmethod(_create_context))

    html_content, snapshot_url = Fetcher._fetch_archiveis_snapshot(
        "https://example.com/paywalled",
        _FakeConfig(),
        archive_domains=("archive.is", "archive.ph"),
    )

    assert html_content is not None
    assert snapshot_url == "https://archive.ph/ABc12"
    assert attempted_domains == ["https://archive.is/", "https://archive.ph/"]


def test_wayback_snapshot_uses_content_location(monkeypatch):
    class _FakeResponse:
        status_code = 302
        headers = {"Content-Location": "/web/20260426000000/https://example.com/post"}

    monkeypatch.setattr(Fetcher, "_get_proxies", staticmethod(lambda *args, **kwargs: ({}, None)))
    monkeypatch.setattr(surf, "_requests_get_interruptibly", lambda *args, **kwargs: _FakeResponse())

    archive_url = Fetcher.save_wayback_snapshot("https://example.com/post", _FakeConfig())

    assert archive_url == "https://web.archive.org/web/20260426000000/https://example.com/post"


def test_markdown_front_matter_includes_archive(tmp_path):
    output_path = tmp_path / "note.md"

    saved = OutputHandler.save_markdown(
        "Title",
        "Body",
        _FakeConfig(),
        output_path=str(output_path),
        html_content="<html><head><title>Title</title></head><body>Body</body></html>",
        source_url="https://example.com/post",
        archive_url="https://web.archive.org/web/20260426000000/https://example.com/post",
    )

    text = Path(saved).read_text(encoding="utf-8")
    assert "source: https://example.com/post" in text
    assert "archive: https://web.archive.org/web/20260426000000/https://example.com/post" in text


def test_markdown_front_matter_includes_meta_description(tmp_path):
    output_path = tmp_path / "note.md"

    saved = OutputHandler.save_markdown(
        "Title",
        "Body",
        _FakeConfig(),
        output_path=str(output_path),
        html_content=(
            "<html><head><title>Title</title>"
            "<meta name='description' content='Example summary'>"
            "</head><body>Body</body></html>"
        ),
        source_url="https://example.com/post",
    )

    text = Path(saved).read_text(encoding="utf-8")
    assert 'description: "Example summary"' in text


def test_markdown_front_matter_uses_iso_datetime_format_for_created_only(tmp_path):
    output_path = tmp_path / "note.md"

    saved = OutputHandler.save_markdown(
        "Title",
        "Body",
        _FakeConfig(),
        output_path=str(output_path),
        html_content=(
            "<html><head><title>Title</title>"
            "<meta property='article:published_time' content='2024-01-02T03:04:05Z'>"
            "</head><body>Body</body></html>"
        ),
        source_url="https://example.com/post",
    )

    text = Path(saved).read_text(encoding="utf-8")
    assert "created: 2024-01-02T" in text
    updated_line = text.split("updated: ", 1)[1].splitlines()[0]
    assert updated_line.count("-") == 2
    assert "T" not in updated_line


def test_web_archive_option_stores_archive_metadata(monkeypatch):
    monkeypatch.setattr(surf_web, "get_config", lambda: _FakeConfig())
    monkeypatch.setattr(
        surf_web.Fetcher,
        "fetch",
        lambda *args, **kwargs: (
            "<html><head><title>Example</title></head>"
            "<body><article><p>Hello world.</p></article></body></html>"
        ),
    )
    monkeypatch.setattr(
        surf_web.OcrHandler,
        "annotate_html_with_ocr",
        lambda cleaned_html, **kwargs: cleaned_html,
    )
    monkeypatch.setattr(
        surf_web.Fetcher,
        "save_wayback_snapshot",
        staticmethod(lambda url, **kwargs: "https://web.archive.org/web/20260426000000/" + url),
    )

    client = surf_web.app.test_client()
    response = client.post(
        "/api/process",
        json={
            "url": "https://example.com/post",
            "lang": "raw",
            "archive_source": True,
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["metadata"]["source_url"] == "https://example.com/post"
    assert payload["metadata"]["archive_url"] == (
        "https://web.archive.org/web/20260426000000/https://example.com/post"
    )
