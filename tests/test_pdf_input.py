"""PDF URL handling: keep the original file for -p, extract text otherwise."""

import io
import json
import pathlib
import sys

import pytest
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

import surf
from surf import (
    Fetcher,
    OutputHandler,
    PdfDownload,
    PdfHandler,
    _extract_direct_markdown_payload,
    _process_fetched_content,
    _read_local_file,
)


class _FakeConfig:
    def __init__(self, base=None):
        self.base = pathlib.Path(base or ".")

    def get(self, section, key, fallback=None):
        return fallback

    def has_section(self, section):
        return False

    def get_path(self, section, key, fallback=None):
        return str(self.base)


class _FakeResponse:
    def __init__(self, content, content_type="application/pdf", filename=None):
        self.status_code = 200
        self.headers = {"Content-Type": content_type}
        if filename:
            self.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        self.content = content
        self.text = content.decode("latin-1", errors="replace")
        self.encoding = None
        self.apparent_encoding = "utf-8"

    def raise_for_status(self):
        return None

    def json(self):
        return json.loads(self.text)


def _make_pdf(lines, title="My PDF Title"):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )

    commands = [b"BT /F1 12 Tf 72 720 Td"]
    for index, line in enumerate(lines):
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if index:
            commands.append(b"0 -20 Td")
        commands.append(f"({escaped}) Tj".encode("latin-1"))
    commands.append(b"ET")
    content = DecodedStreamObject()
    content.set_data(b" ".join(commands))
    page[NameObject("/Contents")] = writer._add_object(content)

    if title:
        writer.add_metadata({"/Title": title})
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_pdf_detection_uses_content_type_and_magic_bytes():
    pdf_bytes = _make_pdf(["Hello PDF."])
    assert PdfHandler.is_pdf_response(_FakeResponse(pdf_bytes))
    assert PdfHandler.is_pdf_response(_FakeResponse(pdf_bytes, content_type="application/octet-stream"))
    assert not PdfHandler.is_pdf_response(_FakeResponse(b"<html>not a pdf</html>", content_type="text/html"))


def test_extract_pdf_text_and_title():
    pdf_bytes = _make_pdf(["Hello PDF World.", "Second line here."], title="A Paper")

    extracted = PdfHandler.extract(pdf_bytes)

    assert extracted["title"] == "A Paper"
    assert extracted["pages"] == 1
    assert "Hello PDF World." in extracted["markdown"]
    assert "Second line here." in extracted["markdown"]


def test_pdf_response_builds_direct_markdown_payload():
    response = _FakeResponse(_make_pdf(["Extracted body text."]), filename="paper.pdf")

    html = PdfHandler.process_response(response, "https://example.com/files/paper.pdf")
    payload = _extract_direct_markdown_payload(html)

    assert payload is not None
    assert payload["is_pdf"] is True
    assert payload["site_name"] == "pdf"
    assert "Extracted body text." in payload["markdown"]


def test_process_fetched_content_marks_pdf_sources():
    response = _FakeResponse(_make_pdf(["Body text from the PDF."]), filename="paper.pdf")
    html = PdfHandler.process_response(response, "https://example.com/files/paper.pdf")

    processed = _process_fetched_content(
        html,
        "https://example.com/files/paper.pdf",
        _FakeConfig(),
        lang_mode="raw",
    )

    assert processed["is_pdf"] is True
    assert processed["source_url"] == "https://example.com/files/paper.pdf"
    assert "Body text from the PDF." in processed["markdown"]


def test_fetcher_returns_raw_pdf_bytes_for_download_mode(monkeypatch):
    pdf_bytes = _make_pdf(["Downloaded original."])
    response = _FakeResponse(pdf_bytes, filename="original.pdf")
    monkeypatch.setattr(surf, "_requests_get_with_system_trust_interruptibly", lambda *a, **k: response)
    monkeypatch.setattr(
        Fetcher,
        "_get_proxies",
        staticmethod(lambda *args, **kwargs: (None, None)),
    )
    monkeypatch.setattr(
        Fetcher,
        "fetch_with_browser",
        staticmethod(lambda *args, **kwargs: pytest.fail("PDF should not need a browser")),
    )

    result = Fetcher.fetch("https://example.com/paper.pdf", _FakeConfig(), pdf_mode="download")

    assert isinstance(result, PdfDownload)
    assert result.content == pdf_bytes
    assert result.filename == "original.pdf"


def test_fetcher_extracts_text_by_default(monkeypatch):
    pdf_bytes = _make_pdf(["Text fetched from a PDF URL."])
    response = _FakeResponse(pdf_bytes, filename="paper.pdf")
    monkeypatch.setattr(surf, "_requests_get_with_system_trust_interruptibly", lambda *a, **k: response)
    monkeypatch.setattr(
        Fetcher,
        "_get_proxies",
        staticmethod(lambda *args, **kwargs: (None, None)),
    )
    monkeypatch.setattr(
        Fetcher,
        "fetch_with_browser",
        staticmethod(lambda *args, **kwargs: pytest.fail("PDF should not need a browser")),
    )

    html = Fetcher.fetch("https://example.com/paper.pdf", _FakeConfig())

    payload = _extract_direct_markdown_payload(html)
    assert payload is not None
    assert payload["is_pdf"] is True
    assert "Text fetched from a PDF URL." in payload["markdown"]


