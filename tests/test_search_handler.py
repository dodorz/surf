"""Tests for keyword search -> selection -> fetch/convert/save support."""

import argparse

import pytest

import surf


DDG_HTML = """
<html><body>
  <div class="result">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=abc">A title</a>
  </div>
  <div class="result">
    <a class="result__a" href="https://example.org/b">B title</a>
  </div>
  <div class="result result--ad">
    <a class="result__a" href="https://ads.example.net/ad">Ad title</a>
  </div>
  <div class="result">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Duplicate</a>
  </div>
</body></html>
"""

BING_HTML = """
<html><body>
  <ol>
    <li class="b_algo"><h2><a href="https://example.com/one">One title</a></h2></li>
    <li class="b_algo"><h2><a
        href="https://www.bing.com/ck/a?!&amp;&amp;p=abc&amp;u=a1aHR0cHM6Ly9leGFtcGxlLm9yZy9mcm9tLWJpbmc&amp;ntb=1">Two title</a></h2></li>
    <li class="b_algo"><h2><a href="https://example.com/one">Duplicate</a></h2></li>
  </ol>
</body></html>
"""


class FakeResponse:
    def __init__(self, content=b"", payload=None):
        self.content = content
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload


def test_parse_duckduckgo_html_unwraps_redirects_and_skips_ads():
    results = surf.SearchHandler._parse_duckduckgo_html(DDG_HTML, limit=10)

    assert results == [
        {"title": "A title", "url": "https://example.com/a"},
        {"title": "B title", "url": "https://example.org/b"},
    ]


def test_parse_duckduckgo_html_respects_limit():
    results = surf.SearchHandler._parse_duckduckgo_html(DDG_HTML, limit=1)

    assert results == [{"title": "A title", "url": "https://example.com/a"}]


def test_parse_bing_html_decodes_redirect_and_dedupes():
    results = surf.SearchHandler._parse_bing_html(BING_HTML, limit=10)

    assert results == [
        {"title": "One title", "url": "https://example.com/one"},
        {"title": "Two title", "url": "https://example.org/from-bing"},
    ]


def test_search_results_drop_tracking_parameters():
    html = (
        '<li class="b_algo"><h2><a href="https://example.com/a?utm_source=x&amp;id=7&amp;fbclid=abc">'
        "A</a></h2></li>"
    )
    results = surf.SearchHandler._parse_bing_html(html, limit=5)

    assert results == [{"title": "A", "url": "https://example.com/a?id=7"}]


def test_source_url_canonicalization_strips_common_trackers():
    url = "https://example.com/post?a=1&utm_medium=x&gclid=y&fbclid=z&spm=123&b=2"
    assert surf.OutputHandler._canonicalize_generic_source_url(url) == "https://example.com/post?a=1&b=2"


def test_search_falls_back_to_next_duckduckgo_endpoint(monkeypatch):
    requested = []

    def fake_get(url, **kwargs):
        requested.append(url)
        if "html.duckduckgo.com" in url:
            return FakeResponse(b"<html><body>no results here</body></html>")
        return FakeResponse(DDG_HTML.encode("utf-8"))

    monkeypatch.setattr(surf, "_requests_get_interruptibly", fake_get)

    results = surf.SearchHandler.search("asyncio tutorial", engine="duckduckgo", limit=2, config=None)

    assert requested[0].startswith("https://html.duckduckgo.com/")
    assert requested[1].startswith("https://lite.duckduckgo.com/")
    assert results == [
        {"title": "A title", "url": "https://example.com/a"},
        {"title": "B title", "url": "https://example.org/b"},
    ]


def test_search_auto_falls_back_to_bing(monkeypatch):
    requested = []

    def fake_get(url, **kwargs):
        requested.append(url)
        if "bing.com" in url:
            return FakeResponse(BING_HTML.encode("utf-8"))
        return FakeResponse(b"<html><body>blocked</body></html>")

    monkeypatch.setattr(surf, "_requests_get_interruptibly", fake_get)

    results = surf.SearchHandler.search("asyncio tutorial", engine="auto", limit=2, config=None)

    assert any("duckduckgo.com" in url for url in requested)
    assert any("bing.com" in url for url in requested)
    assert results == [
        {"title": "One title", "url": "https://example.com/one"},
        {"title": "Two title", "url": "https://example.org/from-bing"},
    ]


def test_search_auto_returns_duckduckgo_results_first(monkeypatch):
    requested = []

    def fake_get(url, **kwargs):
        requested.append(url)
        return FakeResponse(DDG_HTML.encode("utf-8"))

    monkeypatch.setattr(surf, "_requests_get_interruptibly", fake_get)

    results = surf.SearchHandler.search("asyncio tutorial", limit=2, config=None)

    assert requested == ["https://html.duckduckgo.com/html/"]
    assert [item["url"] for item in results] == ["https://example.com/a", "https://example.org/b"]


