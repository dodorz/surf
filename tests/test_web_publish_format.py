"""Tests for the Web UI "publish to Pastebin" output format."""
import surf_web


def test_index_offers_publish_format():
    response = surf_web.app.test_client().get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'value="publish"' in html
    assert "发布到 Pastebin" in html


def test_save_publish_returns_paste_url_without_local_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        surf_web.PublishHandler,
        "publish_to_pastebin",
        staticmethod(lambda title, content, config, **kwargs: "https://pastebin.com/abc123"),
    )
    response = surf_web.app.test_client().post(
        "/api/save",
        json={
            "fileType": "publish",
            "saveDir": str(tmp_path),
            "customTitle": "My Paste",
            "data": {"markdown": "Body text", "title": "My Paste", "metadata": {}},
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["savePath"] == "https://pastebin.com/abc123"
    # Publishing must not write anything into the save directory.
    assert list(tmp_path.iterdir()) == []


def test_save_publish_failure_returns_error(monkeypatch):
    monkeypatch.setattr(
        surf_web.PublishHandler,
        "publish_to_pastebin",
        staticmethod(lambda *args, **kwargs: None),
    )
    response = surf_web.app.test_client().post(
        "/api/save",
        json={
            "fileType": "publish",
            "saveDir": "",
            "customTitle": "",
            "data": {"markdown": "x", "title": "x", "metadata": {}},
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is False
    assert "Pastebin" in payload["error"]


def test_save_job_publish_sets_paste_url_as_save_path(monkeypatch):
    called = {}

    def fake_publish(title, content, config, proxy_mode_override=None, custom_proxy_override=None):
        called.update(
            title=title,
            content=content,
            proxy_mode_override=proxy_mode_override,
            custom_proxy_override=custom_proxy_override,
        )
        return "https://pastebin.com/xyz"

    monkeypatch.setattr(
        surf_web.PublishHandler, "publish_to_pastebin", staticmethod(fake_publish)
    )
    monkeypatch.setattr(
        surf_web,
        "_process_web_request",
        lambda data, translate_sync=False: (
            {"markdown": "hello", "title": "T", "metadata": {}},
            "raw",
            False,
        ),
    )
    job_id = "test-publish-job"
    surf_web._store_save_job(
        job_id,
        {
            "status": "pending",
            "created_at": 0,
            "updated_at": 0,
            "data": {
                "fileType": "publish",
                "saveDir": "",
                "customTitle": "T",
                "data": {"markdown": "hello", "title": "T", "metadata": {}},
                "formData": {
                    "url": "https://example.com/article",
                    "proxy": "no",
                    "custom_proxy": "",
                },
            },
        },
    )
    surf_web._run_web_save_job(job_id)
    job = surf_web._get_save_job(job_id)
    assert job["status"] == "done"
    assert job["savePath"] == "https://pastebin.com/xyz"
    assert called["title"] == "T"
    assert called["content"] == "hello"
    assert called["proxy_mode_override"] == "no"
