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


def test_redirect_resolution_retries_when_first_user_agent_is_not_redirected(monkeypatch):
    class _FakeResponse:
        def __init__(self, url):
            self.url = url

    seen_user_agents = []

    def _fake_get(url, headers=None, **kwargs):
        seen_user_agents.append((headers or {}).get("User-Agent"))
        if len(seen_user_agents) == 1:
            return _FakeResponse(url)
        return _FakeResponse("https://example.com/final")

    monkeypatch.setattr(surf, "_requests_get_interruptibly", _fake_get)

    resolved = Fetcher._resolve_url_with_redirects("https://t.co/abc123")

    assert resolved == "https://example.com/final"
    assert len(seen_user_agents) == 2


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


def test_douban_dispatch_url_is_normalized_before_fetch():
    resolved = Fetcher._resolve_common_short_url(
        "https://www.douban.com/doubanapp/dispatch?uri=%2Ftopic%2F484985482",
        _FakeConfig(),
    )

    assert resolved == "https://www.douban.com/topic/484985482"


def test_douban_dispatch_url_normalizes_before_short_url_resolution(monkeypatch):
    def _should_not_resolve(*args, **kwargs):
        raise AssertionError("Douban dispatch URLs should normalize without redirect resolution")

    monkeypatch.setattr(
        Fetcher,
        "_resolve_url_with_redirects",
        staticmethod(_should_not_resolve),
    )

    resolved = Fetcher._resolve_common_short_url(
        "https://www.douban.com/doubanapp/dispatch?uri=%2Ftopic%2F484985482",
        _FakeConfig(),
    )

    assert resolved == "https://www.douban.com/topic/484985482"


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


# --- click-through interstitials --------------------------------------------
# Some shorteners stop redirecting and render "this link was created recently,
# click through" instead. The destination is still in the markup, and the
# paywall decision must happen on that destination - never on the shortener.


_INTERSTITIAL = """
<html><head><title>da.gd: shorten</title>
<meta name="keywords" content="dagd,da.gd,url,shorten"></head><body>
<p>This short url was created recently. In order to help fight phishing and
abuse, fresh short urls require an explicit click-through before we redirect
you.</p>
<a href="/help">help</a>
<a href="https://codeberg.org/dagd/dagd">open source</a>
<a href="https://buymeacoffee.com/relrod">donate</a>
<a href="{}">{}</a>
</body></html>
"""

_TARGET = "https://www.nytimes.com/2026/10/30/world/asia/china-tiananmen-kristof.html"


def test_interstitial_target_is_extracted():
    html = _INTERSTITIAL.format(_TARGET, _TARGET)
    assert Fetcher._extract_short_url_target(html, "https://da.gd/DSm8") == _TARGET


def test_interstitial_extraction_ignores_navigation_links():
    # "open source"/"donate" point at other hosts but are not printed URLs.
    html = _INTERSTITIAL.format("/x", "")  # no destination link at all
    assert Fetcher._extract_short_url_target(html, "https://da.gd/DSm8") is None


def test_interstitial_extraction_needs_an_interstitial_marker():
    html = (
        "<html><body><a href='https://www.nytimes.com/x'>"
        "https://www.nytimes.com/x</a></body></html>"
    )
    assert Fetcher._extract_short_url_target(html, "https://da.gd/DSm8") is None
    assert Fetcher._extract_short_url_target("", "https://da.gd/DSm8") is None


def test_short_url_resolution_falls_back_to_the_interstitial(monkeypatch):
    monkeypatch.setattr(Fetcher, "_get_proxies", staticmethod(lambda *args, **kwargs: ({}, None)))
    monkeypatch.setattr(
        Fetcher, "_resolve_url_with_redirects", staticmethod(lambda *args, **kwargs: "https://da.gd/DSm8")
    )

    class _Response:
        url = "https://da.gd/DSm8"

        def __init__(self):
            self.content = _INTERSTITIAL.format(_TARGET, _TARGET).encode()
            self.headers = {"Content-Type": "text/html; charset=utf-8"}

    monkeypatch.setattr(
        surf, "_requests_get_with_system_trust_interruptibly", lambda *a, **k: _Response()
    )

    resolved = Fetcher._resolve_common_short_url("https://da.gd/DSm8", _FakeConfig())
    assert resolved == _TARGET
    # The paywall decision then runs against the destination host.
    _, site_name, _ = surf._get_handler_for_url(resolved)
    assert site_name is None or site_name != "da.gd"


def test_publisher_shortener_hosts_are_recognized():
    for url in (
        "https://nyti.ms/abc",
        "https://wapo.st/abc",
        "https://on.wsj.com/abc",
        "https://bloom.bg/abc",
        "https://tny.sh/abc",
        "https://econ.st/abc",
        "https://da.gd/abc",
    ):
        assert Fetcher._is_common_short_url(url), url
    assert not Fetcher._is_common_short_url("https://www.nytimes.com/2026/10/30/x.html")