def test_search_searxng_uses_json_api(monkeypatch):
    requested = []

    payload = {
        "results": [
            {"url": "https://searx.example/a", "title": "Searx A"},
            {"link": "https://searx.example/b", "name": "Searx B"},
        ]
    }

    def fake_get(url, **kwargs):
        requested.append((url, kwargs.get("params")))
        return FakeResponse(payload=payload)

    monkeypatch.setattr(surf, "_requests_get_interruptibly", fake_get)
    config = _FakeConfig({("Search", "searxng_url"): "https://searx.example"})

    results = surf.SearchHandler.search("kw", engine="searxng", limit=5, config=config)

    assert requested[0][0] == "https://searx.example/search"
    assert requested[0][1]["format"] == "json"
    assert results == [
        {"title": "Searx A", "url": "https://searx.example/a"},
        {"title": "Searx B", "url": "https://searx.example/b"},
    ]


def test_search_searxng_requires_config_url():
    with pytest.raises(ValueError):
        surf.SearchHandler.search("kw", engine="searxng", config=None)


def test_search_metaso_uses_api_key_and_json(monkeypatch):
    calls = []
    payload = {"webpages": [{"link": "https://metaso.example/x", "title": "Metaso X"}]}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs.get("json"), kwargs.get("headers")))
        return FakeResponse(payload=payload)

    monkeypatch.setattr(surf, "_requests_post_interruptibly", fake_post)
    config = _FakeConfig({("Search", "metaso_api_key"): "sk-test"})

    results = surf.SearchHandler.search("kw", engine="metaso", limit=3, config=config)

    assert calls[0][0] == "https://metaso.cn/api/v1/search"
    assert calls[0][1]["q"] == "kw"
    assert calls[0][2]["Authorization"] == "Bearer sk-test"
    assert results == [{"title": "Metaso X", "url": "https://metaso.example/x"}]


def test_search_metaso_requires_api_key():
    with pytest.raises(ValueError):
        surf.SearchHandler.search("kw", engine="metaso", config=None)


def test_search_rejects_unknown_engine():
    with pytest.raises(ValueError):
        surf.SearchHandler.search("anything", engine="altavista")


def test_parse_search_selection_supports_ranges_and_all():
    assert surf._parse_search_selection("1,3-4", 5) == [1, 3, 4]
    assert surf._parse_search_selection("all", 3) == [1, 2, 3]
    assert surf._parse_search_selection("", 3) == []


def test_parse_search_selection_rejects_out_of_range():
    with pytest.raises(ValueError):
        surf._parse_search_selection("4", 3)


class _FakeConfig:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, section, key, fallback=None):
        return self.values.get((section, key), fallback)

    def set(self, section, key, value):
        self.values[(section, key)] = value

    def get_path(self, section, key, fallback=None):
        return self.values.get((section, key), fallback)


