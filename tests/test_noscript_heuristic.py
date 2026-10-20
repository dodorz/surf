"""Regression test for the <noscript> browser-fallback heuristic."""

import surf
from surf import Fetcher


def test_ssr_page_with_harmless_noscript_does_not_require_browser():
    html = (
        "<html><head><title>SSR Article</title></head><body>"
        "<noscript>Enable JavaScript for the best experience</noscript>"
        "<main>" + ("<p>Server rendered article text.</p>" * 200) + "</main>"
        "</body></html>"
    )

    assert not Fetcher._fetched_html_needs_browser(html)


def test_noscript_only_page_still_requires_browser():
    html = (
        "<html><head><title>App</title></head><body>"
        "<noscript>Please enable JavaScript to continue.</noscript>"
        "</body></html>"
    )

    assert Fetcher._fetched_html_needs_browser(html)
