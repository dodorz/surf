"""Tests for per-run TTS overrides (CLI --tts-* flags and Web UI TTS options).

``_apply_tts_overrides`` rewrites ``[TTS] voice/rate/volume`` so
``TTSHandler.generate_speech`` picks the override up through the shared
config object. The Web UI exposes a voice dropdown plus rate/volume inputs
(shown only when the Audio format is selected) whose values ride along with
the existing form data into ``/api/save``, ``/api/save-async``, and
``/api/play-audio``.
"""
import asyncio

import surf_web
from surf import Config, TTSHandler, _apply_tts_overrides
from surf_web import build_tts_voice_options


def _make_config(tmp_path):
    return Config(str(tmp_path / "missing-config.ini"))


# --- _apply_tts_overrides ---------------------------------------------------


def test_apply_tts_overrides_sets_voice_rate_volume(tmp_path):
    config = _make_config(tmp_path)
    assert _apply_tts_overrides(config, "en-US-AriaNeural", "+10%", "-20%") is True
    assert config.get("TTS", "voice") == "en-US-AriaNeural"
    assert config.get("TTS", "rate") == "+10%"
    assert config.get("TTS", "volume") == "-20%"


def test_apply_tts_overrides_ignores_empty_values(tmp_path):
    config = _make_config(tmp_path)
    config.set("TTS", "voice", "zh-CN-XiaoxiaoNeural")
    assert _apply_tts_overrides(config, None, "", "   ") is False
    assert config.get("TTS", "voice") == "zh-CN-XiaoxiaoNeural"


def test_apply_tts_overrides_partial_override(tmp_path):
    config = _make_config(tmp_path)
    config.set("TTS", "voice", "zh-CN-XiaoxiaoNeural")
    assert _apply_tts_overrides(config, rate="+10%") is True
    assert config.get("TTS", "voice") == "zh-CN-XiaoxiaoNeural"
    assert config.get("TTS", "rate") == "+10%"


def test_apply_tts_overrides_skips_invalid_rate_volume(tmp_path, caplog):
    config = _make_config(tmp_path)
    config.set("TTS", "rate", "+0%")
    config.set("TTS", "volume", "+0%")
    with caplog.at_level("WARNING"):
        applied = _apply_tts_overrides(config, None, "fast", "loud")
    assert applied is False
    assert config.get("TTS", "rate") == "+0%"
    assert config.get("TTS", "volume") == "+0%"
    assert any("rate" in record.message for record in caplog.records)
    assert any("volume" in record.message for record in caplog.records)


def test_apply_tts_overrides_creates_missing_section(tmp_path):
    config = _make_config(tmp_path)
    assert not config.has_section("TTS")
    _apply_tts_overrides(config, "en-US-GuyNeural")
    assert config.has_section("TTS")
    assert config.get("TTS", "voice") == "en-US-GuyNeural"


def test_tts_handler_reads_overridden_config(tmp_path, monkeypatch):
    """run_tts must pick overridden voice/rate/volume up from the config."""
    config = _make_config(tmp_path)
    _apply_tts_overrides(config, "en-US-AriaNeural", "+10%", "-20%")
    captured = {}

    def fake_communicate(text, voice, rate="+0%", volume="+0%"):
        captured.update(voice=voice, rate=rate, volume=volume)

        class _Fake:
            async def save(self, path):
                return None

        return _Fake()

    monkeypatch.setattr("edge_tts.Communicate", fake_communicate)
    asyncio.run(TTSHandler.generate_speech("hello", str(tmp_path / "out.mp3"), config))
    assert captured == {"voice": "en-US-AriaNeural", "rate": "+10%", "volume": "-20%"}


# --- Web UI voice options ---------------------------------------------------


def test_build_tts_voice_options_includes_default_voice():
    options = build_tts_voice_options("zh-CN-XiaoxiaoNeural")
    values = [opt["value"] for opt in options]
    assert "zh-CN-XiaoxiaoNeural" in values
    selected = [opt for opt in options if opt["selected"]]
    assert [opt["value"] for opt in selected] == ["zh-CN-XiaoxiaoNeural"]


def test_build_tts_voice_options_appends_unknown_default_voice():
    options = build_tts_voice_options("custom-CustomVoice")
    assert options[0]["value"] == "custom-CustomVoice"
    assert options[0]["selected"] is True


