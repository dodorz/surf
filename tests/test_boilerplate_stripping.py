"""Ad / recommendation / promo blocks must not survive extraction.

Paywalled and archived article pages are the problem case: Readability only
finds a short teaser there, so the rescue path returns the *original* container,
which still holds ad slots, "editors' picks", "related content", podcast and
newsletter promos, and share bars.
"""
import surf
from surf import ContentProcessor

ARTICLE_BODY = (
    "<p>The first real paragraph of the article, long enough to count as prose.</p>"
    "<p>The second real paragraph, also comfortably above forty characters.</p>"
)


def _page(extra):
    return (
        "<html><body><article>"
        "<header><h1>Sample Headline</h1><h2>Editors&rsquo; Picks</h2>"
        "<ul><li><a href='/a'>Pick one</a></li><li><a href='/b'>Pick two</a></li></ul>"
        "</header>"
        f"{ARTICLE_BODY}"
        f"{extra}"
        "</article></body></html>"
    )


def test_advertisement_and_recommendation_rails_are_removed():
    html = _page(
        '<div id="c-col-editors-picks"><h2>Editors&rsquo; Picks</h2>'
        "<ul><li><a href='/x'>A story you might like</a></li></ul></div>"
        '<div class="advertisement"><p>Advertisement</p></div>'
        '<div id="bottom-sheet-sensor"><h2>Related Content</h2>'
        "<ul><li><a href='/y'>Another story</a></li></ul></div>"
        '<div id="newsletter-signup"><p>Sign up for our newsletter to get more.</p></div>'
    )
    cleaned = ContentProcessor._strip_boilerplate_blocks(html)
    assert "first real paragraph" in cleaned
    assert "second real paragraph" in cleaned
    for marker in ("c-col-editors-picks", "Related Content", "newsletter-signup", "advertisement"):
        assert marker not in cleaned


def test_podcast_and_share_promos_are_removed():
    html = _page(
        '<div class="podcast-promo"><h3>Podcast</h3>'
        "<p>Listen to our daily show about the stories that matter.</p></div>"
        '<div class="share-tools"><a href="/share">Share</a><a href="/save">Save</a></div>'
    )
    cleaned = ContentProcessor._strip_boilerplate_blocks(html)
    assert "first real paragraph" in cleaned
    assert "podcast-promo" not in cleaned
    assert "share-tools" not in cleaned


def test_chinese_recommendation_headings_are_removed():
    html = _page(
        "<div><h2>相关阅读</h2><ul><li><a href='/1'>另一篇文章</a></li>"
        "<li><a href='/2'>第三篇文章</a></li></ul></div>"
    )
    cleaned = ContentProcessor._strip_boilerplate_blocks(html)
    assert "first real paragraph" in cleaned
    assert "相关阅读" not in cleaned


def test_harmless_markup_keeps_its_prose():
    # No boilerplate markers: the article body must come back untouched (the
    # HTML string itself may be re-serialized).
    html = _page("")
    cleaned = ContentProcessor._strip_boilerplate_blocks(html)
    assert "first real paragraph" in cleaned
    assert "second real paragraph" in cleaned


def test_short_page_is_never_gutted():
    # A tiny document whose only text happens to look like boilerplate must be
    # kept: the guard refuses to drop more than 75% of the text.
    html = "<html><body><article><p>Advertisement</p></article></body></html>"
    assert "Advertisement" in ContentProcessor._strip_boilerplate_blocks(html)


def test_duplicate_headings_are_collapsed():
    html = (
        "<html><body><article>"
        "<div><h1>Same Title</h1></div>"
        "<div><h1>Same Title</h1></div>"
        f"{ARTICLE_BODY}"
        "</article></body></html>"
    )
    cleaned = ContentProcessor._dedupe_repeated_headings(html)
    assert cleaned.count("Same Title") == 1
    assert "first real paragraph" in cleaned


def test_boilerplate_stripping_is_wired_into_extraction():
    # Readability finds only a teaser here, so the rescue path (original
    # container) is used - the case that used to leak ad rails.
    long_body = "<p>" + ("Real article prose that Readability may skip. " * 12) + "</p>"
    html = (
        "<html><head><title>Sample</title></head><body><article>"
        f"<div>{long_body}</div>"
        '<div id="most-popular"><h2>Most Popular</h2>'
        "<ul><li><a href='/p1'>Popular one</a></li><li><a href='/p2'>Popular two</a></li></ul></div>"
        '<div class="ad-slot"><p>Advertisement</p></div>'
        "</article></body></html>"
    )
    _title, cleaned = ContentProcessor.extract_content(html)
    assert "Real article prose" in cleaned
    assert "most-popular" not in cleaned
    assert "ad-slot" not in cleaned
