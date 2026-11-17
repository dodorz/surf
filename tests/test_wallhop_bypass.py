"""wallhop.io is the first paywall bypass in the chain.

It fetches the article from its own network, so it sees content our IP is
denied (DataDome/Cloudflare) or gated behind a paywall. Every response is
validated before it replaces the document: a wallhop answer that is still
paywalled, still a challenge, or just a stub must not throw away what we have.
"""
import surf
from surf import Fetcher


class _FakeResponse:
    def __init__(self, url, body, status_code=200, headers=None):
        self.url = url
        self.status_code = status_code
        self.content = body.encode("utf-8")
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}


def _article_html(extra="", title="I Covered the Tiananmen Square Massacre"):
    body = "<p>" + ("As I pedaled my bicycle toward the gunfire and Tiananmen Square. " * 40) + "</p>"
    body += "<p>" + ("Another full paragraph of the story, long enough to look real. " * 40) + "</p>"
    return (
        f"<html><head><title>WallHop | {title}</title>"
        '<meta property="og:title" content="WallHop | Some Title">'
        "</head><body>"
        f'<div class="bar">wallhop Download Download as MD Original on NYT</div>'
        f"<article>{body}</article>"
        f'<p class="foot">Help keep WallHop running on Ko-fi</p>'
        f"{extra}"
        "</body></html>"
    )


def _stub_html(text="Page not found"):
    return "<html><head><title>x</title></head><body><p>" + text + "</p></body></html>"


def _install_get(monkeypatch, responses):
    """responses: list of callables returning a response, consumed in order."""
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        index = min(len(calls) - 1, len(responses) - 1)
        result = responses[index]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(surf, "_requests_get_interruptibly", fake_get)
    return calls


def test_wallhop_is_enabled_by_default_and_configurable():
    class _Config:
        def __init__(self, value):
            self.value = value

        def get(self, section, key, fallback=None):
            if section == "Paywall" and key == "bypass":
                return self.value
            return fallback

    assert Fetcher._wallhop_enabled(None) is True
    assert Fetcher._wallhop_enabled(_Config(None)) is True
    assert Fetcher._wallhop_enabled(_Config("wallhop")) is True
    assert Fetcher._wallhop_enabled(_Config("wallhop,other")) is True
    assert Fetcher._wallhop_enabled(_Config("")) is False
    assert Fetcher._wallhop_enabled(_Config("none")) is False
    assert Fetcher._wallhop_enabled(_Config("false")) is False
    assert Fetcher._wallhop_enabled(_Config("archive")) is False


def test_wallhop_chrome_and_title_prefix_are_stripped():
    cleaned = Fetcher._strip_wallhop_chrome(_article_html())
    assert "WallHop |" not in cleaned
    assert "Download as MD" not in cleaned
    assert "Ko-fi" not in cleaned
    assert "As I pedaled my bicycle" in cleaned
    # A page that never came from wallhop is returned untouched.
    plain = "<html><head><title>Plain</title></head><body><p>hi</p></body></html>"
    assert Fetcher._strip_wallhop_chrome(plain) == plain


def test_wallhop_raw_path_is_accepted(monkeypatch):
    calls = _install_get(monkeypatch, [_FakeResponse("https://wallhop.io/raw/x", _article_html())])
    html = Fetcher._fetch_wallhop_bypass("https://www.nytimes.com/x")
    assert html and "As I pedaled my bicycle" in html
    assert "WallHop |" not in html
    assert calls[0].startswith("https://wallhop.io/raw/https://www.nytimes.com/x")


def test_wallhop_plain_path_is_used_when_raw_fails(monkeypatch):
    calls = _install_get(
        monkeypatch,
        [
            _FakeResponse("https://wallhop.io/raw/x", _stub_html("Page not found")),
            _FakeResponse("https://wallhop.io/x", _article_html()),
        ],
    )
    html = Fetcher._fetch_wallhop_bypass("https://www.nytimes.com/x")
    assert html and "As I pedaled my bicycle" in html
    assert len(calls) == 2
    assert calls[1] == "https://wallhop.io/https://www.nytimes.com/x"


