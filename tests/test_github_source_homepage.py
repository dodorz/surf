"""Repositories that declare a homepage: front matter source/github swap.

GitHub renders the project homepage in the repository's About sidebar. When a
repo has one, the project site is the real source and the GitHub URL belongs in
its own `github` field; repos without one keep `source` pointing at GitHub.
"""
from bs4 import BeautifulSoup

import surf
from surf import Fetcher, OutputHandler


_REPO_PAGE = """
<html><body>
<ul><li><a href="https://github.com/about">About</a></li>
    <li><a href="https://github.blog/changelog/">Changelog</a></li></ul>
<div class="sidebar">
  <span>About</span>
  <p>Fast 3kB React alternative with the same modern API.</p>
  <ul>
    <li><a href="/topics/components">components</a></li>
    <li><a href="/topics/dom">dom</a></li>
  </ul>
  {homepage_link}
  <span>Resources</span>
  <ul>
    <li><a href="#readme-ov-file">Readme</a></li>
    <li><a href="#MIT-1-ov-file">MIT license</a></li>
    <li><a href="/owner/repo/activity">Activity</a></li>
    <li><a href="/owner/repo/forks">2.6k forks</a></li>
    <li><a href="/contact/report-content">Report repository</a></li>
  </ul>
</div>
</body></html>
"""


def _repo_page(homepage_link=""):
    return _REPO_PAGE.format(homepage_link=homepage_link)


def test_homepage_is_read_from_the_about_sidebar():
    html = _repo_page('<a href="https://preactjs.com">preactjs.com</a>')
    soup = BeautifulSoup(html, "html.parser")
    assert Fetcher._extract_github_homepage(soup) == "https://preactjs.com"


def test_sidebar_navigation_links_are_not_mistaken_for_the_homepage():
    # No external link at all: topics/readme/activity links must not qualify.
    soup = BeautifulSoup(_repo_page(), "html.parser")
    assert Fetcher._extract_github_homepage(soup) is None


def test_github_links_are_ignored():
    html = _repo_page('<a href="https://github.com/owner/repo">github.com/owner/repo</a>')
    soup = BeautifulSoup(html, "html.parser")
    assert Fetcher._extract_github_homepage(soup) is None


def test_site_wide_about_menu_is_ignored():
    # The global header also starts with "About" but has no Resources block.
    soup = BeautifulSoup(
        _repo_page('<a href="https://github.blog/changelog/">Changelog</a>'), "html.parser"
    )
    assert Fetcher._extract_github_homepage(soup) is None
    assert Fetcher._extract_github_homepage(None) is None


def _github_payload(homepage=None):
    if homepage:
        extra = {"surf-github-url": "https://github.com/owner/repo",
                 "surf-github-homepage": homepage}
    else:
        extra = {"surf-github-url": "https://github.com/owner/repo"}
    return surf._build_direct_markdown_payload(
        markdown_text="# repo\n\nreadme body\n",
        title="repo",
        source_url="https://github.com/owner/repo",
        site_name="github",
        base_url="https://github.com/owner/repo",
        extra_meta=extra,
    )


def test_front_matter_swaps_source_and_github_when_a_homepage_exists():
    metadata = OutputHandler._extract_metadata(
        _github_payload("https://preactjs.com"),
        source_url="https://github.com/owner/repo",
    )
    assert metadata["source"] == "https://preactjs.com"
    assert metadata["github"] == "https://github.com/owner/repo"

    yaml = OutputHandler._generate_yaml_frontmatter(metadata)
    assert "source: https://preactjs.com" in yaml
    assert "github: https://github.com/owner/repo" in yaml


def test_front_matter_keeps_github_as_source_without_a_homepage():
    metadata = OutputHandler._extract_metadata(
        _github_payload(None), source_url="https://github.com/owner/repo"
    )
    assert metadata["source"] == "https://github.com/owner/repo"
    assert metadata.get("github") is None

    yaml = OutputHandler._generate_yaml_frontmatter(metadata)
    assert "source: https://github.com/owner/repo" in yaml
    assert "github:" not in yaml


def test_non_github_pages_are_unaffected():
    html = (
        '<html><head><meta name="surf-source-site" content="example">'
        "<title>Example</title></head><body><p>hello</p></body></html>"
    )
    metadata = OutputHandler._extract_metadata(html, source_url="https://example.com/x")
    assert metadata["source"] == "https://example.com/x"
    assert metadata.get("github") is None
