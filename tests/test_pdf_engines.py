"""PDF engine chain: browser engines first, WeasyPrint as a browser-less option.

``generate_pdf`` renders a Markdown-derived HTML document, so a browser is not
required for fidelity.  These tests pin the engine selection ([Output]
pdf_engine), the fallback order, the WeasyPrint renderer and the remote-image
inlining that makes browser-less rendering work behind a proxy.
"""
import pathlib
import sys
import types

import pytest

import surf
from surf import OutputHandler


class _Config:
    def __init__(self, base, engine="auto"):
        self.base = pathlib.Path(base)
        self.engine = engine

    def has_section(self, name):
        return name in {"Output", "Browser"}

    def get(self, section, key, fallback=None):
        if section == "Output" and key == "pdf_engine":
            return self.engine
        return fallback

    def get_path(self, section, key, fallback=None):
        return str(self.base)


class _FakeWeasyPrint(types.ModuleType):
    def __init__(self):
        super().__init__("weasyprint")
        self.rendered = []

    def HTML(self, **kwargs):  # noqa: N802 - mirrors the real API
        module = self

        class _Document:
            def write_pdf(self, filepath):
                module.rendered.append(kwargs)
                with open(filepath, "wb") as handle:
                    handle.write(b"%PDF-1.7 weasyprint")

        return _Document()


def _install_fake_weasyprint(monkeypatch):
    module = _FakeWeasyPrint()
    monkeypatch.setitem(sys.modules, "weasyprint", module)
    return module


def _record_engines(monkeypatch, results):
    calls = []

    def _make(name):
        def _renderer(full_html, md_content, filepath, config):
            calls.append(name)
            outcome = results.get(name, False)
            if outcome:
                pathlib.Path(filepath).write_bytes(b"%PDF-1.4 " + name.encode())
            return outcome

        return _renderer

    monkeypatch.setattr(
        OutputHandler,
        "_pdf_renderers",
        staticmethod(lambda: {name: _make(name) for name in ("playwright", "chrome", "weasyprint")}),
    )
    return calls


# --------------------------------------------------------------- engine chain


def test_pdf_engine_chain_defaults_to_auto_chain():
    assert OutputHandler._pdf_engine_chain(_Config(".")) == ["playwright", "chrome", "weasyprint"]
    assert OutputHandler._pdf_engine_chain(_Config(".", "auto")) == [
        "playwright",
        "chrome",
        "weasyprint",
    ]
    assert OutputHandler._pdf_engine_chain(_Config(".", "")) == [
        "playwright",
        "chrome",
        "weasyprint",
    ]


def test_pdf_engine_chain_parses_explicit_values():
    assert OutputHandler._pdf_engine_chain(_Config(".", "weasyprint")) == ["weasyprint"]
    assert OutputHandler._pdf_engine_chain(_Config(".", "chromium")) == ["chrome"]
    assert OutputHandler._pdf_engine_chain(_Config(".", "WeasyPrint, chrome")) == [
        "weasyprint",
        "chrome",
    ]
    assert OutputHandler._pdf_engine_chain(_Config(".", "chrome,chrome")) == ["chrome"]


def test_pdf_engine_chain_ignores_unknown_names():
    assert OutputHandler._pdf_engine_chain(_Config(".", "prince")) == [
        "playwright",
        "chrome",
        "weasyprint",
    ]
    assert OutputHandler._pdf_engine_chain(_Config(".", "weasyprint,prince")) == ["weasyprint"]


def test_pdf_engine_chain_without_output_section():
    class _NoSections:
        pass

    assert OutputHandler._pdf_engine_chain(_NoSections()) == [
        "playwright",
        "chrome",
        "weasyprint",
    ]


# ------------------------------------------------------------ engine dispatch


def test_auto_chain_falls_back_in_order(tmp_path, monkeypatch):
    calls = _record_engines(monkeypatch, {"weasyprint": True})
    monkeypatch.setattr(OutputHandler, "_playwright_available", staticmethod(lambda: True))

    filepath = OutputHandler.generate_pdf(
        "Title", "# Body", _Config(tmp_path), output_path=str(tmp_path / "out.pdf")
    )

    assert calls == ["playwright", "chrome", "weasyprint"]
    assert pathlib.Path(filepath).read_bytes().startswith(b"%PDF")


def test_auto_chain_skips_missing_playwright(tmp_path, monkeypatch):
    calls = _record_engines(monkeypatch, {"chrome": True})
    monkeypatch.setattr(OutputHandler, "_playwright_available", staticmethod(lambda: False))

    OutputHandler.generate_pdf(
        "Title", "# Body", _Config(tmp_path), output_path=str(tmp_path / "out.pdf")
    )

    assert calls == ["chrome"]


def test_explicit_engine_does_not_fall_back(tmp_path, monkeypatch):
    calls = _record_engines(monkeypatch, {})
    monkeypatch.setattr(OutputHandler, "_playwright_available", staticmethod(lambda: True))

    OutputHandler.generate_pdf(
        "Title", "# Body", _Config(tmp_path, "weasyprint"), output_path=str(tmp_path / "out.pdf")
    )

    assert calls == ["weasyprint"]


