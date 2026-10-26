"""Playwright is an optional dependency.

These tests pin the contract introduced when `playwright` moved from the hard
dependency list into the `browser` extra:

  * the metadata no longer requires Playwright to install Surf;
  * features that need it raise an actionable error instead of a bare
    `ModuleNotFoundError`;
  * PDF generation falls back to a local Chrome/Chromium CLI.
"""
import pathlib
import sys
import types

import pytest

import surf
from surf import OutputHandler


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _project_metadata():
    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python < 3.11
        pytest.skip("tomllib is required to read pyproject.toml")
    return tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_playwright_is_only_an_optional_extra():
    metadata = _project_metadata()["project"]
    assert "playwright" not in metadata["dependencies"]
    assert metadata["optional-dependencies"]["browser"] == ["playwright"]
    # The raw CDP client for the default Obscura backend is a hard dependency.
    assert any(dep.split(">=")[0] == "websockets" for dep in metadata["dependencies"])


def test_websockets_is_importable_without_playwright():
    pytest.importorskip("websockets.sync.client")


def test_require_playwright_raises_actionable_error(monkeypatch):
    # None in sys.modules makes the import fail even when the package is installed.
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    with pytest.raises(surf.PlaywrightNotInstalled) as excinfo:
        surf._require_playwright("interactive login")

    message = str(excinfo.value)
    assert "interactive login" in message
    assert "uv sync --extra browser" in message
    assert "playwright install chromium" in message


def test_require_playwright_returns_helpers(monkeypatch):
    fake_sync_api = types.ModuleType("playwright.sync_api")
    fake_parent = types.ModuleType("playwright")
    sentinel = object()
    fake_sync_api.sync_playwright = sentinel
    fake_sync_api.TimeoutError = ValueError
    fake_parent.sync_api = fake_sync_api
    monkeypatch.setitem(sys.modules, "playwright", fake_parent)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)

    sync_playwright, timeout_error = surf._require_playwright("dynamic page rendering")
    assert sync_playwright is sentinel
    assert timeout_error is ValueError


class _FakeConfig:
    def __init__(self, pdf_dir, chrome_executable=""):
        self.pdf_dir = pdf_dir
        self.chrome_executable = chrome_executable

    def has_section(self, name):
        return name == "Browser"

    def get(self, section, key, fallback=None):
        if section == "Browser" and key == "chrome_executable":
            return self.chrome_executable
        return fallback

    def get_path(self, section, key, fallback=None):
        return str(self.pdf_dir)


def test_generate_pdf_falls_back_to_local_chrome(tmp_path, monkeypatch):
    calls = []

    monkeypatch.setattr(
        OutputHandler, "_generate_with_playwright", staticmethod(lambda *a, **k: False)
    )

    def _fake_chrome(html, filepath, config):
        calls.append(filepath)
        with open(filepath, "wb") as handle:
            handle.write(b"%PDF-1.4 fake")
        return True

    monkeypatch.setattr(
        OutputHandler, "_generate_with_headless_chrome", staticmethod(_fake_chrome)
    )

    filepath = OutputHandler.generate_pdf(
        "Title", "# Body", _FakeConfig(tmp_path), output_path=str(tmp_path / "out.pdf")
    )
    assert calls == [filepath]
    assert pathlib.Path(filepath).read_bytes().startswith(b"%PDF")


def test_generate_pdf_reports_failure_when_no_engine_is_available(tmp_path, monkeypatch):
    monkeypatch.setattr(
        OutputHandler, "_generate_with_playwright", staticmethod(lambda *a, **k: False)
    )
    monkeypatch.setattr(
        OutputHandler, "_generate_with_headless_chrome", staticmethod(lambda *a, **k: False)
    )
    # The legacy behaviour is preserved: a path is returned even when rendering fails.
    filepath = OutputHandler.generate_pdf(
        "Title", "# Body", _FakeConfig(tmp_path), output_path=str(tmp_path / "out.pdf")
    )
    assert filepath.endswith("out.pdf")


def test_headless_chrome_pdf_command(tmp_path, monkeypatch):
    binary = tmp_path / "chrome"
    binary.write_text("")
    monkeypatch.setattr(
        OutputHandler, "_find_chrome_executable", staticmethod(lambda config: str(binary))
    )
    commands = []

    class _Result:
        returncode = 0
        stderr = b""

    def _fake_run(command, **kwargs):
        commands.append(command)
        target = [arg for arg in command if str(arg).startswith("--print-to-pdf=")][0]
        pathlib.Path(target.split("=", 1)[1]).write_bytes(b"%PDF-1.4 fake")
        return _Result()

    monkeypatch.setattr(surf.subprocess, "run", _fake_run)

    output = tmp_path / "out.pdf"
    assert OutputHandler._generate_with_headless_chrome("<html></html>", str(output), _FakeConfig(tmp_path))
    assert commands
    command = commands[0]
    assert command[1] == "--headless=new"
    assert any(arg.startswith("--print-to-pdf=") for arg in command)
    assert command[-1].startswith("file://")


def test_headless_chrome_pdf_retries_with_legacy_flag(tmp_path, monkeypatch):
    binary = tmp_path / "chrome"
    binary.write_text("")
    monkeypatch.setattr(
        OutputHandler, "_find_chrome_executable", staticmethod(lambda config: str(binary))
    )
    commands = []

    class _Result:
        stderr = b"unknown flag"

        def __init__(self, returncode):
            self.returncode = returncode

    def _fake_run(command, **kwargs):
        commands.append(command)
        if command[1] == "--headless=new":
            return _Result(1)
        target = [arg for arg in command if str(arg).startswith("--print-to-pdf=")][0]
        pathlib.Path(target.split("=", 1)[1]).write_bytes(b"%PDF-1.4 fake")
        return _Result(0)

    monkeypatch.setattr(surf.subprocess, "run", _fake_run)

    output = tmp_path / "out.pdf"
    assert OutputHandler._generate_with_headless_chrome("<html></html>", str(output), _FakeConfig(tmp_path))
    assert [command[1] for command in commands] == ["--headless=new", "--headless"]


def test_headless_chrome_without_binary(monkeypatch):
    monkeypatch.setattr(
        OutputHandler, "_find_chrome_executable", staticmethod(lambda config: None)
    )
    assert not OutputHandler._generate_with_headless_chrome(
        "<html></html>", "/tmp/surf-should-not-exist.pdf", _FakeConfig("/tmp")
    )


def test_find_chrome_executable_prefers_configured_path(tmp_path):
    binary = tmp_path / "chrome"
    binary.write_text("")
    assert OutputHandler._find_chrome_executable(_FakeConfig(tmp_path, str(binary))) == str(binary)
