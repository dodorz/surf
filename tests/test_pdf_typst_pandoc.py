"""Typst and pandoc PDF engines.

Both keep the browser out of the picture: Surf converts its Markdown-derived
HTML into Typst markup (or hands the HTML to pandoc) and lets the external
binary render the PDF.  The binary paths use fake executables so the tests need
neither typst nor pandoc installed.
"""
import os
import pathlib
import shutil
import sys
import types

import pytest

import surf
from surf import OutputHandler


pytestmark = pytest.mark.skipif(os.name == "nt", reason="fake binaries are POSIX shell scripts")


class _Config:
    def __init__(self, base, **output):
        self.base = pathlib.Path(base)
        self.output = output

    def has_section(self, name):
        return name in {"Output", "Browser"}

    def get(self, section, key, fallback=None):
        if section == "Output" and key in self.output:
            return self.output[key]
        return fallback

    def get_path(self, section, key, fallback=None):
        return str(self.base)


def _write_script(path, body):
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return str(path)


SAMPLE_HTML = """<html><body>
<h1>Title</h1>
<p>Hello <strong>bold</strong> and <em>italic</em> with <code>inline code</code>
and a <a href="https://example.com">link</a>.</p>
<ul><li>one</li><li>two<ul><li>nested</li></ul></li></ul>
<ol><li>first</li><li>second</li></ol>
<pre><code class="language-python">print(1)
</code></pre>
<table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>
<blockquote><p>quoted</p></blockquote>
<hr>
<h2>中文标题</h2>
<p>中文正文。</p>
</body></html>"""


# ------------------------------------------------------------- html -> typst


def test_html_to_typst_headings_paragraphs_and_emphasis():
    typst_source = OutputHandler._html_to_typst(SAMPLE_HTML)

    assert '#set page(paper: "a4"' in typst_source
    assert "= Title" in typst_source
    assert "== 中文标题" in typst_source
    assert "*bold*" in typst_source
    assert "_italic_" in typst_source
    assert "`inline code`" in typst_source
    assert '#link("https://example.com")[link]' in typst_source


def test_html_to_typst_lists_code_tables_and_quotes():
    typst_source = OutputHandler._html_to_typst(SAMPLE_HTML)

    assert "- one" in typst_source
    assert "  - nested" in typst_source
    assert "+ first" in typst_source
    assert "```python\nprint(1)\n```" in typst_source
    assert "#table(" in typst_source
    assert "columns: 2," in typst_source
    assert "table.header([a], [b])," in typst_source
    assert "[1], [2]," in typst_source
    assert "stroke: (left: 2pt + luma(190))" in typst_source
    assert "#line(length: 100%" in typst_source


def test_html_to_typst_escapes_markup_characters():
    html = "<html><body><p>#tag *star* _under_ [brackets] &lt;tag&gt; ~nbsp 100%</p></body></html>"
    typst_source = OutputHandler._html_to_typst(html)

    assert "\\#tag" in typst_source
    assert "\\*star\\*" in typst_source
    assert "\\_under\\_" in typst_source
    assert "\\[brackets\\]" in typst_source
    assert "\\<tag\\>" in typst_source
    assert "\\~nbsp" in typst_source


def test_html_to_typst_inline_code_with_backtick_uses_raw():
    html = "<html><body><p><code>a`b</code></p></body></html>"
    assert '#raw("a`b")' in OutputHandler._html_to_typst(html)


def test_html_to_typst_images_skip_data_uris():
    html = (
        '<html><body><p><img src="image-1.png"><img src="data:image/png;base64,AAAA"></p>'
        "</body></html>"
    )
    typst_source = OutputHandler._html_to_typst(html)
    assert '#image("image-1.png", width: 100%)' in typst_source
    assert "data:" not in typst_source


def test_generated_typst_compiles_when_bindings_are_installed():
    """Optional check: the `typst` Python bindings are not a Surf dependency.

    When they happen to be installed, compile the generated markup for real to
    prove it is valid Typst rather than a string that merely looks right.
    """
    typst = pytest.importorskip("typst")
    data = typst.compile(OutputHandler._html_to_typst(SAMPLE_HTML).encode("utf-8"), format="pdf")
    assert data.startswith(b"%PDF")


# ------------------------------------------------------------------- typst CLI


