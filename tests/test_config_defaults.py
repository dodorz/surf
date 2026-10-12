"""Tests for config-backed defaults (extractor / archive / no_front_matter / html_inline).

CLI flags can only force these options on; config.ini supplies the defaults.
The Web UI pre-checks its radios/checkboxes from the same config values, and
server-side processing falls back to config only when a key is absent from the
submitted form data (so an explicitly unchecked box is never re-enabled).
"""
import surf_web
from surf import Config, resolve_extractor
from surf_web import resolve_web_bool_option


def _make_config(tmp_path):
    return Config(str(tmp_path / "missing-config.ini"))


# --- resolve_extractor ------------------------------------------------------


def test_resolve_extractor_prefers_cli_override(tmp_path):
    config = _make_config(tmp_path)
    config.set("Content", "extractor", "trafilatura")
    assert resolve_extractor(config, "readability") == "readability"
    assert resolve_extractor(config, "raw") == "raw"
    assert resolve_extractor(config, "auto") == "auto"


def test_resolve_extractor_falls_back_to_config(tmp_path):
    config = _make_config(tmp_path)
    config.set("Content", "extractor", "trafilatura")
    assert resolve_extractor(config, None) == "trafilatura"


def test_resolve_extractor_defaults_to_auto(tmp_path):
    config = _make_config(tmp_path)
    assert resolve_extractor(config) == "auto"


def test_resolve_extractor_invalid_config_value_warns_and_returns_auto(tmp_path, caplog):
    config = _make_config(tmp_path)
    config.set("Content", "extractor", "nonsense")
    with caplog.at_level("WARNING"):
        assert resolve_extractor(config) == "auto"
    assert any("extractor" in record.message.lower() for record in caplog.records)


# --- _config_bool (via resolve_web_bool_option for web) ---------------------


def test_resolve_web_bool_option_explicit_false_beats_config_true(tmp_path):
    """A checkbox the user unchecked must stay off even when config defaults it on."""
    config = _make_config(tmp_path)
    config.set("Output", "archive", "true")
    assert resolve_web_bool_option({"archive_source": False}, "archive_source", config, "Output", "archive") is False
    assert resolve_web_bool_option({"archive_source": True}, "archive_source", config, "Output", "archive") is True


def test_resolve_web_bool_option_missing_key_falls_back_to_config(tmp_path):
    config = _make_config(tmp_path)
    config.set("Output", "archive", "true")
    assert resolve_web_bool_option({}, "archive_source", config, "Output", "archive") is True

    config.set("Output", "html_inline", "yes")
    assert resolve_web_bool_option({}, "html_inline", config, "Output", "html_inline") is True

    assert resolve_web_bool_option({}, "no_front_matter", config, "Output", "no_front_matter") is False


# --- Web UI context defaults ------------------------------------------------


def test_web_ui_context_exposes_config_defaults(tmp_path):
    config = _make_config(tmp_path)
    context = surf_web.get_web_ui_context(config)
    assert context["default_extractor"] == "auto"
    assert context["default_html_inline"] is False
    assert context["default_no_front_matter"] is False
    assert context["default_archive"] is False

    config.set("Content", "extractor", "trafilatura")
    config.set("Output", "html_inline", "1")
    config.set("Output", "no_front_matter", "on")
    config.set("Output", "archive", "TRUE")
    context = surf_web.get_web_ui_context(config)
    assert context["default_extractor"] == "trafilatura"
    assert context["default_html_inline"] is True
    assert context["default_no_front_matter"] is True
    assert context["default_archive"] is True


def test_web_index_prechecks_options_from_config(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    config.set("Content", "extractor", "trafilatura")
    config.set("Output", "archive", "true")
    monkeypatch.setattr(surf_web, "get_config", lambda: config)

    response = surf_web.app.test_client().get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="extractor-trafilatura" name="extractor" value="trafilatura" checked' in html
    assert 'id="extractor-auto" name="extractor" value="auto" checked' not in html
    assert 'id="archiveSource" name="archive_source" checked' in html
    assert 'id="noFrontMatter" name="no_front_matter" checked' not in html


def test_web_index_default_config_checks_auto_extractor_only(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    monkeypatch.setattr(surf_web, "get_config", lambda: config)

    response = surf_web.app.test_client().get("/")
    html = response.get_data(as_text=True)
    assert 'id="extractor-auto" name="extractor" value="auto" checked' in html
    assert 'id="archiveSource" name="archive_source" checked' not in html
    assert 'id="noFrontMatter" name="no_front_matter" checked' not in html
    assert 'id="htmlInline" name="html_inline" checked' not in html
