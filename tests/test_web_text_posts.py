import surf_web
from surf import Fetcher, _build_direct_markdown_payload


class _FakeConfig:
    llm_provider = ""

    def get(self, section, key, fallback=None):
        if section == "Output" and key == "target_language":
            return "zh-cn"
        return fallback

    def get_path(self, section, key, fallback=None):
        return fallback or "."

    def _get_available_llm_providers(self):
        return []

    def get_llm_config(self, provider=None):
        return {"model": "fake-model"}


def test_extract_text_post_title_uses_first_sentence():
    title = surf_web.extract_text_post_title("第一句就是标题。后面还有内容。")

    assert title == "第一句就是标题。"


def test_process_url_treats_plain_text_as_post(monkeypatch):
    monkeypatch.setattr(surf_web, "get_config", lambda: _FakeConfig())
    monkeypatch.setattr(
        surf_web.OcrHandler,
        "annotate_html_with_ocr",
        lambda cleaned_html, **kwargs: cleaned_html,
    )

    def _should_not_fetch(*args, **kwargs):
        raise AssertionError("Fetcher.fetch should not be called for plain text posts")

    monkeypatch.setattr(surf_web.Fetcher, "fetch", _should_not_fetch)

    client = surf_web.app.test_client()
    response = client.post(
        "/api/process",
        json={
            "url": "第一句就是标题。后面还有内容。",
            "lang": "raw",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["title"] == "第一句就是标题。"
    assert "后面还有内容。" in payload["markdown"]
    assert payload["metadata"]["source_url"] is None


def test_github_markdown_target_resolution_repo_root():
    config = _FakeConfig()

    targets = Fetcher._build_github_markdown_targets("https://github.com/USER/PROJECT", config)

    assert targets is not None
    assert targets[0]["raw_url"] == "https://github.com/USER/PROJECT/raw/refs/heads/master/README_zh.md"
    assert targets[0]["source_url"] == "https://github.com/USER/PROJECT/blob/master/README_zh.md"
    assert targets[1]["raw_url"] == "https://github.com/USER/PROJECT/raw/refs/heads/main/README_zh.md"


def test_github_markdown_target_resolution_direct_md_path():
    config = _FakeConfig()

    targets = Fetcher._build_github_markdown_targets(
        "https://github.com/USER/PROJECT/docs/guide.md",
        config,
    )

    assert targets is not None
    assert targets[0]["raw_url"] == "https://github.com/USER/PROJECT/raw/refs/heads/main/docs/guide.md"
    assert targets[0]["source_url"] == "https://github.com/USER/PROJECT/blob/main/docs/guide.md"


def test_process_url_preserves_direct_markdown_payload(monkeypatch):
    monkeypatch.setattr(surf_web, "get_config", lambda: _FakeConfig())

    payload_html = _build_direct_markdown_payload(
        markdown_text="[Next](other.md)",
        title="guide.md",
        source_url="https://github.com/USER/PROJECT/blob/main/docs/guide.md",
        site_name="github",
    )

    monkeypatch.setattr(
        surf_web.Fetcher,
        "fetch",
        lambda *args, **kwargs: payload_html,
    )

    def _should_not_ocr(*args, **kwargs):
        raise AssertionError("Direct markdown payload should skip OCR")

    monkeypatch.setattr(surf_web.OcrHandler, "annotate_html_with_ocr", _should_not_ocr)

    client = surf_web.app.test_client()
    response = client.post(
        "/api/process",
        json={
            "url": "https://github.com/USER/PROJECT/docs/guide.md",
            "lang": "raw",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["title"] == "guide.md"
    assert payload["markdown"] == "[Next](https://github.com/USER/PROJECT/blob/main/docs/other.md)"
    assert payload["metadata"]["source_url"] == "https://github.com/USER/PROJECT/blob/main/docs/guide.md"