def test_typst_engine_writes_pdf(tmp_path, monkeypatch):
    args_file = tmp_path / "args.txt"
    binary = _write_script(
        tmp_path / "typst",
        'printf "%s\\n" "$@" > "$SURF_TEST_ARGS"\n'
        'for last; do :; done\n'
        "printf '%%PDF-1.7 typst' > \"$last\"\n",
    )
    monkeypatch.setenv("SURF_TEST_ARGS", str(args_file))

    output = tmp_path / "out.pdf"
    config = _Config(tmp_path, typst_executable=binary)
    assert OutputHandler._generate_with_typst(SAMPLE_HTML, str(output), config)
    assert output.read_bytes().startswith(b"%PDF")

    recorded = args_file.read_text(encoding="utf-8").splitlines()
    assert recorded[0] == "compile"
    assert "--root" in recorded
    assert recorded[-2].endswith("document.typ")
    assert recorded[-1].endswith("document.pdf")


def test_typst_engine_reports_compilation_errors(tmp_path, monkeypatch):
    binary = _write_script(tmp_path / "typst", 'echo "error: unknown variable" >&2\nexit 1\n')
    output = tmp_path / "out.pdf"
    config = _Config(tmp_path, typst_executable=binary)

    assert not OutputHandler._generate_with_typst(SAMPLE_HTML, str(output), config)
    assert not output.exists()


def test_typst_engine_without_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.delenv("SURF_TYPST_EXECUTABLE", raising=False)
    assert not OutputHandler._generate_with_typst(
        SAMPLE_HTML, str(tmp_path / "out.pdf"), _Config(tmp_path)
    )


def test_typst_font_paths(tmp_path, monkeypatch):
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    config = _Config(tmp_path, typst_fonts=f"{font_dir},/does/not/exist")
    assert OutputHandler._typst_font_paths(config) == [str(font_dir)]
    assert OutputHandler._typst_font_paths(_Config(tmp_path)) == []


# ------------------------------------------------------------------ pandoc CLI


def _pandoc_script(tmp_path):
    return _write_script(
        tmp_path / "pandoc",
        'printf "%s\\n" "$@" > "$SURF_TEST_ARGS"\n'
        'out=""\nprev=""\n'
        'for arg in "$@"; do\n'
        '  if [ "$prev" = "--output" ]; then out="$arg"; fi\n'
        "  prev=\"$arg\"\n"
        "done\n"
        'printf "%%PDF-1.7 pandoc" > "$out"\n',
    )


def test_pandoc_engine_picks_an_installed_pdf_engine(tmp_path, monkeypatch):
    args_file = tmp_path / "args.txt"
    binary = _pandoc_script(tmp_path)
    monkeypatch.setenv("SURF_TEST_ARGS", str(args_file))
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/" + name if name == "typst" else None
    )

    output = tmp_path / "out.pdf"
    config = _Config(tmp_path, pandoc_executable=binary)
    assert OutputHandler._generate_with_pandoc(SAMPLE_HTML, str(output), config)

    recorded = args_file.read_text(encoding="utf-8").splitlines()
    assert "--from=html" in recorded
    assert "--to=pdf" in recorded
    assert "--pdf-engine=typst" in recorded
    assert any(arg.startswith("--resource-path=") for arg in recorded)
    assert recorded[-1].endswith("document.html")


def test_pandoc_engine_respects_configured_engine_and_args(tmp_path, monkeypatch):
    args_file = tmp_path / "args.txt"
    binary = _pandoc_script(tmp_path)
    monkeypatch.setenv("SURF_TEST_ARGS", str(args_file))

    config = _Config(
        tmp_path,
        pandoc_executable=binary,
        pandoc_pdf_engine="xelatex",
        pandoc_args='--toc -V CJKmainfont="Noto Serif CJK SC"',
    )
    assert OutputHandler._generate_with_pandoc(SAMPLE_HTML, str(tmp_path / "out.pdf"), config)

    recorded = args_file.read_text(encoding="utf-8").splitlines()
    assert "--pdf-engine=xelatex" in recorded
    assert "--toc" in recorded
    assert "CJKmainfont=Noto Serif CJK SC" in recorded


