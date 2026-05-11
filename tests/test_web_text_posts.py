import surf_web
from surf import (
    ContentProcessor,
    Fetcher,
    _build_direct_markdown_payload,
    _convert_embedded_html_in_markdown,
    _translation_was_performed,
)


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


def test_translation_metadata_requires_actual_translation(monkeypatch):
    assert not _translation_was_performed("中文正文", "中文正文", "中文标题", "中文标题")
    assert _translation_was_performed("English", "中文", "Title", "标题")

    monkeypatch.setattr(surf_web, "get_config", lambda: _FakeConfig())
    monkeypatch.setattr(
        surf_web.ContentProcessor,
        "translate_if_needed",
        lambda text, title=None, **kwargs: (text, title),
    )

    client = surf_web.app.test_client()
    response = client.post(
        "/api/process",
        json={
            "url": "这是中文标题。这里是中文正文。",
            "lang": "trans",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["metadata"]["translator"] is None
    assert payload["metadata"]["translated_title"] is None


def test_github_markdown_target_resolution_repo_root():
    config = _FakeConfig()

    targets = Fetcher._build_github_markdown_targets("https://github.com/USER/PROJECT", config)

    assert targets is not None
    assert targets[0]["raw_url"] == "https://github.com/USER/PROJECT/raw/refs/heads/master/README_zh.md"
    assert targets[0]["source_url"] == "https://github.com/USER/PROJECT"
    assert targets[0]["base_url"] == "https://github.com/USER/PROJECT/blob/master/README_zh.md"
    assert targets[1]["raw_url"] == "https://github.com/USER/PROJECT/raw/refs/heads/main/README_zh.md"


def test_github_markdown_target_resolution_direct_md_path():
    config = _FakeConfig()

    targets = Fetcher._build_github_markdown_targets(
        "https://github.com/USER/PROJECT/docs/guide.md",
        config,
    )

    assert targets is not None
    assert targets[0]["raw_url"] == "https://github.com/USER/PROJECT/raw/refs/heads/main/docs/guide.md"
    assert targets[0]["source_url"] == "https://github.com/USER/PROJECT/docs/guide.md"
    assert targets[0]["base_url"] == "https://github.com/USER/PROJECT/blob/main/docs/guide.md"


def test_process_url_preserves_direct_markdown_payload(monkeypatch):
    monkeypatch.setattr(surf_web, "get_config", lambda: _FakeConfig())

    payload_html = _build_direct_markdown_payload(
        markdown_text="[Next](other.md)",
        title="guide.md",
        source_url="https://github.com/USER/PROJECT/docs/guide.md",
        site_name="github",
        base_url="https://github.com/USER/PROJECT/blob/main/docs/guide.md",
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
    assert payload["metadata"]["source_url"] == "https://github.com/USER/PROJECT/docs/guide.md"


def test_direct_markdown_converts_embedded_html_but_preserves_fenced_code():
    markdown = """# Guide

<p>Hello <strong>reader</strong>. <a href="docs/more.md">More</a></p>

```html
<p>Keep this example as HTML.</p>
```
"""

    converted = _convert_embedded_html_in_markdown(markdown)

    assert "Hello **reader**. [More](docs/more.md)" in converted
    assert "<p>Hello" not in converted
    assert "<p>Keep this example as HTML.</p>" in converted


def test_to_markdown_preserves_content_inline_svg_as_image():
    html = """
    <article>
      <p>Before</p>
      <svg aria-hidden="true" class="lucide" width="24" height="24">
        <path d="M0 0h24v24H0z"></path>
      </svg>
      <div class="post-media post-media--svg-board">
        <svg width="100%" viewBox="0 0 200 80" xmlns="http://www.w3.org/2000/svg">
          <g class="node c-purple">
            <rect x="10" y="10" width="80" height="40"></rect>
            <text class="th" x="50" y="30">MIR</text>
            <text class="ts" x="50" y="45">Codegen</text>
          </g>
        </svg>
      </div>
      <p>After</p>
    </article>
    """

    markdown = ContentProcessor.to_markdown(html)

    assert "Before" in markdown
    assert "After" in markdown
    assert "![MIR Codegen](data:image/svg+xml;base64," in markdown
    assert "lucide" not in markdown
    assert "MIRCodegen" not in markdown


def test_direct_markdown_embedded_svg_becomes_image():
    markdown = """
# Guide

<div class="post-media post-media--svg-board">
  <svg width="100%" viewBox="0 0 120 60" xmlns="http://www.w3.org/2000/svg">
    <text x="10" y="20">LLVM IR</text>
    <text x="10" y="40">Machine code</text>
  </svg>
</div>
"""

    converted = _convert_embedded_html_in_markdown(markdown)

    assert "![LLVM IR Machine code](data:image/svg+xml;base64," in converted
    assert "<svg" not in converted
    assert "LLVM IRMachine code" not in converted


def test_web_auto_proxy_uses_cli_implicit_proxy_resolution(monkeypatch):
    monkeypatch.setattr(surf_web, "get_config", lambda: _FakeConfig())

    seen = {}

    def fake_fetch(*args, **kwargs):
        seen["proxy_mode_override"] = kwargs.get("proxy_mode_override")
        return _build_direct_markdown_payload(
            markdown_text="content",
            title="PROJECT",
            source_url="https://github.com/USER/PROJECT",
            site_name="github",
            base_url="https://github.com/USER/PROJECT/blob/main/README.md",
        )

    monkeypatch.setattr(surf_web.Fetcher, "fetch", fake_fetch)

    client = surf_web.app.test_client()
    response = client.post(
        "/api/process",
        json={
            "url": "https://github.com/USER/PROJECT",
            "lang": "raw",
            "proxy": "auto",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert seen["proxy_mode_override"] is None


def test_web_proxy_default_is_no():
    client = surf_web.app.test_client()
    response = client.get("/api/proxy-default")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["proxy_mode"] == "no"


def test_web_process_without_proxy_uses_no_proxy_override(monkeypatch):
    monkeypatch.setattr(surf_web, "get_config", lambda: _FakeConfig())

    seen = {}

    def fake_fetch(*args, **kwargs):
        seen["proxy_mode_override"] = kwargs.get("proxy_mode_override")
        return _build_direct_markdown_payload(
            markdown_text="content",
            title="PROJECT",
            source_url="https://github.com/USER/PROJECT",
            site_name="github",
            base_url="https://github.com/USER/PROJECT/blob/main/README.md",
        )

    monkeypatch.setattr(surf_web.Fetcher, "fetch", fake_fetch)

    client = surf_web.app.test_client()
    response = client.post(
        "/api/process",
        json={
            "url": "https://github.com/USER/PROJECT",
            "lang": "raw",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert seen["proxy_mode_override"] == "no"


def test_web_github_defaults_to_translation_language():
    client = surf_web.app.test_client()
    response = client.get("/api/site-defaults?url=https://github.com/USER/PROJECT")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["site_name"] == "github"
    assert payload["lang_mode"] == "trans"


def test_web_github_untouched_translation_mode_checks_translation(monkeypatch):
    monkeypatch.setattr(surf_web, "get_config", lambda: _FakeConfig())

    def fake_fetch(*args, **kwargs):
        return _build_direct_markdown_payload(
            markdown_text="English README",
            title="PROJECT",
            source_url="https://github.com/USER/PROJECT",
            site_name="github",
            base_url="https://github.com/USER/PROJECT/blob/main/README.md",
        )

    def fake_translate(text, *args, **kwargs):
        assert text == "English README"
        return "中文 README", "PROJECT"

    monkeypatch.setattr(surf_web.Fetcher, "fetch", fake_fetch)
    monkeypatch.setattr(surf_web.ContentProcessor, "translate_if_needed", fake_translate)

    client = surf_web.app.test_client()
    response = client.post(
        "/api/process",
        json={
            "url": "https://github.com/USER/PROJECT",
            "lang": "trans",
            "lang_touched": False,
            "proxy": "no",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["markdown"] == "中文 README"