def test_web_ui_context_exposes_tts_defaults(tmp_path):
    config = _make_config(tmp_path)
    context = surf_web.get_web_ui_context(config)
    assert context["default_tts_voice"] == "zh-CN-XiaoxiaoNeural"
    assert context["default_tts_rate"] == "+0%"
    assert context["default_tts_volume"] == "+0%"
    assert any(opt["selected"] for opt in context["tts_voice_options"])

    config.set("TTS", "voice", "en-US-GuyNeural")
    config.set("TTS", "rate", "+10%")
    context = surf_web.get_web_ui_context(config)
    assert context["default_tts_voice"] == "en-US-GuyNeural"
    assert context["default_tts_rate"] == "+10%"
    selected = [opt for opt in context["tts_voice_options"] if opt["selected"]]
    assert [opt["value"] for opt in selected] == ["en-US-GuyNeural"]


def test_web_index_renders_tts_options():
    response = surf_web.app.test_client().get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="ttsOptionsGroup"' in html
    assert 'id="ttsVoice" name="tts_voice"' in html
    assert 'id="ttsRate" name="tts_rate"' in html
    assert 'id="ttsVolume" name="tts_volume"' in html


# --- Web endpoints honour tts form fields -----------------------------------


def test_save_audio_applies_tts_overrides_from_form_data(monkeypatch, tmp_path):
    captured = {}

    def fake_run_tts(title, content, config, speak=False, save_path=None):
        captured.update(
            voice=config.get("TTS", "voice"),
            rate=config.get("TTS", "rate"),
            volume=config.get("TTS", "volume"),
        )

    monkeypatch.setattr(surf_web.TTSHandler, "run_tts", staticmethod(fake_run_tts))
    response = surf_web.app.test_client().post(
        "/api/save",
        json={
            "fileType": "audio",
            "saveDir": str(tmp_path),
            "customTitle": "T",
            "data": {"markdown": "hello", "title": "T", "metadata": {}},
            "formData": {
                "tts_voice": "en-US-AriaNeural",
                "tts_rate": "+10%",
                "tts_volume": "-20%",
            },
        },
    )
    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert captured == {"voice": "en-US-AriaNeural", "rate": "+10%", "volume": "-20%"}


def test_save_audio_without_tts_fields_keeps_config_defaults(monkeypatch, tmp_path):
    captured = {}

    def fake_run_tts(title, content, config, speak=False, save_path=None):
        captured.update(
            voice=config.get("TTS", "voice", fallback="zh-CN-XiaoxiaoNeural"),
            rate=config.get("TTS", "rate", fallback="+0%"),
            volume=config.get("TTS", "volume", fallback="+0%"),
        )

    monkeypatch.setattr(surf_web.TTSHandler, "run_tts", staticmethod(fake_run_tts))
    monkeypatch.setattr(
        surf_web, "get_config", lambda: _make_config(tmp_path)
    )
    response = surf_web.app.test_client().post(
        "/api/save",
        json={
            "fileType": "audio",
            "saveDir": str(tmp_path),
            "customTitle": "T",
            "data": {"markdown": "hello", "title": "T", "metadata": {}},
        },
    )
    assert response.status_code == 200
    assert captured["voice"] == "zh-CN-XiaoxiaoNeural"
    assert captured["rate"] == "+0%"


def test_save_job_audio_applies_tts_overrides(monkeypatch):
    captured = {}

    def fake_run_tts(title, content, config, speak=False, save_path=None):
        captured.update(voice=config.get("TTS", "voice"))

    monkeypatch.setattr(surf_web.TTSHandler, "run_tts", staticmethod(fake_run_tts))
    monkeypatch.setattr(
        surf_web,
        "_process_web_request",
        lambda data, translate_sync=False: (
            {"markdown": "hello", "title": "T", "metadata": {}},
            "raw",
            False,
        ),
    )
    job_id = "test-tts-audio-job"
    surf_web._store_save_job(
        job_id,
        {
            "status": "pending",
            "created_at": 0,
            "updated_at": 0,
            "data": {
                "fileType": "audio",
                "saveDir": "",
                "customTitle": "T",
                "data": {"markdown": "hello", "title": "T", "metadata": {}},
                "formData": {"tts_voice": "en-US-GuyNeural"},
            },
        },
    )
    surf_web._run_web_save_job(job_id)
    job = surf_web._get_save_job(job_id)
    assert job["status"] == "done"
    assert captured["voice"] == "en-US-GuyNeural"


def test_play_audio_applies_tts_overrides(monkeypatch):
    captured = {}

    def fake_run_tts(title, content, config, speak=False, save_path=None):
        captured.update(voice=config.get("TTS", "voice"))

    monkeypatch.setattr(surf_web.TTSHandler, "run_tts", staticmethod(fake_run_tts))
    response = surf_web.app.test_client().post(
        "/api/play-audio",
        json={
            "data": {"markdown": "hello", "title": "T"},
            "formData": {"tts_voice": "en-US-JennyNeural"},
        },
    )
    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert captured["voice"] == "en-US-JennyNeural"