def test_pandoc_engine_without_any_pdf_engine(tmp_path, monkeypatch):
    binary = _pandoc_script(tmp_path)
    monkeypatch.setenv("SURF_TEST_ARGS", str(tmp_path / "args.txt"))
    monkeypatch.setattr(shutil, "which", lambda name: None)

    assert not OutputHandler._generate_with_pandoc(
        SAMPLE_HTML, str(tmp_path / "out.pdf"), _Config(tmp_path, pandoc_executable=binary)
    )


def test_pandoc_engine_reports_failures(tmp_path, monkeypatch):
    binary = _write_script(tmp_path / "pandoc", 'echo "pandoc: oops" >&2\nexit 3\n')
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/typst")

    assert not OutputHandler._generate_with_pandoc(
        SAMPLE_HTML, str(tmp_path / "out.pdf"), _Config(tmp_path, pandoc_executable=binary)
    )


# ------------------------------------------------------------------- plumbing


def test_download_remote_images_writes_files_and_rewrites_sources(tmp_path, monkeypatch):
    class _Response:
        headers = {"Content-Type": "image/png"}
        content = b"\x89PNG-bytes"

        def raise_for_status(self):
            return None

    monkeypatch.setattr(surf.requests, "get", lambda url, **kwargs: _Response())
    monkeypatch.setattr(surf.Fetcher, "_get_proxies", staticmethod(lambda *a, **k: ({}, None)))

    html, saved = OutputHandler._download_remote_images(
        '<html><body><img src="https://example.com/a.png"></body></html>',
        _Config(tmp_path),
        tmp_path,
    )

    assert saved == 1
    assert "image-1.png" in html
    assert (tmp_path / "image-1.png").read_bytes() == b"\x89PNG-bytes"
    assert "https://example.com/a.png" not in html


def test_download_remote_images_noop_without_images(tmp_path):
    html = "<html><body><p>none</p></body></html>"
    assert OutputHandler._download_remote_images(html, _Config(tmp_path), tmp_path) == (html, 0)


def test_resolve_tool_precedence(tmp_path, monkeypatch):
    config_binary = _write_script(tmp_path / "from-config", "exit 0\n")
    env_binary = _write_script(tmp_path / "from-env", "exit 0\n")

    def resolve(config):
        return OutputHandler._resolve_tool(
            config, "typst_executable", "SURF_TYPST_EXECUTABLE", "typst"
        )

    monkeypatch.setenv("SURF_TYPST_EXECUTABLE", env_binary)
    assert resolve(_Config(tmp_path, typst_executable=config_binary)) == env_binary

    monkeypatch.delenv("SURF_TYPST_EXECUTABLE")
    assert resolve(_Config(tmp_path, typst_executable=config_binary)) == config_binary

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
    assert resolve(_Config(tmp_path)) == "/usr/bin/typst"


def test_pdf_engine_chain_knows_the_new_engines():
    assert OutputHandler._pdf_engine_chain(_Config(".", pdf_engine="typst")) == ["typst"]
    assert OutputHandler._pdf_engine_chain(_Config(".", pdf_engine="pandoc, typst")) == [
        "pandoc",
        "typst",
    ]
    assert "typst" in OutputHandler._DEFAULT_PDF_ENGINE_CHAIN
    assert "pandoc" in OutputHandler._DEFAULT_PDF_ENGINE_CHAIN


def test_generate_pdf_reaches_typst_after_browser_engines(tmp_path, monkeypatch):
    calls = []

    def _renderer(name, outcome):
        def _inner(full_html, md_content, filepath, config):
            calls.append(name)
            if outcome:
                pathlib.Path(filepath).write_bytes(b"%PDF-1.7 " + name.encode())
            return outcome

        return _inner

    monkeypatch.setattr(
        OutputHandler,
        "_pdf_renderers",
        staticmethod(
            lambda: {
                name: _renderer(name, name == "typst")
                for name in ("playwright", "chrome", "weasyprint", "typst", "pandoc")
            }
        ),
    )
    monkeypatch.setattr(OutputHandler, "_playwright_available", staticmethod(lambda: True))

    OutputHandler.generate_pdf(
        "Title", "# Body", _Config(tmp_path), output_path=str(tmp_path / "out.pdf")
    )
    assert calls == ["playwright", "chrome", "weasyprint", "typst"]
