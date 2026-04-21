import surf_web


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