def test_wallhop_rejects_a_block_page(monkeypatch):
    # What The Atlantic returns through wallhop when Cloudflare blocks the fetch.
    blocked = _stub_html("Your access has been blocked. Our site's security protections triggered a block.")
    _install_get(monkeypatch, [_FakeResponse("https://wallhop.io/raw/x", blocked)])
    assert Fetcher._fetch_wallhop_bypass("https://www.theatlantic.com/x") is None


def test_wallhop_rejects_a_challenge_page(monkeypatch):
    challenge = (
        "<html><head><title>Just a moment...</title>"
        '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>'
        "</head><body>Checking your browser</body></html>"
    )
    _install_get(monkeypatch, [_FakeResponse("https://wallhop.io/raw/x", challenge)])
    assert Fetcher._fetch_wallhop_bypass("https://www.economist.com/x") is None


def test_wallhop_accepts_a_full_article_on_a_paywall_domain(monkeypatch):
    # The full article still carries the site's paywall scaffolding (meta
    # keywords, CSS classes). Those signals must not reject a response that
    # actually delivered the whole story.
    full = (
        "<html><head><title>Full</title>"
        '<meta name="keywords" content="paywall, metered, news"></head>'
        "<body><div class='paywall-content'>"
        + "".join(
            "<p>" + ("The complete article body, paragraph after paragraph of it. " * 60) + "</p>"
            for _ in range(12)
        )
        + "</div></body></html>"
    )
    assert len(full) > 5000  # real articles are far bigger than a teaser stub
    _install_get(monkeypatch, [_FakeResponse("https://wallhop.io/raw/x", full)])
    html = Fetcher._fetch_wallhop_bypass("https://www.nytimes.com/x")
    assert html and "The complete article body" in html


def test_wallhop_rejects_a_response_that_adds_nothing(monkeypatch):
    # The direct fetch already produced this much article text: a wallhop answer
    # of the same length bypassed nothing, so the archive chain still deserves
    # its turn.
    teaser = (
        "<html><head><title>Teaser</title></head><body>"
        "<div class='paywall-gate'>Subscribe to continue reading this article.</div>"
        "<article>"
        + "".join(
            f"<p>{'Teaser sentence {0} that stops before the real story. '.format(i)}</p>"
            for i in range(120)
        )
        + "</article></body></html>"
    )
    assert len(teaser) > 5000
    baseline = Fetcher._readable_body_chars(teaser)
    assert baseline > 1200
    _install_get(monkeypatch, [_FakeResponse("https://wallhop.io/raw/x", teaser)])
    assert Fetcher._fetch_wallhop_bypass(
        "https://www.wsj.com/x", baseline_chars=baseline
    ) is None

    # The same page is accepted when the blocked fetch produced far less text.
    _install_get(monkeypatch, [_FakeResponse("https://wallhop.io/raw/x", teaser)])
    assert Fetcher._fetch_wallhop_bypass("https://www.wsj.com/x", baseline_chars=100) is not None


def test_incomplete_markers_reject_but_page_signals_do_not():
    # Signals that mean the document itself is cut off or blocked.
    assert Fetcher._wallhop_content_is_incomplete(
        "known paywall domain: wsj.com; truncated content: 466 chars"
    )
    assert Fetcher._wallhop_content_is_incomplete(
        "known paywall domain: nytimes.com; anti-bot CAPTCHA: captcha-delivery.com"
    )
    assert Fetcher._wallhop_content_is_incomplete("bare-domain title: wsj.com")

    # Signals that fire on fully readable pages too: a footer's "Already a
    # subscriber? Sign in" alone must not reject an article that arrived.
    assert not Fetcher._wallhop_content_is_incomplete(
        "known paywall domain: nytimes.com; meta keyword: paywall; meta keyword: metered"
    )
    assert not Fetcher._wallhop_content_is_incomplete(
        "known paywall domain: nytimes.com; paywall text patterns (readable body): 1 matched"
    )
    assert not Fetcher._wallhop_content_is_incomplete(
        "known paywall domain: newyorker.com; paywall CSS elements: 6 found; "
        "explicit free tier: article:content_tier=free"
    )
    assert not Fetcher._wallhop_content_is_incomplete("")