def test_explicit_chain_honours_the_configured_order(tmp_path, monkeypatch):
    calls = _record_engines(monkeypatch, {"chrome": True})

    OutputHandler.generate_pdf(
        "Title",
        "# Body",
        _Config(tmp_path, "weasyprint,chrome,playwright"),
        output_path=str(tmp_path / "out.pdf"),
    )

    # weasyprint is tried first, then chrome succeeds; playwright is never reached.
    assert calls == ["weasyprint", "chrome"]


# ------------------------------------------------------------ Markdown render


def test_generate_pdf_keeps_tables_and_fenced_code(tmp_path, monkeypatch):
    captured = {}

    def _fake_renderer(full_html, filepath, config):
        captured["html"] = full_html
        pathlib.Path(filepath).write_bytes(b"%PDF-1.4")
        return True

    monkeypatch.setattr(OutputHandler, "_generate_with_weasyprint", staticmethod(_fake_renderer))

    markdown_text = (
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "```python\nprint(1)\n```\n\n"
        "- one\n- two\n"
    )
    OutputHandler.generate_pdf(
        "Title", markdown_text, _Config(tmp_path, "weasyprint"), output_path=str(tmp_path / "t.pdf")
    )

    html = captured["html"]
    assert "<table>" in html
    assert "<th>a</th>" in html
    assert "<code" in html and "print(1)" in html
    assert "@page" in html
    assert "<li>one</li>" in html


# ---------------------------------------------------------------- WeasyPrint


def test_weasyprint_engine_is_optional(monkeypatch):
    monkeypatch.setitem(sys.modules, "weasyprint", None)
    assert OutputHandler._generate_with_weasyprint("<html></html>", "/tmp/x.pdf", _Config("/tmp")) is False


def test_weasyprint_engine_renders_and_inlines_images(tmp_path, monkeypatch):
    module = _install_fake_weasyprint(monkeypatch)

    class _Response:
        status_code = 200
        headers = {"Content-Type": "image/png"}
        content = b"\x89PNG-fake"

        def raise_for_status(self):
            return None

    requested = []

    def _fake_get(url, **kwargs):
        requested.append(url)
        return _Response()

    monkeypatch.setattr(surf.requests, "get", _fake_get)
    monkeypatch.setattr(surf.Fetcher, "_get_proxies", staticmethod(lambda *a, **k: ({}, None)))

    output = tmp_path / "out.pdf"
    html = '<html><body><img src="https://example.com/a.png"></body></html>'
    assert OutputHandler._generate_with_weasyprint(html, str(output), _Config(tmp_path))
    assert output.read_bytes().startswith(b"%PDF")

    assert requested == ["https://example.com/a.png"]
    rendered_html = module.rendered[0]["string"]
    assert "data:image/png;base64," in rendered_html
    assert "https://example.com/a.png" not in rendered_html


def test_weasyprint_reports_usable_errors(tmp_path, monkeypatch):
    class _Broken(types.ModuleType):
        def __init__(self):
            super().__init__("weasyprint")

        def HTML(self, **kwargs):  # noqa: N802
            raise RuntimeError("layout exploded")

    monkeypatch.setitem(sys.modules, "weasyprint", _Broken())
    assert not OutputHandler._generate_with_weasyprint(
        "<html><body>text</body></html>", str(tmp_path / "out.pdf"), _Config(tmp_path)
    )


# -------------------------------------------------------------- image inlining


def test_inline_remote_images_rewrites_only_remote_sources(tmp_path, monkeypatch):
    class _Response:
        headers = {"Content-Type": "image/jpeg; charset=binary"}
        content = b"jpeg-bytes"

        def raise_for_status(self):
            return None

    monkeypatch.setattr(surf.requests, "get", lambda url, **kwargs: _Response())
    monkeypatch.setattr(surf.Fetcher, "_get_proxies", staticmethod(lambda *a, **k: ({}, None)))

    html = (
        "<html><body>"
        '<img src="https://example.com/a.jpg">'
        '<img src="/local/b.png">'
        '<img src="data:image/gif;base64,R0lGOD">'
        "</body></html>"
    )
    result = OutputHandler._inline_remote_images(html, _Config(tmp_path))

    assert "data:image/jpeg;base64," in result
    assert "https://example.com/a.jpg" not in result
    assert "/local/b.png" in result
    assert "data:image/gif;base64,R0lGOD" in result


def test_inline_remote_images_keeps_url_when_download_fails():
    def _boom(url, **kwargs):
        raise RuntimeError("network down")

    original_get = surf.requests.get
    surf.requests.get = _boom
    try:
        html = '<html><body><img src="https://example.com/a.png"></body></html>'
        assert OutputHandler._inline_remote_images(html, _Config(".")) == html
    finally:
        surf.requests.get = original_get


def test_inline_remote_images_noop_without_images():
    html = "<html><body><p>no images</p></body></html>"
    assert OutputHandler._inline_remote_images(html, _Config(".")) == html
