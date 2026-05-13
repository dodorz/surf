from pathlib import Path

from surf import _build_direct_markdown_payload
from surf_web import app, Fetcher


def test_process_url_accepts_direct_markdown_payload(monkeypatch):
    def fake_fetch(*args, **kwargs):
        return _build_direct_markdown_payload(
            markdown_text="Author: alice\n\nMain post body.",
            title="Example Topic",
            source_url="https://v2ex.com/t/1",
            site_name="v2ex",
        )

    monkeypatch.setattr(Fetcher, "fetch", fake_fetch)

    client = app.test_client()
    response = client.post(
        "/api/process",
        json={
            "url": "https://v2ex.com/t/1",
            "lang": "raw",
            "proxy": "custom",
            "custom_proxy": "http://127.0.0.1:7890",
            "thread_mode": "default",
        },
    )

    data = response.get_json()
    assert response.status_code == 200
    assert data["success"] is True
    assert data["title"] == "Example Topic"
    assert data["defaultSaveTitle"] == "Example Topic"
    assert data["raw"] == data["markdown"]
    assert "Main post body." in data["markdown"]


def test_save_endpoint_uses_custom_title_and_dir(monkeypatch, tmp_path):
    saved = {}

    def fake_save_markdown(title, content, config, **kwargs):
        saved["title"] = title
        saved["content"] = content
        saved["output_path"] = kwargs["output_path"]
        Path(kwargs["output_path"]).write_text(content, encoding="utf-8")
        return kwargs["output_path"]

    monkeypatch.setattr("surf_web.OutputHandler.save_markdown", fake_save_markdown)

    client = app.test_client()
    response = client.post(
        "/api/save",
        json={
            "fileType": "md",
            "saveDir": str(tmp_path),
            "customTitle": "Custom Name",
            "data": {
                "title": "Original Title",
                "markdown": "# body",
                "metadata": {
                    "html_content": "<html><head><title>Original Title</title></head></html>",
                    "add_front_matter": True,
                    "source_url": "https://example.com/post",
                },
            },
        },
    )

    data = response.get_json()
    assert response.status_code == 200
    assert data["success"] is True
    assert saved["title"] == "Custom Name"
    assert Path(saved["output_path"]).name == "Custom Name.md"
    assert Path(data["savePath"]).name == "Custom Name.md"