def test_save_pdf_download_writes_original_bytes(tmp_path):
    pdf_bytes = _make_pdf(["Saved original bytes."])
    download = PdfDownload(pdf_bytes, "https://example.com/paper.pdf", filename="paper.pdf")

    saved = OutputHandler.save_pdf_download(
        download,
        _FakeConfig(tmp_path),
        output_path=str(tmp_path / "custom.pdf"),
    )

    assert pathlib.Path(saved).read_bytes() == pdf_bytes


def test_save_pdf_download_uses_default_pdf_dir_and_prefix(tmp_path):
    pdf_bytes = _make_pdf(["Saved with prefix."])
    download = PdfDownload(pdf_bytes, "https://example.com/paper.pdf", filename="paper.pdf")

    saved = OutputHandler.save_pdf_download(
        download,
        _FakeConfig(tmp_path),
        filename_prefix="[query]",
    )

    assert pathlib.Path(saved).name == "[query]paper.pdf"
    assert pathlib.Path(saved).parent == tmp_path
    assert pathlib.Path(saved).read_bytes() == pdf_bytes


def test_read_local_pdf_extracts_text(tmp_path):
    pdf_path = tmp_path / "local.pdf"
    pdf_path.write_bytes(_make_pdf(["Local PDF body."], title="Local Paper"))

    html, display_path = _read_local_file(str(pdf_path))
    payload = _extract_direct_markdown_payload(html)

    assert display_path == str(pdf_path)
    assert payload is not None
    assert payload["is_pdf"] is True
    assert payload["title"] == "Local Paper"
    assert "Local PDF body." in payload["markdown"]


def test_image_only_pdf_reports_clear_no_text_error():
    response = _FakeResponse(_make_pdf([], title="Scanned"), filename="scan.pdf")

    with pytest.raises(surf.PdfTextExtractionError) as excinfo:
        PdfHandler.process_response(response, "https://example.com/scan.pdf")

    assert "OCR is not supported" in str(excinfo.value)


def test_web_save_pdf_result_redownloads_the_original(monkeypatch, tmp_path):
    from surf_web import save_pdf_result

    pdf_bytes = _make_pdf(["Original PDF saved from the Web UI."])

    def fake_fetch(url, **kwargs):
        assert kwargs.get("pdf_mode") == "download"
        return PdfDownload(pdf_bytes, url, filename="paper.pdf")

    monkeypatch.setattr(Fetcher, "fetch", staticmethod(fake_fetch))
    result = {
        "source_is_pdf": True,
        "markdown": "extracted text",
        "metadata": {"source_url": "https://example.com/paper.pdf"},
    }

    saved = save_pdf_result(
        "Paper",
        result,
        _FakeConfig(tmp_path),
        str(tmp_path / "web.pdf"),
    )

    assert pathlib.Path(saved).read_bytes() == pdf_bytes


def _install_wayback_pdf(monkeypatch, pdf_bytes, original_url):
    calls = []
    snapshot = "http://web.archive.org/web/20230524231516/https://example.com/paper.pdf"

    def fake_get(url, **kwargs):
        calls.append(url)
        if url == original_url:
            raise requests.exceptions.ConnectionError(
                "NameResolutionError: Failed to resolve 'example.com'"
            )
        if url == "https://archive.org/wayback/available":
            payload = {
                "archived_snapshots": {
                    "closest": {"status": "200", "available": True, "url": snapshot}
                }
            }
            return _FakeResponse(json.dumps(payload).encode(), content_type="application/json")
        if url == "http://web.archive.org/web/20230524231516id_/https://example.com/paper.pdf":
            return _FakeResponse(pdf_bytes, filename="paper.pdf")
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(surf, "_requests_get_with_system_trust_interruptibly", fake_get)
    monkeypatch.setattr(
        Fetcher,
        "_get_proxies",
        staticmethod(lambda *args, **kwargs: (None, None)),
    )
    monkeypatch.setattr(
        Fetcher,
        "fetch_with_browser",
        staticmethod(lambda *args, **kwargs: pytest.fail("Wayback fallback should avoid the browser")),
    )
    return calls


def test_dead_pdf_url_falls_back_to_wayback(monkeypatch):
    original_url = "http://example.com/paper.pdf"
    pdf_bytes = _make_pdf(["Archived PDF text."], title="Archived Paper")
    calls = _install_wayback_pdf(monkeypatch, pdf_bytes, original_url)

    html = Fetcher.fetch(original_url, _FakeConfig())
    payload = _extract_direct_markdown_payload(html)

    assert payload is not None
    assert payload["is_pdf"] is True
    assert payload["source_url"] == original_url
    assert payload["archive_url"].endswith("20230524231516id_/https://example.com/paper.pdf")
    assert "Archived PDF text." in payload["markdown"]
    assert calls[0] == original_url


def test_dead_pdf_url_download_mode_keeps_archived_bytes(monkeypatch):
    original_url = "http://example.com/paper.pdf"
    pdf_bytes = _make_pdf(["Original archived bytes."])
    _install_wayback_pdf(monkeypatch, pdf_bytes, original_url)

    result = Fetcher.fetch(original_url, _FakeConfig(), pdf_mode="download")

    assert isinstance(result, PdfDownload)
    assert result.content == pdf_bytes
    assert result.archive_url.endswith("20230524231516id_/https://example.com/paper.pdf")
