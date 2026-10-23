"""Tests for the Surf Web search API and search-aware saving."""

import pytest

import surf_web


class _FakeConfig:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, section, key, fallback=None):
        return self.values.get((section, key), fallback)

    def set(self, section, key, value):
        self.values[(section, key)] = value

    def get_path(self, section, key, fallback=None):
        return self.values.get((section, key), fallback)


@pytest.fixture()
def client():
    surf_web.app.config.update(TESTING=True)
    with surf_web.app.test_client() as test_client:
        yield test_client


def test_web_search_api_returns_results_and_prefix(client, monkeypatch):
    calls = {}

    def fake_search(cls, keywords, **kwargs):
        calls["keywords"] = keywords
        calls["engine"] = kwargs.get("engine")
        calls["limit"] = kwargs.get("limit")
        return [
            {"title": "Result One", "url": "https://example.com/one"},
            {"title": "Result Two", "url": "https://example.com/two"},
        ]

    monkeypatch.setattr(surf_web.SearchHandler, "search", classmethod(fake_search))

    response = client.post(
        "/api/search",
        json={"keywords": "demo keywords", "search_engine": "bing", "search_limit": 2},
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["keywords"] == "demo keywords"
    assert payload["engine"] == "bing"
    assert payload["limit"] == 2
    assert payload["prefix"] == "[demo keywords]"
    assert [item["url"] for item in payload["results"]] == [
        "https://example.com/one",
        "https://example.com/two",
    ]
    assert calls == {"keywords": "demo keywords", "engine": "bing", "limit": 2}


def test_web_search_api_uses_explicit_prefix(client, monkeypatch):
    monkeypatch.setattr(
        surf_web.SearchHandler,
        "search",
        classmethod(lambda cls, *a, **k: [{"title": "A", "url": "https://example.com/a"}]),
    )

    response = client.post(
        "/api/search",
        json={"keywords": "kw", "search_prefix": "custom"},
    )
    payload = response.get_json()

    assert payload["success"] is True
    assert payload["prefix"] == "[custom]"


def test_web_search_api_requires_keywords(client):
    response = client.post("/api/search", json={"keywords": "   "})
    payload = response.get_json()

    assert payload["success"] is False
    assert "关键词" in payload["error"]


def test_web_search_api_reports_engine_errors(client, monkeypatch):
    def failing_search(cls, *args, **kwargs):
        raise ValueError("searxng requires [Search] searxng_url in config.ini")

    monkeypatch.setattr(surf_web.SearchHandler, "search", classmethod(failing_search))

    response = client.post("/api/search", json={"keywords": "kw", "search_engine": "searxng"})
    payload = response.get_json()

    assert payload["success"] is False
    assert "searxng_url" in payload["error"]


def test_web_text_post_metadata_carries_search_provenance(monkeypatch):
    monkeypatch.setattr(surf_web.ContentProcessor, "translate_if_needed", lambda *a, **k: (a[0], None))

    result, _lang_mode, _pending = surf_web._process_web_request(
        {
            "url": "A plain text post body.",
            "lang": "raw",
            "search_query": "demo keywords",
            "search_prefix": "",
        }
    )

    assert result["metadata"]["search_query"] == "demo keywords"
    assert result["metadata"]["search_prefix"] == "[demo keywords]"


def test_build_output_path_applies_search_prefix(tmp_path):
    path = surf_web.build_output_path(
        "Example Title",
        "md",
        str(tmp_path),
        filename_prefix="[kw]",
    )

    assert path == str(tmp_path / "[kw]Example Title.md")


def test_sync_save_writes_prefixed_filename_and_search_query(client, tmp_path):
    response = client.post(
        "/api/save",
        json={
            "fileType": "md",
            "saveDir": str(tmp_path),
            "customTitle": "Example Title",
            "data": {
                "title": "Example Title",
                "markdown": "# Example\n\nBody\n",
                "metadata": {
                    "title": "Example Title",
                    "html_content": "",
                    "add_front_matter": True,
                    "source_url": None,
                    "search_query": "demo keywords",
                    "search_prefix": "[demo keywords]",
                },
            },
        },
    )
    payload = response.get_json()

    assert payload["success"] is True
    saved = tmp_path / "[demo keywords]Example Title.md"
    assert payload["savePath"] == str(saved)
    text = saved.read_text(encoding="utf-8")
    assert "search_query: demo keywords" in text
    assert "title: Example Title" in text


def test_build_combined_result_payload_keeps_search_provenance():
    combined = surf_web.build_combined_result_payload(
        [
            {
                "title": "One",
                "markdown": "one",
                "metadata": {"search_query": "demo keywords", "search_prefix": "[demo keywords]"},
            },
            {
                "title": "Two",
                "markdown": "two",
                "metadata": {"search_query": "demo keywords", "search_prefix": "[demo keywords]"},
            },
        ]
    )

    assert combined["metadata"]["search_query"] == "demo keywords"
    assert combined["metadata"]["search_prefix"] == "[demo keywords]"
    assert combined["metadata"]["add_front_matter"] is True


def test_build_combined_result_payload_without_search_has_no_front_matter():
    combined = surf_web.build_combined_result_payload(
        [{"title": "One", "markdown": "one", "metadata": {"source_url": "https://example.com/1"}}]
    )

    assert combined["metadata"]["add_front_matter"] is False
    assert combined["metadata"]["search_query"] is None


def test_async_save_worker_applies_search_prefix_and_front_matter(tmp_path):
    surf_web._store_save_job(
        "search-save-job",
        {
            "status": "pending",
            "created_at": 0,
            "updated_at": 0,
            "data": {
                "fileType": "md",
                "saveDir": str(tmp_path),
                "customTitle": "",
                "data": {},
                "formData": {
                    "url": "A plain text search result body.",
                    "lang": "raw",
                    "search_query": "demo keywords",
                    "search_prefix": "",
                },
                "speak": False,
            },
        },
    )

    surf_web._run_web_save_job("search-save-job")
    job = surf_web._get_save_job("search-save-job")

    assert job["status"] == "done", job.get("error")
    saved = tmp_path / "[demo keywords]A plain text search result body.md"
    assert job["savePath"] == str(saved)
    text = saved.read_text(encoding="utf-8")
    assert "search_query: demo keywords" in text


def test_async_save_worker_combines_search_results(tmp_path):
    results = [
        {
            "title": "First",
            "markdown": "First body.",
            "input_url": "First text body.",
            "metadata": {
                "search_query": "demo keywords",
                "search_prefix": "[demo keywords]",
            },
        },
        {
            "title": "Second",
            "markdown": "Second body.",
            "input_url": "Second text body.",
            "metadata": {
                "search_query": "demo keywords",
                "search_prefix": "[demo keywords]",
            },
        },
    ]
    surf_web._store_save_job(
        "search-combine-job",
        {
            "status": "pending",
            "created_at": 0,
            "updated_at": 0,
            "data": {
                "fileType": "md",
                "saveDir": str(tmp_path),
                "customTitle": "Merged results",
                "combine_all": True,
                "results": results,
                "formData": {"lang": "raw", "search_query": "demo keywords", "search_prefix": ""},
                "speak": False,
            },
        },
    )

    surf_web._run_web_save_job("search-combine-job")
    job = surf_web._get_save_job("search-combine-job")

    assert job["status"] == "done", job.get("error")
    saved = tmp_path / "[demo keywords]Merged results.md"
    assert job["savePath"] == str(saved)
    text = saved.read_text(encoding="utf-8")
    assert "search_query: demo keywords" in text
    assert "First text body." in text
    assert "Second text body." in text


def test_async_save_worker_inherits_search_provenance_from_cached_result(tmp_path):
    surf_web._store_save_job(
        "cached-search-save-job",
        {
            "status": "pending",
            "created_at": 0,
            "updated_at": 0,
            "data": {
                "fileType": "md",
                "saveDir": str(tmp_path),
                "customTitle": "",
                "data": {
                    "metadata": {
                        "search_query": "demo keywords",
                        "search_prefix": "[demo keywords]",
                    }
                },
                "formData": {
                    "url": "A plain text search result body.",
                    "lang": "raw",
                },
                "speak": False,
            },
        },
    )

    surf_web._run_web_save_job("cached-search-save-job")
    job = surf_web._get_save_job("cached-search-save-job")

    assert job["status"] == "done", job.get("error")
    saved = tmp_path / "[demo keywords]A plain text search result body.md"
    assert job["savePath"] == str(saved)
    text = saved.read_text(encoding="utf-8")
    assert "search_query: demo keywords" in text


def test_web_search_failure_writes_failed_url_note(client, monkeypatch, tmp_path):
    monkeypatch.setattr(surf_web, "get_config", lambda: _FakeConfig({("Output", "md_dir"): str(tmp_path)}))

    def failing_process(data, translate_sync=False):
        raise ValueError("fetch failed")

    monkeypatch.setattr(surf_web, "_process_web_request", failing_process)

    response = client.post(
        "/api/process",
        json={
            "url": "https://example.com/fail",
            "lang": "raw",
            "search_query": "demo keywords",
        },
    )
    payload = response.get_json()

    assert payload["success"] is False
    saved = tmp_path / "获取失败_example.com_fail.md"
    assert saved.read_text(encoding="utf-8") == "https://example.com/fail\n"


def test_web_non_search_failure_writes_failed_url_note(client, monkeypatch, tmp_path):
    monkeypatch.setattr(surf_web, "get_config", lambda: _FakeConfig({("Output", "md_dir"): str(tmp_path)}))

    def failing_process(data, translate_sync=False):
        raise ValueError("fetch failed")

    monkeypatch.setattr(surf_web, "_process_web_request", failing_process)

    response = client.post(
        "/api/process",
        json={"url": "https://example.com/fail", "lang": "raw"},
    )

    assert response.get_json()["success"] is False
    saved = tmp_path / "获取失败_example.com_fail.md"
    assert saved.read_text(encoding="utf-8") == "https://example.com/fail\n"


def test_web_text_post_failure_does_not_write_note(client, monkeypatch, tmp_path):
    monkeypatch.setattr(surf_web, "get_config", lambda: _FakeConfig({("Output", "md_dir"): str(tmp_path)}))

    def failing_process(data, translate_sync=False):
        raise ValueError("processing failed")

    monkeypatch.setattr(surf_web, "_process_web_request", failing_process)

    response = client.post(
        "/api/process",
        json={"url": "just a plain text post", "lang": "raw"},
    )

    assert response.get_json()["success"] is False
    assert list(tmp_path.glob("*.md")) == []
