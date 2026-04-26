from pathlib import Path

import surf
import surf_web
from surf import Fetcher, OutputHandler


class _FakeConfig:
    llm_provider = ""

    def get(self, section, key, fallback=None):
        if section == "Output" and key == "target_language":
            return "zh-cn"
        return fallback

    def get_path(self, section, key, fallback=None):
        return fallback or "."

    def get_llm_config(self, provider=None):
        return {"model": "fake-model"}


def test_wayback_snapshot_uses_content_location(monkeypatch):
    class _FakeResponse:
        status_code = 302
        headers = {"Content-Location": "/web/20260426000000/https://example.com/post"}

    monkeypatch.setattr(Fetcher, "_get_proxies", staticmethod(lambda *args, **kwargs: ({}, None)))
    monkeypatch.setattr(surf, "_requests_get_interruptibly", lambda *args, **kwargs: _FakeResponse())

    archive_url = Fetcher.save_wayback_snapshot("https://example.com/post", _FakeConfig())

    assert archive_url == "https://web.archive.org/web/20260426000000/https://example.com/post"


def test_markdown_front_matter_includes_archive(tmp_path):
    output_path = tmp_path / "note.md"

    saved = OutputHandler.save_markdown(
        "Title",
        "Body",
        _FakeConfig(),
        output_path=str(output_path),
        html_content="<html><head><title>Title</title></head><body>Body</body></html>",
        source_url="https://example.com/post",
        archive_url="https://web.archive.org/web/20260426000000/https://example.com/post",
    )

    text = Path(saved).read_text(encoding="utf-8")
    assert "source: https://example.com/post" in text
    assert "archive: https://web.archive.org/web/20260426000000/https://example.com/post" in text


def test_web_archive_option_stores_archive_metadata(monkeypatch):
    monkeypatch.setattr(surf_web, "get_config", lambda: _FakeConfig())
    monkeypatch.setattr(
        surf_web.Fetcher,
        "fetch",
        lambda *args, **kwargs: (
            "<html><head><title>Example</title></head>"
            "<body><article><p>Hello world.</p></article></body></html>"
        ),
    )
    monkeypatch.setattr(
        surf_web.OcrHandler,
        "annotate_html_with_ocr",
        lambda cleaned_html, **kwargs: cleaned_html,
    )
    monkeypatch.setattr(
        surf_web.Fetcher,
        "save_wayback_snapshot",
        staticmethod(lambda url, **kwargs: "https://web.archive.org/web/20260426000000/" + url),
    )

    client = surf_web.app.test_client()
    response = client.post(
        "/api/process",
        json={
            "url": "https://example.com/post",
            "lang": "raw",
            "archive_source": True,
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["metadata"]["source_url"] == "https://example.com/post"
    assert payload["metadata"]["archive_url"] == (
        "https://web.archive.org/web/20260426000000/https://example.com/post"
    )
