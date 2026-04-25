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
    assert data["raw"] == data["markdown"]
    assert "Main post body." in data["markdown"]
