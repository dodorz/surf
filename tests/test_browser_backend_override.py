"""Tests for the -B/--browser-backend override mechanism.

The CLI and Web UI both apply the override with the shared
``_apply_browser_backend_override`` helper, which rewrites
``[Browser] backend`` so every headless handler picks it up through
``Fetcher._browser_backend``.
"""
import surf_web
from surf import Config, Fetcher, _apply_browser_backend_override


def _make_config(tmp_path):
    return Config(str(tmp_path / "missing-config.ini"))


def test_override_applies_obscura_and_playwright(tmp_path):
    config = _make_config(tmp_path)
    assert Fetcher._browser_backend(config) == "obscura"

    assert _apply_browser_backend_override(config, "playwright") == "playwright"
    assert Fetcher._browser_backend(config) == "playwright"

    assert _apply_browser_backend_override(config, "obscura") == "obscura"
    assert Fetcher._browser_backend(config) == "obscura"


def test_override_auto_and_unknown_values_are_noops(tmp_path):
    config = _make_config(tmp_path)
    config.set("Browser", "backend", "playwright")

    assert _apply_browser_backend_override(config, "auto") is None
    assert Fetcher._browser_backend(config) == "playwright"

    assert _apply_browser_backend_override(config, None) is None
    assert _apply_browser_backend_override(config, "Chromium") is None
    assert Fetcher._browser_backend(config) == "playwright"


def test_override_creates_missing_section(tmp_path):
    config = _make_config(tmp_path)
    assert not config.has_section("Browser")
    _apply_browser_backend_override(config, "OBSCURA")
    assert config.has_section("Browser")
    assert config.get("Browser", "backend") == "obscura"


def test_web_ui_context_exposes_config_browser_backend(tmp_path):
    config = _make_config(tmp_path)
    context = surf_web.get_web_ui_context(config)
    assert context["config_browser_backend"] == "obscura"

    config.set("Browser", "backend", "playwright")
    context = surf_web.get_web_ui_context(config)
    assert context["config_browser_backend"] == "playwright"


def test_web_ui_context_normalizes_invalid_backend(tmp_path):
    config = _make_config(tmp_path)
    config.set("Browser", "backend", "auto")
    context = surf_web.get_web_ui_context(config)
    assert context["config_browser_backend"] == "obscura"


def test_web_index_renders_browser_backend_options():
    response = surf_web.app.test_client().get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'name="browser_backend"' in html
    assert 'value="obscura"' in html
    assert 'value="playwright"' in html
