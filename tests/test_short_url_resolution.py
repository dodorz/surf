import surf
import surf_web
from surf import Fetcher


class _FakeConfig:
    llm_provider = ""

    def get(self, section, key, fallback=None):
        return fallback

    def get_path(self, section, key, fallback=None):
        return fallback or "."

    def get_llm_config(self, provider=None):
        return {"model": "fake-model"}


def test_common_short_url_resolves_before_special_site_matching(monkeypatch):
    monkeypatch.setattr(Fetcher, "_get_proxies", staticmethod(lambda *args, **kwargs: ({}, None)))
    monkeypatch.setattr(
        Fetcher,
        "_resolve_url_with_redirects",
        staticmethod(lambda *args, **kwargs: "https://x.com/user/status/1234567890"),
    )

    resolved = Fetcher._resolve_common_short_url("https://t.co/abc123", _FakeConfig())
    _, site_name, _ = surf._get_handler_for_url(resolved)

    assert resolved == "https://x.com/user/status/1234567890"
    assert site_name == "twitter"


def test_non_short_url_is_not_resolved(monkeypatch):
    def _should_not_resolve(*args, **kwargs):
        raise AssertionError("regular URLs should not be resolved up front")

    monkeypatch.setattr(
        Fetcher,
        "_resolve_url_with_redirects",
        staticmethod(_should_not_resolve),
    )

    resolved = Fetcher._resolve_common_short_url(
        "https://example.com/article", _FakeConfig()
    )

    assert resolved == "https://example.com/article"


def test_web_process_resolves_short_url_before_fetch_and_metadata(monkeypatch):
    long_url = "https://x.com/user/status/1234567890"

    monkeypatch.setattr(surf_web, "get_config", lambda: _FakeConfig())
    monkeypatch.setattr(
        surf_web.OcrHandler,
        "annotate_html_with_ocr",
        lambda cleaned_html, **kwargs: cleaned_html,
    )
    monkeypatch.setattr(
        Fetcher,
        "_resolve_common_short_url",
        staticmethod(lambda url, *args, **kwargs: long_url if url == "https://t.co/abc123" else url),
    )

    def _fake_fetch(url, *args, **kwargs):
        assert url == long_url
        return "<html><head><title>Resolved</title></head><body><article><p>Hello.</p></article></body></html>"

    monkeypatch.setattr(surf_web.Fetcher, "fetch", _fake_fetch)

    client = surf_web.app.test_client()
    response = client.post(
        "/api/process",
        json={"url": "https://t.co/abc123", "lang": "raw"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["metadata"]["source_url"] == long_url