def test_wallhop_rejects_still_paywalled_content(monkeypatch):
    teaser = (
        '<html><head><title>Teaser</title></head><body>'
        '<div class="paywall-gate">Subscribe to continue reading this article.</div>'
        + "<p>" + ("A short teaser paragraph that is not the whole story. " * 5) + "</p>"
        * 30
        + "</body></html>"
    )
    _install_get(monkeypatch, [_FakeResponse("https://wallhop.io/raw/x", teaser)])
    assert Fetcher._fetch_wallhop_bypass("https://www.wsj.com/x") is None


def test_wallhop_skips_non_http_urls(monkeypatch):
    calls = _install_get(monkeypatch, [])
    assert Fetcher._fetch_wallhop_bypass("/tmp/file.html") is None
    assert Fetcher._fetch_wallhop_bypass("") is None
    assert calls == []


def test_wallhop_failures_do_not_raise(monkeypatch):
    _install_get(monkeypatch, [RuntimeError("network down")])
    assert Fetcher._fetch_wallhop_bypass("https://www.nytimes.com/x") is None


def test_bypass_prefers_wallhop_then_archive(monkeypatch):
    order = []

    def fake_wallhop(url, proxies=None, timeout=None, baseline_chars=0):
        order.append("wallhop")
        return "<html><body>from wallhop</body></html>"

    def fake_archive(*args, **kwargs):
        order.append("archive")
        return "<html><body>from archive</body></html>", "https://archive.ph/123/x"

    monkeypatch.setattr(Fetcher, "_fetch_wallhop_bypass", staticmethod(fake_wallhop))
    monkeypatch.setattr(Fetcher, "_fetch_archiveis_snapshot", staticmethod(fake_archive))

    html, snapshot, source = Fetcher._fetch_paywall_bypass("https://www.nytimes.com/x", None)
    assert order == ["wallhop"]
    assert source == "wallhop.io"
    # wallhop serves live content, not an archive capture.
    assert snapshot is None
    assert "from wallhop" in html


def test_bypass_falls_back_to_archive_when_wallhop_fails(monkeypatch):
    monkeypatch.setattr(
        Fetcher, "_fetch_wallhop_bypass", staticmethod(lambda *a, **k: None)
    )
    monkeypatch.setattr(
        Fetcher,
        "_fetch_archiveis_snapshot",
        staticmethod(lambda *a, **k: ("<html><body>archived</body></html>", "https://archive.ph/1/x")),
    )
    html, snapshot, source = Fetcher._fetch_paywall_bypass("https://www.nytimes.com/x", None)
    assert source == "archive snapshot"
    assert snapshot == "https://archive.ph/1/x"
    assert "archived" in html


def test_bypass_returns_none_when_everything_fails(monkeypatch):
    monkeypatch.setattr(Fetcher, "_fetch_wallhop_bypass", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        Fetcher, "_fetch_archiveis_snapshot", staticmethod(lambda *a, **k: (None, None))
    )
    assert Fetcher._fetch_paywall_bypass("https://www.nytimes.com/x", None) == (None, None, None)


def test_disabled_wallhop_goes_straight_to_archive(monkeypatch):
    class _Config:
        def get(self, section, key, fallback=None):
            if section == "Paywall":
                return "none"
            return fallback

        def has_section(self, section):
            return False

    order = []
    monkeypatch.setattr(
        Fetcher,
        "_fetch_wallhop_bypass",
        staticmethod(lambda *a, **k: order.append("wallhop") or "<html></html>"),
    )
    monkeypatch.setattr(
        Fetcher,
        "_fetch_archiveis_snapshot",
        staticmethod(lambda *a, **k: (order.append("archive") or "<html>arch</html>", "https://archive.ph/1/x")),
    )
    html, snapshot, source = Fetcher._fetch_paywall_bypass("https://www.nytimes.com/x", _Config())
    assert order == ["archive"]
    assert source == "archive snapshot"
