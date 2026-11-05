"""Foreign Affairs: free articles must not be treated as paywalled.

Foreign Affairs names its real article body ``paywall-content`` and shows a
"You are reading a free article" banner when the essay is unlocked, while the
page also carries a "Subscribe to unlock this feature" tools bar and a share
modal. Both used to make Surf take the archive-snapshot path even though the
full text was already in the HTML.
"""

import pytest

import surf
from surf import Fetcher

FA_URL = "https://www.foreignaffairs.com/china/empires-artificial-intelligence-niall-ferguson-eric-schmidt"

_BODY = (
    "In the span of a few years, China and the United States have each assembled "
    "something future historians might recognize as a new kind of empire. "
) * 40


def _fa_page(*, free=True, body=_BODY):
    banner = (
        '<div class="col-12 col-lg-6"><div class="paywall paywall-free-article">'
        "<h3>You are reading a free article</h3>"
        "<p>Subscribe to <em>Foreign Affairs</em> to get unlimited access.</p>"
        '<a id="paywall_free_article_unlocked" href="/subscribe">Subscribe</a>'
        "</div></div>"
        if free
        else '<div class="paywall paywall-locked"><p>Subscribe to continue reading.</p></div>'
    )
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<title>Empires of AI: The Rise and Fall of a New Global Order</title>
<meta name="description" content="An essay on AI empires.">
</head><body>
<main id="content" class="base__main">
  <article class="article d-flex flex-wrap">
    <section class="topper">
      <h1>Empires of AI</h1>
      <h2>The Rise and Fall of a New Global Order</h2>
      <p>By Niall Ferguson, Lisa Klaassen, and Eric Schmidt</p>
    </section>
    <div class="article__body-author-wrapper position-relative w-100">
      <div class="article__content-tools-wrapper col-12 col-lg-6-base-10">
        <div class="article-tools d-lg-block js--article-tools">
          <button class="article-tools__button">Listen</button>
          <span class="tooltip" id="play-upsell">Subscribe to unlock this feature or Sign in .</span>
          <button class="article-tools__button">Share &amp; Download</button>
          <a class="article-tools__button" href="/subscribe">Print</a>
        </div>
        <div class="dismissable modal js--modal newsletter-backdrop">
          <div class="modal__body">
            Share Empires of AI The Rise and Fall of a New Global Order Niall Ferguson
            Share in email Share on Facebook Copy Link Create Citation Download PDF
          </div>
        </div>
        <div class="article-dropcap">
          <div class="rich-text__inner">
            <section class="rich-text article__body-content">
              <div class="article-dropcap">
                {banner}
                <div class="article-dropcap--inner paywall-content">
                  <p>{body}</p>
                </div>
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  </article>
</main>
</body></html>"""


class _FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code
        self.headers = {"Content-Type": "text/html; charset=utf-8"}
        self.encoding = None
        self.apparent_encoding = "utf-8"

    def raise_for_status(self):
        return None


class _FakeConfig:
    def get(self, section, key, fallback=None):
        return fallback

    def has_section(self, name):
        return False

    def get_path(self, section, key, fallback=None):
        return fallback


def test_free_foreign_affairs_article_is_not_a_paywall():
    result = Fetcher._detect_paywall(_fa_page(), url=FA_URL)

    assert result["detected"] is False
    assert "explicit free" in result["reason"]


def test_locked_foreign_affairs_teaser_is_still_a_paywall():
    teaser = "Last spring, a group of former officials gathered in a windowless room."

    result = Fetcher._detect_paywall(_fa_page(free=False, body=teaser), url=FA_URL)

    assert result["detected"] is True
    assert "truncated content" in result["reason"]


def test_paywall_content_container_alone_is_not_a_wall():
    html = f"""<html><head><title>An Essay</title></head><body>
    <article><div class="article-dropcap--inner paywall-content"><p>{'Words. ' * 900}</p></div></article>
    </body></html>"""

    result = Fetcher._detect_paywall(html, url="https://example.com/essay")

    assert result["detected"] is False
    assert "paywall CSS elements" not in (result["reason"] or "")


def test_handler_strips_tools_modal_and_subscribe_banner(monkeypatch):
    monkeypatch.setattr(
        surf,
        "_requests_get_with_system_trust_interruptibly",
        lambda *a, **k: _FakeResponse(_fa_page()),
    )

    html = Fetcher._fetch_foreignaffairs_article(FA_URL, _FakeConfig())

    assert html is not None
    # Reading chrome is gone...
    assert "Subscribe to unlock this feature" not in html
    assert "Share on Facebook" not in html
    assert "You are reading a free article" not in html
    # ...while the article body survives.
    assert "new kind of empire" in html
    assert "paywall-content" in html
    # The free-access state is passed on to the paywall detector.
    assert 'name="surf-free-article"' in html


def test_handler_keeps_locked_page_markers(monkeypatch):
    teaser = "Only the opening paragraph is served here."
    monkeypatch.setattr(
        surf,
        "_requests_get_with_system_trust_interruptibly",
        lambda *a, **k: _FakeResponse(_fa_page(free=False, body=teaser)),
    )

    html = Fetcher._fetch_foreignaffairs_article(FA_URL, _FakeConfig())

    assert 'name="surf-free-article"' not in html
    assert "paywall-locked" in html
    assert Fetcher._detect_paywall(html, url=FA_URL)["detected"] is True


def test_handler_returns_none_when_fetch_fails(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(surf, "_requests_get_with_system_trust_interruptibly", _boom)

    assert Fetcher._fetch_foreignaffairs_article(FA_URL, _FakeConfig()) is None


def test_foreign_affairs_urls_use_the_special_handler():
    handler, site_name, _config = surf._get_handler_for_url(FA_URL)

    assert site_name == "foreignaffairs"
    assert handler is Fetcher._fetch_foreignaffairs_article


@pytest.mark.parametrize(
    "url",
    [
        "https://www.foreignaffairs.com/authors/niall-ferguson",
        "https://www.foreignaffairs.com/topics/political-development",
        "https://www.foreignaffairs.com/regions/china",
        "https://www.foreignaffairs.com/subscribe",
    ],
)
def test_foreign_affairs_listing_pages_skip_the_handler(url):
    assert surf._get_handler_for_url(url) == (None, None, None)