def _search_args(**overrides):
    defaults = dict(
        url="asyncio tutorial",
        output=None,
        O=False,
        search_engine=None,
        search_limit=None,
        search_preview=None,
        search_top=None,
        feel_lucky=False,
        search_merge=False,
        search_prefix=None,
        search_delay=None,
        archive=False,
        no_front_matter=False,
        html_inline=False,
        speak=False,
        llm=None,
        format=None,
        h=False,
        p=False,
        a=False,
        P=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_select_search_results_top_and_lucky():
    results = [{"title": f"T{i}", "url": f"https://e/{i}"} for i in range(5)]
    config = _FakeConfig()
    parser = argparse.ArgumentParser()

    assert surf._select_search_results(results, "kw", _search_args(search_top=2), config, parser) == results[:2]
    assert surf._select_search_results(results, "kw", _search_args(feel_lucky=True), config, parser) == results[:1]


def test_build_search_filename_prefix_uses_keywords_by_default():
    assert surf._build_search_filename_prefix(_search_args(), "asyncio tutorial") == "[asyncio tutorial]"


def test_build_search_filename_prefix_honors_override_and_truncates():
    assert surf._build_search_filename_prefix(_search_args(search_prefix="my/prefix-"), "kw") == "[myprefix]"
    long_keywords = "x" * 100
    prefix = surf._build_search_filename_prefix(_search_args(), long_keywords)
    assert prefix == "[" + "x" * surf.SearchHandler.MAX_PREFIX_LENGTH + "]"


def test_build_search_filename_prefix_keeps_existing_brackets():
    assert surf._build_search_filename_prefix(_search_args(search_prefix="[kw]"), "ignored") == "[kw]"


def test_search_preview_defaults_to_five():
    parser = argparse.ArgumentParser()
    assert surf._resolve_search_preview(_search_args(), _FakeConfig(), parser) == 5
    assert surf._resolve_search_preview(_search_args(search_preview=2), _FakeConfig(), parser) == 2
    assert surf._resolve_search_preview(
        _search_args(), _FakeConfig({("Search", "preview"): "3"}), parser
    ) == 3


def test_select_search_results_interactive(monkeypatch, capsys):
    results = [{"title": f"T{i}", "url": f"https://e/{i}"} for i in range(4)]
    monkeypatch.setattr(surf.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "2-3")

    selected = surf._select_search_results(
        results,
        "kw",
        _search_args(search_preview=3),
        _FakeConfig(),
        argparse.ArgumentParser(),
    )

    printed = capsys.readouterr().out
    assert "T0" in printed and "https://e/0" in printed
    assert "T3" not in printed  # preview limited to 3 entries
    assert selected == [results[1], results[2]]


def test_select_search_results_non_tty_processes_preview(monkeypatch):
    results = [{"title": f"T{i}", "url": f"https://e/{i}"} for i in range(4)]
    monkeypatch.setattr(surf.sys.stdin, "isatty", lambda: False)

    selected = surf._select_search_results(
        results,
        "kw",
        _search_args(),
        _FakeConfig(),
        argparse.ArgumentParser(),
    )

    assert selected == results


def test_run_search_batch_processes_selected_results(monkeypatch):
    results = [
        {"title": "One", "url": "https://one.example/a"},
        {"title": "Two", "url": "https://two.example/b"},
        {"title": "Three", "url": "https://three.example/c"},
    ]
    monkeypatch.setattr(surf.SearchHandler, "search", classmethod(lambda cls, *a, **k: results))
    prepared = []

    def fake_prepare(args, config, parser, proxy_mode, custom_proxy):
        prepared.append(args.url)
        if "two.example" in args.url:
            raise SystemExit(1)
        return {"url": args.url, "processed": {"title": args.url}}

    saved = []

    def fake_save(prep, args, config, filename_prefix="", search_query=None):
        saved.append((prep["url"], filename_prefix, search_query))

    monkeypatch.setattr(surf, "_prepare_single_url", fake_prepare)
    monkeypatch.setattr(surf, "_save_single_result", fake_save)

    args = _search_args(search_top=3, search_prefix="kw-")

    surf._run_search_batch(args, _FakeConfig(), argparse.ArgumentParser(), None, None)

    assert prepared == [item["url"] for item in results]
    assert saved == [
        ("https://one.example/a", "[kw]", "asyncio tutorial"),
        ("https://three.example/c", "[kw]", "asyncio tutorial"),
    ]


def test_run_search_batch_merge_saves_single_document(monkeypatch):
    results = [
        {"title": "One", "url": "https://one.example/a"},
        {"title": "Two", "url": "https://two.example/b"},
    ]
    monkeypatch.setattr(surf.SearchHandler, "search", classmethod(lambda cls, *a, **k: results))
    monkeypatch.setattr(
        surf,
        "_prepare_single_url",
        lambda args, config, parser, proxy_mode, custom_proxy: {"url": args.url, "processed": {"title": args.url}},
    )
    merged = []

    def fake_merge(prepared, args, config, keywords, filename_prefix="", **kwargs):
        merged.append(([item["url"] for item in prepared], keywords, filename_prefix))

    monkeypatch.setattr(surf, "_save_merged_search_results", fake_merge)

    args = _search_args(search_top=2, search_merge=True, search_prefix="kw-")
    surf._run_search_batch(args, _FakeConfig(), argparse.ArgumentParser(), None, None)

    assert merged == [([item["url"] for item in results], "asyncio tutorial", "[kw]")]


def test_run_search_batch_rejects_output_file_path():
    args = _search_args(output="note.md")

    with pytest.raises(SystemExit):
        surf._run_search_batch(args, _FakeConfig(), argparse.ArgumentParser(), None, None)


def test_run_search_batch_exits_when_every_result_fails(monkeypatch):
    monkeypatch.setattr(
        surf.SearchHandler,
        "search",
        classmethod(lambda cls, *a, **k: [{"title": "One", "url": "https://one.example/a"}]),
    )

    def fake_prepare(args, config, parser, proxy_mode, custom_proxy):
        raise SystemExit(1)

    monkeypatch.setattr(surf, "_prepare_single_url", fake_prepare)

    with pytest.raises(SystemExit) as excinfo:
        surf._run_search_batch(_search_args(search_top=1), _FakeConfig(), argparse.ArgumentParser(), None, None)

    assert excinfo.value.code == 1


def test_save_markdown_filename_prefix_and_search_query(tmp_path):
    config = _FakeConfig(
        {
            ("Output", "md_dir"): str(tmp_path),
        }
    )

    saved = surf.OutputHandler.save_markdown(
        "Example Title",
        "# Example\n\nBody\n",
        config,
        add_front_matter=True,
        search_query="example keywords",
        filename_prefix="kw-",
    )

    assert saved == str(tmp_path / "kw-Example Title.md")
    text = (tmp_path / "kw-Example Title.md").read_text(encoding="utf-8")
    assert "title: Example Title" in text
    assert "search_query: example keywords" in text
