#!/usr/bin/env -S uv run
import argparse
import base64
import configparser
import getpass
import os
import sys
import logging
import json
import shutil
import subprocess
import tempfile
import io
import locale
import ssl
import socket
import requests  # type: ignore
import threading
import queue
from requests.utils import get_encoding_from_headers
from readability import Document
import markdownify
from langdetect import detect
import warnings
import asyncio
import edge_tts
from playsound import playsound
from datetime import datetime
from dateutil import parser as date_parser  # type: ignore
from bs4 import BeautifulSoup, UnicodeDammit
from html import escape, unescape
import trafilatura
import re
import unicodedata
import signal
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlencode, urljoin, urlparse, urlunparse


def _build_direct_markdown_payload(
    markdown_text,
    title,
    source_url,
    site_name="markdown",
    base_url=None,
):
    """Wrap raw markdown so downstream code can detect and bypass HTML extraction."""
    safe_title = escape(title or "Untitled")
    safe_source_url = escape(source_url or "")
    safe_site_name = escape(site_name or "markdown")
    safe_base_url = escape(base_url or source_url or "")
    safe_markdown = escape(markdown_text or "")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{safe_title}</title>
<meta name="source-url" content="{safe_source_url}">
<meta name="content-base-url" content="{safe_base_url}">
<meta name="surf-source-site" content="{safe_site_name}">
<meta name="surf-direct-markdown" content="true">
</head>
<body>
<pre id="surf-direct-markdown" hidden>{safe_markdown}</pre>
</body>
</html>"""


def _extract_direct_markdown_payload(html_content):
    """Return embedded markdown payload when the fetch stage already resolved raw markdown."""
    if not html_content:
        return None
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        marker = soup.find("meta", attrs={"name": "surf-direct-markdown"})
        if not marker or (marker.get("content") or "").strip().lower() != "true":
            return None
        payload_node = soup.find(id="surf-direct-markdown")
        if payload_node is None:
            return None
        title_tag = soup.find("title")
        source_meta = soup.find("meta", attrs={"name": "source-url"})
        base_meta = soup.find("meta", attrs={"name": "content-base-url"})
        site_meta = soup.find("meta", attrs={"name": "surf-source-site"})
        markdown_text = payload_node.get_text()
        return {
            "markdown": markdown_text or "",
            "title": title_tag.get_text(strip=True) if title_tag else "Untitled",
            "source_url": source_meta.get("content") if source_meta else None,
            "base_url": base_meta.get("content") if base_meta else None,
            "site_name": site_meta.get("content") if site_meta else None,
            "soup": soup,
        }
    except Exception:
        return None


def _render_markdown_to_html(markdown_text):
    """Render markdown for HTML/PDF output when the fetch step already returned markdown."""
    try:
        import markdown  # type: ignore

        body = markdown.markdown(markdown_text or "")
    except Exception:
        body = f"<pre>{escape(markdown_text or '')}</pre>"
    return f"<article>{body}</article>"


def _strip_v2ex_leading_metadata(markdown_text):
    if not markdown_text:
        return markdown_text or ""

    lines = markdown_text.splitlines()
    index = 0
    metadata_patterns = (
        r"^Author:\s+.+$",
        r"^Node:\s+.+$",
        r"^Published:\s+.+$",
        r"^Source:\s+https?://.+$",
    )

    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if any(re.match(pattern, line) for pattern in metadata_patterns):
            index += 1
            continue
        break

    if index >= len(lines):
        return ""

    return "\n".join(lines[index:]).lstrip("\r\n")


_EMBEDDED_HTML_TAGS = (
    "a",
    "abbr",
    "article",
    "aside",
    "audio",
    "b",
    "blockquote",
    "br",
    "caption",
    "cite",
    "code",
    "dd",
    "del",
    "details",
    "div",
    "dl",
    "dt",
    "em",
    "figcaption",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "ins",
    "kbd",
    "li",
    "mark",
    "ol",
    "p",
    "picture",
    "pre",
    "q",
    "s",
    "section",
    "source",
    "span",
    "strong",
    "svg",
    "sub",
    "summary",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
    "video",
)
_EMBEDDED_HTML_TAG_PATTERN = "|".join(sorted(_EMBEDDED_HTML_TAGS, key=len, reverse=True))
_EMBEDDED_HTML_PAIR_RE = re.compile(
    rf"<(?P<tag>{_EMBEDDED_HTML_TAG_PATTERN})\b[^>]*>.*?</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)
_EMBEDDED_HTML_VOID_RE = re.compile(
    rf"<(?:br|hr|img|source)\b[^>]*(?:/?>)",
    re.IGNORECASE,
)
_MARKDOWN_FENCE_RE = re.compile(r"^\s*(```+|~~~+)")


_SVG_ATTR_CASE_MAP = {
    "viewbox": "viewBox",
    "preserveaspectratio": "preserveAspectRatio",
    "gradientunits": "gradientUnits",
    "gradienttransform": "gradientTransform",
    "patternunits": "patternUnits",
    "patterncontentunits": "patternContentUnits",
    "markerheight": "markerHeight",
    "markerwidth": "markerWidth",
    "refx": "refX",
    "refy": "refY",
}

_SVG_BOARD_STYLE = """
text{font-family:Arial,"Noto Sans",sans-serif}
.t{fill:#444441;font-size:14px;font-weight:400}
.ts{fill:#5f5e5a;font-size:12px;font-weight:400}
.th{fill:#444441;font-size:14px;font-weight:500}
.arr{stroke:#77736b;stroke-width:1.5px}
.leader{stroke:#77736b;stroke-width:.5px;stroke-dasharray:3 3}
.box rect{fill:#f7f6f2;stroke:#d8d4ca}
.c-purple rect,.c-purple circle,.c-purple ellipse{fill:#eeedfe;stroke:#534ab7;stroke-width:.5px}
.c-purple .th,.c-purple .t{fill:#3c3489}.c-purple .ts{fill:#534ab7}
.c-teal rect,.c-teal circle,.c-teal ellipse{fill:#e1f5ee;stroke:#0f6e56;stroke-width:.5px}
.c-teal .th,.c-teal .t{fill:#085041}.c-teal .ts{fill:#0f6e56}
.c-coral rect,.c-coral circle,.c-coral ellipse{fill:#faece7;stroke:#993c1d;stroke-width:.5px}
.c-coral .th,.c-coral .t{fill:#712b13}.c-coral .ts{fill:#993c1d}
.c-pink rect,.c-pink circle,.c-pink ellipse{fill:#faeaf0;stroke:#993556;stroke-width:.5px}
.c-pink .th,.c-pink .t{fill:#72243e}.c-pink .ts{fill:#993556}
.c-gray rect,.c-gray circle,.c-gray ellipse{fill:#f1efe8;stroke:#5f5e5a;stroke-width:.5px}
.c-gray .th,.c-gray .t{fill:#444441}.c-gray .ts{fill:#5f5e5a}
.c-blue rect,.c-blue circle,.c-blue ellipse{fill:#e6f1fb;stroke:#185fa5;stroke-width:.5px}
.c-blue .th,.c-blue .t{fill:#0c447c}.c-blue .ts{fill:#185fa5}
.c-green rect,.c-green circle,.c-green ellipse{fill:#eaf3de;stroke:#3b6d11;stroke-width:.5px}
.c-green .th,.c-green .t{fill:#27500a}.c-green .ts{fill:#3b6d11}
.c-amber rect,.c-amber circle,.c-amber ellipse{fill:#fff3d7;stroke:#9b650d;stroke-width:.5px}
.c-amber .th,.c-amber .t{fill:#714707}.c-amber .ts{fill:#9b650d}
""".strip()


def _tag_classes(tag):
    attrs = getattr(tag, "attrs", None) if tag is not None else None
    if not isinstance(attrs, dict):
        return set()
    classes = attrs.get("class")
    if not classes:
        return set()
    if isinstance(classes, str):
        return set(classes.split())
    return {str(cls) for cls in classes}


def _tag_get(tag, key, default=None):
    attrs = getattr(tag, "attrs", None) if tag is not None else None
    if not isinstance(attrs, dict):
        return default
    return attrs.get(key, default)


def _svg_numeric_size(svg):
    def parse_dimension(value):
        if value is None:
            return None
        match = re.match(r"\s*(\d+(?:\.\d+)?)", str(value))
        return float(match.group(1)) if match else None

    width = parse_dimension(_tag_get(svg, "width"))
    height = parse_dimension(_tag_get(svg, "height"))
    viewbox = _tag_get(svg, "viewBox") or _tag_get(svg, "viewbox")
    if (width is None or height is None) and viewbox:
        numbers = re.findall(r"-?\d+(?:\.\d+)?", str(viewbox))
        if len(numbers) >= 4:
            width = width if width is not None else float(numbers[2])
            height = height if height is not None else float(numbers[3])
    return width, height


def _svg_alt_text(svg):
    for attr in ("aria-label", "title", "alt"):
        value = (_tag_get(svg, attr) or "").strip()
        if value:
            return re.sub(r"\s+", " ", value)

    title = svg.find("title")
    if title:
        value = title.get_text(" ", strip=True)
        if value:
            return re.sub(r"\s+", " ", value)

    text_parts = [
        text.strip()
        for text in svg.find_all(string=True)
        if text.strip() and getattr(text, "parent", None) and text.parent.name not in {"style", "script"}
    ]
    text = " ".join(text_parts)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 120:
        text = text[:117].rstrip() + "..."
    return text or "inline SVG illustration"


def _svg_is_decorative(svg):
    classes = _tag_classes(svg)
    text = svg.get_text(" ", strip=True)
    width, height = _svg_numeric_size(svg)
    if (_tag_get(svg, "aria-hidden") or "").strip().lower() == "true":
        return True
    if "lucide" in classes:
        return True
    if width and height and width <= 32 and height <= 32 and not text:
        return True
    if not text and not svg.find(["path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "image"]):
        return True
    return False


def _svg_is_content_illustration(svg):
    if _svg_is_decorative(svg):
        return False

    parent = svg.find_parent(["figure", "div", "section", "article"])
    parent_classes = _tag_classes(parent)
    svg_classes = _tag_classes(svg)
    if parent and parent.name == "figure":
        return True
    if parent_classes.intersection({"post-media", "post-media--svg-board"}):
        return True
    if svg_classes.intersection({"post-media", "post-media--svg-board"}):
        return True

    width, height = _svg_numeric_size(svg)
    graphic_count = len(svg.find_all(["path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "image"]))
    text_count = len([text for text in svg.find_all(string=True) if text.strip()])
    area = (width or 0) * (height or 0)
    return bool((area >= 10000 or width == 100) and (text_count >= 2 or graphic_count >= 6))


def _normalize_svg_attribute_case(svg):
    for tag in [svg] + list(svg.find_all(True)):
        if not isinstance(getattr(tag, "attrs", None), dict):
            continue
        for lower_name, proper_name in _SVG_ATTR_CASE_MAP.items():
            if lower_name in tag.attrs and proper_name not in tag.attrs:
                tag.attrs[proper_name] = tag.attrs.pop(lower_name)


def _sanitize_svg_for_markdown(svg):
    for unsafe in svg.find_all(["script", "foreignObject", "iframe", "object", "embed"]):
        unsafe.decompose()
    if not isinstance(getattr(svg, "attrs", None), dict):
        svg.attrs = {}
    if not _tag_get(svg, "xmlns"):
        svg["xmlns"] = "http://www.w3.org/2000/svg"
    _normalize_svg_attribute_case(svg)

    parent = svg.find_parent()
    if parent and "post-media--svg-board" in _tag_classes(parent) and not svg.find("style"):
        style_tag = BeautifulSoup("", "html.parser").new_tag("style")
        style_tag.string = _SVG_BOARD_STYLE
        svg.insert(0, style_tag)


def _svg_to_data_uri(svg):
    _sanitize_svg_for_markdown(svg)
    svg_markup = str(svg)
    encoded = base64.b64encode(svg_markup.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _prepare_inline_svgs_for_markdown(html):
    if not html or "<svg" not in html.lower():
        return html

    soup = BeautifulSoup(html, "html.parser")
    changed = False
    for svg in list(soup.find_all("svg")):
        if _svg_is_content_illustration(svg):
            alt_text = _svg_alt_text(svg)
            title = svg.find("title")
            title_text = title.get_text(" ", strip=True) if title else ""
            img = soup.new_tag("img")
            img["src"] = _svg_to_data_uri(svg)
            img["alt"] = alt_text
            if title_text:
                img["title"] = title_text
            svg.replace_with(img)
            changed = True
        elif _svg_is_decorative(svg):
            svg.decompose()
            changed = True

    return str(soup) if changed else html


def _markdownify_embedded_html_fragment(html_fragment):
    html_fragment = _prepare_inline_svgs_for_markdown(html_fragment)
    converted = markdownify.markdownify(html_fragment or "", heading_style="ATX")
    converted = re.sub(r"\n{3,}", "\n\n", converted).strip()
    return converted


def _convert_embedded_html_in_markdown_chunk(markdown_text):
    def replace_pair(match):
        return _markdownify_embedded_html_fragment(match.group(0))

    def replace_void(match):
        return _markdownify_embedded_html_fragment(match.group(0))

    converted = _EMBEDDED_HTML_PAIR_RE.sub(replace_pair, markdown_text)
    converted = _EMBEDDED_HTML_VOID_RE.sub(replace_void, converted)
    return converted


def _convert_embedded_html_in_markdown(markdown_text):
    """Convert embedded HTML fragments in fetched markdown while preserving code fences."""
    if not markdown_text or not re.search(r"<[A-Za-z][A-Za-z0-9:-]*(?:\s|>|/>)", markdown_text):
        return markdown_text or ""

    chunks = []
    current = []
    in_fence = False

    def flush_current(convert_html):
        if not current:
            return
        chunk = "".join(current)
        if convert_html:
            chunk = _convert_embedded_html_in_markdown_chunk(chunk)
        chunks.append(chunk)
        current.clear()

    for line in markdown_text.splitlines(keepends=True):
        if _MARKDOWN_FENCE_RE.match(line):
            if in_fence:
                current.append(line)
                flush_current(False)
                in_fence = False
            else:
                flush_current(True)
                current.append(line)
                in_fence = True
            continue
        current.append(line)

    flush_current(not in_fence)
    return "".join(chunks)


def _translation_was_performed(
    original_text,
    translated_text,
    original_title=None,
    translated_title=None,
    title_was_translated=True,
):
    if (translated_text or "") != (original_text or ""):
        return True
    if title_was_translated and (translated_title or "") != (original_title or ""):
        return True
    return False


def _extract_source_url_from_html(html_blob, default_url):
    """Return the embedded source URL when fetchers resolved or normalized the original URL."""
    if not html_blob:
        return default_url
    try:
        soup = BeautifulSoup(html_blob, "html.parser")
        meta_tag = soup.find("meta", attrs={"name": "source-url"})
        if meta_tag and meta_tag.get("content"):
            return meta_tag["content"]
    except Exception:
        pass
    return default_url


def _process_fetched_content(
    html_content,
    request_url,
    config,
    *,
    site_name=None,
    site_config=None,
    lang_mode="trans",
    ocr_args=None,
    proxy_mode_override=None,
    custom_proxy_override=None,
    llm_provider=None,
):
    """
    Convert fetched HTML into normalized title/HTML/Markdown output.

    This shared post-fetch pipeline keeps CLI and Surf Web behavior aligned when
    they run with the same fetch-related settings.
    """
    if not html_content:
        raise ValueError(f"Failed to fetch usable content from {request_url}")

    source_url = _extract_source_url_from_html(html_content, request_url)
    content_base_url = source_url
    direct_markdown_payload = _extract_direct_markdown_payload(html_content)

    if direct_markdown_payload:
        title = direct_markdown_payload.get("title") or "Untitled"
        md_content = _convert_embedded_html_in_markdown(direct_markdown_payload.get("markdown") or "")
        content_base_url = direct_markdown_payload.get("base_url") or source_url
        cleaned_html = _render_markdown_to_html(md_content)
        logger.info(f"Using direct markdown payload. Title: {title}")
    else:
        title, cleaned_html = ContentProcessor.extract_content(html_content)
        if not title:
            title = "Untitled"
        logger.info(f"Title: {title}")

    if not direct_markdown_payload:
        try:
            cleaned_html = OcrHandler.annotate_html_with_ocr(
                cleaned_html,
                source_url=source_url,
                site_name=site_name,
                site_config=site_config,
                args=ocr_args,
                config=config,
                proxy_mode_override=proxy_mode_override,
                custom_proxy_override=custom_proxy_override,
            )
        except Exception as e:
            logger.warning(f"Image OCR failed and was skipped: {e}")

        md_content = ContentProcessor.to_markdown(cleaned_html)

    social_title = OutputHandler._extract_social_first_sentence_title(html_content, source_url=source_url)
    if social_title:
        title = social_title

    source_site = None
    if direct_markdown_payload:
        source_site = (direct_markdown_payload.get("site_name") or "").strip().lower()
    else:
        parsed_payload = _extract_direct_markdown_payload(html_content)
        if parsed_payload:
            source_site = (parsed_payload.get("site_name") or "").strip().lower()
        elif source_url and re.match(r"^https?://((www|cn)\.)?v2ex\.com/t/\d+", source_url, re.IGNORECASE):
            source_site = "v2ex"

    if source_site == "v2ex":
        md_content = _strip_v2ex_leading_metadata(md_content)

    md_content = (md_content or "").lstrip("\r\n")
    original_md = md_content
    original_title = title
    original_description = OutputHandler._extract_html_meta_description(html_content)
    translated_description = None
    translated_title = None
    translation_performed = False
    skip_title_translation = site_config.get("skip_title_translation", False) if site_config else False

    if lang_mode == "raw":
        logger.info("Language mode set to 'raw'. Skipping translation.")
    else:
        translated_md, translated_title = ContentProcessor.translate_if_needed(
            md_content,
            title=None if skip_title_translation else title,
            target_lang=config.get("Output", "target_language", fallback="zh-cn"),
            config=config,
            llm_provider=llm_provider,
        )
        translation_performed = _translation_was_performed(
            original_md,
            translated_md,
            original_title,
            translated_title,
            title_was_translated=not skip_title_translation,
        )
        if original_description and translated_md != original_md:
            translated_description, _ = ContentProcessor.translate_if_needed(
                original_description,
                title=None,
                target_lang=config.get("Output", "target_language", fallback="zh-cn"),
                config=config,
                llm_provider=llm_provider,
            )

        if skip_title_translation:
            translated_title = original_title

        if lang_mode == "both":
            logger.info("Language mode set to 'both'. Combining original and translation.")
            if translated_md != original_md:
                title = f"{translated_title} ({original_title})"
                md_content = f"{translated_md}\n\n---\n\n### Original Content / 原文内容\n\n{original_md}"
            else:
                title = translated_title
                md_content = translated_md
        else:
            md_content = translated_md
            title = translated_title

    base_url_for_links = content_base_url or source_url or request_url
    if base_url_for_links:
        md_content = OutputHandler._convert_markdown_urls_to_absolute(md_content, base_url_for_links)
        cleaned_html = OutputHandler._convert_urls_to_absolute(cleaned_html, base_url_for_links)

    return {
        "title": title,
        "cleaned_html": cleaned_html,
        "markdown": md_content,
        "raw_markdown": original_md,
        "original_title": original_title,
        "original_description": original_description,
        "translated_description": translated_description,
        "translated_title": translated_title,
        "translation_performed": translation_performed,
        "source_url": source_url,
        "content_base_url": content_base_url,
        "html_content": html_content,
    }


def _get_version():
    """从 pyproject.toml [project] 小节读取版本号"""
    try:
        with open("pyproject.toml", "r", encoding="utf-8") as f:
            content = f.read()
            # 匹配 [project] 小节后的 version = "..."
            match = re.search(r'\[project\].*?version\s*=\s*["\']([^"\']+)["\']', content, re.DOTALL)
            return match.group(1) if match else "0.0.0"
    except Exception:
        return "0.0.0"


__version__ = _get_version()

# Suppress warnings
warnings.filterwarnings("ignore")

# Configure logging (default: WARNING level, no timestamps)
logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)
_INTERRUPTED = False


def _handle_sigint(signum, frame):
    """Record Ctrl+C and raise KeyboardInterrupt in the main thread promptly."""
    global _INTERRUPTED
    _INTERRUPTED = True
    raise KeyboardInterrupt


def _install_interrupt_handler():
    """Install a consistent Ctrl+C handler when the runtime supports it."""
    try:
        signal.signal(signal.SIGINT, _handle_sigint)
    except Exception:
        # Keep the default behavior on runtimes that reject custom handlers.
        pass


def _raise_if_interrupted():
    """Abort the current workflow after a previously received Ctrl+C."""
    if _INTERRUPTED:
        raise KeyboardInterrupt


def _run_subprocess_interruptibly(command, **kwargs):
    """
    Run a subprocess while ensuring Ctrl+C stops both Surf and the child process.

    This is mainly used for helpers that may block for a while, such as twitter-cli.
    """
    kwargs = dict(kwargs)
    timeout = kwargs.pop("timeout", None)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=kwargs.pop("text", False),
        encoding=kwargs.pop("encoding", None),
        errors=kwargs.pop("errors", None),
        env=kwargs.pop("env", None),
        cwd=kwargs.pop("cwd", None),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except KeyboardInterrupt:
        logger.warning("Interrupted while waiting for subprocess: %s", " ".join(command))
        try:
            process.terminate()
        except Exception:
            pass
        try:
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        raise
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        raise

    if kwargs.pop("check", False) and process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode,
            command,
            output=stdout,
            stderr=stderr,
        )

    class _CompletedProcess:
        def __init__(self, args, returncode, stdout, stderr):
            self.args = args
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    return _CompletedProcess(command, process.returncode, stdout, stderr)


def _decode_subprocess_output(value):
    """Decode subprocess output robustly across Windows console locales."""
    if isinstance(value, bytes):
        for encoding in ("utf-8", "utf-8-sig", sys.getfilesystemencoding() or "", locale.getpreferredencoding(False)):
            if not encoding:
                continue
            try:
                return value.decode(encoding)
            except UnicodeDecodeError:
                continue
        return value.decode("utf-8", errors="replace")
    return value or ""


def _call_interruptibly(func, *args, poll_interval=0.2, **kwargs):
    """
    Run a blocking callable in a daemon thread so Ctrl+C can interrupt promptly.

    This is especially helpful on Windows, where some network operations may not
    reliably stop when the main thread receives Ctrl+C.
    """
    result_queue = queue.Queue(maxsize=1)

    def _worker():
        try:
            result_queue.put(("result", func(*args, **kwargs)))
        except BaseException as exc:
            result_queue.put(("error", exc))

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()

    while True:
        _raise_if_interrupted()
        try:
            kind, payload = result_queue.get(timeout=poll_interval)
        except queue.Empty:
            continue
        if kind == "error":
            raise payload
        return payload


def _requests_get_interruptibly(*args, **kwargs):
    """Wrapper around requests.get that remains responsive to Ctrl+C."""
    return _call_interruptibly(requests.get, *args, **kwargs)


def _requests_post_interruptibly(*args, **kwargs):
    """Wrapper around requests.post that remains responsive to Ctrl+C."""
    return _call_interruptibly(requests.post, *args, **kwargs)


def _session_get_interruptibly(session, *args, **kwargs):
    """Wrapper around requests.Session.get that remains responsive to Ctrl+C."""
    return _call_interruptibly(session.get, *args, **kwargs)


class _SystemTrustHTTPAdapter(requests.adapters.HTTPAdapter):
    """Requests adapter that uses the OS/OpenSSL default trust store."""

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = ssl.create_default_context()
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        kwargs["ssl_context"] = ssl.create_default_context()
        return super().proxy_manager_for(*args, **kwargs)


def _configure_stdout_utf8():
    """Best-effort stdout UTF-8 reconfiguration for Windows console output."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


_SYSTEM_TRUST_REQUESTS_SESSION = requests.Session()
_SYSTEM_TRUST_REQUESTS_SESSION.mount("https://", _SystemTrustHTTPAdapter())


def _requests_get_with_system_trust_interruptibly(*args, **kwargs):
    """Requests.get via a session that uses the system default trust chain."""
    return _session_get_interruptibly(_SYSTEM_TRUST_REQUESTS_SESSION, *args, **kwargs)


def _get_local_dns_addresses(hostname):
    addresses = []
    if not hostname:
        return addresses
    seen = set()
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            infos = socket.getaddrinfo(hostname, None, family, socket.SOCK_STREAM)
        except Exception:
            continue
        for info in infos:
            address = ((info[4] or [None])[0] or "").strip()
            if address and address not in seen:
                seen.add(address)
                addresses.append(address)
    return addresses


def _resolve_host_via_google_doh(hostname, timeout=10):
    if not hostname:
        return []
    try:
        response = _requests_get_interruptibly(
            "https://dns.google/resolve",
            params={"name": hostname, "type": "A"},
            headers={"Accept": "application/dns-json", "User-Agent": "Mozilla/5.0"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return []

    addresses = []
    seen = set()
    for item in payload.get("Answer") or []:
        if not isinstance(item, dict) or item.get("type") != 1:
            continue
        address = str(item.get("data") or "").strip()
        if address and address not in seen:
            seen.add(address)
            addresses.append(address)
    return addresses


def _analyze_network_fetch_failure(url, error):
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return None

    error_text = str(error or "").lower()
    suspicious_markers = (
        "unexpected_eof_while_reading",
        "err_connection_closed",
        "connection was reset",
        "connection reset",
        "could not connect to server",
        "failed to connect",
        "timed out",
    )
    if not any(marker in error_text for marker in suspicious_markers):
        return None

    local_addresses = _get_local_dns_addresses(hostname)
    doh_addresses = _resolve_host_via_google_doh(hostname)
    dns_polluted = bool(local_addresses and doh_addresses and set(local_addresses).isdisjoint(set(doh_addresses)))

    if dns_polluted:
        message = (
            f"Possible DNS pollution or network interception for {hostname}: "
            f"local DNS resolved to {', '.join(local_addresses[:3])}, "
            f"but trusted DNS resolved to {', '.join(doh_addresses[:3])}. "
            "Try a working proxy or a different network."
        )
    elif doh_addresses:
        message = (
            f"Possible network block while reaching {hostname}. "
            f"Trusted DNS resolved to {', '.join(doh_addresses[:3])}, but the connection still failed. "
            "Try a working proxy or a different network."
        )
    else:
        message = None

    return {
        "hostname": hostname,
        "local_addresses": local_addresses,
        "trusted_addresses": doh_addresses,
        "dns_polluted": dns_polluted,
        "message": message,
    }


def _diagnose_network_fetch_failure(url, error):
    analysis = _analyze_network_fetch_failure(url, error)
    return analysis.get("message") if analysis else None


def _resolve_proxy_args(args, parser, config):
    """Resolve and validate CLI proxy arguments, with config fallback for custom mode."""
    proxy_mode = args.proxy
    custom_proxy = args.set_proxy
    if args.c is not None:
        if args.proxy and args.proxy != "custom":
            parser.error("-c cannot be combined with --proxy except --proxy custom")
        if args.set_proxy:
            parser.error("-c cannot be combined with --set-proxy")
        proxy_mode = "custom"
        custom_proxy = args.c
    if args.n:
        if args.proxy and args.proxy != "no":
            parser.error("-n cannot be combined with --proxy except --proxy no")
        if args.c is not None or args.set_proxy:
            parser.error("-n cannot be combined with custom proxy arguments")
        proxy_mode = "no"
        custom_proxy = None

    if custom_proxy and proxy_mode != "custom":
        parser.error("--set-proxy requires --proxy custom")

    config_custom_proxy = (config.get("Network", "custom_proxy", fallback="") or "").strip()
    if proxy_mode == "custom" and not (custom_proxy or config_custom_proxy):
        parser.error("--proxy custom requires --set-proxy PROXY, -c PROXY, or [Network] custom_proxy in config")

    return proxy_mode, custom_proxy


def _normalize_thread_argv(argv):
    """Allow `surf -t URL` even though argparse optional values are ambiguous."""
    normalized = []
    index = 0
    while index < len(argv):
        token = argv[index]
        normalized.append(token)
        if token in {"-t", "--thread"} and index + 1 < len(argv):
            next_token = argv[index + 1]
            if re.match(r"^https?://", next_token, re.IGNORECASE):
                normalized.append("after")
        index += 1
    return normalized


def setup_verbose_logging():
    """Enable verbose logging with timestamps."""
    logging.getLogger().setLevel(logging.INFO)
    # Update existing handlers
    for handler in logging.getLogger().handlers:
        handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))


def resolve_user_path(path):
    """
    Normalize user-supplied filesystem paths.

    On Windows, Unix-style separators are accepted and `~` resolves to the
    current user's profile directory (`%USERPROFILE%`).
    """
    if path is None:
        return None

    raw_path = str(path).strip()
    if not raw_path or raw_path == "-":
        return raw_path

    if os.name == "nt":
        normalized = raw_path.replace("/", "\\")
        normalized = os.path.expanduser(normalized)
        return os.path.normpath(normalized)

    return os.path.expanduser(raw_path)


def _get_default_config_path():
    """
    Resolve the default config path.

    Prefer config.ini next to the launched executable/script. If that does not
    exist, fall back to $XDG_CONFIG_HOME/surf/config.ini, or
    ~/.config/surf/config.ini when XDG_CONFIG_HOME is unset.
    """
    argv0 = (sys.argv[0] or "").strip() if sys.argv else ""
    if argv0 and argv0 not in {"-c", "-m"}:
        executable_path = resolve_user_path(argv0)
        if executable_path and os.path.isfile(executable_path):
            local_config = os.path.join(os.path.dirname(executable_path), "config.ini")
            if os.path.exists(local_config):
                return local_config

    xdg_config_home = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if xdg_config_home:
        config_home = resolve_user_path(xdg_config_home)
    else:
        config_home = resolve_user_path(os.path.join("~", ".config"))
    return os.path.join(config_home, "surf", "config.ini")


class Config:
    def __init__(self, config_path=None):
        config_path = resolve_user_path(config_path) if config_path else _get_default_config_path()
        # Disable interpolation to allow '%' in values (e.g. for TTS rate/voltage)
        self.config = configparser.ConfigParser(interpolation=None)
        if not os.path.exists(config_path):
            logger.warning(f"Config file {config_path} not found. Using defaults.")
        self.config.read(config_path)

        # Set default LLM provider
        self.llm_provider = self.get("LLM", "provider", fallback="L1")

    def get(self, section, key, fallback=None):
        return self.config.get(section, key, fallback=fallback)

    def get_path(self, section, key, fallback=None):
        value = self.get(section, key, fallback=fallback)
        return resolve_user_path(value)

    def _resolve_llm_section_name(self, provider_name):
        """Resolve an LLM provider section name case-insensitively."""
        if not provider_name:
            return None
        requested = provider_name.strip()
        requested_folded = requested.casefold()
        for section in self.config.sections():
            if not section.startswith("LLM."):
                continue
            available_provider = section.split(".", 1)[1]
            if available_provider.casefold() == requested_folded:
                return section
        return None

    def get_llm_config(self, provider_override=None):
        """Get LLM configuration for the specified provider or the default one.

        Args:
            provider_override (str, optional): Override the default LLM provider.

        Returns:
            dict: Dictionary containing base_url, api_key, and model for the LLM provider.

        Raises:
            ValueError: If the specified LLM provider is not found in the config.
        """
        provider = provider_override or self.llm_provider
        section = self._resolve_llm_section_name(provider)

        if not section:
            raise ValueError(
                f"LLM provider '{provider}' not found in config. "
                f"Available providers: {self._get_available_llm_providers()}"
            )

        return {
            "base_url": self.get(section, "base_url"),
            "api_key": self.get(section, "api_key"),
            "model": self.get(section, "model"),
        }

    def _get_available_llm_providers(self):
        """Get a list of available LLM provider names from the config."""
        return [section.split(".")[1] for section in self.config.sections() if section.startswith("LLM.")]


class Fetcher:
    _COMMON_SHORT_URL_HOSTS = {
        "t.co",
        "bit.ly",
        "bitly.com",
        "tinyurl.com",
        "goo.gl",
        "ow.ly",
        "buff.ly",
        "is.gd",
        "soo.gd",
        "s.id",
        "lnkd.in",
        "fb.me",
        "ift.tt",
        "shorturl.at",
        "rebrand.ly",
        "cutt.ly",
        "trib.al",
        "amzn.to",
        "youtu.be",
        "b23.tv",
        "xhslink.com",
    }

    _HTML_META_CHARSET_RE = re.compile(rb'<meta\s+charset\s*=\s*["\']?([^"\'>\s]+)', re.IGNORECASE)
    _HTML_CONTENT_CHARSET_RE = re.compile(rb'content\s*=\s*["\'][^"\']*charset\s*=\s*([^"\';\s]+)', re.IGNORECASE)

    @staticmethod
    def _sniff_html_charset(prefix: bytes):
        """Charset from <meta charset> or meta Content-Type content=...;charset= (HTML5)."""
        if not prefix:
            return None
        m = Fetcher._HTML_META_CHARSET_RE.search(prefix)
        if m:
            raw = m.group(1).decode("ascii", errors="ignore").strip()
            return raw.lower() if raw else None
        m = Fetcher._HTML_CONTENT_CHARSET_RE.search(prefix)
        if m:
            raw = m.group(1).decode("ascii", errors="ignore").strip()
            return raw.lower() if raw else None
        return None

    @staticmethod
    def _decode_response_text(response):
        """
        Decode HTML bytes with charset hints from headers and markup before
        falling back to requests' default text decoding.
        """
        content = response.content
        prefix = content[:32768]

        # Order matters: many servers send "text/html" without charset; requests
        # then defaults to ISO-8859-1 while the document is UTF-8 in <meta charset>.
        definite = []
        sniffed = Fetcher._sniff_html_charset(prefix)
        if sniffed:
            definite.append(sniffed)

        header_enc = get_encoding_from_headers(response.headers)
        if header_enc:
            low = {e.lower() for e in definite}
            if header_enc.lower() not in low:
                definite.append(header_enc)

        if "utf-8" not in {e.lower() for e in definite}:
            definite.append("utf-8")

        for encoding in definite:
            try:
                return content.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue

        candidates = []
        ct = (response.headers.get("Content-Type") or "").lower()
        header_has_charset = "charset=" in ct
        for encoding in (
            getattr(response, "encoding", None),
            getattr(response, "apparent_encoding", None),
            "utf-8",
            "gb18030",
        ):
            if not encoding:
                continue
            # Unlabeled HTML: requests' ISO-8859-1 default is a poor first guess.
            if not header_has_charset and encoding.upper() == "ISO-8859-1":
                continue
            if encoding not in candidates:
                candidates.append(encoding)

        dammit = UnicodeDammit(content, known_definite_encodings=candidates)
        if dammit.unicode_markup:
            return dammit.unicode_markup

        for encoding in candidates:
            try:
                return content.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue

        return response.text

    @staticmethod
    def _build_github_markdown_targets(url, config):
        """
        Resolve supported GitHub URLs to raw markdown fetch targets.

        Supported inputs:
        - Repo root: https://github.com/OWNER/REPO
        - Markdown file without branch: https://github.com/OWNER/REPO/path/to/file.md
        - Markdown file with blob/raw branch context
        """
        parsed = urlparse(url)
        if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
            return None

        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) < 2:
            return None

        owner, repo = path_parts[:2]
        rest = path_parts[2:]
        target_lang = (config.get("Output", "target_language", fallback="zh-cn") or "zh-cn").lower()
        lang_code = target_lang.split("-")[0]
        repo_title = f"{owner}/{repo}"
        repo_source_url = f"https://github.com/{owner}/{repo}"

        if not rest:
            readme_names = [f"README_{lang_code}.md", "README.md"]
            branch_candidates = ["master", "main"]
            candidates = []
            for name in readme_names:
                for branch in branch_candidates:
                    blob_url = f"https://github.com/{owner}/{repo}/blob/{branch}/{name}"
                    candidates.append(
                        {
                            "raw_url": f"https://github.com/{owner}/{repo}/raw/refs/heads/{branch}/{name}",
                            "source_url": repo_source_url,
                            "base_url": blob_url,
                            "title": repo_title,
                        }
                    )
            return candidates

        branch = None
        file_parts = None
        if rest[0] in {"blob", "tree"} and len(rest) >= 3:
            branch = rest[1]
            file_parts = rest[2:]
        elif rest[0] == "raw" and len(rest) >= 3:
            if len(rest) >= 5 and rest[1] == "refs" and rest[2] == "heads":
                branch = rest[3]
                file_parts = rest[4:]
            else:
                branch = rest[1]
                file_parts = rest[2:]
        elif rest[-1].lower().endswith(".md"):
            file_parts = rest

        if not file_parts or not file_parts[-1].lower().endswith(".md"):
            return None

        normalized_path = "/".join(file_parts)
        branch_candidates = [branch] if branch else ["main", "master"]
        title = file_parts[-1]
        source_path_url = f"{repo_source_url}/{normalized_path}"
        targets = []
        for candidate_branch in branch_candidates:
            blob_url = f"https://github.com/{owner}/{repo}/blob/{candidate_branch}/{normalized_path}"
            targets.append(
                {
                    "raw_url": f"https://github.com/{owner}/{repo}/raw/refs/heads/{candidate_branch}/{normalized_path}",
                    "source_url": blob_url if branch else source_path_url,
                    "base_url": blob_url,
                    "title": title,
                }
            )
        return targets

    @staticmethod
    def _fetch_github_markdown(url, config, proxy_mode_override=None, custom_proxy_override=None):
        """Fetch raw markdown from supported GitHub repo/file URLs and bypass HTML-to-Markdown conversion."""
        targets = Fetcher._build_github_markdown_targets(url, config)
        if not targets:
            return None

        req_proxies, _ = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        last_error = None
        for target in targets:
            try:
                logger.info(f"Fetching GitHub markdown directly: {target['raw_url']}")
                response = _requests_get_interruptibly(
                    target["raw_url"], headers=headers, proxies=req_proxies, timeout=20
                )
                response.raise_for_status()
                markdown_text = Fetcher._decode_response_text(response)
                if markdown_text and markdown_text.strip():
                    return _build_direct_markdown_payload(
                        markdown_text=markdown_text,
                        title=target["title"],
                        source_url=target["source_url"],
                        site_name="github",
                        base_url=target.get("base_url") or target["source_url"],
                    )
            except Exception as exc:
                last_error = exc
                logger.info(f"GitHub markdown candidate failed: {target['raw_url']} ({exc})")

        if last_error:
            logger.warning(f"GitHub markdown direct fetch failed: {last_error}")
        return None

    @staticmethod
    def _get_system_proxy_win():
        """
        Get Windows system proxy from WinINET (registry / Internet Settings).
        Returns (req_proxies, pw_proxy) or (None, None).
        """
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            )
            # Enable proxy
            enable = winreg.QueryValueEx(key, "ProxyEnable")[0]
            if enable:
                proxy = winreg.QueryValueEx(key, "ProxyServer")[0]
                if proxy:
                    return Fetcher._parse_proxy_server_config(proxy)
            return None, None
        except Exception as e:
            logger.warning(f"Failed to get Windows proxy: {e}")
            return None, None

    @staticmethod
    def _is_windows():
        return os.name == "nt"

    @staticmethod
    def _normalize_proxy_mode(mode):
        if mode is None:
            return None
        normalized = str(mode).strip().lower()
        if not normalized:
            return None
        aliases = {
            "auto": "env",
            "set": "custom",
        }
        return aliases.get(normalized, normalized)

    @staticmethod
    def _read_env_proxies():
        http_proxy = os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY")
        https_proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
        no_proxy = os.environ.get("no_proxy") or os.environ.get("NO_PROXY")

        req_proxies = {}
        if http_proxy:
            req_proxies["http"] = http_proxy
        if https_proxy:
            req_proxies["https"] = https_proxy
        if not req_proxies:
            req_proxies = None

        server = https_proxy or http_proxy
        pw_proxy = None
        if server:
            pw_proxy = {"server": server}
            if no_proxy:
                pw_proxy["bypass"] = no_proxy

        return req_proxies, pw_proxy

    @staticmethod
    def _parse_proxy_server_config(proxy_config):
        """
        Parse proxy config strings like:
        - host:port
        - http=host:port;https=host:port
        """
        if not proxy_config:
            return None, None

        proxies = {}
        pw_proxy_server = None
        for part in str(proxy_config).split(";"):
            part = part.strip()
            if not part:
                continue
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.strip().lower()
                v = v.strip()
                if not v:
                    continue
                proxies[k] = v
                if k == "https" and not pw_proxy_server:
                    pw_proxy_server = v
                elif k == "http" and not pw_proxy_server:
                    pw_proxy_server = v
            else:
                proxies["http"] = part
                proxies["https"] = part
                pw_proxy_server = part

        req_proxies = {}
        if "http" in proxies:
            req_proxies["http"] = proxies["http"]
        if "https" in proxies:
            req_proxies["https"] = proxies["https"]
        if not req_proxies:
            req_proxies = None

        pw_proxy = {"server": pw_proxy_server} if pw_proxy_server else None
        return req_proxies, pw_proxy

    @staticmethod
    def _read_winhttp_proxies():
        """
        Read WinHTTP proxy (`netsh winhttp show proxy`) as the last fallback.
        Returns (req_proxies, pw_proxy) or (None, None).
        """
        if not Fetcher._is_windows():
            return None, None

        try:
            result = _run_subprocess_interruptibly(
                ["netsh", "winhttp", "show", "proxy"],
                check=False,
                text=True,
                timeout=5,
            )
            output = (result.stdout or "") + "\n" + (result.stderr or "")
            lower = output.lower()
            if "direct access (no proxy server)" in lower:
                return None, None

            match = re.search(r"Proxy Server\(s\)\s*:\s*(.+)", output, re.IGNORECASE)
            if not match:
                return None, None

            proxy_config = match.group(1).strip()
            if not proxy_config:
                return None, None

            req_proxies, pw_proxy = Fetcher._parse_proxy_server_config(proxy_config)
            if req_proxies or pw_proxy:
                logger.info(f"Using WinHTTP proxy: {proxy_config}")
            return req_proxies, pw_proxy
        except Exception as e:
            logger.debug(f"Could not read WinHTTP proxy: {e}")
            return None, None

    @staticmethod
    def _get_proxies(config, proxy_mode_override=None, custom_proxy_override=None):
        """
        Returns a dictionary of proxies based on configuration.
        For requests: {'http': '...', 'https': '...'} or None
        For playwright: {'server': '...'} or None

        Args:
            config: Config object
            proxy_mode_override: Override proxy_mode (env/no/win/custom)
            custom_proxy_override: Override custom_proxy from command line
        """
        mode_override = Fetcher._normalize_proxy_mode(proxy_mode_override)
        config_mode = Fetcher._normalize_proxy_mode(config.get("Network", "proxy_mode", fallback="env"))
        config_custom = (config.get("Network", "custom_proxy", fallback="") or "").strip()
        custom_override = (custom_proxy_override or "").strip()

        # 1) Explicit mode from CLI/Web request
        if mode_override:
            if mode_override == "no":
                return None, None
            if mode_override == "env":
                return Fetcher._read_env_proxies()
            if mode_override == "custom":
                custom = custom_override or config_custom
                if custom:
                    return {"http": custom, "https": custom}, {"server": custom}
                logger.warning(
                    "Custom proxy mode selected but no proxy URL provided. Falling back to direct connection."
                )
                return None, None
            if mode_override == "win":
                if not Fetcher._is_windows():
                    logger.warning("Proxy mode 'win' is only available on Windows.")
                    return None, None
                req_proxies, pw_proxy = Fetcher._get_system_proxy_win()
                if req_proxies or pw_proxy:
                    return req_proxies, pw_proxy
                logger.info("Windows Internet Settings proxy is not enabled.")
                return None, None

            logger.warning(f"Unknown proxy mode '{mode_override}', falling back to direct connection.")
            return None, None

        # 2) Environment variables (implicit default path)
        req_proxies, pw_proxy = Fetcher._read_env_proxies()
        if req_proxies or pw_proxy:
            return req_proxies, pw_proxy

        # 3) INI configuration
        if config_mode == "no":
            return None, None
        if config_mode == "custom":
            if config_custom:
                return {"http": config_custom, "https": config_custom}, {"server": config_custom}
        elif config_mode == "win":
            if Fetcher._is_windows():
                req_proxies, pw_proxy = Fetcher._get_system_proxy_win()
                if req_proxies or pw_proxy:
                    return req_proxies, pw_proxy
            else:
                logger.warning("Ignoring INI proxy_mode=win on non-Windows platform.")
        elif config_mode == "env":
            # Already tried environment above.
            pass
        elif config_mode:
            logger.warning(f"Unknown INI proxy_mode '{config_mode}', ignoring.")

        # 4) WinHTTP fallback
        req_proxies, pw_proxy = Fetcher._read_winhttp_proxies()
        if req_proxies or pw_proxy:
            return req_proxies, pw_proxy

        return None, None

    @staticmethod
    def fetch(
        url,
        config,
        use_browser=False,
        proxy_mode_override=None,
        custom_proxy_override=None,
        fetch_thread=None,
        fetch_thread_author=None,
        twitter_backend=None,
        twitter_cli_bin=None,
        twitter_browser=None,
        twitter_profile=None,
    ):
        """
        Fetches the content of a URL.
        For Twitter/X URLs, uses the official oEmbed API.
        If use_browser is True, uses Playwright.
        Otherwise, uses requests.

        Args:
            url: URL to fetch
            config: Config object
            use_browser: Force use browser
            proxy_mode_override: Override proxy_mode from command line
            custom_proxy_override: Override custom_proxy from command line
        """
        url = Fetcher._resolve_common_short_url(
            url,
            config,
            proxy_mode_override=proxy_mode_override,
            custom_proxy_override=custom_proxy_override,
        )

        logger.info(f"Fetching {url}...")

        handler, site_name, site_config = _get_handler_for_url(url)

        # Apply site defaults for proxy if not overridden
        if (
            site_config
            and site_config.get("default_no_proxy")
            and proxy_mode_override is None
            and not Fetcher._is_windows()
        ):
            logger.info(f"{site_name}: Using site default 'no proxy'")
            proxy_mode_override = "no"

        if handler:
            logger.info(f"Using special handler for {site_name}")
            if site_name == "twitter":
                html_content = handler(
                    url,
                    config,
                    proxy_mode_override,
                    custom_proxy_override,
                    fetch_thread=fetch_thread,
                    fetch_thread_author=fetch_thread_author,
                    backend=twitter_backend,
                    cli_bin=twitter_cli_bin,
                    browser=twitter_browser,
                    profile=twitter_profile,
                )
            elif site_name in {"bluesky", "weibo", "threads", "v2ex", "reddit"}:
                html_content = handler(
                    url,
                    config,
                    proxy_mode_override,
                    custom_proxy_override,
                    fetch_thread=fetch_thread,
                    fetch_thread_author=fetch_thread_author,
                )
            else:
                html_content = handler(url, config, proxy_mode_override, custom_proxy_override)
            if html_content:
                return html_content
            if site_config and site_config.get("no_generic_fallback"):
                logger.info(f"{site_name}: Special handler failed; skipping generic fallback")
                return None

        req_proxies, pw_proxy = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)

        if not use_browser:
            should_use_browser = False
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            if Fetcher._is_douban_url(url):
                cookie_header = AuthHandler.cookie_header_for_douban()
                if cookie_header:
                    headers["Cookie"] = cookie_header
                    logger.info("douban: Using saved login cookies for HTTP requests")
            try:
                logger.info(f"Requests Proxies: {req_proxies if req_proxies else 'None'}")
                response = _requests_get_with_system_trust_interruptibly(
                    url,
                    headers=headers,
                    proxies=req_proxies,
                    timeout=10,
                )
                response.raise_for_status()
                decoded_text = Fetcher._decode_response_text(response)

                # Check if likely dynamic (heuristic: very short content or explicit noscript)
                if len(decoded_text) < 1000 or "<noscript>" in decoded_text or Fetcher._is_cloudflare_challenge(decoded_text):
                    logger.info("Content seems short, requires JS, or is a Cloudflare challenge. Switching to browser...")
                    should_use_browser = True
                else:
                    return decoded_text

            except Exception as e:
                if Fetcher._should_retry_without_proxy(proxy_mode_override, req_proxies, e):
                    logger.warning(f"Requests failed via implicit proxy: {e}. Retrying direct connection...")
                    try:
                        response = _requests_get_with_system_trust_interruptibly(url, headers=headers, timeout=10)
                        response.raise_for_status()
                        decoded_text = Fetcher._decode_response_text(response)
                        if len(decoded_text) < 1000 or "<noscript>" in decoded_text or Fetcher._is_cloudflare_challenge(decoded_text):
                            logger.info("Direct retry succeeded but content still seems short, requires JS, or is a Cloudflare challenge. Switching to browser...")
                            should_use_browser = True
                        else:
                            return decoded_text
                    except Exception as retry_error:
                        logger.warning(f"Direct retry after proxy failure failed: {retry_error}. Switching to browser...")
                        return Fetcher.fetch_with_browser(url, config, proxy_mode_override, custom_proxy_override)
                logger.warning(f"Requests failed: {e}. Switching to browser...")

            if should_use_browser:
                logger.info("Using browser fallback for dynamic content.")

            return Fetcher.fetch_with_browser(url, config, proxy_mode_override, custom_proxy_override)
        else:
            return Fetcher.fetch_with_browser(url, config, proxy_mode_override, custom_proxy_override)

    @staticmethod
    def _is_twitter_article_only_link(html_content):
        """
        Check if the oEmbed HTML only contains a link (Twitter Article case).
        This happens when the oEmbed API cannot extract article content and only returns a t.co link.

        Args:
            html_content: HTML content from oEmbed API

        Returns:
            bool: True if it's just a link-only response
        """
        if not html_content:
            return True

        soup = BeautifulSoup(html_content, "html.parser")

        # Find the tweet text paragraph
        p_tag = soup.find("p")
        if not p_tag:
            return True

        # Get text content of the p tag
        text = p_tag.get_text(strip=True)

        # Check if the content is just a t.co link
        # Pattern: content is only "https://t.co/XXXXXX"
        if re.match(r"^https://t\.co/\w+$", text):
            logger.info("Detected Twitter Article (link-only oEmbed response)")
            return True

        # Also check if there's minimal text and only contains links
        # If the <p> tag only contains <a> tags with t.co links, it's likely an article
        links = p_tag.find_all("a")
        if links:
            # Check if all links are t.co links
            all_tco = all("t.co/" in a.get("href", "") for a in links)
            if all_tco:
                logger.info("Detected Twitter Article (t.co links only)")
                return True

        return False

    @staticmethod
    def _get_twitter_backend_options(
        config,
        backend=None,
        cli_bin=None,
        browser=None,
        profile=None,
    ):
        """Resolve Twitter backend options from CLI overrides and config."""
        return {
            "backend": (backend or config.get("Twitter", "backend", fallback="cli") or "cli").strip().lower(),
            "cli_bin": (cli_bin or config.get("Twitter", "cli_bin", fallback="") or "").strip(),
            "browser": (browser or config.get("Twitter", "browser", fallback="") or "").strip(),
            "profile": (profile or config.get("Twitter", "profile", fallback="") or "").strip(),
        }

    @staticmethod
    def _tag_twitter_html_content(html_content, kind, author=None):
        """Annotate generated Twitter/X HTML so later stages can distinguish tweet vs article."""
        if not html_content or kind not in {"tweet", "article"}:
            return html_content
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            head = soup.find("head")
            if not head:
                head = soup.new_tag("head")
                if soup.html:
                    soup.html.insert(0, head)
                else:
                    html = soup.new_tag("html")
                    html.append(head)
                    body = soup.new_tag("body")
                    for child in list(soup.contents):
                        body.append(child.extract())
                    html.append(body)
                    soup.append(html)

            meta = soup.find("meta", attrs={"name": "surf-twitter-kind"})
            if not meta:
                meta = soup.new_tag("meta")
                meta.attrs["name"] = "surf-twitter-kind"
                head.append(meta)
            meta.attrs["content"] = kind

            source_meta = soup.find("meta", attrs={"name": "surf-source-site"})
            if not source_meta:
                source_meta = soup.new_tag("meta")
                source_meta.attrs["name"] = "surf-source-site"
                head.append(source_meta)
            source_meta.attrs["content"] = "twitter"

            if author:
                author_meta = soup.find("meta", attrs={"name": "surf-author"})
                if not author_meta:
                    author_meta = soup.new_tag("meta")
                    author_meta.attrs["name"] = "surf-author"
                    head.append(author_meta)
                author_meta.attrs["content"] = str(author).strip()
            return str(soup)
        except Exception:
            return html_content

    @staticmethod
    def _resolve_twitter_cli_command(cli_bin=""):
        """Resolve the twitter-cli invocation. Surf always uses uvx --from twitter-cli twitter."""
        uvx_bin = shutil.which("uvx")
        if not uvx_bin:
            return None
        if cli_bin:
            logger.info(
                "Ignoring --twitter-cli-bin=%s; Surf always invokes twitter-cli via uvx",
                cli_bin,
            )
        return [uvx_bin, "--from", "twitter-cli", "twitter"]

    @staticmethod
    def _extract_twitter_text_lines(value):
        """Flatten text-ish values from twitter-cli JSON into paragraph lines."""
        if value is None:
            return []
        if isinstance(value, str):
            return [line.strip() for line in value.splitlines() if line.strip()]
        if isinstance(value, list):
            lines = []
            for item in value:
                lines.extend(Fetcher._extract_twitter_text_lines(item))
            return lines
        if isinstance(value, dict):
            lines = []
            for key in (
                "articleText",
                "full_text",
                "fullText",
                "text",
                "raw_text",
                "rawText",
                "content",
                "display_text",
                "displayText",
            ):
                if key in value:
                    lines.extend(Fetcher._extract_twitter_text_lines(value.get(key)))
            return lines
        return []

    @staticmethod
    def _extract_twitter_media_urls(value):
        """Collect image/media URLs from nested twitter-cli structures."""
        urls = []
        seen = set()

        def add(url):
            if isinstance(url, str):
                normalized = url.strip()
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    urls.append(normalized)

        def walk(node):
            if isinstance(node, dict):
                for key, item in node.items():
                    low = key.lower()
                    if low in {
                        "url",
                        "media_url",
                        "media_url_https",
                        "original_img_url",
                        "originalimageurl",
                        "original_image_url",
                        "image",
                        "image_url",
                        "imageurl",
                        "image_src",
                        "image_source",
                        "thumbnail_url",
                        "thumbnailurl",
                        "mediaurl",
                        "mediaurlhttps",
                        "display_url",
                    }:
                        add(item)
                    else:
                        walk(item)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(value)
        return urls

    @staticmethod
    def _extract_twitter_author_info(data):
        """Best-effort author extraction from twitter-cli structured data."""
        author = data.get("author") or data.get("user") or data.get("account") or {}
        if not isinstance(author, dict):
            author = {}
        name = author.get("name") or data.get("authorName") or data.get("userName") or ""
        screen_name = (
            author.get("screen_name")
            or author.get("screenName")
            or author.get("username")
            or data.get("screen_name")
            or data.get("screenName")
            or data.get("username")
            or ""
        )
        return str(name).strip(), str(screen_name).strip()

    @staticmethod
    def _get_twitter_author_label(author_name="", screen_name="", source_url=None):
        author_name = str(author_name or "").strip()
        screen_name = str(screen_name or "").strip().lstrip("@")
        if screen_name:
            return f"@{screen_name}"
        if author_name:
            return author_name
        if source_url:
            match = re.match(r"^https?://(www\.)?(twitter|x)\.com/([^/]+)/status/\d+", source_url, re.IGNORECASE)
            if match:
                return f"@{match.group(3)}"
        return ""

    @staticmethod
    def _render_twitter_entities_to_html(text, entities):
        if not text:
            return ""
        if not isinstance(entities, list):
            return escape(text)

        marks = {}
        link_ranges = {}

        def _to_int(value):
            try:
                return int(value)
            except Exception:
                return None

        for entity in entities:
            if not isinstance(entity, dict):
                continue
            start = _to_int(
                entity.get("from")
                or entity.get("start")
                or entity.get("offset")
                or entity.get("indices", [None, None])[0]
            )
            end = _to_int(
                entity.get("to")
                or entity.get("end")
                or entity.get("offset_end")
                or entity.get("indices", [None, None])[1]
            )
            if start is None or end is None or start < 0 or end <= start:
                continue

            kinds = set()
            for key in ("type", "types", "style", "styles", "format", "formats"):
                value = entity.get(key)
                if isinstance(value, str):
                    kinds.update(part.strip().lower() for part in re.split(r"[\s,|/+]+", value) if part.strip())
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            kinds.add(item.strip().lower())

            if entity.get("bold") or "bold" in kinds or "strong" in kinds:
                marks.setdefault(start, []).append("<strong>")
                marks.setdefault(end, []).append("</strong>")
            if entity.get("italic") or "italic" in kinds or "emphasis" in kinds or "italicized" in kinds:
                marks.setdefault(start, []).append("<em>")
                marks.setdefault(end, []).append("</em>")

            link_url = (
                entity.get("expanded_url") or entity.get("expandedUrl") or entity.get("url") or entity.get("href")
            )
            if isinstance(link_url, str) and link_url.strip():
                link_ranges[(start, end)] = link_url.strip()

        if not marks and not link_ranges:
            return escape(text)

        out = []
        for idx, char in enumerate(text):
            if idx in marks:
                for token in sorted(marks[idx], key=lambda item: item.startswith("</")):
                    out.append(token)
            for (start, end), link_url in link_ranges.items():
                if idx == start:
                    out.append(f"<a href='{escape(link_url)}'>")
                if idx == end:
                    out.append("</a>")
            out.append(escape(char))

        text_len = len(text)
        if text_len in marks:
            closing = [token for token in marks[text_len] if token.startswith("</")]
            opening = [token for token in marks[text_len] if not token.startswith("</")]
            out.extend(closing + opening)
        for (start, end), _ in link_ranges.items():
            if end == text_len:
                out.append("</a>")
        return "".join(out)

    @staticmethod
    def _extract_twitter_rich_text_html(value):
        if value is None:
            return []
        if isinstance(value, str):
            return [escape(line.strip()) for line in value.splitlines() if line.strip()]
        if isinstance(value, list):
            blocks = []
            for item in value:
                blocks.extend(Fetcher._extract_twitter_rich_text_html(item))
            return blocks
        if isinstance(value, dict):
            text = value.get("text")
            entities = value.get("entities") or value.get("facets") or value.get("annotations")
            if isinstance(text, str) and text.strip():
                rendered = Fetcher._render_twitter_entities_to_html(text, entities)
                return [part for part in rendered.splitlines() if part.strip()]

            blocks = []
            for key in (
                "articleText",
                "full_text",
                "fullText",
                "text",
                "raw_text",
                "rawText",
                "content",
                "display_text",
                "displayText",
            ):
                if key in value:
                    blocks.extend(Fetcher._extract_twitter_rich_text_html(value.get(key)))
                    if blocks:
                        break
            return blocks
        return []

    @staticmethod
    def _extract_fx_text_and_entities(node):
        if isinstance(node, str):
            return node, None
        if not isinstance(node, dict):
            return "", None

        for key in ("text", "content", "value", "raw_text", "rawText"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                entities = (
                    node.get("entities") or node.get("facets") or node.get("annotations") or node.get("formatting")
                )
                return value, entities
            if isinstance(value, dict):
                nested_text, nested_entities = Fetcher._extract_fx_text_and_entities(value)
                if nested_text:
                    return nested_text, nested_entities
        return "", None

    @staticmethod
    def _extract_fx_block_type(block):
        if not isinstance(block, dict):
            return ""
        for key in ("type", "block_type", "kind", "name", "$type"):
            value = block.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
        return ""

    @staticmethod
    def _apply_fx_ranges(text, style_ranges=None, entity_ranges=None, entity_map=None):
        text = text or ""
        opens = {}
        closes = {}
        media_refs = []

        def add_open(pos, token):
            opens.setdefault(pos, []).append(token)

        def add_close(pos, token):
            closes.setdefault(pos, []).append(token)

        def get_entity(key):
            if isinstance(entity_map, dict):
                if key in entity_map:
                    return entity_map[key]
                key_str = str(key)
                if key_str in entity_map:
                    return entity_map[key_str]
            return None

        if isinstance(style_ranges, list):
            for item in style_ranges:
                if not isinstance(item, dict):
                    continue
                try:
                    offset = int(item.get("offset", 0))
                    length = int(item.get("length", 0))
                except Exception:
                    continue
                if length <= 0:
                    continue
                end = offset + length
                style = str(item.get("style", "")).strip().upper()
                if style == "BOLD":
                    add_open(offset, "<strong>")
                    add_close(end, "</strong>")
                elif style == "ITALIC":
                    add_open(offset, "<em>")
                    add_close(end, "</em>")
                elif style == "CODE":
                    add_open(offset, "<code>")
                    add_close(end, "</code>")

        if isinstance(entity_ranges, list):
            for item in entity_ranges:
                if not isinstance(item, dict):
                    continue
                try:
                    offset = int(item.get("offset", 0))
                    length = int(item.get("length", 0))
                except Exception:
                    continue
                if length < 0:
                    continue
                end = offset + length
                entity = get_entity(item.get("key"))
                if not isinstance(entity, dict):
                    continue
                entity_type = str(entity.get("type", "")).strip().upper()
                data = entity.get("data") or {}
                if entity_type == "LINK":
                    href = data.get("url") or data.get("href") or data.get("expanded_url") or data.get("expandedUrl")
                    if isinstance(href, str) and href.strip():
                        add_open(offset, f"<a href='{escape(href.strip())}'>")
                        add_close(end, "</a>")
                elif entity_type == "CODE":
                    add_open(offset, "<code>")
                    add_close(end, "</code>")
                elif entity_type in {"IMAGE", "PHOTO", "MEDIA", "EMBEDDED_LINK"}:
                    media_refs.append(entity)

        out = []
        for idx, char in enumerate(text):
            if idx in closes:
                out.extend(sorted(closes[idx], reverse=True))
            if idx in opens:
                out.extend(opens[idx])
            if char == "\n":
                out.append("<br>")
            else:
                out.append(escape(char))
        text_len = len(text)
        if text_len in closes:
            out.extend(sorted(closes[text_len], reverse=True))
        if text_len in opens:
            out.extend(opens[text_len])
        return "".join(out), media_refs

    @staticmethod
    def _render_fx_text_html(text, entities=None):
        if not text:
            return ""

        if not entities:
            return escape(text)

        if isinstance(entities, dict):
            style_ranges = entities.get("inlineStyleRanges") or entities.get("inline_style_ranges")
            entity_ranges = entities.get("entityRanges") or entities.get("entity_ranges")
            entity_map = entities.get("entityMap") or entities.get("entity_map")
            if style_ranges or entity_ranges:
                rendered, _ = Fetcher._apply_fx_ranges(
                    text,
                    style_ranges=style_ranges,
                    entity_ranges=entity_ranges,
                    entity_map=entity_map,
                )
                return rendered

            normalized_entities = []
            for key in (
                "urls",
                "url",
                "hashtags",
                "symbols",
                "user_mentions",
                "mentions",
                "media",
            ):
                value = entities.get(key)
                if isinstance(value, list):
                    normalized_entities.extend(item for item in value if isinstance(item, dict))
                elif isinstance(value, dict):
                    normalized_entities.append(value)
            if normalized_entities:
                return Fetcher._render_twitter_entities_to_html(text, normalized_entities)

        if isinstance(entities, list):
            return Fetcher._render_twitter_entities_to_html(text, entities)

        return escape(text)

    @staticmethod
    def _extract_twitter_media_urls_from_entity(entity):
        if not isinstance(entity, dict):
            return []
        data = entity.get("data") or {}
        candidates = []
        for node in (entity, data):
            candidates.extend(Fetcher._extract_twitter_media_urls(node))
        seen = []
        for url in candidates:
            if url not in seen:
                seen.append(url)
        return seen

    @staticmethod
    def _is_fx_likely_code_line(text):
        if not text:
            return False
        stripped = text.strip()
        if not stripped:
            return False
        command_patterns = [
            r"^[A-Za-z0-9_.-]+\s+[A-Za-z0-9_/~.-]",
            r"^(vim|cat|cd|ls|pwd|git|docker|python|pip|uv|npm|pnpm|yarn|hermes|rm|cp|mv)\b",
            r"^~?/[A-Za-z0-9_./-]+$",
            r"^[A-Za-z_][A-Za-z0-9_]*\s*[:=]\s*.+$",
        ]
        if any(re.match(pattern, stripped) for pattern in command_patterns):
            return True
        if "`" in stripped:
            return True
        if stripped.count("/") >= 2 and " " not in stripped:
            return True
        return False

    @staticmethod
    def _render_fx_block_html(block, entity_map=None):
        if not isinstance(block, dict):
            return [], []

        block_type = Fetcher._extract_fx_block_type(block)
        text = block.get("text") if isinstance(block.get("text"), str) else ""
        if not text:
            text, _ = Fetcher._extract_fx_text_and_entities(block)
        inline_style_ranges = block.get("inlineStyleRanges") or block.get("inline_style_ranges")
        entity_ranges = block.get("entityRanges") or block.get("entity_ranges")
        rendered, media_refs = Fetcher._apply_fx_ranges(
            text,
            style_ranges=inline_style_ranges,
            entity_ranges=entity_ranges,
            entity_map=entity_map,
        )
        html_chunks = []

        if block_type == "atomic":
            media_refs.append(block)
            return [], media_refs

        if text:
            if block_type == "code-block" or "code" in block_type or block.get("language") or block.get("code"):
                html_chunks.append(f"<pre><code>{escape(text)}</code></pre>")
            elif block_type in {"blockquote", "pullquote"} or "quote" in block_type:
                html_chunks.append(f"<blockquote><p>{rendered}</p></blockquote>")
            elif block_type in {"header-one", "title"}:
                html_chunks.append(f"<h1>{rendered}</h1>")
            elif block_type in {"header-two", "header", "heading"}:
                html_chunks.append(f"<h2>{rendered}</h2>")
            elif block_type in {"header-three", "subheading", "subtitle"}:
                html_chunks.append(f"<h3>{rendered}</h3>")
            elif block_type == "ordered-list-item":
                html_chunks.append(f"<li data-list-type='ol'>{rendered}</li>")
            elif block_type == "unordered-list-item":
                html_chunks.append(f"<li data-list-type='ul'>{rendered}</li>")
            elif block_type == "unstyled" and Fetcher._is_fx_likely_code_line(text):
                html_chunks.append(f"<pre><code>{escape(text)}</code></pre>")
            else:
                html_chunks.append(f"<p>{rendered}</p>")

        return html_chunks, media_refs

    @staticmethod
    def _extract_fx_block_sequence(article, tweet):
        blocks_html = []
        seen_images = set()
        entity_map = (article.get("content") or {}).get("entityMap") or {}
        ordered_media_queue = []

        def enqueue_media_from(node):
            for media_url in Fetcher._extract_twitter_media_urls(node):
                if media_url not in ordered_media_queue:
                    ordered_media_queue.append(media_url)

        enqueue_media_from(article.get("cover_media") or {})
        enqueue_media_from(article.get("media_entities") or [])
        enqueue_media_from(tweet.get("media") or [])
        enqueue_media_from(tweet.get("media_extended") or [])

        def append_media_from(node):
            media_urls = Fetcher._extract_twitter_media_urls(node)
            for media_url in media_urls:
                if media_url not in seen_images:
                    seen_images.add(media_url)
                    blocks_html.append(f"<p><img src='{escape(media_url)}' alt='tweet media'></p>")

        content = article.get("content") or {}
        blocks = content.get("blocks") or []
        if isinstance(blocks, list) and blocks:
            pending_list_type = None
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                block_fragments, media_refs = Fetcher._render_fx_block_html(block, entity_map=entity_map)
                if block_fragments:
                    for fragment in block_fragments:
                        if fragment.startswith("<li "):
                            list_type = "ol" if "data-list-type='ol'" in fragment else "ul"
                            clean_fragment = fragment.replace(" data-list-type='ol'", "").replace(
                                " data-list-type='ul'", ""
                            )
                            if pending_list_type != list_type:
                                if pending_list_type:
                                    blocks_html.append(f"</{pending_list_type}>")
                                blocks_html.append(f"<{list_type}>")
                                pending_list_type = list_type
                            blocks_html.append(clean_fragment)
                        else:
                            if pending_list_type:
                                blocks_html.append(f"</{pending_list_type}>")
                                pending_list_type = None
                            blocks_html.append(fragment)
                else:
                    if pending_list_type:
                        blocks_html.append(f"</{pending_list_type}>")
                        pending_list_type = None

                # fxTwitter article often uses atomic blocks as positional media placeholders
                # while the actual URLs live in top-level media_entities / cover_media.
                if Fetcher._extract_fx_block_type(block) == "atomic":
                    appended_from_block = len(seen_images)
                    for entity in media_refs:
                        append_media_from(entity)
                    append_media_from(block)
                    if len(seen_images) == appended_from_block:
                        while ordered_media_queue:
                            next_url = ordered_media_queue.pop(0)
                            if next_url not in seen_images:
                                seen_images.add(next_url)
                                blocks_html.append(f"<p><img src='{escape(next_url)}' alt='tweet media'></p>")
                                break
                    continue

                for entity in media_refs:
                    append_media_from(entity)
                append_media_from(block)
            if pending_list_type:
                blocks_html.append(f"</{pending_list_type}>")

        if not blocks_html:
            text = (tweet.get("text") or "").strip()
            entities = tweet.get("entities") or tweet.get("facets") or tweet.get("annotations")
            if not text:
                raw_text = tweet.get("raw_text") or {}
                text, entities = Fetcher._extract_fx_text_and_entities(raw_text)
            if text:
                blocks_html.append(f"<p>{Fetcher._render_fx_text_html(text, entities)}</p>")

        append_media_from(article.get("cover_media") or {})
        append_media_from(article.get("media_entities") or [])
        append_media_from(tweet.get("media") or [])
        append_media_from(tweet.get("media_extended") or [])
        return blocks_html

    @staticmethod
    def _log_fx_article_structure(article):
        try:
            content = article.get("content") or {}
            blocks = content.get("blocks") or []
            entity_map = content.get("entityMap") or {}
            block_types = []
            for idx, block in enumerate(blocks[:20]):
                if not isinstance(block, dict):
                    continue
                block_types.append(
                    {
                        "i": idx,
                        "type": Fetcher._extract_fx_block_type(block) or "unknown",
                        "text_len": len(block.get("text") or ""),
                        "styles": len(block.get("inlineStyleRanges") or block.get("inline_style_ranges") or []),
                        "entities": len(block.get("entityRanges") or block.get("entity_ranges") or []),
                    }
                )

            entity_types = []
            if isinstance(entity_map, dict):
                for key, entity in list(entity_map.items())[:30]:
                    if not isinstance(entity, dict):
                        continue
                    entity_types.append(
                        {
                            "key": key,
                            "type": str(entity.get("type", "unknown")),
                            "data_keys": sorted(list((entity.get("data") or {}).keys()))[:8],
                        }
                    )

            logger.info(
                "fxTwitter article structure: blocks=%s entityMap=%s block_types=%s entity_types=%s",
                len(blocks) if isinstance(blocks, list) else 0,
                len(entity_map) if isinstance(entity_map, dict) else 0,
                json.dumps(block_types, ensure_ascii=False),
                json.dumps(entity_types, ensure_ascii=False),
            )
        except Exception as e:
            logger.debug("Failed to log fxTwitter article structure: %s", e)

    @staticmethod
    def _is_twitter_decorative_image(img):
        src = (img.get("src") or "").strip().lower()
        alt = (img.get("alt") or "").strip().lower()
        if not src:
            return True
        if src.startswith("data:"):
            return True
        if any(token in src for token in ("profile_images", "/emoji/", "emoji", "avatar", "icon")):
            return True
        if alt in {"", "image"}:
            return False
        if any(token in alt for token in ("avatar", "profile photo", "emoji", "icon")):
            return True
        parent_link = img.find_parent("a", href=True)
        if (
            parent_link
            and "/photo/" not in parent_link.get("href", "")
            and "/status/" not in parent_link.get("href", "")
        ):
            if "pbs.twimg.com/media/" not in src and "video_thumb" not in src:
                return True
        return False

    @staticmethod
    def _twitter_node_has_block_children(node):
        for child in getattr(node, "children", []):
            if getattr(child, "name", None) in {"p", "div", "ul", "ol", "li", "blockquote", "pre", "h1", "h2", "h3"}:
                return True
        return False

    @staticmethod
    def _twitter_pre_looks_like_code(text):
        if not text:
            return False

        raw = str(text).strip("\n")
        if not raw:
            return False

        lines = [line.rstrip() for line in raw.splitlines()]
        non_empty = [line for line in lines if line.strip()]
        if not non_empty:
            return False

        joined = "\n".join(non_empty)
        lowered = joined.lower()
        code_markers = [
            "{",
            "}",
            ";",
            "=>",
            "::",
            "```",
            "<?",
            "</",
            "def ",
            "class ",
            "function ",
            "return ",
            "const ",
            "let ",
            "var ",
            "import ",
            "#include",
            "SELECT ",
        ]
        if any(marker in joined for marker in code_markers):
            return True
        if any(lowered.startswith(prefix) for prefix in ("def ", "class ", "function ", "import ", "from ")):
            return True

        indented = sum(1 for line in non_empty if re.match(r"^(?:\t| {4,})\S", line))
        if indented >= 2:
            return True

        punctuated = sum(1 for line in non_empty if re.search(r"[{}();=<>\[\]]", line) and len(line.strip()) <= 160)
        if punctuated >= max(2, len(non_empty) // 2):
            return True

        return False

    @staticmethod
    def _normalize_twitter_markup_fragment(node, normalize_pre_blocks=False):
        fragment = BeautifulSoup(str(node), "html.parser")
        root = fragment.find()
        if not root:
            return ""

        if normalize_pre_blocks:
            pre_nodes = []
            if root.name == "pre":
                pre_nodes.append(root)
            pre_nodes.extend(root.find_all("pre"))

            for pre in pre_nodes:
                if Fetcher._twitter_pre_looks_like_code(pre.get_text("\n", strip=False)):
                    continue
                for code in pre.find_all("code"):
                    code.unwrap()
                pre.name = "p"

            code_nodes = []
            if root.name == "code":
                code_nodes.append(root)
            code_nodes.extend(root.find_all("code"))

            for code in code_nodes:
                if code.find_parent("pre"):
                    continue
                if not Fetcher._twitter_pre_looks_like_code(code.get_text("\n", strip=False)):
                    code.unwrap()

        for img in root.find_all("img"):
            img.decompose()

        for tag in root.find_all(True):
            style = (tag.get("style") or "").lower()
            classes = " ".join(tag.get("class") or []).lower()
            is_bold = (
                tag.name in {"strong", "b"}
                or "font-weight" in style
                and any(token in style for token in ("bold", "600", "700", "800", "900"))
                or any(token in classes for token in ("bold", "semibold", "fontbold", "font-semibold"))
            )
            is_italic = tag.name in {"em", "i"} or "font-style:italic" in style or "italic" in classes

            if tag.name == "span":
                if is_bold:
                    tag.name = "strong"
                elif is_italic:
                    tag.name = "em"
                else:
                    tag.unwrap()
                    continue

            if tag.name == "a":
                href = (tag.get("href") or "").strip()
                tag.attrs = {"href": href} if href else {}
                continue

            if tag.name in {"strong", "b"}:
                tag.name = "strong"
                tag.attrs = {}
            elif tag.name in {"em", "i"}:
                tag.name = "em"
                tag.attrs = {}
            elif tag.name == "br":
                tag.attrs = {}
            elif tag.name not in {"p", "div", "blockquote", "pre", "code", "ul", "ol", "li", "h1", "h2", "h3"}:
                tag.attrs = {}

        html = str(root)
        html = re.sub(r">\s+<", "><", html)
        return html.strip()

    @staticmethod
    def _extract_twitter_dom_sequence(best_article, normalize_pre_blocks=False):
        blocks = []
        seen_text = set()
        seen_images = set()
        consumed_nodes = set()

        for node in best_article.find_all(True):
            node_id = id(node)
            if node_id in consumed_nodes:
                continue

            if node.name == "img":
                if Fetcher._is_twitter_decorative_image(node):
                    continue
                src = (node.get("src") or "").strip()
                if src and src not in seen_images:
                    seen_images.add(src)
                    blocks.append(("image", src))
                continue

            is_text_candidate = False
            if node.get("data-testid") == "tweetText":
                is_text_candidate = True
            elif node.name in {"p", "blockquote", "pre", "li", "h1", "h2", "h3"}:
                is_text_candidate = True
            elif (
                node.name == "div"
                and node.get_text(" ", strip=True)
                and not Fetcher._twitter_node_has_block_children(node)
            ):
                is_text_candidate = True

            if not is_text_candidate:
                continue

            if node.find_parent(attrs={"data-testid": "tweetText"}) and node.get("data-testid") != "tweetText":
                continue

            text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
            if len(text) < 2:
                continue
            if text.lower() in seen_text:
                continue

            html = Fetcher._normalize_twitter_markup_fragment(node, normalize_pre_blocks=normalize_pre_blocks)
            if not html:
                continue

            seen_text.add(text.lower())
            for descendant in node.find_all(True):
                consumed_nodes.add(id(descendant))
            consumed_nodes.add(node_id)
            blocks.append(("html", html))

        return blocks

    @staticmethod
    def _extract_twitter_dom_content(html_content, source_url=None):
        """
        Preserve the main tweet/article DOM when browser fetching succeeded.
        This keeps inline styles like <strong> and embedded images instead of flattening to plain text.
        """
        if not html_content:
            return None

        soup = BeautifulSoup(html_content, "html.parser")
        target_status_id = Fetcher._extract_twitter_status_id(source_url)

        def _score_article(article):
            score = 0
            text_length = len(article.get_text(" ", strip=True))
            score += min(text_length, 1000)
            if article.find(attrs={"data-testid": "tweetText"}):
                score += 500
            if article.find("img"):
                score += 120 * len(article.find_all("img"))
            if target_status_id:
                for link in article.find_all("a", href=True):
                    if f"/status/{target_status_id}" in link.get("href", ""):
                        score += 400
                        break
            return score

        best_article = None
        articles = soup.find_all("article")
        if articles:
            best_article = max(articles, key=_score_article)

        kind = "tweet"
        if source_url and Fetcher._is_twitter_article_url(source_url):
            kind = "article"
            main = soup.find("main")
            if main and len(main.get_text(" ", strip=True)) > 80:
                best_article = main

        if not best_article:
            return None

        blocks = Fetcher._extract_twitter_dom_sequence(best_article, normalize_pre_blocks=(kind == "article"))
        if not blocks:
            return None
        text_length = sum(len(re.sub(r"<[^>]+>", "", value)) for kind, value in blocks if kind == "html")
        image_count = sum(1 for kind, _ in blocks if kind == "image")
        if text_length < 20 and image_count == 0:
            return None

        title = None
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        if not title:
            title = "X Post" if kind == "tweet" else "X Article"

        author_label = Fetcher._get_twitter_author_label(source_url=source_url)

        html_parts = [
            "<html><head><meta charset='utf-8'>",
            f"<title>{escape(title)}</title>",
            "</head><body><article>",
        ]
        for kind_name, value in blocks:
            if kind_name == "image":
                html_parts.append(f"<p><img src='{escape(value)}' alt='tweet media'></p>")
            else:
                html_parts.append(value)
        html_parts.append("</article></body></html>")
        return Fetcher._tag_twitter_html_content("".join(html_parts), kind, author=author_label)

    @staticmethod
    def _convert_twitter_cli_json_to_html(payload, source_url):
        """Convert twitter-cli structured output to compact HTML for downstream extraction."""
        if not isinstance(payload, dict):
            return None

        if payload.get("ok") is False:
            error = payload.get("error") or {}
            logger.info(
                "twitter-cli returned structured error: %s",
                error.get("code") or error.get("message") or "unknown_error",
            )
            return None

        data = payload.get("data", payload)
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict):
            return None

        article_title = data.get("articleTitle") or data.get("article_title") or data.get("title") or ""
        author_name, screen_name = Fetcher._extract_twitter_author_info(data)
        author_label = Fetcher._get_twitter_author_label(author_name, screen_name, source_url=source_url)
        kind = "article" if str(article_title).strip() else "tweet"
        title_base = str(article_title).strip() or author_name or (f"@{screen_name}" if screen_name else "X Post")
        doc_title = title_base if kind == "article" else f"{title_base} - X Post"

        lines = []
        for key in ("articleText", "full_text", "fullText", "text", "raw_text", "rawText"):
            lines.extend(Fetcher._extract_twitter_rich_text_html(data.get(key)))
            if lines:
                break
        if not lines:
            lines = Fetcher._extract_twitter_rich_text_html(data)
        if not lines:
            lines = [escape(line) for line in Fetcher._extract_twitter_text_lines(data)]
        if not lines:
            return None

        html_parts = [
            "<html><head><meta charset='utf-8'>",
            f"<title>{escape(doc_title)}</title>",
            f"<meta name='surf-twitter-kind' content='{escape(kind)}'>",
            "</head><body><article>",
            f"<h1>{escape(title_base)}</h1>",
        ]

        for line in lines:
            html_parts.append(f"<p>{line}</p>")

        for media_url in Fetcher._extract_twitter_media_urls(data):
            html_parts.append(f"<p><img src='{escape(media_url)}' alt='tweet media'></p>")

        html_parts.append("</article></body></html>")
        return Fetcher._tag_twitter_html_content("".join(html_parts), kind, author=author_label)

    @staticmethod
    def _repair_twitter_cli_mojibake_text(text):
        """Repair UTF-8 text that arrived through a Windows single-byte decode path."""
        if not isinstance(text, str) or not text:
            return text
        if not any(marker in text for marker in ("Ã", "Â", "â", "å", "æ", "ç", "ðŸ", "ï")):
            return text
        try:
            repaired = text.encode("cp1252").decode("utf-8")
        except UnicodeError:
            try:
                repaired = text.encode("cp1252", errors="ignore").decode("utf-8")
            except UnicodeError:
                return text
        original_score = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
        repaired_score = sum(1 for char in repaired if "\u4e00" <= char <= "\u9fff")
        if repaired_score > original_score:
            return repaired
        if "ðŸ" in text and "ðŸ" not in repaired:
            return repaired
        return text

    @staticmethod
    def _repair_twitter_cli_mojibake_value(value):
        """Recursively repair mojibake in twitter-cli JSON payloads."""
        if isinstance(value, str):
            return Fetcher._repair_twitter_cli_mojibake_text(value)
        if isinstance(value, list):
            return [Fetcher._repair_twitter_cli_mojibake_value(item) for item in value]
        if isinstance(value, dict):
            return {
                key: Fetcher._repair_twitter_cli_mojibake_value(item)
                for key, item in value.items()
            }
        return value

    @staticmethod
    def _fetch_twitter_via_cli(
        url,
        config,
        proxy_mode_override=None,
        custom_proxy_override=None,
        cli_bin=None,
        browser=None,
        profile=None,
    ):
        """
        Fetch Twitter/X content via twitter-cli using local browser cookies.
        Uses structured JSON output and converts it into HTML for the normal pipeline.
        """
        cli_command = Fetcher._resolve_twitter_cli_command(cli_bin)
        if not cli_command:
            logger.info("uvx not found; skipping twitter-cli backend")
            return None

        req_proxies, _ = Fetcher._get_twitter_forced_proxies(config, proxy_mode_override, custom_proxy_override)
        proxy_url = None
        if req_proxies:
            proxy_url = req_proxies.get("https") or req_proxies.get("http")

        env = os.environ.copy()
        env.setdefault("UV_CACHE_DIR", os.path.join(os.getcwd(), ".uv-cache"))
        AuthHandler.apply_twitter_cookie_env(env)
        if browser:
            env["TWITTER_BROWSER"] = browser
        if profile:
            env["TWITTER_CHROME_PROFILE"] = profile
        if proxy_url:
            env["TWITTER_PROXY"] = proxy_url
        env.setdefault("PYTHONIOENCODING", "utf-8")

        commands = [
            cli_command + ["article", url, "--json"],
            cli_command + ["tweet", url, "--json", "--full-text"],
        ]

        for command in commands:
            try:
                logger.info("Trying twitter-cli backend: %s", " ".join(command))
                completed = _run_subprocess_interruptibly(
                    command,
                    timeout=60,
                    env=env,
                    check=False,
                )
            except Exception as e:
                logger.warning(f"twitter-cli execution failed: {e}")
                return None

            stdout = _decode_subprocess_output(completed.stdout)
            stderr = _decode_subprocess_output(completed.stderr).strip()
            if completed.returncode != 0:
                logger.info(
                    "twitter-cli command failed (%s): %s",
                    completed.returncode,
                    stderr or "no output",
                )
                continue

            try:
                payload = json.loads(stdout or "")
                payload = Fetcher._repair_twitter_cli_mojibake_value(payload)
            except Exception as e:
                logger.info(f"twitter-cli stdout could not be parsed as JSON: {e}")
                continue

            html = Fetcher._convert_twitter_cli_json_to_html(payload, url)
            if html:
                return html

        return None

    @staticmethod
    def _fetch_twitter_cli_tweet_json(
        url,
        config,
        proxy_mode_override=None,
        custom_proxy_override=None,
        cli_bin=None,
        browser=None,
        profile=None,
    ):
        """Fetch tweet detail JSON from twitter-cli stdout, including replies when authenticated."""
        cli_command = Fetcher._resolve_twitter_cli_command(cli_bin)
        if not cli_command:
            return None

        req_proxies, _ = Fetcher._get_twitter_forced_proxies(config, proxy_mode_override, custom_proxy_override)
        proxy_url = None
        if req_proxies:
            proxy_url = req_proxies.get("https") or req_proxies.get("http")

        env = os.environ.copy()
        env.setdefault("UV_CACHE_DIR", os.path.join(os.getcwd(), ".uv-cache"))
        env.setdefault("PYTHONIOENCODING", "utf-8")
        AuthHandler.apply_twitter_cookie_env(env)
        if browser:
            env["TWITTER_BROWSER"] = browser
        if profile:
            env["TWITTER_CHROME_PROFILE"] = profile
        if proxy_url:
            env["TWITTER_PROXY"] = proxy_url

        command = cli_command + ["tweet", url, "--max", "50", "--json", "--full-text"]
        logger.info("Trying twitter-cli thread backend: %s", " ".join(command))
        try:
            completed = _run_subprocess_interruptibly(
                command,
                timeout=60,
                env=env,
                check=False,
            )
        except Exception as e:
            logger.warning(f"twitter-cli thread execution failed: {e}")
            return None

        if completed.returncode != 0:
            logger.info(
                "twitter-cli thread command failed (%s): %s",
                completed.returncode,
                _decode_subprocess_output(completed.stderr).strip() or "no output",
            )
            return None

        try:
            payload = json.loads(_decode_subprocess_output(completed.stdout) or "")
            return Fetcher._repair_twitter_cli_mojibake_value(payload)
        except Exception as e:
            logger.info(f"twitter-cli thread stdout could not be parsed as JSON: {e}")
            return None

    @staticmethod
    def _fetch_twitter_content(
        url,
        config,
        proxy_mode_override=None,
        custom_proxy_override=None,
        fetch_thread=None,
        fetch_thread_author=None,
        backend=None,
        cli_bin=None,
        browser=None,
        profile=None,
    ):
        """Fetch Twitter/X content with selectable backend and native fallback."""
        options = Fetcher._get_twitter_backend_options(
            config,
            backend=backend,
            cli_bin=cli_bin,
            browser=browser,
            profile=profile,
        )
        backend_name = options["backend"]
        cli_first = []
        if backend_name == "cli":
            cli_first = ["cli"]
        elif backend_name == "native":
            cli_first = ["native"]
        else:
            cli_first = ["cli", "native"]

        for mode in cli_first:
            if mode == "cli":
                html = Fetcher._fetch_twitter_via_cli(
                    url,
                    config,
                    proxy_mode_override,
                    custom_proxy_override,
                    cli_bin=options["cli_bin"],
                    browser=options["browser"],
                    profile=options["profile"],
                )
            else:
                html = Fetcher._fetch_twitter_oembed(url, config, proxy_mode_override, custom_proxy_override)
            if html:
                thread_mode = Fetcher._normalize_thread_mode(fetch_thread)
                thread_author = Fetcher._normalize_thread_author(fetch_thread_author)
                if thread_mode and Fetcher._extract_twitter_status_id(url):
                    thread_items, thread_current_index = Fetcher._get_twitter_thread_items(
                        url, config, proxy_mode_override, custom_proxy_override, thread_mode, thread_author
                    )
                    if not thread_items:
                        logger.warning(
                            "Twitter/X thread expansion requested, but no thread items were available. "
                            "Login with `surf --login twitter` or provide twitter-cli cookies/tokens for more reliable thread fetching."
                        )
                    html = Fetcher._merge_thread_context_into_html(html, "twitter", thread_items, thread_current_index)
                return html
        req_proxies, _ = Fetcher._get_twitter_forced_proxies(config, proxy_mode_override, custom_proxy_override)
        fallback_html = Fetcher._fetch_twitter_status_fallbacks(url, proxies=req_proxies)
        if fallback_html:
            thread_mode = Fetcher._normalize_thread_mode(fetch_thread)
            thread_author = Fetcher._normalize_thread_author(fetch_thread_author)
            if thread_mode and Fetcher._extract_twitter_status_id(url):
                thread_items, thread_current_index = Fetcher._get_twitter_thread_items(
                    url, config, proxy_mode_override, custom_proxy_override, thread_mode, thread_author
                )
                if not thread_items:
                    logger.warning(
                        "Twitter/X thread expansion requested, but no thread items were available. "
                        "Login with `surf --login twitter` or provide twitter-cli cookies/tokens for more reliable thread fetching."
                    )
                fallback_html = Fetcher._merge_thread_context_into_html(
                    fallback_html, "twitter", thread_items, thread_current_index
                )
            return fallback_html
        return None

    @staticmethod
    def _extract_twitter_oembed_links(html_content):
        """Extract links from Twitter/X oEmbed HTML in order."""
        if not html_content:
            return []
        soup = BeautifulSoup(html_content, "html.parser")
        links = []
        for a in soup.find_all("a", href=True):
            href = a.get("href", "").strip()
            if href and href not in links:
                links.append(href)
        return links

    @staticmethod
    def _extract_twitter_status_id(url):
        """Extract tweet status ID from Twitter/X URL."""
        if not url:
            return None
        match = re.search(r"/status/(\d+)", url)
        return match.group(1) if match else None

    @staticmethod
    def _extract_twitter_article_id(url):
        """Extract Twitter/X article ID from both canonical and profile article URLs."""
        if not url:
            return None
        match = re.search(r"/(?:i/)?article/(\d+)", url)
        return match.group(1) if match else None

    @staticmethod
    def _is_twitter_article_url(url):
        """Check whether the URL points to a Twitter/X Article page."""
        return bool(Fetcher._extract_twitter_article_id(url))

    @staticmethod
    def _normalize_twitter_article_url(url):
        """
        Normalize profile-style Twitter/X Article URLs to the canonical /i/article/<id> form.
        Example: https://x.com/user/article/123 -> https://x.com/i/article/123
        """
        if not url:
            return url
        article_id = Fetcher._extract_twitter_article_id(url)
        if not article_id:
            return url
        parsed = urlparse(url)
        query = f"?{parsed.query}" if parsed.query else ""
        fragment = f"#{parsed.fragment}" if parsed.fragment else ""
        normalized = f"{parsed.scheme or 'https'}://{parsed.netloc or 'x.com'}/i/article/{article_id}{query}{fragment}"
        if normalized != url:
            logger.info(f"Normalized Twitter Article URL: {normalized}")
        return normalized

    @staticmethod
    def _is_twitter_placeholder_text(text):
        """
        Detect common X login/placeholder copy that should not be treated as article content.
        """
        if not text:
            return False

        normalized = unicodedata.normalize("NFKC", text).strip().lower()
        normalized = re.sub(r"[\u2018\u2019\u201b\u2032\u00b4`]", "'", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        normalized_no_punct = re.sub(r"[^a-z0-9\s]", "", normalized)

        primary_pairs = [
            ("don't miss what's happening", "people on x are the first to know"),
            ("dont miss what's happening", "people on x are the first to know"),
            ("dont miss whats happening", "people on x are the first to know"),
            ("join x today", "already have an account"),
            ("sign up", "log in"),
        ]
        for left, right in primary_pairs:
            if left in normalized and right in normalized:
                return True
            left_no_punct = re.sub(r"[^a-z0-9\s]", "", left)
            right_no_punct = re.sub(r"[^a-z0-9\s]", "", right)
            if left_no_punct in normalized_no_punct and right_no_punct in normalized_no_punct:
                return True

        extra_patterns = [
            r"\bpeople on x are the first to know\b",
            r"\bdon'?t miss what'?s happening\b",
            r"\bjoin x today\b",
        ]
        for pattern in extra_patterns:
            if re.search(pattern, normalized):
                return True
            normalized_no_apostrophe = normalized_no_punct
            if re.search(
                pattern.replace("'", "").replace("?", ""),
                normalized_no_apostrophe,
            ):
                return True

        return False

    @staticmethod
    def _is_twitter_placeholder_content(html_content):
        """Detect whether an HTML snippet is mostly X login/placeholder content."""
        if not html_content:
            return True
        raw = html_content.lower()
        raw = raw.replace("\u2019", "'").replace("&#39;", "'").replace("&rsquo;", "'").replace("&apos;", "'")
        raw = re.sub(r"\s+", " ", raw)
        if re.search(r"don'?t miss what'?s happening", raw) and "people on x are the first to know" in raw:
            return True
        soup = BeautifulSoup(html_content, "html.parser")
        text = soup.get_text(" ", strip=True)
        return Fetcher._is_twitter_placeholder_text(text)

    @staticmethod
    def _resolve_url_with_redirects(url, proxies=None, timeout=30):
        """Resolve short links (for example t.co) to their final destination URL."""
        if not url:
            return None
        header_variants = [
            {"User-Agent": "Mozilla/5.0"},
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
            },
        ]
        last_resolved = None
        for headers in header_variants:
            try:
                response = _requests_get_interruptibly(
                    url, headers=headers, proxies=proxies, timeout=timeout, allow_redirects=True
                )
                resolved = response.url or url
                last_resolved = resolved
                if resolved and resolved != url:
                    return resolved
            except Exception as e:
                logger.debug(f"Failed to resolve redirect URL {url}: {e}")
        return last_resolved

    @staticmethod
    def _is_common_short_url(url):
        if not url:
            return False
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        if parsed.scheme.lower() not in {"http", "https"}:
            return False
        host = (parsed.netloc or "").split("@")[-1].split(":")[0].lower()
        if host.startswith("www."):
            host = host[4:]
        return host in Fetcher._COMMON_SHORT_URL_HOSTS

    @staticmethod
    def _resolve_common_short_url(
        url,
        config,
        proxy_mode_override=None,
        custom_proxy_override=None,
        timeout=20,
    ):
        normalized_douban = Fetcher._normalize_douban_url(url)
        if normalized_douban != url:
            logger.info(f"Normalized Douban URL for processing: {url} -> {normalized_douban}")
            return normalized_douban

        if not Fetcher._is_common_short_url(url):
            return url

        req_proxies, _ = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)
        resolved = Fetcher._resolve_url_with_redirects(url, proxies=req_proxies, timeout=timeout)
        if resolved and resolved != url:
            logger.info(f"Resolved short URL for processing: {url} -> {resolved}")
            return resolved
        return url

    @staticmethod
    def save_wayback_snapshot(
        url,
        config=None,
        proxy_mode_override=None,
        custom_proxy_override=None,
        timeout=60,
    ):
        if not url:
            return None
        parsed = urlparse(url)
        if parsed.scheme.lower() not in {"http", "https"}:
            return None

        proxies = None
        if config is not None:
            proxies, _ = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)

        save_url = f"https://web.archive.org/save/{quote(url, safe=':/')}"
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            logger.info(f"Saving URL to Wayback Machine: {url}")
            response = _requests_get_interruptibly(
                save_url,
                headers=headers,
                proxies=proxies,
                timeout=timeout,
                allow_redirects=False,
            )
        except Exception as e:
            logger.warning(f"Wayback snapshot request failed for {url}: {e}")
            return None

        if response.status_code >= 400:
            logger.warning(
                "Wayback snapshot request failed for %s: HTTP %s",
                url,
                response.status_code,
            )
            return None

        archive_ref = (
            response.headers.get("Content-Location")
            or response.headers.get("content-location")
            or response.headers.get("Location")
            or response.headers.get("location")
            or ""
        ).strip()
        if archive_ref:
            archive_url = urljoin("https://web.archive.org/", archive_ref)
            logger.info(f"Wayback snapshot URL: {archive_url}")
            return archive_url

        logger.info(f"Wayback snapshot submitted without archive location: {save_url}")
        return save_url

    @staticmethod
    def _extract_twitter_article_target(url, oembed_html, proxies=None):
        """
        Try to resolve the actual Twitter/X article URL from oEmbed links.
        Falls back to the original URL.
        """
        links = Fetcher._extract_twitter_oembed_links(oembed_html)
        for link in links:
            if "t.co/" not in link:
                continue
            resolved = Fetcher._resolve_url_with_redirects(link, proxies=proxies)
            if resolved and Fetcher._is_twitter_article_url(resolved):
                normalized = Fetcher._normalize_twitter_article_url(resolved)
                logger.info(f"Resolved Twitter Article URL: {normalized}")
                return normalized
        return Fetcher._normalize_twitter_article_url(url)

    @staticmethod
    def _fetch_twitter_syndication_html(url, proxies=None):
        """
        Fallback extraction via Twitter syndication endpoint for status pages.
        Returns a compact HTML document when successful, otherwise None.
        """
        status_id = Fetcher._extract_twitter_status_id(url)
        if not status_id:
            return None

        api_url = f"https://cdn.syndication.twimg.com/tweet-result?id={status_id}&lang=en"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }

        try:
            response = _requests_get_interruptibly(api_url, headers=headers, proxies=proxies, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.debug(f"Syndication fallback failed for {url}: {e}")
            return None

        text = (data.get("text") or "").strip()
        if not text or Fetcher._is_twitter_placeholder_text(text):
            return None

        user = data.get("user") or {}
        user_name = (user.get("name") or "").strip()
        screen_name = (user.get("screen_name") or "").strip()
        author_label = Fetcher._get_twitter_author_label(user_name, screen_name, source_url=url)
        title_base = user_name or (f"@{screen_name}" if screen_name else "X Post")
        title = f"{title_base} - X Post"

        html_parts = [
            "<html><head><meta charset='utf-8'>",
            f"<title>{escape(title)}</title>",
            "</head><body><article>",
            f"<h1>{escape(title_base)}</h1>",
        ]

        for para in [p.strip() for p in text.splitlines() if p.strip()]:
            html_parts.append(f"<p>{escape(para)}</p>")

        media = data.get("mediaDetails") or []
        for item in media:
            if not isinstance(item, dict):
                continue
            media_url = (item.get("media_url_https") or item.get("media_url") or "").strip()
            if media_url:
                html_parts.append(f"<p><img src='{escape(media_url)}' alt='tweet media'></p>")

        html_parts.append("</article></body></html>")
        return Fetcher._tag_twitter_html_content("".join(html_parts), "tweet", author=author_label)

    @staticmethod
    def _fetch_twitter_fxapi_html(url, proxies=None):
        """
        Fallback extraction via api.fxtwitter.com when X login wall blocks browser/oEmbed.
        Supports tweet text and article blocks.
        """
        status_id = Fetcher._extract_twitter_status_id(url)
        if not status_id:
            return None

        api_url = f"https://api.fxtwitter.com/status/{status_id}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }
        try:
            response = _requests_get_interruptibly(api_url, headers=headers, proxies=proxies, timeout=30)
            response.raise_for_status()
            payload = response.json()
        except Exception as e:
            logger.debug(f"fxTwitter fallback failed for {url}: {e}")
            return None

        tweet = payload.get("tweet") if isinstance(payload, dict) else None
        if not isinstance(tweet, dict):
            return None

        author = tweet.get("author") or {}
        author_name = (author.get("name") or "").strip()
        screen_name = (author.get("screen_name") or "").strip()
        author_label = Fetcher._get_twitter_author_label(author_name, screen_name, source_url=url)
        title_base = author_name or (f"@{screen_name}" if screen_name else "X Post")

        article = tweet.get("article") or {}
        article_title = (article.get("title") or "").strip()
        doc_title = article_title or f"{title_base} - X Post"
        kind = "article" if article_title else "tweet"

        if kind == "article":
            Fetcher._log_fx_article_structure(article)

        body_blocks = Fetcher._extract_fx_block_sequence(article, tweet)
        if not body_blocks:
            return None

        html_parts = [
            "<html><head><meta charset='utf-8'>",
            f"<title>{escape(doc_title)}</title>",
            "</head><body><article>",
            f"<h1>{escape(doc_title)}</h1>",
        ]

        html_parts.extend(body_blocks)

        html_parts.append("</article></body></html>")
        return Fetcher._tag_twitter_html_content("".join(html_parts), kind, author=author_label)

    @staticmethod
    def _fetch_twitter_status_fallbacks(url, proxies=None, article_target_url=None):
        """
        Try status-id based fallback endpoints that do not require loading x.com directly.
        Prefer syndication for tweets, then fxTwitter for both tweets/articles.
        """
        syndication_html = Fetcher._fetch_twitter_syndication_html(url, proxies=proxies)
        if syndication_html:
            logger.info("Using Twitter/X syndication fallback content")
            return syndication_html

        target_url = article_target_url or url
        fxapi_html = Fetcher._fetch_twitter_fxapi_html(target_url, proxies=proxies)
        if fxapi_html:
            logger.info("Using fxTwitter API fallback content")
            return fxapi_html

        return None

    @staticmethod
    def _extract_twitter_structured_content(html_content, source_url=None):
        """
        Extract meaningful Twitter/X text from structured metadata (JSON-LD/meta tags).
        Returns a compact HTML document when successful, otherwise None.
        """
        if not html_content:
            return None

        soup = BeautifulSoup(html_content, "html.parser")
        candidates = []
        seen = set()

        def add_candidate(text):
            if not text:
                return
            value = re.sub(r"\s+", " ", text).strip()
            if len(value) < 30:
                return
            if Fetcher._is_twitter_placeholder_text(value):
                return
            key = value.lower()
            if key in seen:
                return
            seen.add(key)
            candidates.append(value)

        def collect_from_obj(obj):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    k = str(key).lower()
                    if k in {"articlebody", "description", "text", "headline", "name"}:
                        if isinstance(value, str):
                            add_candidate(value)
                    collect_from_obj(value)
            elif isinstance(obj, list):
                for item in obj:
                    collect_from_obj(item)

        # JSON-LD often contains articleBody/text for tweet/article pages.
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                data = json.loads(raw)
                collect_from_obj(data)
            except Exception:
                continue

        # Meta tags are weaker but still useful if DOM content is blocked.
        meta_selectors = [
            ("meta", {"property": "og:description"}),
            ("meta", {"name": "description"}),
            ("meta", {"name": "twitter:description"}),
        ]
        for tag_name, attrs in meta_selectors:
            tag = soup.find(tag_name, attrs=attrs)
            if tag:
                add_candidate(tag.get("content"))

        title = None
        title_tag = soup.find("meta", attrs={"property": "og:title"})
        if title_tag and title_tag.get("content"):
            title = title_tag.get("content").strip()
        elif soup.title and soup.title.string:
            title = soup.title.string.strip()

        if title and Fetcher._is_twitter_placeholder_text(title):
            title = None

        if not candidates:
            return None

        best = max(candidates, key=len)
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", best) if p.strip()]
        if not paragraphs:
            paragraphs = [best]

        html_parts = [
            "<html><head><meta charset='utf-8'>",
            f"<title>{escape(title or 'X Content')}</title>",
            "</head><body><article>",
        ]
        if title:
            html_parts.append(f"<h1>{escape(title)}</h1>")
        for para in paragraphs:
            html_parts.append(f"<p>{escape(para)}</p>")
        html_parts.append("</article></body></html>")
        author_label = Fetcher._get_twitter_author_label(source_url=source_url)
        return Fetcher._tag_twitter_html_content("".join(html_parts), "tweet", author=author_label)

    @staticmethod
    def _get_twitter_forced_proxies(config, proxy_mode_override=None, custom_proxy_override=None):
        """
        Get preferred proxies for Twitter/X.
        Priority: explicit mode > env vars > INI > WinHTTP.
        If no explicit proxy is requested, callers may still retry without proxy.

        Returns:
            tuple: (req_proxies, pw_proxy)
        """
        req_proxies, pw_proxy = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)
        if req_proxies or pw_proxy:
            return req_proxies, pw_proxy

        logger.info("Twitter: No proxy resolved; using direct connection.")
        return None, None

    @staticmethod
    def _is_proxy_related_error(error):
        """Return True when an error likely comes from proxy negotiation or routing."""
        message = str(error).lower()
        proxy_markers = [
            "err_proxy_connection_failed",
            "proxyerror",
            "proxy error",
            "proxy connection",
            "cannot connect to proxy",
            "failed to establish a new connection",
            "407 proxy",
            "tunnel error",
            "socks",
            "ssleoferror",
            "unexpected_eof_while_reading",
            "eof occurred in violation of protocol",
        ]
        return any(marker in message for marker in proxy_markers)

    @staticmethod
    def _should_retry_without_proxy(proxy_mode_override, proxy_value, error):
        """
        Allow automatic direct-connection fallback only for implicit proxy usage.
        Explicit `-x win` / `-x custom` should still be treated as user intent.
        """
        if proxy_mode_override is not None:
            return False
        if not proxy_value:
            return False
        return Fetcher._is_proxy_related_error(error)

    @staticmethod
    def _should_retry_twitter_without_proxy(proxy_mode_override, proxy_value, error):
        """
        Allow automatic direct-connection fallback only for implicit Twitter proxy usage.
        Explicit `-x win` / `-x custom` should still be treated as user intent.
        """
        return Fetcher._should_retry_without_proxy(proxy_mode_override, proxy_value, error)

    @staticmethod
    def _clean_twitter_article_content(html_content):
        """
        Clean Twitter/X article HTML by removing unrelated UI elements.
        Keeps: article text, images, author name, date
        Removes: avatars, view counts, reply counts, analytics, ads, etc.

        Args:
            html_content: Raw HTML from Twitter/X

        Returns:
            Cleaned HTML content
        """
        if not html_content:
            return html_content

        soup = BeautifulSoup(html_content, "html.parser")

        # Remove by CSS selectors (common Twitter/X UI elements)
        selectors_to_remove = [
            # Analytics and metrics
            "[data-testid='app-text-transition-container']",  # View counts, metrics with animated numbers
            "[data-testid='likeCount']",
            "[data-testid='replyCount']",
            "[data-testid='retweetCount']",
            "[data-testid='analyticsButton']",
            # Action buttons
            "[data-testid='like']",
            "[data-testid='reply']",
            "[data-testid='retweet']",
            "[data-testid='share']",
            "[data-testid='bookmark']",
            # Avatars and profile images
            "[data-testid='Tweet-User-Avatar']",
            "[data-testid='UserAvatar']",
            "img[src*='profile_images']",
            # Promotional elements
            "[data-testid='premium-upgrade-button']",
            "[data-testid='subscribe-button']",
            "a[href*='premium']",
            "a[href*='subscribe']",
            # General UI elements
            "[role='menu']",
            "[role='dialog']",
            "[aria-label='Analytics']",
            # Common class-based selectors (Twitter uses obfuscated classes, but these patterns work)
            "div[class*='css-1dbjc4n r-1']",  # Many UI containers share this pattern
        ]

        removed_count = 0
        for selector in selectors_to_remove:
            try:
                elements = soup.select(selector)
                for el in elements:
                    el.decompose()
                    removed_count += 1
            except Exception as e:
                logger.debug(f"Error removing elements with selector {selector}: {e}")

        # Remove by text content patterns
        text_patterns_to_remove = [
            "Views",
            "view",
            "analytics",
            "promoted",
            "sponsored",
            # Article footer CTA (Call to Action)
            "想发布你自己的文章",  # Chinese: "Want to publish your own article?"
            "Want to publish your own article",  # English version
            "发布你自己的文章",  # Shorter Chinese version
            "publish your own article",  # English keywords
        ]

        for text in text_patterns_to_remove:
            try:
                for el in soup.find_all(string=re.compile(text, re.IGNORECASE)):
                    parent = el.parent
                    if parent and parent.name not in ["script", "style"]:
                        parent.decompose()
                        removed_count += 1
            except Exception as e:
                logger.debug(f"Error removing text pattern '{text}': {e}")

        # Remove empty containers that might remain
        for div in soup.find_all("div"):
            if not div.get_text(strip=True) and not div.find("img"):
                # Check if it has any meaningful content
                if len(div.find_all(recursive=False)) == 0:
                    div.decompose()
                    removed_count += 1

        if removed_count > 0:
            logger.info(f"Twitter Article: Removed {removed_count} UI elements")

        return str(soup)

    @staticmethod
    def _clean_xiaohongshu_content(html_content):
        """
        Clean Xiaohongshu content by adding proper styling.
        Keeps: note title, content, images
        Adds styling for avatar images (60x60 size)
        Adds meta tag to fix 403 image errors

        Args:
            html_content: Raw HTML from Xiaohongshu

        Returns:
            Cleaned HTML content with proper styling
        """
        if not html_content:
            return html_content

        soup = BeautifulSoup(html_content, "html.parser")

        # Add styling for avatar images (60x60 size)
        # Avatar images are from sns-avatar-qc.xhscdn.com
        for img in soup.find_all("img", src=re.compile(r"sns-avatar-qc\.xhscdn\.com", re.IGNORECASE)):
            img["style"] = "width: 60px; height: 60px; object-fit: cover; border-radius: 50%;"
            logger.debug(f"Applied 60x60 styling to avatar: {img.get('src', '')}")

        # Fix images with 403 errors by adding proper referrer meta tag
        # Some xhscdn images require a referrer
        meta_referrer = soup.new_tag("meta")
        meta_referrer["name"] = "referrer"
        meta_referrer["content"] = "no-referrer-when-downgrade"

        head = soup.find("head")
        if head:
            head.insert(0, meta_referrer)
        else:
            head = soup.new_tag("head")
            head.insert(0, meta_referrer)
            soup.insert(0, head)

        return str(soup)

    @staticmethod
    def _get_zhihu_headers(referer_url):
        """Build browser-like headers for Zhihu web/API requests."""
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": referer_url,
            "Origin": "https://www.zhihu.com",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "X-Requested-With": "fetch",
        }

    @staticmethod
    def _get_zhihu_html_headers(referer_url):
        """Build browser-like headers for fetching Zhihu page HTML (not API)."""
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.zhihu.com/",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-site",
            "sec-fetch-user": "?1",
            "Upgrade-Insecure-Requests": "1",
            "Connection": "keep-alive",
            "Cache-Control": "max-age=0",
        }

    @staticmethod
    def _extract_zhihu_answer_id(url):
        """Extract answer id from Zhihu answer URL."""
        match = re.search(r"/question/\d+/answer/(\d+)", url)
        return match.group(1) if match else None

    @staticmethod
    def _extract_zhihu_article_id(url):
        """Extract article id from Zhihu column URL."""
        patterns = [
            r"zhuanlan\.zhihu\.com/p/(\d+)",
            r"www\.zhihu\.com/p/(\d+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _format_unix_timestamp(timestamp_value):
        """Format a unix timestamp into a readable local datetime string."""
        if not timestamp_value:
            return ""
        try:
            return datetime.fromtimestamp(int(timestamp_value)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return ""

    @staticmethod
    def _clean_zhihu_content_html(content_html):
        """Remove Zhihu internal search jump links while preserving visible text."""
        content_html = (content_html or "").strip()
        if not content_html:
            return ""

        soup = BeautifulSoup(content_html, "html.parser")
        for anchor in soup.find_all("a", href=True):
            href = (anchor.get("href") or "").strip()
            if re.match(r"^https://zhida\.zhihu\.com/search(?:[/?#]|$)", href, re.IGNORECASE):
                anchor.unwrap()
        return str(soup)

    @staticmethod
    def _build_zhihu_html(
        title,
        content_html,
        source_url,
        author_name="",
        question_title="",
        created_time="",
        updated_time="",
        voteup_count=None,
        comment_count=None,
    ):
        """Build a compact HTML document from Zhihu structured content."""
        display_title = (title or question_title or "Zhihu").strip()
        content_html = Fetcher._clean_zhihu_content_html(content_html)
        if not content_html:
            return None

        head_parts = [
            "<html><head><meta charset='utf-8'>",
            "<meta name='referrer' content='no-referrer-when-downgrade'>",
            "<meta name='surf-source-site' content='zhihu'>",
        ]
        if author_name and author_name.strip():
            head_parts.append(f"<meta name='surf-author' content='{escape(author_name.strip())}'>")
        if created_time:
            head_parts.append(f"<meta name='surf-created' content='{escape(created_time)}'>")
        if updated_time and updated_time != created_time:
            head_parts.append(f"<meta name='surf-updated' content='{escape(updated_time)}'>")
        head_parts.append(f"<title>{escape(display_title)}</title>")
        head_parts.append("</head><body><article>")

        metadata_parts = []
        if question_title and question_title.strip() and question_title.strip() != display_title:
            metadata_parts.append(f"<p>Question: {escape(question_title.strip())}</p>")

        return "".join(
            [
                "".join(head_parts),
                f"<h1>{escape(display_title)}</h1>",
                "".join(metadata_parts),
                content_html,
                "</article></body></html>",
            ]
        )

    @staticmethod
    def _extract_zhihu_dom_content(html_content, source_url):
        """Fallback extraction from rendered Zhihu DOM."""
        if not html_content:
            return None

        soup = BeautifulSoup(html_content, "html.parser")
        page_text = soup.get_text(" ", strip=True)
        if "安全验证" in page_text or "验证你是否是真人" in page_text or "captcha" in page_text.lower():
            logger.warning("Zhihu browser fallback returned a security verification page")
            return None

        title = ""
        title_selectors = [
            "h1.QuestionHeader-title",
            "h1.Post-Title",
            "h1",
        ]
        for selector in title_selectors:
            node = soup.select_one(selector)
            if node and node.get_text(" ", strip=True):
                title = node.get_text(" ", strip=True)
                break
        if not title and soup.title and soup.title.string:
            title = soup.title.string.strip()

        author_name = ""
        for selector in [".AuthorInfo-name", ".Post-Author .UserLink-link", ".UserLink-link"]:
            node = soup.select_one(selector)
            if node and node.get_text(" ", strip=True):
                author_name = node.get_text(" ", strip=True)
                break

        content_node = None
        candidate_selectors = [
            ".AnswerItem .RichContent .RichContent-inner",
            ".RichContent .RichContent-inner",
            ".Post-RichTextContainer .RichText",
            ".Post-RichText",
            ".RichText",
            "article",
        ]
        for selector in candidate_selectors:
            nodes = soup.select(selector)
            if not nodes:
                continue
            content_node = max(nodes, key=lambda node: len(node.get_text(" ", strip=True)))
            if content_node and len(content_node.get_text(" ", strip=True)) > 80:
                break

        if not content_node:
            return None

        content_html = "".join(str(child) for child in content_node.contents).strip() or str(content_node)
        return Fetcher._build_zhihu_html(
            title=title,
            content_html=content_html,
            source_url=source_url,
            author_name=author_name,
        )

    @staticmethod
    def _fetch_zhihu_alt_page(answer_id=None, article_id=None, proxies=None, cookie_header=None):
        """
        Fetch public Zhihu mirror pages as a non-Playwright fallback.
        These pages can be more permissive than the canonical answer/article URLs.
        """
        candidate_urls = []
        if answer_id:
            candidate_urls.extend(
                [
                    f"https://en.zhihu.com/answer/{answer_id}",
                    f"https://www.zhihu.com/answer/{answer_id}",
                ]
            )
        if article_id:
            candidate_urls.extend(
                [
                    f"https://en.zhihu.com/p/{article_id}",
                    f"https://www.zhihu.com/p/{article_id}",
                ]
            )

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if cookie_header:
            headers["Cookie"] = cookie_header

        for candidate_url in candidate_urls:
            try:
                response = _requests_get_interruptibly(candidate_url, headers=headers, proxies=proxies, timeout=20)
                response.raise_for_status()
                decoded_text = Fetcher._decode_response_text(response)
                if len(decoded_text) < 500:
                    continue
                extracted = Fetcher._extract_zhihu_dom_content(decoded_text, candidate_url)
                if extracted:
                    return extracted
            except Exception as e:
                logger.info(f"Zhihu alt page fetch failed for {candidate_url}: {e}")

        return None

    @staticmethod
    def _fetch_zhihu_page_html(url, proxies=None, cookie_header=None):
        """Fetch Zhihu page HTML directly, trying desktop and mobile URL variants."""
        article_id = Fetcher._extract_zhihu_article_id(url)
        answer_id = Fetcher._extract_zhihu_answer_id(url)

        # Build candidate URLs: desktop first, then mobile (mobile often has weaker anti-bot)
        candidate_urls = []
        if article_id:
            candidate_urls.extend([
                ("desktop", url),
                ("desktop", f"https://www.zhihu.com/p/{article_id}"),
                ("mobile", f"https://m.zhihu.com/article/{article_id}"),
            ])
        if answer_id:
            candidate_urls.extend([
                ("desktop", url),
                ("desktop", f"https://www.zhihu.com/answer/{answer_id}"),
                ("mobile", f"https://m.zhihu.com/answer/{answer_id}"),
            ])
        if not article_id and not answer_id:
            candidate_urls.append(("desktop", url))

        # Deduplicate by URL while preserving order
        seen = set()
        unique_candidates = []
        for mode, u in candidate_urls:
            if u not in seen:
                seen.add(u)
                unique_candidates.append((mode, u))

        mobile_ua = (
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
        )

        # Use a session so cookies from the homepage carry over
        session = requests.Session()
        if proxies:
            session.proxies.update(proxies)

        try:
            # Visit homepage first to acquire anti-bot cookies (z_c0, _zap, etc.)
            if not cookie_header:
                home_headers = Fetcher._get_zhihu_html_headers("https://www.zhihu.com/")
                try:
                    home_resp = session.get("https://www.zhihu.com/", headers=home_headers, timeout=15)
                    logger.debug("Zhihu homepage pre-visit: %d, cookies: %s", home_resp.status_code, list(session.cookies.keys()))
                except Exception as e:
                    logger.debug("Zhihu homepage pre-visit failed: %s", e)

            for mode, candidate_url in unique_candidates:
                try:
                    if mode == "mobile":
                        headers = Fetcher._get_zhihu_html_headers(candidate_url)
                        headers["User-Agent"] = mobile_ua
                    else:
                        headers = Fetcher._get_zhihu_html_headers(candidate_url)
                    if cookie_header:
                        headers["Cookie"] = cookie_header
                    response = session.get(candidate_url, headers=headers, timeout=30)
                    response.raise_for_status()
                    text = Fetcher._decode_response_text(response)
                    if len(text) > 500:
                        logger.info("Zhihu direct HTML fetch succeeded for %s (%d bytes)", candidate_url, len(text))
                        return text
                except Exception as e:
                    logger.debug("Zhihu direct HTML fetch failed for %s: %s", candidate_url, e)
        finally:
            session.close()
        return None

    @staticmethod
    def _extract_zhihu_initial_state(html_content, source_url):
        """Try to extract article content from Zhihu's embedded initial state JSON."""
        try:
            import json as _json

            # Zhihu embeds initial data in <script id="js-initialData"> or similar
            match = re.search(
                r'<script\s+id="js-initialData"\s*>(.*?)</script>',
                html_content,
                re.DOTALL,
            )
            if not match:
                return None

            data = _json.loads(match.group(1))

            # Try article path (zhuanlan)
            article_data = None
            for key in data:
                if isinstance(data[key], dict):
                    # Common patterns: initialState.entities.articles
                    entities = data[key].get("entities", {})
                    articles = entities.get("articles", {})
                    if articles:
                        article_data = next(iter(articles.values()))
                        break

            if article_data and article_data.get("content"):
                return Fetcher._build_zhihu_html(
                    title=article_data.get("title", ""),
                    content_html=article_data.get("content", ""),
                    source_url=source_url,
                    author_name=(article_data.get("author") or {}).get("name", ""),
                    created_time=Fetcher._format_unix_timestamp(article_data.get("created")),
                    updated_time=Fetcher._format_unix_timestamp(article_data.get("updated")),
                    voteup_count=article_data.get("voteupCount"),
                    comment_count=article_data.get("commentCount"),
                )

            # Try answer path
            answer_data = None
            for key in data:
                if isinstance(data[key], dict):
                    entities = data[key].get("entities", {})
                    answers = entities.get("answers", {})
                    if answers:
                        answer_data = next(iter(answers.values()))
                        break

            if answer_data and answer_data.get("content"):
                return Fetcher._build_zhihu_html(
                    title=(answer_data.get("question") or {}).get("title", ""),
                    question_title=(answer_data.get("question") or {}).get("title", ""),
                    content_html=answer_data.get("content", ""),
                    source_url=source_url,
                    author_name=(answer_data.get("author") or {}).get("name", ""),
                    created_time=Fetcher._format_unix_timestamp(answer_data.get("createdTime")),
                    updated_time=Fetcher._format_unix_timestamp(answer_data.get("updatedTime")),
                    voteup_count=answer_data.get("voteupCount"),
                    comment_count=answer_data.get("commentCount"),
                )
        except Exception as e:
            logger.debug("Zhihu initial state extraction failed: %s", e)
        return None

    @staticmethod
    def _fetch_zhihu_content(url, config, proxy_mode_override=None, custom_proxy_override=None):
        """
        Fetch Zhihu answer/article content via public API first.
        Falls back to browser-rendered DOM extraction when the API is blocked.
        """
        if proxy_mode_override is None:
            proxy_mode_override = "no"
            custom_proxy_override = None
            logger.info("zhihu: Forcing 'no proxy' inside special handler")

        req_proxies, _ = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)
        headers = Fetcher._get_zhihu_headers(url)
        cookie_header = AuthHandler.cookie_header_for_zhihu()
        if cookie_header:
            headers = {**headers, "Cookie": cookie_header}
            logger.info("zhihu: Using saved login cookies for HTTP/API requests")

        answer_id = Fetcher._extract_zhihu_answer_id(url)
        article_id = Fetcher._extract_zhihu_article_id(url)

        try:
            if answer_id:
                api_url = (
                    "https://www.zhihu.com/api/v4/answers/"
                    f"{answer_id}?include=content,comment_count,voteup_count,"
                    "created_time,updated_time,author.headline,question.title"
                )
                response = _requests_get_interruptibly(api_url, headers=headers, proxies=req_proxies, timeout=30)
                response.raise_for_status()
                payload = response.json()
                html_content = Fetcher._build_zhihu_html(
                    title=(payload.get("question") or {}).get("title", ""),
                    question_title=(payload.get("question") or {}).get("title", ""),
                    content_html=payload.get("content", ""),
                    source_url=url,
                    author_name=(payload.get("author") or {}).get("name", ""),
                    created_time=Fetcher._format_unix_timestamp(payload.get("created_time")),
                    updated_time=Fetcher._format_unix_timestamp(payload.get("updated_time")),
                    voteup_count=payload.get("voteup_count"),
                    comment_count=payload.get("comment_count"),
                )
                if html_content:
                    return html_content

            if article_id:
                api_url = (
                    "https://www.zhihu.com/api/v4/articles/"
                    f"{article_id}?include=title,content,comment_count,voteup_count,"
                    "created,updated,author.name"
                )
                response = _requests_get_interruptibly(api_url, headers=headers, proxies=req_proxies, timeout=30)
                response.raise_for_status()
                payload = response.json()
                html_content = Fetcher._build_zhihu_html(
                    title=payload.get("title", ""),
                    content_html=payload.get("content", ""),
                    source_url=url,
                    author_name=(payload.get("author") or {}).get("name", ""),
                    created_time=Fetcher._format_unix_timestamp(payload.get("created") or payload.get("created_time")),
                    updated_time=Fetcher._format_unix_timestamp(payload.get("updated") or payload.get("updated_time")),
                    voteup_count=payload.get("voteup_count"),
                    comment_count=payload.get("comment_count"),
                )
                if html_content:
                    return html_content
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else None
            if code in (401, 403):
                tip = ""
                if not cookie_header:
                    tip = " Run `surf --login zhihu` to save cookies for the API."
                logger.info(
                    "Zhihu API returned HTTP %s; trying mirror page / browser.%s",
                    code,
                    tip,
                )
            else:
                logger.warning(f"Zhihu API fetch failed: {e}")
        except Exception as e:
            logger.warning(f"Zhihu API fetch failed: {e}")

        alt_html = Fetcher._fetch_zhihu_alt_page(
            answer_id=answer_id,
            article_id=article_id,
            proxies=req_proxies,
            cookie_header=cookie_header,
        )
        if alt_html:
            return alt_html

        # Try direct HTML fetch with full browser-like headers (faster than Playwright)
        direct_html = Fetcher._fetch_zhihu_page_html(url, proxies=req_proxies, cookie_header=cookie_header)
        if direct_html:
            extracted = Fetcher._extract_zhihu_dom_content(direct_html, url)
            if extracted:
                return extracted
            # DOM extraction failed; try extracting embedded initial state JSON
            json_extracted = Fetcher._extract_zhihu_initial_state(direct_html, url)
            if json_extracted:
                return json_extracted

        try:
            browser_html = Fetcher.fetch_with_browser(url, config, proxy_mode_override, custom_proxy_override)
            return Fetcher._extract_zhihu_dom_content(browser_html, url)
        except Exception as e:
            logger.warning(f"Zhihu browser fallback failed: {e}")
            return None

    @staticmethod
    def _fetch_twitter_oembed(url, config, proxy_mode_override=None, custom_proxy_override=None):
        """
        Fetch Twitter/X tweet using the official oEmbed API.
        Returns HTML content from the oEmbed response.
        For Twitter Articles (long-form content), oEmbed only returns a link,
        so we detect this case and fall back to browser fetching.

        Args:
            url: Twitter/X URL (tweet or profile URL)
            config: Config object
            proxy_mode_override: Override proxy_mode from command line
            custom_proxy_override: Override custom_proxy from command line

        Returns:
            HTML content string from oEmbed API, or None if oEmbed failed/article detected
        """
        article_url = Fetcher._normalize_twitter_article_url(url)
        if Fetcher._is_twitter_article_url(url):
            logger.info("Direct Twitter Article URL detected; skipping oEmbed and fetching canonical article URL")
            try:
                article_html = Fetcher.fetch_with_browser(
                    article_url,
                    config,
                    proxy_mode_override,
                    custom_proxy_override,
                    is_twitter_article=True,
                )
            except Exception as e:
                logger.warning(
                    "Twitter Article browser fetch failed, trying status-id fallbacks: %s",
                    e,
                )
                req_proxies, _ = Fetcher._get_twitter_forced_proxies(config, proxy_mode_override, custom_proxy_override)
                return Fetcher._fetch_twitter_status_fallbacks(url, proxies=req_proxies, article_target_url=article_url)
            if article_html and not Fetcher._is_twitter_placeholder_content(article_html):
                return Fetcher._tag_twitter_html_content(article_html, "article")
            return article_html

        # oEmbed API endpoint for Twitter
        oembed_url = f"https://publish.twitter.com/oembed?url={url}"

        logger.info(f"Fetching Twitter content via oEmbed API: {oembed_url}")

        # Prefer configured proxy for Twitter/X, but callers may still fall back to direct access.
        req_proxies, _ = Fetcher._get_twitter_forced_proxies(config, proxy_mode_override, custom_proxy_override)

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

        try:
            response = _requests_get_interruptibly(oembed_url, headers=headers, proxies=req_proxies, timeout=30)
            response.raise_for_status()

            data = response.json()
            html_content = data.get("html", "")

            # Log metadata from oEmbed response
            author_name = data.get("author_name", "")
            provider_name = data.get("provider_name", "")

            logger.info(f"oEmbed response: author={author_name}, provider={provider_name}")

            # oEmbed occasionally returns login-wall placeholder copy; do not trust it.
            if Fetcher._is_twitter_placeholder_content(html_content):
                logger.info("oEmbed returned placeholder/login text, trying syndication fallback")
                fallback_html = Fetcher._fetch_twitter_status_fallbacks(url, proxies=req_proxies)
                if fallback_html:
                    return fallback_html

                logger.info("Syndication fallback unavailable, falling back to browser fetch")
                try:
                    browser_html = Fetcher.fetch_with_browser(
                        url,
                        config,
                        proxy_mode_override,
                        custom_proxy_override,
                        is_twitter_article=True,
                    )
                except Exception as e:
                    logger.warning(
                        "Twitter/X browser fallback failed after placeholder oEmbed response: %s",
                        e,
                    )
                    browser_html = None
                if browser_html and not Fetcher._is_twitter_placeholder_content(browser_html):
                    return Fetcher._tag_twitter_html_content(browser_html, "article")
                fallback_html = Fetcher._fetch_twitter_status_fallbacks(url, proxies=req_proxies)
                if fallback_html:
                    return fallback_html
                logger.warning("Browser fallback still returned placeholder/empty content for Twitter/X")
                return None

            # Check if this is a Twitter Article (oEmbed only returns link)
            if Fetcher._is_twitter_article_only_link(html_content):
                logger.info("oEmbed returned link-only content (Twitter Article), fetching with browser...")
                article_target_url = Fetcher._extract_twitter_article_target(url, html_content, proxies=req_proxies)
                # Fetch directly with browser and clean content
                try:
                    article_html = Fetcher.fetch_with_browser(
                        article_target_url,
                        config,
                        proxy_mode_override,
                        custom_proxy_override,
                        is_twitter_article=True,
                    )
                except Exception as e:
                    logger.warning(
                        "Twitter Article browser fetch failed, trying status-id fallbacks: %s",
                        e,
                    )
                    article_html = None
                if article_html and not Fetcher._is_twitter_placeholder_content(article_html):
                    return Fetcher._tag_twitter_html_content(article_html, "article")
                fallback_html = Fetcher._fetch_twitter_status_fallbacks(
                    url,
                    proxies=req_proxies,
                    article_target_url=article_target_url,
                )
                if fallback_html:
                    return fallback_html
                return article_html

            return html_content
        except requests.exceptions.RequestException as e:
            if Fetcher._should_retry_twitter_without_proxy(proxy_mode_override, req_proxies, e):
                logger.warning(f"oEmbed proxy request failed; retrying Twitter/X without proxy: {e}")
                return Fetcher._fetch_twitter_oembed(
                    url,
                    config,
                    proxy_mode_override="no",
                    custom_proxy_override=None,
                )
            logger.warning(f"oEmbed API failed ({e}), trying status-id fallbacks")
            fallback_html = Fetcher._fetch_twitter_status_fallbacks(url, proxies=req_proxies)
            if fallback_html:
                return fallback_html
            logger.warning("Status-id fallbacks unavailable, falling back to browser fetch")
            return None

    @staticmethod
    def _fetch_wechat_article(url, config, proxy_mode_override=None, custom_proxy_override=None):
        from playwright.sync_api import sync_playwright

        req_proxies, pw_proxy = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, proxy=pw_proxy) if pw_proxy else p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile MicroMessenger/8.0.30"
            )
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_selector("#js_content", timeout=15000)
                except Exception:
                    pass
                title = page.evaluate(
                    "() => (document.querySelector('#activity-name')?.innerText || document.title || '').trim()"
                )
                content = page.evaluate("""() => {
                    const el = document.querySelector('#js_content') || document.querySelector('.rich_media_content');
                    if (el && el.innerHTML && el.innerHTML.trim().length > 20) return el.innerHTML;
                    const scripts = Array.from(document.scripts).map(s => s.textContent || '');
                    let match = null;
                    for (const sc of scripts) {
                        let m = sc.match(/desc\\s*:\\s*JsDecode\\((\"[\\s\\S]*?\")\\)/);
                        if (m) { match = m[1]; break; }
                        m = sc.match(/desc\\s*:\\s*\"([\\s\\S]*?)\"/);
                        if (m) { match = '"' + m[1] + '"'; break; }
                    }
                    if (match) {
                        try {
                            const raw = JSON.parse(match);
                            return raw;
                        } catch(e) {}
                    }
                    return '';
                }""")
                html_content = None
                if content and content.strip():
                    html_content = f"<html><head><meta charset='utf-8'><title>{title or 'Untitled'}</title></head><body><article>{content}</article></body></html>"
                return html_content
            except Exception as e:
                logger.warning(f"WeChat handler failed: {e}")
                return None
            finally:
                browser.close()

    @staticmethod
    def _is_twitter_url(url):
        """Check if URL is from Twitter/X."""
        return bool(re.match(r"^https?://(www\.)?(twitter|x)\.com/", url, re.IGNORECASE))

    @staticmethod
    def _create_stealth_context(browser, url=None, auth_site_name=None):
        """
        Create a browser context with anti-detection measures.
        Uses stealth settings to avoid being detected as automation.
        """
        is_zhihu_url = bool(
            url and re.match(r"^https?://((www\.)?zhihu\.com|zhuanlan\.zhihu\.com)/", url, re.IGNORECASE)
        )

        # Twitter/X specific settings
        if url and Fetcher._is_twitter_url(url):
            logger.info("Using Twitter-specific stealth settings")
            viewport = {"width": 1280, "height": 800}
            user_agent = (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            locale = "en-US"
            timezone = "America/New_York"
        elif is_zhihu_url:
            viewport = {"width": 1440, "height": 1080}
            user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            locale = "zh-CN"
            timezone = "Asia/Shanghai"
        else:
            viewport = {"width": 1920, "height": 1080}
            user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            locale = "en-US"
            timezone = "America/New_York"

        context_options = {
            "viewport": viewport,
            "user_agent": user_agent,
            "locale": locale,
            "timezone_id": timezone,
            "extra_http_headers": {
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8" if is_zhihu_url else "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            },
        }

        # If auth state is available for this site, use it; otherwise fallback to clean context.
        if auth_site_name:
            context = AuthHandler.create_context_with_auth(browser, auth_site_name, **context_options)
        else:
            context = browser.new_context(**context_options)

        # Add script to hide webdriver property and automation markers
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['zh-CN', 'zh', 'en']
            });
            window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
            // Override permissions query
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : originalQuery(parameters)
            );
            // Hide automation-related properties
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
            // Mock WebGL vendor/renderer to look like a real GPU
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) return 'Intel Inc.';
                if (parameter === 37446) return 'Intel Iris OpenGL Engine';
                return getParameter.call(this, parameter);
            };
        """)

        return context

    @staticmethod
    def fetch_with_browser(
        url,
        config,
        proxy_mode_override=None,
        custom_proxy_override=None,
        is_twitter_article=False,
        trusted_host_map=None,
    ):
        logger.info("Launching browser...")
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

        twitter_target_url = (
            Fetcher._normalize_twitter_article_url(url)
            if Fetcher._is_twitter_url(url) and Fetcher._is_twitter_article_url(url)
            else url
        )

        is_zhihu_url = bool(re.match(r"^https?://((www\.)?zhihu\.com|zhuanlan\.zhihu\.com)/", url, re.IGNORECASE))

        # For Twitter/X URLs, prefer detected proxy settings first and retry direct if needed.
        is_twitter_url = Fetcher._is_twitter_url(url)
        if is_twitter_url:
            _, pw_proxy = Fetcher._get_twitter_forced_proxies(config, proxy_mode_override, custom_proxy_override)
            logger.info("Twitter/X URL detected - using preferred proxy settings")
        else:
            _, pw_proxy = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)

        if pw_proxy:
            logger.info(f"Playwright Proxy: {pw_proxy}")
        else:
            logger.info("Playwright Proxy: None")

        with sync_playwright() as p:
            browser = None
            profile_context = None
            twitter_profile_dir = AuthHandler.get_twitter_profile_dir() if is_twitter_url else None

            # Prefer persistent Twitter profile if it exists (more reliable than storage_state alone).
            if (
                is_twitter_url
                and twitter_profile_dir
                and os.path.exists(twitter_profile_dir)
                and os.listdir(twitter_profile_dir)
            ):
                persistent_args = {"headless": True}
                if pw_proxy:
                    persistent_args["proxy"] = pw_proxy
                try:
                    profile_context = p.chromium.launch_persistent_context(
                        twitter_profile_dir, channel="chrome", **persistent_args
                    )
                except Exception as e:
                    logger.info(f"Chrome channel unavailable for twitter profile fetch, fallback to Chromium: {e}")
                    profile_context = p.chromium.launch_persistent_context(twitter_profile_dir, **persistent_args)
                context = profile_context
                page = context.pages[0] if context.pages else context.new_page()
            else:
                # Launch browser with stealth args
                # Use visible browser for Zhihu to avoid headless detection
                use_headless = not is_zhihu_url
                launch_args = {
                    "headless": use_headless,
                    "args": [
                        "--disable-blink-features=AutomationControlled",
                        "--disable-features=IsolateOrigins,site-per-process",
                        "--disable-web-security",
                        "--disable-features=BlockInsecurePrivateNetworkRequests",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-infobars",
                        "--disable-background-timer-throttling",
                        "--disable-popup-blocking",
                        "--disable-extensions",
                    ],
                }
                if trusted_host_map:
                    resolver_rules = ", ".join(
                        f"MAP {host} {address}" for host, address in trusted_host_map.items() if host and address
                    )
                    if resolver_rules:
                        launch_args["args"].append(f"--host-resolver-rules={resolver_rules}")
                        logger.info("Playwright Host Resolver Rules: %s", resolver_rules)
                if pw_proxy:
                    launch_args["proxy"] = pw_proxy

                browser = p.chromium.launch(**launch_args)

                # Attach persisted auth state for sites with saved login sessions.
                auth_site_name = None
                if is_twitter_url:
                    auth_site_name = "twitter"
                elif is_zhihu_url:
                    auth_site_name = "zhihu"
                elif Fetcher._is_reddit_url(url):
                    auth_site_name = "reddit"
                elif Fetcher._is_douban_url(url):
                    auth_site_name = "douban"
                context = Fetcher._create_stealth_context(browser, url, auth_site_name=auth_site_name)
                page = context.new_page()

            try:
                # For Twitter/X, use domcontentloaded + timeout instead of networkidle
                # because X has persistent connections that never reach networkidle
                if is_twitter_url:
                    logger.info("Using domcontentloaded strategy for Twitter/X")
                    page.goto(twitter_target_url, wait_until="domcontentloaded", timeout=60000)
                    # Wait longer for content to hydrate
                    page.wait_for_timeout(5000)
                elif is_zhihu_url:
                    logger.info("Using Zhihu browser strategy: visible browser, homepage first, then article")
                    content_selectors = [
                        ".Post-RichTextContainer",
                        ".RichContent .RichContent-inner",
                        ".AnswerItem",
                        "article",
                        "h1.Post-Title",
                        "h1.QuestionHeader-title",
                    ]

                    def _zhihu_wait_for_content(page, timeout_ms=10000):
                        """Wait for Zhihu article content to appear; return True if found."""
                        for selector in content_selectors:
                            try:
                                page.wait_for_selector(selector, timeout=timeout_ms)
                                logger.info("Zhihu content appeared: %s", selector)
                                return True
                            except PlaywrightTimeoutError:
                                continue
                        return False

                    def _zhihu_needs_login(page):
                        """Check if the current page is a login or security verification page."""
                        try:
                            page_text = page.evaluate("document.body ? document.body.innerText : ''")
                            if not page_text:
                                return False
                            if "安全验证" in page_text or "验证你是否是真人" in page_text:
                                return True
                            if "登录" in page_text[:500] and len(page_text) < 2000:
                                return True
                            return False
                        except Exception:
                            return False

                    # Step 1: Visit homepage to acquire cookies / check login status
                    try:
                        page.goto("https://www.zhihu.com/", wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(2000)
                    except Exception as e:
                        logger.debug("Zhihu homepage pre-visit failed: %s", e)

                    # Step 2: Navigate to the target article
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(3000)
                    try:
                        page.wait_for_load_state("load", timeout=15000)
                    except PlaywrightTimeoutError:
                        pass

                    # Step 3: Check if content loaded or login is needed
                    if not _zhihu_wait_for_content(page, timeout_ms=5000):
                        if _zhihu_needs_login(page):
                            logger.info("Zhihu requires login; please log in manually in the browser window...")
                            logger.info("Waiting up to 120 seconds for login...")
                            # Wait for user to log in and be redirected back to content
                            if _zhihu_wait_for_content(page, timeout_ms=120000):
                                # Login succeeded — save auth state and re-navigate
                                try:
                                    state = context.storage_state()
                                    AuthHandler.save_state("zhihu", state)
                                    logger.info("Saved Zhihu auth state after login")
                                except Exception as e:
                                    logger.debug("Failed to save auth state: %s", e)
                                # Re-navigate to the article URL to get fresh content
                                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                                page.wait_for_timeout(3000)
                                _zhihu_wait_for_content(page, timeout_ms=15000)
                else:
                    try:
                        page.goto(url, wait_until="networkidle", timeout=60000)
                    except PlaywrightTimeoutError as e:
                        logger.warning(
                            "Browser networkidle wait timed out; retrying with domcontentloaded: %s",
                            e,
                        )
                        try:
                            page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        except PlaywrightTimeoutError as fallback_error:
                            logger.warning(
                                "Browser domcontentloaded retry also timed out; using partial page content: %s",
                                fallback_error,
                            )
                    page.wait_for_timeout(2000)

                # Retry page.content() if the page is still mid-navigation
                for _content_attempt in range(3):
                    try:
                        content = page.content()
                        break
                    except Exception as content_err:
                        if "navigating" in str(content_err).lower() and _content_attempt < 2:
                            logger.debug("Page still navigating; waiting and retrying content()")
                            page.wait_for_timeout(3000)
                        else:
                            raise

                if is_twitter_url:
                    if Fetcher._is_twitter_placeholder_content(content):
                        logger.info("Browser returned placeholder/login content, trying syndication fallback")
                        req_proxies, _ = Fetcher._get_twitter_forced_proxies(
                            config, proxy_mode_override, custom_proxy_override
                        )
                        syndication_html = Fetcher._fetch_twitter_syndication_html(
                            twitter_target_url, proxies=req_proxies
                        )
                        if syndication_html:
                            logger.info("Using Twitter/X syndication fallback content")
                            return syndication_html
                        fxapi_html = Fetcher._fetch_twitter_fxapi_html(twitter_target_url, proxies=req_proxies)
                        if fxapi_html:
                            logger.info("Using fxTwitter API fallback content")
                            return fxapi_html
                        logger.warning(
                            "Browser returned placeholder/login content and syndication fallback was unavailable"
                        )
                        return None

                    # Clean Twitter Article content if needed
                    if is_twitter_article:
                        logger.info("Cleaning Twitter Article content...")
                        content = Fetcher._clean_twitter_article_content(content)

                    dom_content = Fetcher._extract_twitter_dom_content(content, source_url=twitter_target_url)
                    if dom_content:
                        logger.info("Using preserved Twitter/X DOM content")
                        return dom_content

                    structured = Fetcher._extract_twitter_structured_content(content, source_url=twitter_target_url)
                    if structured:
                        logger.info("Using structured Twitter/X content extraction")
                        return Fetcher._tag_twitter_html_content(
                            structured, "article" if is_twitter_article else "tweet"
                        )

                return content
            except Exception as e:
                if is_twitter_url:
                    if Fetcher._should_retry_twitter_without_proxy(proxy_mode_override, pw_proxy, e):
                        logger.warning(
                            "Browser fetch failed through Twitter/X proxy; retrying without proxy: %s",
                            e,
                        )
                        return Fetcher.fetch_with_browser(
                            url,
                            config,
                            proxy_mode_override="no",
                            custom_proxy_override=None,
                            is_twitter_article=is_twitter_article,
                        )
                    logger.warning(f"Browser fetch failed for Twitter/X, trying fxTwitter fallback: {e}")
                    req_proxies, _ = Fetcher._get_twitter_forced_proxies(
                        config, proxy_mode_override, custom_proxy_override
                    )
                    fxapi_html = Fetcher._fetch_twitter_fxapi_html(twitter_target_url, proxies=req_proxies)
                    if fxapi_html:
                        logger.info("Using fxTwitter API fallback content")
                        return fxapi_html
                if not trusted_host_map:
                    analysis = _analyze_network_fetch_failure(url, e)
                    if analysis and analysis.get("dns_polluted") and analysis.get("trusted_addresses"):
                        hostname = analysis.get("hostname")
                        trusted_ip = analysis["trusted_addresses"][0]
                        if hostname and trusted_ip:
                            logger.warning(
                                "Browser fetch failed and local DNS looks polluted; retrying with trusted DNS mapping %s -> %s",
                                hostname,
                                trusted_ip,
                            )
                            return Fetcher.fetch_with_browser(
                                url,
                                config,
                                proxy_mode_override=proxy_mode_override,
                                custom_proxy_override=custom_proxy_override,
                                is_twitter_article=is_twitter_article,
                                trusted_host_map={hostname: trusted_ip},
                            )
                logger.error(f"Browser fetch failed: {e}")
                raise
            finally:
                try:
                    context.close()
                except Exception as close_error:
                    logger.debug(f"Ignoring context close error: {close_error}")
                if browser:
                    try:
                        browser.close()
                    except Exception as close_error:
                        logger.debug(f"Ignoring browser close error: {close_error}")

    @staticmethod
    def _normalize_thread_author_key(author):
        if not author:
            return ""
        return re.sub(r"\s+", " ", str(author).strip()).lower()

    @staticmethod
    def _escape_thread_text(text):
        return escape(text or "").replace("\n", "<br>")

    @staticmethod
    def _extract_bluesky_embed_blocks(post_data, fallback_did=""):
        if not isinstance(post_data, dict):
            return []

        blocks = []
        seen_image_urls = set()
        seen_link_targets = set()

        def normalize_image_key(url):
            if not url:
                return ""
            normalized = str(url).strip()
            normalized = re.sub(r"@(?:jpeg|jpg|png|webp)$", "", normalized, flags=re.IGNORECASE)
            return normalized

        def append_image(url, alt=""):
            image_key = normalize_image_key(url)
            if not url or not image_key or image_key in seen_image_urls:
                return
            seen_image_urls.add(image_key)
            blocks.append(
                {
                    "type": "image",
                    "url": url,
                    "alt": alt or "",
                }
            )

        def append_link(uri, title="", description="", thumb=""):
            link_key = str(uri or "").strip()
            if not link_key or link_key in seen_link_targets:
                return
            seen_link_targets.add(link_key)
            blocks.append(
                {
                    "type": "external",
                    "uri": uri,
                    "title": title or uri,
                    "description": description or "",
                    "thumb": thumb or "",
                }
            )

        def collect_from_embed(embed_data):
            if not isinstance(embed_data, dict):
                return

            embed_type = str(embed_data.get("$type") or "").lower()

            if "recordwithmedia" in embed_type:
                collect_from_embed(embed_data.get("media"))
                return

            if "images" in embed_type:
                for image in embed_data.get("images", []) or []:
                    if not isinstance(image, dict):
                        continue
                    image_url = image.get("fullsize") or image.get("thumb") or image.get("url") or image.get("src")
                    if not image_url:
                        image_obj = image.get("image") or {}
                        image_url = (
                            image_obj.get("fullsize")
                            or image_obj.get("thumb")
                            or image_obj.get("url")
                            or image_obj.get("src")
                        )
                        if not image_url:
                            image_ref = image_obj.get("ref", {}).get("$link", "")
                            if image_ref and fallback_did:
                                image_url = (
                                    "https://cdn.bsky.app/img/feed_fullsize/plain/" f"{fallback_did}/{image_ref}@jpeg"
                                )
                    append_image(image_url, image.get("alt") or "")
                return

            if "external" in embed_type:
                external = embed_data.get("external") or {}
                append_link(
                    external.get("uri") or external.get("url") or "",
                    external.get("title") or "",
                    external.get("description") or "",
                    external.get("thumb") or "",
                )
                return

            if "video" in embed_type:
                playlist = embed_data.get("playlist") or embed_data.get("uri") or ""
                if playlist:
                    blocks.append(
                        {
                            "type": "video",
                            "playlist": playlist,
                            "thumb": embed_data.get("thumbnail") or embed_data.get("thumb") or "",
                            "alt": embed_data.get("alt") or "",
                        }
                    )
                return

        collect_from_embed(post_data.get("embed"))
        collect_from_embed(post_data.get("record", {}).get("embed"))
        return blocks

    @staticmethod
    def _render_thread_media_blocks(media_blocks):
        if not media_blocks:
            return ""

        html_parts = ["<div class='surf-thread-media'>"]
        for block in media_blocks:
            block_type = block.get("type")
            if block_type == "image":
                image_url = escape(block.get("url") or "")
                if not image_url:
                    continue
                alt_text = escape(block.get("alt") or "")
                html_parts.append(
                    "<p class='surf-thread-image'>"
                    f'<img src="{image_url}" alt="{alt_text}" style="max-width: 100%; margin: 0.5em 0;">'
                    "</p>"
                )
            elif block_type == "external":
                uri = escape(block.get("uri") or "")
                if not uri:
                    continue
                title = escape(block.get("title") or uri)
                description = escape(block.get("description") or "")
                thumb = escape(block.get("thumb") or "")
                html_parts.append("<div class='surf-thread-link-card'>")
                if thumb:
                    html_parts.append(
                        "<p class='surf-thread-image'>"
                        f'<img src="{thumb}" alt="{title}" style="max-width: 100%; margin: 0.5em 0;">'
                        "</p>"
                    )
                html_parts.append(f'<p><strong>Link:</strong> <a href="{uri}">{title}</a></p>')
                if description:
                    html_parts.append(f"<p>{description}</p>")
                html_parts.append("</div>")
            elif block_type == "video":
                playlist = escape(block.get("playlist") or "")
                thumb = escape(block.get("thumb") or "")
                if thumb:
                    html_parts.append(
                        "<p class='surf-thread-image'>"
                        f"<img src=\"{thumb}\" alt=\"{escape(block.get('alt') or 'video thumbnail')}\" style=\"max-width: 100%; margin: 0.5em 0;\">"
                        "</p>"
                    )
                if playlist:
                    html_parts.append(f'<p><a href="{playlist}">View video playlist</a></p>')
        html_parts.append("</div>")
        return "".join(html_parts)

    @staticmethod
    def _render_thread_post_block(item, heading):
        author = escape(item.get("author") or "Unknown")
        handle = escape(item.get("handle") or "")
        timestamp = escape(item.get("timestamp") or "")
        permalink = escape(item.get("permalink") or "")
        text_html = Fetcher._escape_thread_text(item.get("text") or "")
        media_html = Fetcher._render_thread_media_blocks(item.get("media") or [])

        html_parts = ["<section class='surf-thread-post'>", f"<h2>{escape(heading)}</h2>"]
        meta_parts = [f"<strong>{author}</strong>"]
        if handle:
            meta_parts.append(handle)
        if timestamp:
            meta_parts.append(timestamp)
        if meta_parts:
            html_parts.append(f"<p>{' | '.join(meta_parts)}</p>")
        if text_html:
            html_parts.append(f"<p>{text_html}</p>")
        if media_html:
            html_parts.append(media_html)
        if permalink:
            html_parts.append(f'<p><a href="{permalink}">View post</a></p>')
        html_parts.append("</section>")
        return "".join(html_parts)

    @staticmethod
    def _normalize_thread_mode(fetch_thread, default_mode="after"):
        if fetch_thread is False:
            return None
        if isinstance(fetch_thread, str):
            mode = fetch_thread.strip().lower()
            if mode == "off":
                return None
            if mode in {"after", "before", "both"}:
                return mode
        return default_mode

    @staticmethod
    def _normalize_thread_author(fetch_thread_author, default_author="all"):
        if isinstance(fetch_thread_author, str):
            author = fetch_thread_author.strip().lower()
            if author in {"same", "all"}:
                return author
        return default_author

    @staticmethod
    def _ensure_source_site_meta(html_content, site_name):
        if not html_content:
            return html_content
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            head = soup.find("head")
            if not head:
                head = soup.new_tag("head")
                if soup.html:
                    soup.html.insert(0, head)
                else:
                    html = soup.new_tag("html")
                    html.append(head)
                    body = soup.new_tag("body")
                    for child in list(soup.contents):
                        body.append(child.extract())
                    html.append(body)
                    soup.append(html)
            source_meta = soup.find("meta", attrs={"name": "surf-source-site"})
            if not source_meta:
                source_meta = soup.new_tag("meta")
                source_meta.attrs["name"] = "surf-source-site"
                head.append(source_meta)
            source_meta.attrs["content"] = site_name
            return str(soup)
        except Exception:
            return html_content

    @staticmethod
    def _merge_thread_context_into_html(html_content, site_name, thread_items, current_index=None):
        if not html_content or not thread_items:
            return Fetcher._ensure_source_site_meta(html_content, site_name)

        if current_index is None or current_index < 0 or current_index >= len(thread_items):
            current_index = len(thread_items) - 1

        before_items = thread_items[:current_index]
        after_items = thread_items[current_index + 1 :]
        if not before_items and not after_items:
            return Fetcher._ensure_source_site_meta(html_content, site_name)

        try:
            soup = BeautifulSoup(html_content, "html.parser")
            article = soup.find("article")
            if not article:
                body = soup.find("body")
                article = soup.new_tag("article")
                if body:
                    for child in list(body.contents):
                        article.append(child.extract())
                    body.append(article)
                else:
                    soup.append(article)

            section = soup.new_tag("section")
            section.attrs["class"] = ["surf-thread-context"]

            title = soup.new_tag("h2")
            title.string = "Thread Context"
            section.append(title)

            for item in before_items:
                fragment = BeautifulSoup(Fetcher._render_thread_post_block(item, "Earlier Post"), "html.parser")
                for child in fragment.contents:
                    section.append(child)

            for item in after_items:
                fragment = BeautifulSoup(Fetcher._render_thread_post_block(item, "Later Reply"), "html.parser")
                for child in fragment.contents:
                    section.append(child)

            article.append(section)
            return Fetcher._ensure_source_site_meta(str(soup), site_name)
        except Exception as e:
            logger.warning(f"Failed to merge thread context: {e}")
            return Fetcher._ensure_source_site_meta(html_content, site_name)

    @staticmethod
    def _render_social_thread_html(site_name, page_url, thread_items, current_index=None):
        if not thread_items:
            return None

        site_labels = {
            "bluesky": "Bluesky",
            "threads": "Threads",
            "weibo": "Weibo",
        }
        site_label = site_labels.get(site_name, site_name.title())
        if current_index is None or current_index < 0 or current_index >= len(thread_items):
            current_index = len(thread_items) - 1

        current = thread_items[current_index]
        author = escape(current.get("author") or "Unknown")
        before_items = thread_items[:current_index]
        after_items = thread_items[current_index + 1 :]

        html_parts = [
            "<!DOCTYPE html>",
            '<html lang="en"><head>',
            '<meta charset="utf-8">',
            f"<title>{site_label} Post by {author}</title>",
            f'<meta name="surf-source-site" content="{escape(site_name)}">',
            "</head><body><article>",
            f"<h1>{site_label} Post</h1>",
        ]

        if before_items:
            html_parts.append("<section class='surf-thread-context'><h2>Earlier Posts</h2>")
            for item in before_items:
                html_parts.append(Fetcher._render_thread_post_block(item, "Earlier Post"))
            html_parts.append("</section>")

        html_parts.append(Fetcher._render_thread_post_block(current, "Current Post"))
        if after_items:
            html_parts.append("<section class='surf-thread-context'><h2>Replies</h2>")
            for item in after_items:
                html_parts.append(Fetcher._render_thread_post_block(item, "Later Reply"))
            html_parts.append("</section>")
        html_parts.append(f'<p><a href="{escape(page_url)}">View on {site_label}</a></p>')
        html_parts.append("</article></body></html>")
        return "".join(html_parts)

    @staticmethod
    def _extract_thread_items(items, current_index, mode, author_scope="same"):
        if not items:
            return [], -1
        if current_index is None or current_index < 0 or current_index >= len(items):
            current_index = len(items) - 1
        if not mode:
            return [items[current_index]], 0

        current = items[current_index]
        current_author_key = current.get("author_key", "")
        start_index = current_index
        end_index = current_index

        if mode in {"before", "both"}:
            while start_index > 0:
                previous = items[start_index - 1]
                if author_scope == "same" and previous.get("author_key", "") != current_author_key:
                    break
                start_index -= 1
        if mode in {"after", "both"}:
            while end_index + 1 < len(items):
                following = items[end_index + 1]
                if author_scope == "same" and following.get("author_key", "") != current_author_key:
                    break
                end_index += 1

        return items[start_index : end_index + 1], current_index - start_index

    @staticmethod
    def _fetch_social_thread_page(
        url,
        config,
        proxy_mode_override=None,
        custom_proxy_override=None,
        fetch_thread=None,
        fetch_thread_author=None,
    ):
        site_name = "threads" if "threads.net" in url.lower() else "weibo"
        thread_mode = Fetcher._normalize_thread_mode(fetch_thread)
        thread_author = Fetcher._normalize_thread_author(fetch_thread_author)

        from playwright.sync_api import sync_playwright

        _, pw_proxy = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)

        with sync_playwright() as p:
            launch_args = {
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            if pw_proxy:
                launch_args["proxy"] = pw_proxy

            browser = p.chromium.launch(**launch_args)
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            page = context.new_page()

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4000)

                data = page.evaluate(
                    """(args) => {
                        const site = args.site;
                        const threadMode = args.threadMode || '';
                        const threadAuthor = args.threadAuthor || 'same';
                        const currentUrl = window.location.href;
                        const urlMatch = currentUrl.match(site === 'threads'
                            ? /\\/post\\/([^/?#]+)/
                            : /(detail|status)\\/([^/?#]+)|\\/([^/?#]+)\\?/);
                        const targetId = urlMatch ? (urlMatch[2] || urlMatch[3] || urlMatch[1] || '') : '';

                        const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                        const textFrom = (node, selectors) => {
                            for (const selector of selectors) {
                                const found = node.querySelector(selector);
                                if (found && clean(found.innerText)) return clean(found.innerText);
                            }
                            return '';
                        };

                        const allNodes = Array.from(document.querySelectorAll('article, [role="article"]'));
                        const items = allNodes.map((node, index) => {
                            const links = Array.from(node.querySelectorAll('a[href]')).map(a => a.href);
                            const permalink = links.find(href => {
                                if (site === 'threads') return /\\/post\\//.test(href);
                                return /m\\.weibo\\.cn|weibo\\.com/.test(href);
                            }) || '';

                            let author = '';
                            let handle = '';
                            let timestamp = textFrom(node, ['time', 'a time']);
                            let text = '';

                            if (site === 'threads') {
                                author = textFrom(node, ['h1', 'h2', 'h3', '[data-pressable-container="true"] span']);
                                handle = links.find(href => /threads\\.net\\/@/.test(href)) || '';
                                const textNode = Array.from(node.querySelectorAll('span, div')).find(el => {
                                    const textValue = clean(el.innerText);
                                    return textValue && textValue.length > 20;
                                });
                                text = textNode ? clean(textNode.innerText) : '';
                            } else {
                                author = textFrom(node, ['header span', 'h1', 'h2', '.m-text-cut', '.wbpro-screen-box span']);
                                handle = links.find(href => /\\/(u|n)\\//.test(href)) || '';
                                const textNode = node.querySelector('[data-testid="post-content"], .detail_wbtext_4CRf9, .wbpro-feed-content, .weibo-text');
                                text = textNode ? clean(textNode.innerText) : clean(node.innerText);
                            }

                            const authorKey = clean(author).toLowerCase();
                            const permalinkIdMatch = permalink.match(site === 'threads'
                                ? /\\/post\\/([^/?#]+)/
                                : /(detail|status)\\/([^/?#]+)|\\/([^/?#]+)$/);
                            const permalinkId = permalinkIdMatch ? (permalinkIdMatch[2] || permalinkIdMatch[3] || permalinkIdMatch[1] || '') : '';

                            return {
                                index,
                                author,
                                authorKey,
                                handle,
                                timestamp,
                                text,
                                permalink,
                                permalinkId,
                            };
                        }).filter(item => item.author && item.text);

                        if (!items.length) return { items: [] };

                        let currentIndex = items.length - 1;
                        if (targetId) {
                            const matchedIndex = items.findIndex(item => item.permalinkId && item.permalinkId === targetId);
                            if (matchedIndex >= 0) currentIndex = matchedIndex;
                        }

                        let startIndex = currentIndex;
                        let endIndex = currentIndex;
                        if (threadMode === 'before' || threadMode === 'both') {
                            while (
                                startIndex > 0 &&
                                (threadAuthor === 'all' || items[startIndex - 1].authorKey === items[currentIndex].authorKey)
                            ) {
                                startIndex -= 1;
                            }
                        }
                        if (threadMode === 'after' || threadMode === 'both') {
                            while (
                                endIndex + 1 < items.length &&
                                (threadAuthor === 'all' || items[endIndex + 1].authorKey === items[currentIndex].authorKey)
                            ) {
                                endIndex += 1;
                            }
                        }

                        return { items: items.slice(startIndex, endIndex + 1), currentIndex: currentIndex - startIndex };
                    }""",
                    {"site": site_name, "threadMode": thread_mode or "", "threadAuthor": thread_author},
                )

                items = []
                for item in data.get("items", []):
                    normalized = {
                        "author": (item.get("author") or "").strip(),
                        "author_key": Fetcher._normalize_thread_author_key(item.get("author")),
                        "handle": (item.get("handle") or "").strip(),
                        "timestamp": (item.get("timestamp") or "").strip(),
                        "text": (item.get("text") or "").strip(),
                        "permalink": (item.get("permalink") or "").strip(),
                    }
                    if normalized["author"] and normalized["text"]:
                        items.append(normalized)

                if not items:
                    return None

                return Fetcher._render_social_thread_html(
                    site_name, url, items, data.get("currentIndex", len(items) - 1)
                )
            except Exception as e:
                logger.warning(f"{site_name} social thread handler failed: {e}")
                return None
            finally:
                context.close()
                browser.close()

    @staticmethod
    def _get_twitter_thread_items(
        url,
        config,
        proxy_mode_override=None,
        custom_proxy_override=None,
        thread_mode="after",
        thread_author="all",
    ):
        cli_payload = Fetcher._fetch_twitter_cli_tweet_json(
            url,
            config,
            proxy_mode_override=proxy_mode_override,
            custom_proxy_override=custom_proxy_override,
        )
        cli_items, cli_current_index = Fetcher._extract_twitter_cli_thread_items(
            cli_payload,
            Fetcher._extract_twitter_status_id(url) or "",
            thread_mode,
            thread_author,
        )
        if cli_items:
            logger.info("Using twitter-cli thread data: %d item(s)", len(cli_items))
            return cli_items, cli_current_index

        try:
            from playwright.sync_api import sync_playwright

            _, pw_proxy = Fetcher._get_twitter_forced_proxies(config, proxy_mode_override, custom_proxy_override)

            with sync_playwright() as p:
                launch_args = {
                    "headless": True,
                    "args": ["--disable-blink-features=AutomationControlled"],
                }
                if pw_proxy:
                    launch_args["proxy"] = pw_proxy

                browser = p.chromium.launch(**launch_args)
                context = Fetcher._create_stealth_context(browser, url, auth_site_name="twitter")
                page = context.new_page()

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(5000)
                    data = page.evaluate(
                        """(targetStatusId) => {
                            const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                            const articles = Array.from(document.querySelectorAll("article[data-testid='tweet']"));
                            const items = articles.map((article) => {
                                const authorNode = article.querySelector("[data-testid='User-Name']");
                                const textNode = article.querySelector("[data-testid='tweetText']");
                                const links = Array.from(article.querySelectorAll("a[href*='/status/']"));
                                const permalink = (links.find(link => /\\/status\\/\\d+/.test(link.href)) || {}).href || '';
                                const statusMatch = permalink.match(/\\/status\\/(\\d+)/);
                                return {
                                    author: clean(authorNode ? authorNode.innerText.split('@')[0] : ''),
                                    timestamp: clean((links.find(link => link.querySelector('time')) || {}).innerText || ''),
                                    text: clean(textNode ? textNode.innerText : ''),
                                    permalink,
                                    statusId: statusMatch ? statusMatch[1] : '',
                                };
                            }).filter(item => item.author && item.text);

                            return { items, currentIndex: items.findIndex(item => item.statusId === targetStatusId) };
                        }""",
                        Fetcher._extract_twitter_status_id(url) or "",
                    )

                    items = []
                    for item in data.get("items", []):
                        items.append(
                            {
                                "author": item.get("author", ""),
                                "author_key": Fetcher._normalize_thread_author_key(item.get("author")),
                                "handle": "",
                                "timestamp": item.get("timestamp", ""),
                                "text": item.get("text", ""),
                                "permalink": item.get("permalink", ""),
                            }
                        )

                    if not items:
                        return [], -1

                    current_index = data.get("currentIndex", -1)
                    if current_index is None or current_index < 0:
                        current_index = len(items) - 1

                    return Fetcher._extract_thread_items(items, current_index, thread_mode, thread_author)
                finally:
                    context.close()
                    browser.close()
        except Exception as e:
            logger.warning(f"Failed to fetch Twitter/X thread items: {e}")
            return [], -1

    @staticmethod
    def _extract_twitter_cli_thread_items(payload, target_status_id, thread_mode="after", thread_author="all"):
        if not isinstance(payload, dict) or payload.get("ok") is False:
            return [], -1

        data = payload.get("data", payload)
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return [], -1

        items = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            author = entry.get("author") or {}
            if isinstance(author, dict):
                author_name = author.get("name") or author.get("screenName") or author.get("screen_name") or ""
                handle = author.get("screenName") or author.get("screen_name") or ""
                author_key = author.get("id") or handle or author_name
            else:
                author_name = str(author)
                handle = ""
                author_key = author_name

            text = entry.get("text") or entry.get("full_text") or entry.get("fullText") or ""
            tweet_id = str(entry.get("id") or entry.get("statusId") or entry.get("status_id") or "")
            media_blocks = [
                {"type": "image", "url": media_url}
                for media_url in Fetcher._extract_twitter_media_urls(entry)
            ]
            permalink = entry.get("url") or (f"https://x.com/{handle}/status/{tweet_id}" if handle and tweet_id else "")
            normalized = {
                "author": str(author_name).strip(),
                "author_key": Fetcher._normalize_thread_author_key(author_key),
                "handle": f"@{handle}" if handle else "",
                "timestamp": entry.get("createdAtLocal") or entry.get("createdAtISO") or entry.get("createdAt") or "",
                "text": str(text).strip(),
                "media": media_blocks,
                "permalink": permalink,
                "status_id": tweet_id,
            }
            if normalized["author"] and normalized["text"]:
                items.append(normalized)

        if not items:
            return [], -1

        current_index = next(
            (index for index, item in enumerate(items) if target_status_id and item.get("status_id") == target_status_id),
            0,
        )
        return Fetcher._extract_thread_items(items, current_index, thread_mode, thread_author)

    @staticmethod
    def _resolve_xhslink_short_url(url, proxies=None, timeout=30):
        """
        Resolve xhslink.com short URL to full xiaohongshu.com URL.
        Only keeps xsec_token parameter in the final URL.

        Args:
            url: The short URL (e.g., http://xhslink.com/o/xxxxx)
            proxies: Proxy configuration for requests
            timeout: Request timeout in seconds

        Returns:
            tuple: (resolved_url, cleaned_url) or (None, None) if resolution fails
        """
        import requests
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

        if "xhslink.com" not in url:
            # Not a short link, return as-is
            return url, url

        try:
            logger.info(f"Resolving xhslink short URL: {url}")
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
            }

            # First, make a HEAD request to check the redirect chain
            session = requests.Session()
            # Don't use allow_redirects=True initially to see the redirect chain
            response = _session_get_interruptibly(
                session, url, headers=headers, proxies=proxies, timeout=timeout, allow_redirects=True
            )
            final_url = response.url

            logger.info(f"Resolved to: {final_url}")

            # Check if we got a valid xiaohongshu URL
            if "xiaohongshu.com" not in final_url:
                logger.warning(f"Resolved URL does not contain xiaohongshu.com: {final_url}")
                # The URL might still be a redirect page, let's check the response content
                response_text_lower = response.text.lower()
                if "xiaohongshu" in response_text_lower or "redirect" in response_text_lower:
                    logger.warning(
                        "Response appears to contain xiaohongshu or redirect, trying to extract URL from content"
                    )
                    # Try to find redirect URL in the HTML - look for various patterns
                    patterns = [
                        r'https://[^"\s<>]+xiaohongshu\.com/explore/[^"\s<>]*',
                        r'https://[^"\s<>]+xiaohongshu\.com/discovery/item/[^"\s<>]*',
                        r'https://[^"\s<>]+xiaohongshu\.com[^"\s<>]*',
                    ]
                    for pattern in patterns:
                        redirect_match = re.search(pattern, response.text)
                        if redirect_match:
                            final_url = redirect_match.group(0)
                            logger.info(f"Extracted URL from redirect page: {final_url}")
                            break

            # Parse the URL and clean it
            parsed = urlparse(final_url)
            query_params = parse_qs(parsed.query)

            # Only keep xsec_token parameter
            cleaned_params = {}
            if "xsec_token" in query_params:
                cleaned_params["xsec_token"] = query_params["xsec_token"]

            # Rebuild the URL with only xsec_token
            cleaned_query = urlencode(cleaned_params, doseq=True)
            cleaned_url = urlunparse(parsed._replace(query=cleaned_query))

            logger.info(f"Cleaned URL: {cleaned_url}")
            return final_url, cleaned_url

        except Exception as e:
            logger.warning(f"Failed to resolve xhslink URL {url}: {e}")
            import traceback

            logger.debug(f"Traceback: {traceback.format_exc()}")
            return None, None

    @staticmethod
    def _canonicalize_xiaohongshu_source_url(url):
        """
        Normalize Xiaohongshu source URLs to keep only the canonical path and xsec_token.
        This strips share-tracking query params such as source/webshare/xhsshare.
        """
        if not url:
            return url

        parsed = urlparse(url)
        hostname = (parsed.netloc or "").lower()
        if "xiaohongshu.com" not in hostname:
            return url

        query_params = parse_qs(parsed.query)
        cleaned_params = {}
        if "xsec_token" in query_params:
            cleaned_params["xsec_token"] = query_params["xsec_token"]

        cleaned_query = urlencode(cleaned_params, doseq=True)
        return urlunparse(parsed._replace(query=cleaned_query, fragment=""))

    @staticmethod
    def _canonicalize_douban_source_url(url):
        if not url:
            return url

        normalized = Fetcher._normalize_douban_url(url)
        parsed = urlparse(normalized)
        hostname = (parsed.netloc or "").lower()
        if "douban.com" not in hostname:
            return normalized

        return urlunparse(parsed._replace(query="", fragment=""))

    @staticmethod
    def _canonicalize_xiaohongshu_image_url(url):
        """Normalize Xiaohongshu CDN image URLs for stable comparison/deduplication."""
        if not url:
            return ""
        normalized = str(url).strip()
        if normalized.startswith("//"):
            normalized = f"https:{normalized}"
        normalized = normalized.replace("\\u002F", "/").replace("\\/", "/")
        normalized = normalized.split("?", 1)[0].split("#", 1)[0].split("!", 1)[0]
        return normalized

    @staticmethod
    def _xiaohongshu_image_match_key(url):
        """Build a looser key so the same image can match across resized/CDN variants."""
        normalized = Fetcher._canonicalize_xiaohongshu_image_url(url)
        if not normalized:
            return ""
        parsed = urlparse(normalized)
        path = (parsed.path or "").strip("/")
        if not path:
            return normalized
        parts = [p for p in path.split("/") if p]
        if not parts:
            return normalized
        tail = parts[-1]
        if "." in tail:
            tail = tail.rsplit(".", 1)[0]
        if len(parts) >= 2:
            return "/".join([parts[-2], tail])
        return tail

    @staticmethod
    def _normalize_xiaohongshu_gallery_order(image_urls):
        """
        Apply a pragmatic gallery-order correction for Xiaohongshu notes.
        Current observed pattern in test cases: the last note image is emitted first.
        """
        if not image_urls or len(image_urls) <= 1:
            return image_urls
        return image_urls[1:] + image_urls[:1]

    @staticmethod
    def _fetch_xiaohongshu(url, config, proxy_mode_override=None, custom_proxy_override=None):
        """
        Fetch Xiaohongshu (小红书) content with authentication support.
        Supports both direct URLs and xhslink.com short URLs.
        Requires prior login using --login xiaohongshu
        """
        from playwright.sync_api import sync_playwright

        req_proxies, pw_proxy = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)

        # Check if this is a short link and resolve it
        original_short_url = None
        if "xhslink.com" in url:
            original_short_url = url
            _, url = Fetcher._resolve_xhslink_short_url(url, proxies=req_proxies)
            if not url:
                logger.error("Failed to resolve xhslink short URL")
                return None
            logger.info(f"Using resolved URL for fetching: {url}")

        # Xiaohongshu fetches require an already prepared login state.
        state = AuthHandler.load_state("xiaohongshu")
        if not state:
            logger.error(
                "No saved auth state found for xiaohongshu. Prepare it explicitly with "
                "`surf --login xiaohongshu` on a machine with a desktop session, then "
                "transfer it with `surf --export-auth xiaohongshu <FILE>` / "
                "`surf --import-auth xiaohongshu <FILE>` if this server is headless."
            )
            return None

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, proxy=pw_proxy) if pw_proxy else p.chromium.launch(headless=True)

            # Try to use saved auth state
            context = AuthHandler.create_context_with_auth(
                browser,
                "xiaohongshu",
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            page = context.new_page()

            try:
                logger.info(f"Navigating to: {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)

                # Check if we're still on login page (auth failed or not logged in)
                current_url = page.url
                logger.info(f"Current page URL: {current_url}")

                if "/login" in current_url or "/signin" in current_url:
                    logger.warning(
                        "Saved auth state appears expired for xiaohongshu. Refresh it with "
                        "`surf --login xiaohongshu`, then re-run the fetch. On headless servers, "
                        "re-export the refreshed state and import it there."
                    )
                    return None

                # Extract note content
                # Xiaohongshu note pages have URLs like: https://www.xiaohongshu.com/explore/NOTE_ID
                title = page.evaluate("""() => {
                    const titleEl = document.querySelector('h1.title') ||
                                     document.querySelector('.note-title') ||
                                     document.querySelector('h1');
                    return titleEl?.innerText?.trim() || document.title;
                }
                """)
                logger.info(f"Extracted title: {title[:100] if title else 'None'}...")

                content = page.evaluate("""() => {
                    // Try multiple selectors for note content
                    const selectors = [
                        '.note-content',
                        '.content',
                        '.desc',
                        '.note-desc',
                        '[class*="content"]',
                        '[class*="desc"]'
                    ];
                    
                    for (const selector of selectors) {
                        const el = document.querySelector(selector);
                        if (el && el.innerText && el.innerText.trim().length > 10) {
                            return el.innerHTML;
                        }
                    }
                    
                    // Fallback: get main content area
                    const main = document.querySelector('main') || document.querySelector('article');
                    if (main) return main.innerHTML;
                    
                    return document.body.innerHTML;
                }
                """)

                # Extract note images from note-detail data first; broad JSON scanning
                # tends to pull unrelated UI/media assets from the page.
                images = page.evaluate("""() => {
                    const normalizeUrl = (value) => {
                        if (!value || typeof value !== 'string') {
                            return '';
                        }
                        let normalized = value
                            .trim();
                        normalized = normalized.split('\\\\u002F').join('/');
                        normalized = normalized.split('\\u002F').join('/');
                        normalized = normalized.split('\\\\/').join('/');
                        normalized = normalized.split('\\/').join('/');
                        if (normalized.startsWith('//')) {
                            normalized = `https:${normalized}`;
                        }
                        return normalized;
                    };

                    const ordered = [];
                    const seen = new Set();
                    const pushUrl = (value) => {
                        const url = normalizeUrl(value);
                        if (!url || !url.includes('xhscdn.com') || url.includes('sns-avatar-qc.xhscdn.com') || seen.has(url)) {
                            return;
                        }
                        seen.add(url);
                        ordered.push(url);
                    };

                    const collectFromImageList = (list) => {
                        if (!Array.isArray(list)) {
                            return false;
                        }
                        const before = ordered.length;
                        for (const item of list) {
                            if (!item || typeof item !== 'object') {
                                continue;
                            }
                            const directCandidates = [
                                item.url,
                                item.urlDefault,
                                item.urlPre,
                                item.masterUrl,
                                item.original,
                                item.origin,
                            ];
                            for (const candidate of directCandidates) {
                                pushUrl(candidate);
                            }
                            const infoList = item.infoList || item.imageInfoList || item.variants;
                            if (Array.isArray(infoList)) {
                                for (const info of infoList) {
                                    if (!info || typeof info !== 'object') {
                                        continue;
                                    }
                                    pushUrl(info.url);
                                    pushUrl(info.urlDefault);
                                    pushUrl(info.urlPre);
                                }
                            }
                        }
                        return ordered.length > before;
                    };

                    const searchNoteObjects = (value, depth = 0) => {
                        if (!value || depth > 12) {
                            return false;
                        }
                        if (Array.isArray(value)) {
                            for (const item of value) {
                                if (searchNoteObjects(item, depth + 1)) {
                                    return true;
                                }
                            }
                            return false;
                        }
                        if (typeof value !== 'object') {
                            return false;
                        }

                        const noteLikeLists = [
                            value.imageList,
                            value.imagesList,
                            value.noteImageList,
                            value.noteImages,
                        ];
                        for (const list of noteLikeLists) {
                            if (collectFromImageList(list)) {
                                return true;
                            }
                        }

                        const noteLikeChildren = [
                            value.noteDetailMap,
                            value.noteDetail,
                            value.noteData,
                            value.note,
                            value.noteCard,
                            value.currentNote,
                            value.post,
                            value.data,
                        ];
                        for (const child of noteLikeChildren) {
                            if (searchNoteObjects(child, depth + 1)) {
                                return true;
                            }
                        }

                        for (const key of Object.keys(value)) {
                            const lowerKey = key.toLowerCase();
                            if (
                                lowerKey.includes('notedetail') ||
                                lowerKey.includes('notecard') ||
                                lowerKey.includes('imagelist') ||
                                lowerKey.includes('imageslist')
                            ) {
                                if (searchNoteObjects(value[key], depth + 1)) {
                                    return true;
                                }
                            }
                        }

                        return false;
                    };

                    const parseJsonText = (text) => {
                        if (!text || (!text.includes('imageList') && !text.includes('noteDetail') && !text.includes('noteCard'))) {
                            return false;
                        }
                        try {
                            return searchNoteObjects(JSON.parse(text));
                        } catch {
                            return false;
                        }
                    };

                    const jsonSources = [
                        window.__INITIAL_STATE__,
                        window.__INITIAL_DATA__,
                        window.__NEXT_DATA__,
                        window.__NUXT__,
                    ];
                    for (const source of jsonSources) {
                        if (source && searchNoteObjects(source)) {
                            return ordered;
                        }
                    }

                    const nextData = document.getElementById('__NEXT_DATA__');
                    if (nextData && parseJsonText(nextData.textContent || '')) {
                        return ordered;
                    }

                    for (const script of document.querySelectorAll('script[type="application/json"], script[type="application/ld+json"], script')) {
                        const text = script.textContent || '';
                        if (parseJsonText(text)) {
                            return ordered;
                        }
                    }

                    const getImageUrl = (img) => {
                        const candidates = [
                            img.currentSrc,
                            img.src,
                            img.getAttribute('data-src'),
                            img.getAttribute('data-original'),
                            img.getAttribute('data-xhs-img'),
                            img.getAttribute('data-image'),
                        ];
                        for (const candidate of candidates) {
                            const before = ordered.length;
                            pushUrl(candidate);
                            if (ordered.length > before) {
                                return true;
                            }
                        }
                        return false;
                    };

                    const contentRoots = [
                        document.querySelector('.note-content'),
                        document.querySelector('.content'),
                        document.querySelector('.note-desc'),
                        document.querySelector('[class*="note"]'),
                        document.querySelector('[class*="swiper"]'),
                        document.querySelector('[class*="carousel"]'),
                        document.querySelector('main'),
                        document.querySelector('article'),
                    ].filter(Boolean);

                    for (const root of contentRoots) {
                        for (const img of root.querySelectorAll('img')) {
                            getImageUrl(img);
                        }
                    }

                    if (ordered.length) {
                        return ordered;
                    }

                    const noteScopedRoots = [
                        document.querySelector('main'),
                        document.querySelector('article'),
                        document.querySelector('[class*="note"]'),
                        document.body,
                    ].filter(Boolean);

                    for (const root of noteScopedRoots) {
                        for (const img of root.querySelectorAll('img')) {
                            getImageUrl(img);
                        }
                        if (ordered.length) {
                            return ordered;
                        }
                    }
                    return ordered;
                }
                """)

                logger.info(f"Extracted content length: {len(content) if content else 0} chars, images: {len(images)}")

                # Check if content is too short (might indicate page not fully loaded)
                if not content or len(content.strip()) < 50:
                    logger.warning("Content is too short, page might not have loaded correctly")
                    # Try to get more info about the page
                    page_info = page.evaluate("""() => {
                        return {
                            url: window.location.href,
                            title: document.title,
                            bodyLength: document.body?.innerText?.length || 0
                        };
                    }""")
                    logger.info(f"Page info: {page_info}")

                # Build HTML with full page structure (for cleaning)
                # Store the cleaned URL in a meta tag for later use in metadata
                content_soup = BeautifulSoup(content or "", "html.parser")
                content_image_map = {}
                content_image_key_map = {}
                content_image_list = []
                for img in content_soup.find_all("img"):
                    img_url = (img.get("src") or img.get("data-src") or img.get("data-original") or "").strip()
                    canonical_img_url = Fetcher._canonicalize_xiaohongshu_image_url(img_url)
                    if (
                        canonical_img_url
                        and "xhscdn.com" in canonical_img_url
                        and "sns-avatar-qc.xhscdn.com" not in canonical_img_url
                        and canonical_img_url not in content_image_map
                    ):
                        content_image_map[canonical_img_url] = img_url
                        content_image_list.append(img_url)
                        match_key = Fetcher._xiaohongshu_image_match_key(img_url)
                        if match_key and match_key not in content_image_key_map:
                            content_image_key_map[match_key] = img_url

                ordered_content_images = []
                seen_ordered_images = set()
                for img_url in images:
                    canonical_img_url = Fetcher._canonicalize_xiaohongshu_image_url(img_url)
                    match_key = Fetcher._xiaohongshu_image_match_key(img_url)
                    if (
                        canonical_img_url
                        and canonical_img_url in content_image_map
                        and canonical_img_url not in seen_ordered_images
                    ):
                        ordered_content_images.append(content_image_map[canonical_img_url])
                        seen_ordered_images.add(canonical_img_url)
                    elif match_key and match_key in content_image_key_map:
                        matched_content_url = content_image_key_map[match_key]
                        matched_canonical = Fetcher._canonicalize_xiaohongshu_image_url(matched_content_url)
                        if matched_canonical and matched_canonical not in seen_ordered_images:
                            ordered_content_images.append(matched_content_url)
                            seen_ordered_images.add(matched_canonical)

                gallery_images = ordered_content_images[:]
                if not gallery_images and content_image_list:
                    # Prefer only images that were actually present in the extracted note body.
                    # This avoids unrelated JSON images and duplicate re-insertion.
                    gallery_images = content_image_list[:]
                    for img_url in gallery_images:
                        canonical_img_url = Fetcher._canonicalize_xiaohongshu_image_url(img_url)
                        if canonical_img_url:
                            seen_ordered_images.add(canonical_img_url)

                if not gallery_images:
                    seen_gallery_images = set()
                    for img_url in images:
                        canonical_img_url = Fetcher._canonicalize_xiaohongshu_image_url(img_url)
                        if canonical_img_url and canonical_img_url not in seen_gallery_images:
                            gallery_images.append(img_url)
                            seen_gallery_images.add(canonical_img_url)

                gallery_images = Fetcher._normalize_xiaohongshu_gallery_order(gallery_images)

                if gallery_images:
                    # Rebuild note images once and remove original in-content copies
                    # so the note body keeps exactly one gallery.
                    for img in content_soup.find_all("img"):
                        img_url = (img.get("src") or img.get("data-src") or img.get("data-original") or "").strip()
                        canonical_img_url = Fetcher._canonicalize_xiaohongshu_image_url(img_url)
                        if (
                            canonical_img_url
                            and "xhscdn.com" in canonical_img_url
                            and "sns-avatar-qc.xhscdn.com" not in canonical_img_url
                        ):
                            img.decompose()

                content = str(content_soup)

                html_parts = [
                    "<html><head><meta charset='utf-8'>",
                    f"<title>{title}</title>",
                    f'<meta name="source-url" content="{Fetcher._canonicalize_xiaohongshu_source_url(url)}">',
                    '<meta name="surf-source-site" content="xiaohongshu">',
                    "</head><body><article>",
                    f"<h1>{title}</h1>",
                ]

                # Insert a single ordered gallery when we can align the structured order
                # against images already present in the extracted content block.
                if gallery_images:
                    html_parts.append("<div class='images'>")
                    for img_url in gallery_images:
                        html_parts.append(f'<img src="{img_url}" />')
                    html_parts.append("</div>")

                html_parts.append(content)
                html_parts.append("</article></body></html>")
                html_content = "".join(html_parts)

                # Clean the content to remove unrelated UI elements
                html_content = Fetcher._clean_xiaohongshu_content(html_content)

                return html_content

            except Exception as e:
                logger.error(f"Xiaohongshu handler failed: {e}")
                import traceback

                logger.error(f"Traceback: {traceback.format_exc()}")
                return None
            finally:
                browser.close()

    @staticmethod
    def _is_ncpssd_secure_article_url(url):
        """Whether URL is an NCPSSD secure article detail page."""
        try:
            parsed = urlparse(url)
            host = (parsed.netloc or "").lower()
            path = parsed.path or ""
            return host in {
                "ncpssd.cn",
                "www.ncpssd.cn",
                "ncpssd.org",
                "www.ncpssd.org",
            } and path.startswith("/Literature/secure/articleinfo")
        except Exception:
            return False

    @staticmethod
    def download_ncpssd_pdf(
        url,
        config,
        output_path=None,
        proxy_mode_override=None,
        custom_proxy_override=None,
    ):
        """
        Download the original NCPSSD full-text PDF by clicking the page's `全文下载` button.
        Returns the saved local PDF path on success, otherwise None.
        """
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

        if not Fetcher._is_ncpssd_secure_article_url(url):
            logger.info("NCPSSD direct PDF download skipped: URL is not a secure article detail page")
            return None

        parsed_input = urlparse(url)
        host = (parsed_input.netloc or "").lower()
        effective_url = url
        if host in {"ncpssd.cn", "ncpssd.org"}:
            effective_url = urlunparse(parsed_input._replace(netloc=f"www.{host}"))
            logger.info(f"NCPSSD: normalized download host to {urlparse(effective_url).netloc}")

        _, pw_proxy = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)

        with sync_playwright() as p:
            launch_args = {
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            if pw_proxy:
                launch_args["proxy"] = pw_proxy

            browser = p.chromium.launch(**launch_args)
            context = AuthHandler.create_context_with_auth(
                browser,
                "ncpssd",
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                accept_downloads=True,
            )
            page = context.new_page()

            try:

                def _normalize_ncpssd_text(raw):
                    text = re.sub(r"\s+", " ", str(raw or "")).strip()
                    text = re.sub(r"^(作\s*者\s*[：:])", "", text)
                    text = re.sub(r"^(出\s*版\s*物\s*[：:])", "", text)
                    text = re.sub(
                        r"(中文期刊文章|外文期刊文章|中文图书章节|外文图书章节|古籍文献|学位论文)$",
                        "",
                        text,
                    ).strip()
                    return text.strip()

                def _normalize_ncpssd_author(raw):
                    author = _normalize_ncpssd_text(raw)
                    # Remove common footnotes and transliteration annotations, e.g.:
                    # "田力[1] [Tian Li]" -> "田力"
                    author = re.sub(r"\[\s*\d+(?:\s*[,;，；]\s*\d+)*\s*\]", "", author)
                    author = re.sub(r"\[[^\]]*\]", "", author)
                    author = re.sub(r"\([^)]*\)", "", author)
                    author = re.sub(r"\s*([,;，；])\s*", r"\1", author)
                    author = re.sub(r"\s+", " ", author).strip(" ,;，；")
                    return author

                def _build_ncpssd_pdf_basename(meta):
                    if not isinstance(meta, dict):
                        return None
                    title = _normalize_ncpssd_text(meta.get("title"))
                    creator = _normalize_ncpssd_author(meta.get("creator"))
                    media = _normalize_ncpssd_text(meta.get("media"))
                    parts = [p for p in [title, creator, media] if p]
                    if not parts:
                        return None
                    return OutputHandler._safe_filename_title("-".join(parts), max_len=180)

                page_pdf_basename = None

                def _resolve_output_pdf_path(suggested_filename, preferred_basename=None):
                    suggested = os.path.basename(suggested_filename or "NCPSSD-fulltext.pdf")
                    stem, _ = os.path.splitext(suggested)
                    derived_stem = OutputHandler._safe_filename_title(stem)
                    preferred = OutputHandler._safe_filename_title(preferred_basename) if preferred_basename else None
                    safe_name = f"{(preferred or derived_stem)}.pdf"

                    resolved_output = resolve_user_path(output_path) if output_path else None
                    if resolved_output == "-":
                        logger.warning(
                            "NCPSSD direct PDF download does not support stdout output. "
                            "Falling back to default pdf_dir."
                        )
                        resolved_output = None

                    if resolved_output:
                        is_dir_target = resolved_output.endswith(("/", "\\")) or os.path.isdir(resolved_output)
                        if is_dir_target:
                            os.makedirs(resolved_output, exist_ok=True)
                            return os.path.join(resolved_output, safe_name)

                        filepath = resolved_output
                        filepath_dir = os.path.dirname(filepath)
                        if filepath_dir and not os.path.exists(filepath_dir):
                            os.makedirs(filepath_dir)
                        if not filepath.lower().endswith(".pdf"):
                            filepath = f"{filepath}.pdf"
                        return filepath

                    pdf_dir = config.get_path("Output", "pdf_dir", fallback=".")
                    if not os.path.exists(pdf_dir):
                        os.makedirs(pdf_dir)
                    return os.path.join(pdf_dir, safe_name)

                logger.info("NCPSSD: Opening article page for direct PDF download")
                page.goto(effective_url, wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(2000)
                try:
                    page_meta = page.evaluate("""() => ({
                            title: (document.querySelector('#h2_title_c') || document.querySelector('h1') || {}).innerText || '',
                            creator: (document.querySelector('#p_creator') || {}).innerText || '',
                            media: (document.querySelector('#p_media') || {}).innerText || '',
                            typeName: (document.querySelector('#ftl_urlTypename') || {}).value || '中文期刊文章',
                            lngid: (document.querySelector('#ftl_urlId') || {}).value || '',
                            pageType: window.pageType || 2,
                        })""")
                    if not any((page_meta or {}).get(k) for k in ("title", "creator", "media")):
                        page.wait_for_timeout(1200)
                        page_meta = page.evaluate("""() => ({
                                title: (document.querySelector('#h2_title_c') || document.querySelector('h1') || {}).innerText || '',
                                creator: (document.querySelector('#p_creator') || {}).innerText || '',
                                media: (document.querySelector('#p_media') || {}).innerText || '',
                                typeName: (document.querySelector('#ftl_urlTypename') || {}).value || '中文期刊文章',
                                lngid: (document.querySelector('#ftl_urlId') || {}).value || '',
                                pageType: window.pageType || 2,
                            })""")
                    # Some article pages populate metadata via async API only; fallback to that endpoint.
                    if not any((page_meta or {}).get(k) for k in ("title", "creator", "media")):
                        try:
                            lngid = str((page_meta or {}).get("lngid") or "").strip()
                            if lngid:
                                table_endpoint = f"https://{urlparse(effective_url).netloc}/articleinfoHandler/getjournalarticletable"
                                table_payload = {
                                    "lngid": lngid,
                                    "type": (page_meta.get("typeName") or "中文期刊文章"),
                                    "pageType": page_meta.get("pageType") or 2,
                                }
                                table_resp = context.request.post(
                                    table_endpoint,
                                    data=json.dumps(table_payload, ensure_ascii=False),
                                    headers={
                                        "Content-Type": "application/json; charset=utf-8",
                                        "Referer": effective_url,
                                    },
                                    timeout=30000,
                                )
                                table_text = table_resp.text()
                                table_json = json.loads(table_text) if table_text.strip().startswith("{") else {}
                                table_data = table_json.get("data") if isinstance(table_json, dict) else None
                                if isinstance(table_data, dict):
                                    page_meta = {
                                        **page_meta,
                                        "title": table_data.get("titlec") or page_meta.get("title") or "",
                                        "creator": table_data.get("showwriter") or page_meta.get("creator") or "",
                                        "media": table_data.get("mediac") or page_meta.get("media") or "",
                                    }
                        except Exception:
                            pass
                    page_pdf_basename = _build_ncpssd_pdf_basename(page_meta)
                except Exception:
                    page_pdf_basename = None
                try:
                    page.wait_for_function(
                        """() => {
                            const btn = document.querySelector('#a_down');
                            if (!btn || !window.$) return false;
                            const events = window.$._data(btn, 'events');
                            return !!(events && events.click && events.click.length);
                        }""",
                        timeout=15000,
                    )
                except Exception:
                    logger.info(
                        "NCPSSD: a_down click handler not observed before timeout; continuing with best effort click"
                    )

                # The site binds a click handler to the visible "全文下载" control.
                button = page.locator("a:has-text('全文下载'), button:has-text('全文下载')").first
                if button.count() == 0:
                    button = page.get_by_text("全文下载", exact=False).first
                if button.count() == 0:
                    logger.warning(
                        "NCPSSD: '全文下载' button was not found. If login is required, run `surf --login ncpssd` first."
                    )
                    return None

                captured_read_download_pdf_url = None

                def _capture_download_chain_response(resp):
                    nonlocal captured_read_download_pdf_url
                    if "/Literature/readDownloadUrl" not in resp.url:
                        return
                    try:
                        payload = resp.json()
                    except Exception:
                        return
                    if not isinstance(payload, dict):
                        return
                    data_field = payload.get("data")
                    candidate = None
                    if isinstance(data_field, dict):
                        candidate = data_field.get("url")
                    elif isinstance(data_field, str):
                        candidate = data_field
                    if not candidate:
                        candidate = payload.get("url")
                    if candidate and str(candidate).strip():
                        captured_read_download_pdf_url = str(candidate).strip()

                page.on("response", _capture_download_chain_response)

                # Path A: browser-native download event (works on some pages/versions)
                try:
                    with page.expect_download(timeout=10000) as download_info:
                        button.click(force=True)
                    download = download_info.value
                    filepath = _resolve_output_pdf_path(
                        download.suggested_filename or "NCPSSD-fulltext.pdf",
                        preferred_basename=page_pdf_basename,
                    )
                    download.save_as(filepath)
                    logger.info(f"NCPSSD original PDF saved to {filepath}")
                    return filepath
                except PlaywrightTimeoutError:
                    logger.info(
                        "NCPSSD: No direct download event after clicking '全文下载'; "
                        "trying the site's readurl API path used by the same button."
                    )

                page.wait_for_timeout(1200)
                pdf_url = captured_read_download_pdf_url
                if pdf_url:
                    logger.info("NCPSSD: Captured signed URL from readDownloadUrl response")
                    logger.info(f"NCPSSD: readDownloadUrl url = {pdf_url}")

                # Path B2: reproduce the site's official download chain:
                # /Literature/readDownloadUrl -> ms.util.getMinioSign() -> GET data.url with sign/site/dotype headers
                if not pdf_url:
                    try:
                        download_meta = page.evaluate("""() => ({
                                type: (document.querySelector('#ftl_urlTypename') || {}).value || '中文期刊文章',
                                articleid: (document.querySelector('#ftl_urlId') || {}).value || '',
                                titleC: (document.querySelector('#h2_title_c') || {}).innerText || '',
                                showWriter: (((document.querySelector('#p_creator') || {}).innerText || '')
                                    .replace('作　　者：', '').trim()),
                                periodname: (((document.querySelector('#p_media') || {}).innerText || '')
                                    .replace('出 版 物：', '').trim()),
                                pageType: window.pageType || 2,
                            })""")
                        articleid = str((download_meta or {}).get("articleid") or "").strip()
                        if articleid:
                            rd_payload = {
                                "type": (download_meta.get("type") or "中文期刊文章"),
                                "articleid": articleid,
                                "id": articleid,
                                "periodname": download_meta.get("periodname") or "",
                                "downcount": 1,
                                "readcount": -1,
                                "gch": "",
                                "class": "",
                                "titleC": download_meta.get("titleC") or "",
                                "showWriter": download_meta.get("showWriter") or "",
                                "pageType": download_meta.get("pageType") or 2,
                            }
                            rd_endpoint = f"https://{urlparse(effective_url).netloc}/Literature/readDownloadUrl"
                            rd_resp = context.request.post(
                                rd_endpoint,
                                data=json.dumps(rd_payload, ensure_ascii=False),
                                headers={
                                    "Content-Type": "application/json; charset=utf-8",
                                    "Referer": effective_url,
                                },
                                timeout=30000,
                            )
                            rd_text = rd_resp.text()
                            rd_json = json.loads(rd_text) if rd_text.strip().startswith("{") else {}
                            candidate = None
                            if isinstance(rd_json, dict):
                                data_field = rd_json.get("data")
                                if isinstance(data_field, dict):
                                    candidate = data_field.get("url")
                                elif isinstance(data_field, str):
                                    candidate = data_field
                                if not candidate:
                                    candidate = rd_json.get("url")
                            if candidate:
                                pdf_url = str(candidate).strip()
                                logger.info(f"NCPSSD: readDownloadUrl (explicit) url = {pdf_url}")

                                sign = page.evaluate("""() => new Promise((resolve) => {
                                        try {
                                            if (window.ms && ms.util && typeof ms.util.getMinioSign === 'function') {
                                                ms.util.getMinioSign(function (s) { resolve(s || ''); });
                                            } else {
                                                resolve('');
                                            }
                                        } catch (e) {
                                            resolve('');
                                        }
                                    })""")
                                if sign:
                                    pdf_resp = context.request.get(
                                        pdf_url,
                                        timeout=60000,
                                        headers={
                                            "sign": str(sign),
                                            "site": "npssd",
                                            "dotype": "down",
                                            "Referer": effective_url,
                                        },
                                    )
                                    pdf_body = pdf_resp.body()
                                    final_ct = (pdf_resp.headers.get("content-type") or "").lower()
                                    if "application/pdf" in final_ct or pdf_body.startswith(b"%PDF"):
                                        parsed_pdf_url = urlparse(pdf_url)
                                        suggested_name = os.path.basename(parsed_pdf_url.path) or f"{articleid}.pdf"
                                        filepath = _resolve_output_pdf_path(
                                            suggested_name,
                                            preferred_basename=page_pdf_basename,
                                        )
                                        with open(filepath, "wb") as f:
                                            f.write(pdf_body)
                                        logger.info(f"NCPSSD original PDF saved to {filepath}")
                                        return filepath
                    except Exception as explicit_chain_error:
                        logger.info(f"NCPSSD: explicit readDownloadUrl/sign chain failed: {explicit_chain_error}")

                # Path B3: fallback path -> /Literature/readurl?id=... -> final PDF URL.
                article_id = (
                    page.locator("#ftl_urlId").input_value().strip() if page.locator("#ftl_urlId").count() > 0 else ""
                )
                logger.info(f"NCPSSD: article id = {article_id or '<empty>'}")
                if not article_id:
                    logger.warning("NCPSSD: Failed to resolve article id (`#ftl_urlId`) for readurl API")
                    return None

                parsed_effective = urlparse(effective_url)
                preferred_www = parsed_effective.netloc or "www.ncpssd.cn"
                preferred_bare = preferred_www[4:] if preferred_www.startswith("www.") else preferred_www
                readurl_endpoints = [
                    f"https://{preferred_www}/Literature/readurl?id={article_id}",
                    f"https://{preferred_bare}/Literature/readurl?id={article_id}",
                    "https://www.ncpssd.cn/Literature/readurl?id=" + article_id,
                    "https://ncpssd.cn/Literature/readurl?id=" + article_id,
                    "https://www.ncpssd.org/Literature/readurl?id=" + article_id,
                    "https://ncpssd.org/Literature/readurl?id=" + article_id,
                ]

                if not pdf_url:
                    for endpoint in readurl_endpoints:
                        try:
                            resp = context.request.get(
                                endpoint,
                                timeout=30000,
                                headers={"Referer": effective_url},
                            )
                            if resp.status >= 400:
                                logger.info(f"NCPSSD: readurl endpoint HTTP {resp.status}: {endpoint}")
                                continue
                            ct = (resp.headers.get("content-type") or "").lower()
                            body = resp.body()
                            logger.info(f"NCPSSD: readurl endpoint ok ct={ct} url={endpoint}")

                            if "application/json" in ct or body[:1] == b"{":
                                try:
                                    payload = json.loads(body.decode("utf-8", errors="ignore"))
                                    candidate = payload.get("url") if isinstance(payload, dict) else None
                                    if candidate:
                                        pdf_url = str(candidate).strip()
                                        logger.info(f"NCPSSD: readurl payload url = {pdf_url}")
                                        break
                                except Exception:
                                    pass

                            if "application/pdf" in ct and body:
                                filepath = _resolve_output_pdf_path(
                                    f"{article_id}.pdf",
                                    preferred_basename=page_pdf_basename,
                                )
                                with open(filepath, "wb") as f:
                                    f.write(body)
                                logger.info(f"NCPSSD original PDF saved to {filepath}")
                                return filepath
                        except Exception as endpoint_error:
                            logger.debug(f"NCPSSD readurl endpoint attempt failed ({endpoint}): {endpoint_error}")

                if not pdf_url:
                    logger.warning("NCPSSD: readurl API did not return a downloadable PDF URL")
                    return None

                pdf_resp = context.request.get(
                    pdf_url,
                    timeout=60000,
                    headers={"Referer": effective_url},
                )
                if pdf_resp.status >= 400:
                    logger.warning(f"NCPSSD: final PDF URL request failed with HTTP {pdf_resp.status}")
                    return None

                pdf_body = pdf_resp.body()
                final_ct = (pdf_resp.headers.get("content-type") or "").lower()
                if "application/pdf" not in final_ct and not pdf_body.startswith(b"%PDF"):
                    logger.warning(
                        "NCPSSD: final URL did not return PDF bytes "
                        "(likely permission denied for this account/article)."
                    )
                    return None

                parsed_pdf_url = urlparse(pdf_url)
                suggested_name = os.path.basename(parsed_pdf_url.path) or f"{article_id}.pdf"
                filepath = _resolve_output_pdf_path(
                    suggested_name,
                    preferred_basename=page_pdf_basename,
                )
                with open(filepath, "wb") as f:
                    f.write(pdf_body)
                logger.info(f"NCPSSD original PDF saved to {filepath}")
                return filepath

            except PlaywrightTimeoutError:
                logger.warning(
                    "NCPSSD: Timed out waiting for download after clicking '全文下载'. "
                    "If the page requires login, run `surf --login ncpssd`."
                )
                return None
            except Exception as e:
                logger.warning(f"NCPSSD direct PDF download failed: {e}")
                return None
            finally:
                try:
                    context.close()
                except Exception:
                    pass
                browser.close()

    @staticmethod
    def _fetch_ncpssd_article(url, config, proxy_mode_override=None, custom_proxy_override=None):
        """
        Fetch NCPSSD (国家哲学社会科学文献中心) literature pages.
        Mandates: force browser, no translation, h1 title, full content capture.
        """
        from playwright.sync_api import sync_playwright

        _, pw_proxy = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)

        with sync_playwright() as p:
            launch_args = {
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            if pw_proxy:
                launch_args["proxy"] = pw_proxy

            browser = p.chromium.launch(**launch_args)
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            page = context.new_page()

            try:
                page.goto(url, wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(3000)

                # Extract data using JS
                data = page.evaluate("""() => {
                    const getVal = (id) => document.getElementById(id)?.innerText?.trim() || "";

                    // User mandated: Use h1 as title
                    const title = document.querySelector('h1')?.innerText?.trim() || document.title;
                    const title_e = getVal('h3_title_e');
                    const creator = getVal('p_creator');
                    const institutions = getVal('p_institutions');
                    const media = getVal('p_media');
                    const year = getVal('p_year');
                    const abstract_z = getVal('p_remark');
                    const abstract_e = getVal('p_remarke');
                    const page_range = getVal('p_page');
                    const page_count = getVal('p_pagination');
                    const keywords = getVal('p_keyword');
                    const classification = getVal('p_class');

                    return {
                        title, title_e, creator, institutions, media, year,
                        abstract_z, abstract_e, page_range, page_count, keywords, classification
                    };
                }""")

                # Construct HTML
                html_parts = [
                    "<html><head><meta charset='utf-8'>",
                    f"<title>{data['title']}</title>",
                    "</head><body><article>",
                    f"<h1>{data['title']}</h1>",
                ]

                if data["title_e"]:
                    html_parts.append(f"<h2>{data['title_e']}</h2>")
                if data["creator"]:
                    html_parts.append(f"<p>{data['creator']}</p>")
                if data["institutions"]:
                    html_parts.append(f"<p>{data['institutions']}</p>")
                if data["media"]:
                    html_parts.append(f"<p>{data['media']}</p>")
                if data["year"]:
                    html_parts.append(f"<p>{data['year']}</p>")
                if data["abstract_z"]:
                    html_parts.append(f"<div><h3>中文摘要</h3><p>{data['abstract_z']}</p></div>")
                if data["abstract_e"]:
                    html_parts.append(f"<div><h3>英文摘要</h3><p>{data['abstract_e']}</p></div>")
                if data["keywords"]:
                    html_parts.append(f"<p>{data['keywords']}</p>")
                if data["page_range"]:
                    html_parts.append(f"<p>{data['page_range']}</p>")
                if data["page_count"]:
                    html_parts.append(f"<p>{data['page_count']}</p>")
                if data["classification"]:
                    html_parts.append(f"<p>{data['classification']}</p>")

                html_parts.append("</article></body></html>")
                return "".join(html_parts)

            except Exception as e:
                logger.warning(f"NCPSSD handler failed: {e}")
                return None
            finally:
                browser.close()

    @staticmethod
    def _fetch_github_readme(url, config, proxy_mode_override=None, custom_proxy_override=None):
        """
        Fetch GitHub repository README content.
        Extracts README from <article class="markdown-body"> element.
        Prefers language-specific READMEs (e.g., README_zh.md) if available.
        Removes permalink anchors (aria-label^="Permalink:") to clean up output.
        """
        direct_markdown = Fetcher._fetch_github_markdown(url, config, proxy_mode_override, custom_proxy_override)
        if direct_markdown:
            return direct_markdown

        import re
        from urllib.parse import urlparse
        from playwright.sync_api import sync_playwright

        logger.info(f"Fetching GitHub README: {url}")

        # Parse URL to get owner and repo
        parsed = urlparse(url)
        path_match = re.match(r"^/([^/]+)/([^/]+)", parsed.path)
        if not path_match:
            logger.warning(f"Invalid GitHub URL format: {url}")
            return None

        owner, repo = path_match.groups()

        _, pw_proxy = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)

        with sync_playwright() as p:
            launch_args = {
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            if pw_proxy:
                launch_args["proxy"] = pw_proxy

            browser = p.chromium.launch(**launch_args)
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            page = context.new_page()

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)

                # Check for language-specific README links
                readme_data = page.evaluate("""() => {
                    // Look for language-specific README links in the file list
                    const links = Array.from(document.querySelectorAll('a'));
                    const readmeLinks = links.filter(a => {
                        const href = a.getAttribute('href') || '';
                        return /\\/README_[a-z]{2}(\\.md)?$/i.test(href);
                    });

                    // Get the repo title - try multiple selectors for robustness
                    let title = '';
                    const titleSelectors = [
                        'h1 strong[itemprop="name"]',
                        'h1 [itemprop="name"]',
                        '.repository-content h1',
                        '[data-testid="repo-title"]',
                        'h1[class*="title"]',
                        'h1',
                    ];
                    for (const selector of titleSelectors) {
                        const el = document.querySelector(selector);
                        if (el && el.innerText) {
                            title = el.innerText.trim();
                            break;
                        }
                    }

                    // Get description - try multiple selectors
                    let description = '';
                    const descSelectors = [
                        '[data-testid="about-description"] p',
                        '[data-testid="repository-description"]',
                        '[data-testid="about-description"]',
                        '.repository-content .BorderGrid-cell p',
                        '.repository-content p',
                        '[class*="description"]',
                    ];
                    for (const selector of descSelectors) {
                        const el = document.querySelector(selector);
                        if (el && el.innerText) {
                            description = el.innerText.trim();
                            break;
                        }
                    }

                    return { readmeLinks: readmeLinks.map(a => a.href), title, description };
                }
                """)

                target_url = url
                # Check if we should navigate to a language-specific README
                target_lang = config.get("Output", "target_language", fallback="zh-cn")
                lang_code = target_lang.split("-")[0]  # e.g., "zh" from "zh-cn"

                if readme_data.get("readmeLinks"):
                    for link in readme_data["readmeLinks"]:
                        if f"README_{lang_code}" in link or f"readme_{lang_code}" in link.lower():
                            target_url = link if link.startswith("http") else f"https://github.com{link}"
                            logger.info(f"Found language-specific README: {target_url}")
                            break

                # Navigate to the target README URL if different
                if target_url != url:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(2000)

                # Extract README content
                html_content = page.evaluate("""() => {
                    const article = document.querySelector('article.markdown-body');
                    if (!article) return null;

                    // Remove permalink anchors
                    const permalinks = article.querySelectorAll('a[aria-label^="Permalink:"]');
                    permalinks.forEach(a => a.remove());

                    // Also remove anchor links with symbol
                    const anchorLinks = article.querySelectorAll('a.anchor');
                    anchorLinks.forEach(a => a.remove());

                    return article.outerHTML;
                }
                """)

                if not html_content:
                    logger.warning("No README content found in article.markdown-body")
                    return None

                # Construct full HTML document
                title = readme_data.get("title", repo)
                description = readme_data.get("description", "")

                html_parts = [
                    "<!DOCTYPE html>",
                    '<html lang="en"><head>',
                    '<meta charset="utf-8">',
                    f"<title>{title}</title>",
                    "</head><body>",
                    f"<h1>{title}</h1>",
                ]

                if description:
                    html_parts.append(f"<p><strong>Description:</strong> {description}</p>")

                html_parts.append(f'<p><strong>Repository:</strong> <a href="{url}">{url}</a></p>')
                html_parts.append(html_content)
                html_parts.append("</body></html>")

                return "".join(html_parts)

            except Exception as e:
                logger.warning(f"GitHub handler failed: {e}")
                return None
            finally:
                browser.close()

    @staticmethod
    def _fetch_arxiv(url, config, proxy_mode_override=None, custom_proxy_override=None):
        """
        Fetch arxiv paper content.

        Strategy: For /abs/ URLs, redirect to the /html/ version which has the full paper content.
        The /abs/ page often only returns the BibTeX modal due to Readability extraction issues,
        while the /html/ version has well-structured content in <div class="ltx_page_main">.

        Also extracts metadata (title, authors, abstract) from the abstract page and includes it.
        """
        from urllib.parse import urlparse

        parsed = urlparse(url)
        path = parsed.path

        # Extract paper ID from various arxiv URL formats
        # e.g., /abs/2605.15184, /abs/2605.15184v1, /pdf/2605.15184
        paper_id_match = re.match(r"/(?:abs|pdf|html)/(\d{4}\.\d{4,5}(?:v\d+)?)", path)
        if not paper_id_match:
            logger.warning(f"Cannot extract paper ID from arxiv URL: {url}")
            return None

        paper_id_full = paper_id_match.group(1)
        # Strip version suffix for HTML URL construction
        paper_id = re.sub(r"v\d+$", "", paper_id_full)

        logger.info(f"Fetching arxiv paper: {paper_id}")

        _, pw_proxy = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)

        # Fetch the abstract page to extract metadata
        abs_url = f"https://arxiv.org/abs/{paper_id}"
        html_url = f"https://arxiv.org/html/{paper_id}v1"

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            abs_response = _requests_get_with_system_trust_interruptibly(abs_url, headers=headers, timeout=15)
            abs_response.raise_for_status()
            abs_html = Fetcher._decode_response_text(abs_response)
        except Exception as e:
            logger.warning(f"Failed to fetch arxiv abstract page: {e}")
            abs_html = ""

        # Extract metadata from abstract page using regex (no BS4 dependency needed)
        title = ""
        abstract = ""
        authors = []
        subjects = ""
        created_time = ""

        title_match = re.search(r'<h1 class="title mathjax"><span class="descriptor">Title:</span>\s*(.*?)</h1>', abs_html, re.DOTALL)
        if title_match:
            title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip()

        abstract_match = re.search(r'<blockquote class="abstract mathjax">\s*<span class="descriptor">Abstract:</span>\s*(.*?)</blockquote>', abs_html, re.DOTALL)
        if abstract_match:
            abstract = re.sub(r"<[^>]+>", "", abstract_match.group(1)).strip()

        authors_match = re.search(r'<div class="authors"><span class="descriptor">Authors:</span>(.*?)</div>', abs_html, re.DOTALL)
        if authors_match:
            authors = [re.sub(r"<[^>]+>", "", a).strip() for a in re.findall(r"<a[^>]*>(.*?)</a>", authors_match.group(1))]

        subjects_match = re.search(r'<span class="primary-subject">(.*?)</span>', abs_html)
        if subjects_match:
            subjects = re.sub(r"<[^>]+>", "", subjects_match.group(1)).strip()

        # Extract initial submission time from submission history
        # Format: [v1] Thu, 14 May 2026 17:58:41 UTC (516 KB)
        submission_match = re.search(r'\[v1\].*?(\w+,\s+\d+\s+\w+\s+\d{4}\s+\d{2}:\d{2}:\d{2}\s+UTC)', abs_html, re.DOTALL)
        if submission_match:
            raw_time = submission_match.group(1).strip()
            try:
                from email.utils import parsedate_to_datetime
                from datetime import datetime, timezone
                # Parse: "Thu, 14 May 2026 17:58:41 UTC"
                dt = datetime.strptime(raw_time, "%a, %d %b %Y %H:%M:%S %Z")
                dt = dt.replace(tzinfo=timezone.utc)
                created_time = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                created_time = raw_time

        # Fetch the HTML version of the paper
        try:
            html_response = _requests_get_with_system_trust_interruptibly(html_url, headers=headers, timeout=30)
            html_response.raise_for_status()
            paper_html = Fetcher._decode_response_text(html_response)
        except Exception as e:
            logger.warning(f"Failed to fetch arxiv HTML version: {e}")
            paper_html = ""

        if not paper_html:
            # Fallback: try to extract from abstract page directly
            # The abstract page has the content in #abs div
            if abs_html:
                abs_content_match = re.search(r'<div id="content-inner">(.*?)<div class="submission-history">', abs_html, re.DOTALL)
                if abs_content_match:
                    content = abs_content_match.group(1)
                    content = re.sub(r'<div class="extra-services">.*', "", content, flags=re.DOTALL)
                    content = re.sub(r'<div class="endorsers">.*', "", content, flags=re.DOTALL)
                    return f"<html><head><meta charset='utf-8'><title>{title}</title></head><body>{content}</body></html>"
            return None

        # Extract main content from HTML version
        main_content_match = re.search(r'<div class="ltx_page_main">(.*?)</div>\s*</div>\s*<footer', paper_html, re.DOTALL)
        if not main_content_match:
            # Try broader match
            main_content_match = re.search(r'<div class="ltx_page_main">(.*?)<footer', paper_html, re.DOTALL)

        main_content = main_content_match.group(1) if main_content_match else ""

        if not main_content:
            # Try article tag
            article_match = re.search(r"<article[^>]*>(.*?)</article>", paper_html, re.DOTALL)
            main_content = article_match.group(1) if article_match else ""

        # Build final HTML with metadata
        from html import escape
        html_parts = [
            "<!DOCTYPE html>",
            "<html><head><meta charset='utf-8'>",
            "<meta name='surf-source-site' content='arxiv'>",
        ]

        if authors:
            html_parts.append(f"<meta name='surf-author' content='{escape(', '.join(authors))}'>")
        if created_time:
            html_parts.append(f"<meta name='surf-created' content='{escape(created_time)}'>")

        html_parts.append(f"<title>{title}</title>")
        html_parts.append("</head><body>")

        if title:
            html_parts.append(f"<h1>{title}</h1>")

        if authors:
            html_parts.append(f"<p><strong>Authors:</strong> {', '.join(authors)}</p>")

        if subjects:
            html_parts.append(f"<p><strong>Subjects:</strong> {subjects}</p>")

        html_parts.append(f'<p><strong>arXiv:</strong> <a href="{abs_url}">{paper_id}</a></p>')

        if abstract:
            html_parts.append(f"<div><h2>Abstract</h2><p>{abstract}</p></div>")

        if main_content:
            html_parts.append(main_content)

        html_parts.append("</body></html>")

        return "".join(html_parts)

    @staticmethod
    def _fetch_wikipedia(url, config, proxy_mode_override=None, custom_proxy_override=None):
        """
        Fetch Wikipedia article with content optimization.
        Removes citation marks, fixes table captions, and cleans up navigation elements.
        """
        from playwright.sync_api import sync_playwright

        logger.info(f"Fetching Wikipedia article: {url}")

        _, pw_proxy = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)

        with sync_playwright() as p:
            launch_args = {
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            if pw_proxy:
                launch_args["proxy"] = pw_proxy

            browser = p.chromium.launch(**launch_args)
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            page = context.new_page()

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2000)

                html_content = page.evaluate("""() => {
                    const content = document.querySelector('#mw-content-text .mw-parser-output');
                    if (!content) return null;

                    // Clone to avoid modifying the actual DOM
                    const clone = content.cloneNode(true);

                    // Remove citation reference links (superscript numbers in brackets)
                    const citations = clone.querySelectorAll('sup.reference, sup a[href^="#cite_note"]');
                    citations.forEach(el => el.remove());

                    // Remove edit section links
                    const editLinks = clone.querySelectorAll('.mw-editsection, .mw-editsection-bracket');
                    editLinks.forEach(el => el.remove());

                    // Remove table of contents
                    const toc = clone.querySelector('#toc, .toc, .toccolours');
                    if (toc) toc.remove();

                    // Fix table captions - move them before the table
                    const tables = clone.querySelectorAll('table');
                    tables.forEach(table => {
                        const caption = table.querySelector('caption');
                        if (caption && caption.parentElement === table) {
                            // Caption is already in correct position for most browsers
                            // Just ensure it's visible
                            caption.style.display = 'table-caption';
                        }
                    });

                    // Remove ambox (article message boxes)
                    const amboxes = clone.querySelectorAll('.ambox, .mbox, .tmbox');
                    amboxes.forEach(el => el.remove());

                    // Remove navbox (navigation templates at bottom)
                    const navboxes = clone.querySelectorAll('.navbox, .navbox-inner, .navbox-subgroup');
                    navboxes.forEach(el => el.remove());

                    // Remove infobox if it's too large (optional, keep for now but style it)
                    const infobox = clone.querySelector('.infobox');
                    if (infobox) {
                        infobox.style.maxWidth = '300px';
                        infobox.style.float = 'right';
                        infobox.style.margin = '0 0 1em 1em';
                    }

                    // Remove hatnotes (disambiguation notices)
                    const hatnotes = clone.querySelectorAll('.hatnote, .dablink, .rellink');
                    hatnotes.forEach(el => el.remove());

                    // Remove "See also", "References", "External links" section headers if empty
                    const headings = clone.querySelectorAll('h2, h3');
                    headings.forEach(h => {
                        const nextEl = h.nextElementSibling;
                        if (nextEl && (nextEl.tagName === 'H2' || nextEl.tagName === 'H3')) {
                            h.remove();
                        }
                    });

                    // Remove empty paragraphs
                    const paragraphs = clone.querySelectorAll('p');
                    paragraphs.forEach(p => {
                        if (!p.textContent.trim()) {
                            p.remove();
                        }
                    });

                    return clone.innerHTML;
                }
                """)

                if not html_content:
                    logger.warning("No Wikipedia content found")
                    return None

                # Get title
                title = page.evaluate("""() => {
                    return document.querySelector('#firstHeading')?.innerText?.trim() ||
                           document.querySelector('h1')?.innerText?.trim() ||
                           document.title;
                }
                """)

                html_parts = [
                    "<!DOCTYPE html>",
                    '<html lang="en"><head>',
                    '<meta charset="utf-8">',
                    f"<title>{title}</title>",
                    "<style>",
                    "table { border-collapse: collapse; margin: 1em 0; }",
                    "table, th, td { border: 1px solid #ccc; padding: 0.5em; }",
                    "caption { font-weight: bold; margin-bottom: 0.5em; }",
                    ".infobox { background: #f9f9f9; }",
                    "</style>",
                    "</head><body>",
                    f"<h1>{title}</h1>",
                    html_content,
                    "</body></html>",
                ]

                return "".join(html_parts)

            except Exception as e:
                logger.warning(f"Wikipedia handler failed: {e}")
                return None
            finally:
                browser.close()

    @staticmethod
    def _fetch_bluesky(
        url,
        config,
        proxy_mode_override=None,
        custom_proxy_override=None,
        fetch_thread=None,
        fetch_thread_author=None,
    ):
        """
        Fetch Bluesky post using the official public API.
        Uses app.bsky.feed.getPostThread to get the post and its replies.
        """
        import re
        import requests
        from urllib.parse import urlparse

        logger.info(f"Fetching Bluesky post via API: {url}")

        # Parse URL to extract handle and post ID
        # URL format: https://bsky.app/profile/handle.bsky.social/post/postid
        parsed = urlparse(url)
        path_match = re.match(r"^/profile/([^/]+)/post/([^/]+)", parsed.path)

        if not path_match:
            logger.warning(f"Invalid Bluesky URL format: {url}")
            return None

        handle, post_id = path_match.groups()

        # Construct the at:// URI required by the API
        # We need to resolve the handle to a DID first
        try:
            req_proxies, _ = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)

            headers = {
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }

            # Resolve handle to DID
            resolve_url = f"https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle={handle}"
            resolve_resp = _requests_get_interruptibly(resolve_url, headers=headers, proxies=req_proxies, timeout=30)

            if resolve_resp.status_code != 200:
                logger.warning(f"Failed to resolve Bluesky handle: {resolve_resp.text}")
                return None

            did = resolve_resp.json().get("did")
            if not did:
                logger.warning("No DID found for handle")
                return None

            # Construct at:// URI
            at_uri = f"at://{did}/app.bsky.feed.post/{post_id}"

            # Fetch post thread
            api_url = f"https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread?uri={at_uri}"
            resp = _requests_get_interruptibly(api_url, headers=headers, proxies=req_proxies, timeout=30)

            if resp.status_code != 200:
                logger.warning(f"Bluesky API request failed: {resp.status_code} - {resp.text}")
                return None

            data = resp.json()
            thread = data.get("thread", {})
            post = thread.get("post", {})
            record = post.get("record", {})

            if not record:
                logger.warning("No post record found in Bluesky API response")
                return None

            author = post.get("author", {})
            original_author_key = author.get("did") or author.get("handle") or author.get("displayName") or "unknown"

            thread_items = []

            def append_post_item(post_data):
                if not isinstance(post_data, dict):
                    return
                record_data = post_data.get("record", {})
                author_data = post_data.get("author", {})
                author_name = author_data.get("displayName") or author_data.get("handle", "Unknown")
                author_handle = author_data.get("handle", "")
                created_at = record_data.get("createdAt", "")
                thread_items.append(
                    {
                        "author": author_name,
                        "author_key": Fetcher._normalize_thread_author_key(
                            author_data.get("did") or author_handle or author_name
                        ),
                        "handle": f"@{author_handle}" if author_handle else "",
                        "timestamp": created_at,
                        "text": record_data.get("text", ""),
                        "media": Fetcher._extract_bluesky_embed_blocks(
                            post_data,
                            fallback_did=author_data.get("did") or did,
                        ),
                        "permalink": url if post_data is post else "",
                    }
                )

            thread_mode = Fetcher._normalize_thread_mode(fetch_thread)
            thread_author = Fetcher._normalize_thread_author(fetch_thread_author)
            if thread_mode in {"before", "both"}:
                parent = thread.get("parent")
                ancestors = []
                while isinstance(parent, dict):
                    parent_post = parent.get("post", {})
                    parent_author = parent_post.get("author", {})
                    parent_author_key = (
                        parent_author.get("did")
                        or parent_author.get("handle")
                        or parent_author.get("displayName")
                        or ""
                    )
                    if thread_author == "same" and parent_author_key != original_author_key:
                        break
                    ancestors.append(parent_post)
                    parent = parent.get("parent")
                for ancestor in reversed(ancestors):
                    append_post_item(ancestor)

            append_post_item(post)

            current_index = len(thread_items) - 1

            if thread_mode in {"after", "both"}:
                if thread_author == "all":
                    def append_reply_tree(reply_nodes):
                        for reply in reply_nodes or []:
                            if not isinstance(reply, dict):
                                continue
                            reply_post = reply.get("post", {})
                            append_post_item(reply_post)
                            append_reply_tree(reply.get("replies", []))

                    append_reply_tree(thread.get("replies", []))
                else:
                    replies = thread.get("replies", [])
                    cursor = replies[0] if replies else None
                    while isinstance(cursor, dict):
                        reply_post = cursor.get("post", {})
                        reply_author = reply_post.get("author", {})
                        reply_author_key = (
                            reply_author.get("did")
                            or reply_author.get("handle")
                            or reply_author.get("displayName")
                            or ""
                        )
                        if reply_author_key != original_author_key:
                            break
                        append_post_item(reply_post)
                        nested_replies = cursor.get("replies", [])
                        cursor = nested_replies[0] if nested_replies else None

            html_content = Fetcher._render_social_thread_html("bluesky", url, thread_items, current_index)

            return html_content

        except Exception as e:
            logger.warning(f"Bluesky handler failed: {e}")
            return None

    @staticmethod
    def _get_v2ex_proxy_overrides(config, proxy_mode_override, custom_proxy_override):
        """V2EX is commonly unreachable directly, so prefer configured proxy unless explicitly overridden."""
        if proxy_mode_override is not None:
            return proxy_mode_override, custom_proxy_override

        custom_proxy = (config.get("Network", "custom_proxy", fallback="") or "").strip()
        if custom_proxy:
            logger.info("V2EX: Forcing configured custom proxy")
            return "custom", custom_proxy_override

        config_mode = Fetcher._normalize_proxy_mode(config.get("Network", "proxy_mode", fallback="env"))
        if config_mode and config_mode != "no":
            logger.info(f"V2EX: Forcing configured proxy mode '{config_mode}'")
            return config_mode, custom_proxy_override

        logger.warning("V2EX: No proxy configured; direct access may fail")
        return proxy_mode_override, custom_proxy_override

    @staticmethod
    def _v2ex_topic_page_url(url, page_number):
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["p"] = [str(page_number)]
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                urlencode(query, doseq=True),
                parsed.fragment,
            )
        )

    @staticmethod
    def _v2ex_markdown_from_node(node):
        if not node:
            return ""
        html = str(node)
        markdown = markdownify.markdownify(html, heading_style="ATX")
        markdown = re.sub(r"\n{3,}", "\n\n", markdown)
        return markdown.strip()

    @staticmethod
    def _canonicalize_v2ex_source_url(url):
        if not url:
            return url
        try:
            parsed = urlparse(url)
        except Exception:
            return url
        host = (parsed.netloc or "").split("@")[-1].split(":")[0].lower()
        if host.startswith("www."):
            host = host[4:]
        if host not in {"v2ex.com", "cn.v2ex.com"}:
            return url
        fragment = parsed.fragment or ""
        if re.fullmatch(r"reply\d+", fragment, re.IGNORECASE):
            return urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    parsed.params,
                    parsed.query,
                    "",
                )
            )
        return url

    @staticmethod
    def _extract_v2ex_topic_page(html_content, page_url, include_replies=False):
        soup = BeautifulSoup(html_content, "html.parser")
        title_node = soup.select_one("#Main .box .header h1") or soup.find("h1")
        title = re.sub(r"\s+", " ", title_node.get_text(" ", strip=True)).strip() if title_node else "V2EX Topic"

        header = title_node.find_parent(class_="header") if title_node else soup.select_one("#Main .box .header")
        author = None
        published = None
        node_name = None
        if header:
            small = header.find("small", class_="gray")
            if small:
                author_link = small.find("a", href=re.compile(r"^/member/"))
                if author_link:
                    author = author_link.get_text(" ", strip=True)
                time_span = small.find("span", attrs={"title": True})
                if time_span:
                    published = time_span.get("title")
            breadcrumbs = header.select(".flex-one-row a")
            if breadcrumbs:
                node_name = breadcrumbs[-1].get_text(" ", strip=True)

        topic_content = soup.select_one("#Main .topic_content")
        topic_markdown = Fetcher._v2ex_markdown_from_node(topic_content)
        if not topic_markdown:
            return None

        lines = []
        meta = []
        if author:
            meta.append(f"Author: {author}")
        if node_name:
            meta.append(f"Node: {node_name}")
        if published:
            meta.append(f"Published: {published}")
        meta.append(f"Source: {page_url}")
        lines.extend(meta)
        lines.append("")
        lines.append(topic_markdown)

        replies = []
        max_page = 1
        for link in soup.find_all("a", href=True):
            match = re.search(r"[?&]p=(\d+)", link.get("href") or "")
            if match:
                max_page = max(max_page, int(match.group(1)))

        if include_replies:
            for cell in soup.select('#Main .cell[id^="r_"]'):
                reply_content = cell.select_one(".reply_content")
                if not reply_content:
                    continue
                floor_node = cell.select_one(".no")
                author_node = cell.select_one('a.dark[href^="/member/"]')
                time_node = cell.select_one(".ago[title]")
                floor = floor_node.get_text(" ", strip=True) if floor_node else "?"
                reply_author = author_node.get_text(" ", strip=True) if author_node else "Unknown"
                reply_time = time_node.get("title") if time_node else ""
                reply_markdown = Fetcher._v2ex_markdown_from_node(reply_content)
                if reply_markdown:
                    replies.append(
                        {
                            "floor": floor,
                            "author": reply_author,
                            "time": reply_time,
                            "markdown": reply_markdown,
                        }
                    )

        return {
            "title": title,
            "markdown_lines": lines,
            "replies": replies,
            "max_page": max_page,
        }

    @staticmethod
    def _fetch_v2ex_topic(
        url,
        config,
        proxy_mode_override=None,
        custom_proxy_override=None,
        fetch_thread=None,
        fetch_thread_author=None,
    ):
        """Fetch V2EX topic pages as structured Markdown, with replies only when requested."""
        try:
            proxy_mode_override, custom_proxy_override = Fetcher._get_v2ex_proxy_overrides(
                config, proxy_mode_override, custom_proxy_override
            )
            req_proxies, _ = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)
            include_replies = fetch_thread is not None and fetch_thread is not False
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }

            response = _requests_get_interruptibly(url, headers=headers, proxies=req_proxies, timeout=20)
            response.raise_for_status()
            html_content = Fetcher._decode_response_text(response)
            first_page = Fetcher._extract_v2ex_topic_page(
                html_content, response.url or url, include_replies=include_replies
            )
            if not first_page:
                return None

            markdown_lines = first_page["markdown_lines"]
            replies = list(first_page["replies"])
            seen_reply_keys = {(reply["floor"], reply["author"], reply["markdown"]) for reply in replies}

            if include_replies and first_page["max_page"] > 1:
                for page_number in range(2, first_page["max_page"] + 1):
                    page_url = Fetcher._v2ex_topic_page_url(response.url or url, page_number)
                    try:
                        page_response = _requests_get_interruptibly(
                            page_url, headers=headers, proxies=req_proxies, timeout=20
                        )
                        page_response.raise_for_status()
                        page_html = Fetcher._decode_response_text(page_response)
                        page_data = Fetcher._extract_v2ex_topic_page(
                            page_html, page_response.url or page_url, include_replies=True
                        )
                        if not page_data:
                            continue
                        for reply in page_data["replies"]:
                            key = (reply["floor"], reply["author"], reply["markdown"])
                            if key in seen_reply_keys:
                                continue
                            seen_reply_keys.add(key)
                            replies.append(reply)
                    except Exception as exc:
                        logger.warning(f"V2EX: Failed to fetch replies page {page_number}: {exc}")

            if include_replies and replies:
                markdown_lines.extend(["", "## Replies", ""])
                for reply in replies:
                    heading = f"### #{reply['floor']} {reply['author']}"
                    markdown_lines.append(heading)
                    if reply["time"]:
                        markdown_lines.append("")
                        markdown_lines.append(reply["time"])
                    markdown_lines.append("")
                    markdown_lines.append(reply["markdown"])
                    markdown_lines.append("")

            markdown_text = "\n".join(markdown_lines).strip() + "\n"
            return _build_direct_markdown_payload(
                markdown_text=markdown_text,
                title=first_page["title"],
                source_url=response.url or url,
                site_name="v2ex",
            )
        except Exception as e:
            logger.warning(f"V2EX handler failed: {e}")
            return None

    @staticmethod
    def _is_reddit_url(url):
        return bool(
            url
            and re.match(
                r"^https?://((www|old|new)\.)?reddit\.com/|^https?://redd\.it/",
                url,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _is_douban_url(url):
        return bool(url and re.match(r"^https?://([^.]+\.)?douban\.com/", url, re.IGNORECASE))

    @staticmethod
    def _normalize_douban_url(url):
        if not url:
            return url
        try:
            parsed = urlparse(url)
        except Exception:
            return url
        host = (parsed.netloc or "").split("@")[-1].split(":")[0].lower()
        if host.startswith("www."):
            host = host[4:]
        if host != "douban.com":
            return url
        path = parsed.path or ""
        if path != "/doubanapp/dispatch":
            return url

        query_params = parse_qs(parsed.query, keep_blank_values=True)
        uri_values = query_params.get("uri") or []
        if not uri_values:
            return url
        decoded_uri = unquote(uri_values[0]).strip()
        if not decoded_uri:
            return url
        if decoded_uri.startswith("/"):
            return urlunparse(("https", "www.douban.com", decoded_uri, "", "", ""))
        if re.match(r"^https?://([^.]+\.)?douban\.com/", decoded_uri, re.IGNORECASE):
            return decoded_uri
        return url

    @staticmethod
    def _normalize_reddit_url(url):
        """Normalize Reddit URLs to canonical post/comment URLs on www.reddit.com."""
        parsed = urlparse(url)
        hostname = (parsed.netloc or "").lower()
        path = parsed.path or "/"

        if hostname == "redd.it":
            post_id = path.strip("/").split("/", 1)[0]
            if post_id:
                return f"https://www.reddit.com/comments/{post_id}"

        if hostname in {"old.reddit.com", "new.reddit.com"}:
            parsed = parsed._replace(netloc="www.reddit.com")

        return urlunparse(parsed)

    @staticmethod
    def _build_reddit_json_url(url):
        """Build the Reddit JSON endpoint URL for a post/comment permalink."""
        normalized = Fetcher._normalize_reddit_url(url)
        parsed = urlparse(normalized)
        path = parsed.path.rstrip("/")
        if not path:
            return normalized
        if not path.endswith(".json"):
            path = f"{path}.json"
        query_pairs = parse_qs(parsed.query, keep_blank_values=True)
        query_pairs["raw_json"] = ["1"]
        return urlunparse(
            parsed._replace(
                path=path,
                query=urlencode(query_pairs, doseq=True),
                fragment="",
            )
        )

    @staticmethod
    def _get_reddit_headers(referer_url, cookie_header=None):
        headers = {
            "User-Agent": "surf/1.1 (+https://github.com/dodorz/surf)",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": referer_url,
        }
        if cookie_header:
            headers["Cookie"] = cookie_header
        return headers

    @staticmethod
    def _markdownify_reddit_html(html_fragment):
        """Convert Reddit HTML fragments from JSON fields into markdown."""
        if not html_fragment:
            return ""
        try:
            html_text = unescape(html_fragment)
            markdown_text = markdownify.markdownify(html_text, heading_style="ATX")
            markdown_text = re.sub(r"\n{3,}", "\n\n", markdown_text or "")
            return markdown_text.strip()
        except Exception:
            return ""

    @staticmethod
    def _extract_reddit_comment_items(children, current_items=None, depth=0):
        if current_items is None:
            current_items = []

        for child in children or []:
            if not isinstance(child, dict) or child.get("kind") != "t1":
                continue
            data = child.get("data") or {}
            body_markdown = Fetcher._markdownify_reddit_html(data.get("body_html")) or (data.get("body") or "").strip()
            author = (data.get("author") or "[deleted]").strip()
            item = {
                "kind": "comment",
                "id": data.get("id") or "",
                "author": author,
                "author_key": Fetcher._normalize_thread_author_key(author),
                "depth": depth,
                "score": data.get("score"),
                "created_time": Fetcher._format_unix_timestamp(data.get("created_utc")),
                "permalink": data.get("permalink") or "",
                "markdown": body_markdown,
            }
            current_items.append(item)
            replies = data.get("replies")
            if isinstance(replies, dict):
                reply_listing = ((replies.get("data") or {}).get("children") or [])
                Fetcher._extract_reddit_comment_items(reply_listing, current_items=current_items, depth=depth + 1)

        return current_items

    @staticmethod
    def _build_reddit_markdown_payload(post_data, comment_items, source_url, include_comments=False):
        title = (post_data.get("title") or "Reddit Post").strip() or "Reddit Post"
        subreddit = (post_data.get("subreddit_name_prefixed") or "").strip()
        author = (post_data.get("author") or "[deleted]").strip()
        created_time = Fetcher._format_unix_timestamp(post_data.get("created_utc"))
        score = post_data.get("score")
        comment_count = post_data.get("num_comments")
        is_self = bool(post_data.get("is_self"))
        outbound_url = (post_data.get("url_overridden_by_dest") or post_data.get("url") or "").strip()

        lines = []
        if subreddit:
            lines.append(f"Subreddit: {subreddit}")
        if author:
            lines.append(f"Author: {author}")
        if created_time:
            lines.append(f"Posted: {created_time}")
        if isinstance(score, int):
            lines.append(f"Score: {score}")
        if isinstance(comment_count, int):
            lines.append(f"Comments: {comment_count}")
        lines.append(f"Source: {source_url}")
        lines.append("")

        body_markdown = Fetcher._markdownify_reddit_html(post_data.get("selftext_html")) or (post_data.get("selftext") or "").strip()
        if body_markdown:
            lines.append(body_markdown)
            lines.append("")

        if not is_self and outbound_url and outbound_url != source_url:
            lines.append(f"Link: {outbound_url}")
            lines.append("")

        if include_comments and comment_items:
            lines.extend(["## Comments", ""])
            for item in comment_items:
                indent = "  " * min(int(item.get("depth") or 0), 8)
                heading = f"{indent}- {item.get('author') or '[deleted]'}"
                metadata = []
                if isinstance(item.get("score"), int):
                    metadata.append(f"score {item['score']}")
                if item.get("created_time"):
                    metadata.append(item["created_time"])
                if metadata:
                    heading += f" ({', '.join(metadata)})"
                lines.append(heading)
                lines.append("")
                comment_md = (item.get("markdown") or "").strip() or "[deleted]"
                for para in comment_md.splitlines():
                    lines.append(f"{indent}  {para}" if para else "")
                lines.append("")

        markdown_text = "\n".join(lines).strip() + "\n"
        return _build_direct_markdown_payload(
            markdown_text=markdown_text,
            title=title,
            source_url=source_url,
            site_name="reddit",
        )

    @staticmethod
    def _fetch_reddit_content(
        url,
        config,
        proxy_mode_override=None,
        custom_proxy_override=None,
        fetch_thread=None,
        fetch_thread_author=None,
    ):
        """Fetch Reddit posts via the comments JSON endpoint, optionally using saved login cookies."""
        try:
            normalized_url = Fetcher._normalize_reddit_url(url)
            json_url = Fetcher._build_reddit_json_url(normalized_url)
            req_proxies, _ = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)
            cookie_header = AuthHandler.cookie_header_for_reddit()
            headers = Fetcher._get_reddit_headers(normalized_url, cookie_header=cookie_header)
            response = _requests_get_with_system_trust_interruptibly(
                json_url,
                headers=headers,
                proxies=req_proxies,
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list) or len(payload) < 2:
                return None

            post_children = ((payload[0].get("data") or {}).get("children") or [])
            if not post_children:
                return None
            post_entry = post_children[0]
            if not isinstance(post_entry, dict) or post_entry.get("kind") != "t3":
                return None
            post_data = post_entry.get("data") or {}
            permalink = (post_data.get("permalink") or "").strip()
            source_url = urljoin("https://www.reddit.com", permalink) if permalink else normalized_url

            comment_listing = (payload[1].get("data") or {}).get("children") or []
            comment_items = Fetcher._extract_reddit_comment_items(comment_listing)

            thread_mode = Fetcher._normalize_thread_mode(fetch_thread, default_mode=None)
            thread_author = Fetcher._normalize_thread_author(fetch_thread_author)
            include_comments = thread_mode in {"after", "both"}
            if thread_mode == "before":
                logger.info("reddit: --thread before is not applicable for top-level post fetch; ignoring.")
            if include_comments and thread_author == "same":
                original_author_key = Fetcher._normalize_thread_author_key(post_data.get("author"))
                comment_items = [
                    item for item in comment_items if item.get("author_key") == original_author_key
                ]

            return Fetcher._build_reddit_markdown_payload(
                post_data,
                comment_items,
                source_url,
                include_comments=include_comments,
            )
        except Exception as e:
            logger.warning(f"Reddit handler failed: {e}")
            return None

    # ─────────────────────────────────────────────────────────────────
    # Paywall detection signals
    # ─────────────────────────────────────────────────────────────────
    _KNOWN_PAYWALL_DOMAINS = {
        "medium.com", "nytimes.com", "wsj.com", "bloomberg.com",
        "ft.com", "economist.com", "washingtonpost.com", "latimes.com",
        "theatlantic.com", "newyorker.com", "hbr.org",
        "barrons.com", "businessinsider.com", "telegraph.co.uk",
        "thetimes.co.uk", "scientificamerican.com", "technologyreview.com",
        "newrepublic.com", "foreignaffairs.com", "nationalgeographic.com",
        "vanityfair.com", "vogue.com", "newyorkmag.com", "nymag.com",
        "seattletimes.com", "bostonglobe.com", "chicagotribune.com",
        "sfchronicle.com", "staradvertiser.com", "denverpost.com",
        "dallasnews.com", "orlandosentinel.com", "sun-sentinel.com",
        "theinformation.com", "themarkup.org", "restofworld.org",
        "defector.com", "404media.co", "theguardian.com",
        "independent.co.uk", "haaretz.com", "timesofisrael.com",
        "lemonde.fr", "lefigaro.fr", "lesechos.fr", "spiegel.de",
        "zeit.de", "faz.net", "sueddeutsche.de", "handelsblatt.com",
        "corriere.it", "ilsole24ore.com", "elpais.com", "elmundo.es",
        "expansion.com", "nikkei.com", "asahi.com", "mainichi.jp",
        "caixinglobal.com", "thepaper.cn", "jiemian.com",
    }
    _PAYWALL_CSS_PATTERNS = re.compile(
        r"(paywall|paywall-content|paywall-overlay|paywall-banner|paywall-modal|"
        r"metered-content|metered-paywall|meter-wall|meter_overlay|meter-modal|"
        r"article-blur|article-locked|article-gated|content-gate|content-locked|"
        r"premium-content|premium-article|premium-wall|premium-gate|"
        r"subscriber-content|subscriber-only|subscriber-wall|subscription-wall|"
        r"register-wall|sign-in-wall|login-wall|login-required|"
        r"hard-paywall|soft-paywall|dynamic-paywall|regwall|reg-wall|"
        r"gate-content|gated-content|locked-content|blocked-content|"
        r"barrier|piano-paywall|piano-container|piano-wall|"
        r"tp-modal|tp-container|tp-backdrop|tp-iframe|"
        r"fc-ab-root|fc-dialog-container|fc-dialog-overlay|"
        r"evolok-paywall|pelcro-paywall|zephr-paywall|"
        r"wall\.paywall|wall-paywall|content-gating)",
        re.IGNORECASE,
    )
    _PAYWALL_TEXT_PATTERNS = [
        re.compile(r"To\s+(continue\s+)?read(ing)?\s+(this\s+)?(article|story|content)", re.IGNORECASE),
        re.compile(r"Subscribe\s+(to|for|now)\s+(continue|read|unlock|access)", re.IGNORECASE),
        re.compile(r"(Sign|Log)\s*in\s+to\s+(continue|read|access|view)", re.IGNORECASE),
        re.compile(r"Create\s+(a|your|an)\s+(free\s+)?account\s+to\s+(continue|read)", re.IGNORECASE),
        re.compile(r"You('ve| have) reached (your|the) (free\s+)?article\s+limit", re.IGNORECASE),
        re.compile(r"(free|complimentary)\s+articles?\s+(remaining|left)", re.IGNORECASE),
        re.compile(r"This\s+(article|content)\s+is\s+(reserved|exclusive)\s+for\s+subscribers", re.IGNORECASE),
        re.compile(r"Unlock\s+(this|the)\s+(article|content|story)", re.IGNORECASE),
        re.compile(r"Start\s+your\s+(free\s+)?(trial|subscription)", re.IGNORECASE),
        re.compile(r"(become|join)\s+(a|our)\s+(member|subscriber)", re.IGNORECASE),
        re.compile(r"view(s)?\s+(our|subscription|access)\s+options", re.IGNORECASE),
        re.compile(r"register\s+(now|today|free)\s+to\s+(read|continue|access)", re.IGNORECASE),
        re.compile(r"(already\s+(a|our)\s+)?subscriber\?(\s*sign\s*in)?", re.IGNORECASE),
        re.compile(r"Enjoying\s+this\s+article\?", re.IGNORECASE),
        re.compile(r"Subscribe\s+for\s+unlimited\s+access", re.IGNORECASE),
    ]
    _PAYWALL_META_KEYWORDS = {
        "paywall", "metered", "premium", "subscriber-only", "subscription", "gated",
    }
    _ANTI_BOT_CAPTCHA_DOMAINS = {
        "captcha-delivery.com", "geo.captcha-delivery.com",
        "challenges.cloudflare.com", "hcaptcha.com", "recaptcha.net",
        "gstatic.com/recaptcha",
    }
    _ANTI_BOT_TEXT_PATTERNS = [
        re.compile(r"please\s+enable\s+(js|javascript)\s+and\s+disable", re.IGNORECASE),
        re.compile(r"(verify|confirm)\s+(you|that\s+you)\s+(are|'?re)\s+(a\s+)?human", re.IGNORECASE),
        re.compile(r"checking\s+(if\s+the\s+site\s+connection\s+is\s+secure|your\s+browser)", re.IGNORECASE),
        re.compile(r"(dd\s*=\s*\{|data-cfasync\s*=\s*[\"']false[\"'])", re.IGNORECASE),
    ]

    @staticmethod
    def _is_cloudflare_challenge(html_content):
        if not html_content:
            return False
        lower = html_content.lower()
        if "challenges.cloudflare.com" in lower:
            return True
        for pattern in Fetcher._ANTI_BOT_TEXT_PATTERNS:
            if pattern.search(lower):
                return True
        return False

    # ─────────────────────────────────────────────────────────────────
    # Paywall detection
    # ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _detect_paywall(html_content, url=None):
        """Multi-signal paywall detection.  Returns {detected, reason, confidence}."""
        if not html_content:
            return {"detected": False, "reason": None, "confidence": 0.0}
        signals = []
        confidence = 0.0

        # ── Known domain ──
        if url:
            try:
                host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
                host = ".".join(host.rsplit(".", 2)[-3:]) if host.count(".") >= 2 else host
                for domain in Fetcher._KNOWN_PAYWALL_DOMAINS:
                    if host.endswith(domain):
                        signals.append(f"known paywall domain: {domain}")
                        confidence += 0.3
                        break
            except Exception:
                pass

        soup = BeautifulSoup(html_content, "html.parser")

        # ── Anti-bot CAPTCHA on known domain ──
        if confidence >= 0.3:
            captcha_detected = False
            for tag_name, attr in [("script", "src"), ("iframe", "src")]:
                for el in soup.find_all(tag_name):
                    val = (el.get(attr) or "").lower()
                    for cd in Fetcher._ANTI_BOT_CAPTCHA_DOMAINS:
                        if cd in val:
                            captcha_detected = True
                            signals.append(f"anti-bot CAPTCHA: {cd}")
                            break
                    if captcha_detected:
                        break
                if captcha_detected:
                    break
            if not captcha_detected:
                text_raw = soup.get_text(" ", strip=True)
                for ap in Fetcher._ANTI_BOT_TEXT_PATTERNS:
                    if ap.search(text_raw):
                        captcha_detected = True
                        signals.append("anti-bot text pattern matched")
                        break
            if captcha_detected:
                confidence += 0.40

        # ── Bare-domain title ──
        title_tag = soup.find("title")
        title_text = title_tag.get_text(" ", strip=True) if title_tag else ""
        if title_text and url:
            host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
            title_clean = title_text.lower().rstrip("/")
            host_bare = ".".join(host.rsplit(".", 2)[-3:]) if host.count(".") >= 2 else host
            if title_clean in {host, host_bare, host.lstrip("www."), host.removeprefix("www.")}:
                signals.append(f"bare-domain title: {title_text}")
                confidence += 0.45
            elif re.match(r"^[\w.-]+\.[a-z]{2,}$", title_clean, re.IGNORECASE):
                tp = title_clean.split(".")
                hp = host.lstrip("www.").split(".")
                if len(tp) >= 2 and len(hp) >= 2 and tp[-2:] == hp[-2:]:
                    signals.append(f"bare-domain title: {title_text}")
                    confidence += 0.40

        # ── CSS class / id patterns ──
        paywall_elements = []
        for element in soup.select(
            "[class*='paywall'], [id*='paywall'], "
            "[class*='metered'], [class*='meter-'], [class*='premium'], "
            "[class*='subscriber'], [class*='subscription'], "
            "[class*='register-wall'], [class*='sign-in-wall'], [class*='login-wall'], "
            "[class*='content-gate'], [class*='content-locked'], "
            "[class*='article-locked'], [class*='article-gated'], "
            "[data-paywall], [data-metered], "
            "[class*='tp-modal'], [class*='tp-container'], [class*='tp-backdrop'], "
            "[class*='fc-ab-root'], [class*='fc-dialog'], "
            "[class*='piano-paywall'], [class*='pelcro'], [class*='zephr']"
        ):
            paywall_elements.append(element)
        for tag in soup.find_all(True):
            cls = (tag.get("class") or [])
            cls_str = " ".join(cls) if isinstance(cls, list) else str(cls)
            elem_id = str(tag.get("id") or "")
            if Fetcher._PAYWALL_CSS_PATTERNS.search(f"{cls_str} {elem_id}"):
                if tag not in paywall_elements:
                    paywall_elements.append(tag)
        if paywall_elements:
            signals.append(f"paywall CSS elements: {len(paywall_elements)} found")
            confidence += 0.50

        # ── Paywall text snippets ──
        text_content = soup.get_text(" ", strip=True).lower()
        text_matches = 0
        for pattern in Fetcher._PAYWALL_TEXT_PATTERNS:
            if pattern.search(text_content):
                text_matches += 1
        if text_matches >= 2:
            signals.append(f"paywall text patterns: {text_matches} matched")
            confidence += 0.35
        elif text_matches == 1:
            signals.append(f"paywall text pattern: 1 matched")
            confidence += 0.20

        # ── Meta tag keywords ──
        for meta in soup.find_all("meta"):
            combined = " ".join([
                (meta.get("name") or "").lower(),
                (meta.get("content") or "").lower(),
                (meta.get("property") or "").lower(),
            ])
            for keyword in Fetcher._PAYWALL_META_KEYWORDS:
                if keyword in combined:
                    signals.append(f"meta keyword: {keyword}")
                    confidence += 0.10
                    break

        # ── Content truncation ──
        if confidence >= 0.3:
            article_text = ""
            for sel in ("article", "[role='main']", "main",
                        ".post-content", ".article-content", ".story-content",
                        ".article-body", ".post-body"):
                article_el = soup.select_one(sel)
                if article_el:
                    article_text = article_el.get_text(" ", strip=True)
                    break
            if not article_text:
                article_text = text_content
            if len(article_text) < 800:
                signals.append(f"truncated content: {len(article_text)} chars")
                confidence += 0.15

        # ── Explicit free-content signals ──
        # Some sites (e.g. Wired) mark articles as free via meta tags.
        # Respect these to avoid false positives on mixed free/paid domains.
        for meta in soup.find_all("meta"):
            prop = (meta.get("property") or meta.get("name") or "").lower()
            content = (meta.get("content") or "").lower()
            if prop == "article:content_tier" and content in ("free", "non-subscriber", "non-subscription"):
                signals.append(f"explicit free tier: {prop}={content}")
                confidence -= 0.60
                break
            if prop == "article:access" and content == "public":
                signals.append(f"explicit free tier: {prop}={content}")
                confidence -= 0.60
                break

        detected = confidence >= 0.50
        return {
            "detected": detected,
            "reason": "; ".join(signals) if signals else None,
            "confidence": max(min(confidence, 1.0), 0.0),
        }

    # ─────────────────────────────────────────────────────────────────
    # archive.is snapshot fallback
    # ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _has_captcha(page):
        try:
            found = page.locator(
                "iframe[src*='recaptcha'], iframe[src*='captcha'], "
                "div.g-recaptcha, div[class*='captcha'], "
                ".g-recaptcha, #captcha, [id*='captcha']"
            )
            return found.count() > 0
        except Exception:
            return False

    @staticmethod
    def _wait_for_captcha_resolution(page, step_label, timeout_seconds=120, *, headless=False):
        import time as _time
        if not Fetcher._has_captcha(page):
            return True
        if headless:
            logger.info(
                f"archive.is: CAPTCHA detected ({step_label}) in headless mode; "
                "will retry with visible browser."
            )
            return False
        logger.info(
            f"archive.is: CAPTCHA detected ({step_label}). "
            f"Waiting for manual completion (timeout: {timeout_seconds}s)..."
        )
        print(f"\n{'─' * 56}")
        print(f"  ⚠ archive.is 需要完成 CAPTCHA 验证（{step_label}）")
        print(f"  请在打开的浏览器窗口中完成验证，程序将自动继续...")
        print(f"  超时时间：{timeout_seconds} 秒")
        print(f"{'─' * 56}\n")
        deadline = _time.monotonic() + timeout_seconds
        while _time.monotonic() < deadline:
            _time.sleep(2)
            try:
                has = Fetcher._has_captcha(page)
            except Exception:
                # Playwright hiccup — assume page may have changed, check
                # again next cycle.
                logger.debug("archive.is: CAPTCHA check failed transiently, retrying...")
                continue
            if not has:
                logger.info("archive.is: CAPTCHA resolved by user.")
                _time.sleep(1.5)
                return True
            # Periodic progress message
            remaining = max(0, deadline - _time.monotonic())
            if remaining < 10 or int(remaining) % 20 == 0:
                logger.debug(f"archive.is: still waiting for CAPTCHA... (~{int(remaining)}s left)")
        logger.warning("archive.is: CAPTCHA was not completed within the timeout.")
        return False

    @staticmethod
    def _fetch_archiveis_snapshot(url, config, proxy_mode_override=None, custom_proxy_override=None):
        """
        Try to fetch the latest archive.is snapshot for *url*.

        Navigates directly to ``https://archive.is/<url>``, handles
        CAPTCHA (headless → visible fallback), picks the latest snapshot
        from the listing page, and returns ``(html_content, snapshot_url)``
        or ``(None, None)``.
        """
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
        except ImportError:
            logger.warning("Playwright is not installed; cannot fetch from archive.is")
            return None, None

        logger.info(f"Trying archive.is snapshot for: {url}")

        _, pw_proxy = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)
        browser_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-web-security", "--no-first-run",
            "--no-default-browser-check", "--disable-infobars",
            "--disable-background-timer-throttling",
            "--disable-popup-blocking", "--disable-extensions",
        ]

        def _launch_browser(p, headless):
            kwargs = {"headless": headless, "args": list(browser_args)}
            if pw_proxy:
                kwargs["proxy"] = pw_proxy
            return p.chromium.launch(**kwargs)

        def _run_workflow(page, headless=False):
            """Core archive.is flow: go to listing page → pick snapshot → extract."""

            def _ensure_no_captcha(label, timeout_s=60):
                import time as _time
                _time.sleep(1.5)
                return Fetcher._wait_for_captcha_resolution(
                    page, label, timeout_seconds=timeout_s, headless=headless,
                )

            # ── navigate to archive.is/<url> ──
            listing_url = f"https://archive.is/{quote(url, safe='')}"
            logger.info(f"archive.is: opening listing page: {listing_url}")
            try:
                page.goto(listing_url, wait_until="domcontentloaded", timeout=20000)
            except PlaywrightTimeoutError:
                logger.warning("archive.is: timeout loading listing page")
                return None, None
            # Let the page settle — listing pages may have deferred scripts.
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(2000)
            if not _ensure_no_captcha("listing page", timeout_s=120):
                return None, None

            # ── find snapshot ──
            snapshot_url = None
            current_url = page.url

            # Did archive.is redirect us directly to a snapshot?
            if re.search(r"archive\.(is|ph|today|fo|li|vn|md)/[0-9a-zA-Z]{3,}$", current_url, re.IGNORECASE):
                if "run=1" not in current_url and "search=" not in current_url.lower():
                    snapshot_url = current_url
                    logger.info(f"archive.is: redirected to snapshot: {snapshot_url}")

            if not snapshot_url:
                # Scan links for snapshot shortcodes
                all_links = page.locator("a[href]")
                link_count = all_links.count()
                candidates = []
                for i in range(min(link_count, 200)):
                    try:
                        href = all_links.nth(i).get_attribute("href") or ""
                    except Exception:
                        continue
                    if not href or href.startswith("#") or href.startswith("javascript:"):
                        continue
                    # Match snapshot shortcodes: /<alphanum>  (e.g. /WOQ7d)
                    # But NOT the listing /search/run URLs
                    abs_href = urljoin("https://archive.is/", href)
                    m = re.search(
                        r"archive\.(is|ph|today|fo|li|vn|md)/([0-9a-zA-Z]{3,20})$",
                        abs_href, re.IGNORECASE,
                    )
                    if m:
                        code = m.group(2)
                        # Exclude known non-snapshot paths
                        if code.lower() in {"search", "submit", "faq", "about", "blog", "domain"}:
                            continue
                        # Try to get text/label for this link
                        try:
                            link_text = all_links.nth(i).inner_text().strip()[:200]
                        except Exception:
                            link_text = ""
                        candidates.append((abs_href, link_text, i))
                        logger.info(f"archive.is: candidate snapshot [{i}]: {abs_href}  ({link_text[:60]})")
                if candidates:
                    # First one is usually the latest
                    snapshot_url = candidates[0][0]
                    logger.info(f"archive.is: selected latest snapshot: {snapshot_url}")

            if not snapshot_url:
                logger.warning(f"archive.is: no snapshot found for {url}")
                return None, None

            # ── open snapshot page ──
            # archive.is injects tracking pixels into archived pages that
            # keep the network busy forever, so we use domcontentloaded
            # (DOM ready) instead of networkidle, then wait manually.
            _snap_loaded = False
            for _attempt in range(3):
                logger.info(f"archive.is: navigating to snapshot: {snapshot_url}")
                try:
                    page.goto(snapshot_url, wait_until="domcontentloaded", timeout=30000)
                    _snap_loaded = True
                except PlaywrightTimeoutError:
                    logger.warning(
                        f"archive.is: timeout loading snapshot page (attempt {_attempt+1}/3)"
                    )
                    continue
                page.wait_for_timeout(3000)
                if not _ensure_no_captcha("snapshot page", timeout_s=30):
                    continue
                # Give embedded content a moment to render.
                try:
                    page.wait_for_load_state("load", timeout=10000)
                except PlaywrightTimeoutError:
                    pass
                break

            if not _snap_loaded:
                logger.warning("archive.is: failed to load snapshot page after 3 attempts")
                return None, None

            # Archive.is typically embeds the real content inside an
            # iframe within #DIVALREADYARCHIVEDPAGE.  page.content()
            # only returns the outer frame, so we must reach into the
            # iframe when present.
            html_content = page.content()

            content_iframe = page.locator("#DIVALREADYARCHIVEDPAGE iframe").first
            if content_iframe.count() > 0:
                logger.info("archive.is: content is in an iframe, switching to frame content")
                try:
                    frame = content_iframe.content_frame()
                    if frame is not None:
                        html_content = frame.content()
                except Exception as exc:
                    logger.warning(f"archive.is: could not read content iframe: {exc}")

            if not html_content or len(html_content) < 500:
                logger.warning("archive.is: snapshot content too short")
                return None, None

            # Remove archive.is toolbar / framing elements while
            # keeping #DIVALREADYARCHIVEDPAGE which contains the actual
            # archived page content.
            soup = BeautifulSoup(html_content, "html.parser")
            for toolbar in soup.select(
                "#HEADER, #__teaser, "
                "#disclaimer, #toolbar, .toolbar, #archive-header"
            ):
                toolbar.decompose()

            # Rewrite every relative URL to be absolute against the
            # archive.is snapshot URL.  The <base> tag approach is not
            # sufficient because _convert_markdown_urls_to_absolute uses
            # its own base URL (source_url) and ignores the <base> tag.
            # By physically rewriting URLs we make the base URL choice
            # irrelevant for downstream processing.
            _base = snapshot_url.rstrip("/") + "/"
            _url_attrs = (
                "src", "href", "srcset", "data-src", "data-original",
                "data-url", "data-srcset", "poster", "content",
            )
            for tag in soup.find_all(True):
                for attr in _url_attrs:
                    val = tag.get(attr)
                    if not val:
                        continue
                    if attr == "srcset":
                        parts = []
                        for entry in val.split(","):
                            entry = entry.strip()
                            tokens = entry.rsplit(" ", 1)
                            url_part = tokens[0].strip()
                            resolved = urljoin(_base, url_part)
                            if len(tokens) == 2:
                                parts.append(f"{resolved} {tokens[1]}")
                            else:
                                parts.append(resolved)
                        tag[attr] = ", ".join(parts)
                    elif attr == "content":
                        # Only rewrite if it looks like a URL
                        if re.match(r"^https?://", val, re.IGNORECASE) or not re.match(r"^https?://|^data:", val, re.IGNORECASE):
                            tag[attr] = urljoin(_base, val)
                    else:
                        resolved = urljoin(_base, val)
                        if resolved != val or not re.match(r"^(https?://|data:)", val, re.IGNORECASE):
                            tag[attr] = resolved

            html_content = str(soup)

            logger.info(f"archive.is: snapshot fetched successfully: {snapshot_url}")
            return html_content, snapshot_url

        # ── Main: headless first, then visible fallback ──
        try:
            with sync_playwright() as p:
                browser = _launch_browser(p, headless=True)
                context = Fetcher._create_stealth_context(browser, "https://archive.is/")
                page = context.new_page()
                page.set_default_timeout(30000)
                html_result, snap_url = _run_workflow(page, headless=True)
                try:
                    browser.close()
                except Exception:
                    pass
                if html_result is not None:
                    return html_result, snap_url

                logger.info(
                    "archive.is: headless attempt failed; "
                    "retrying with visible browser for manual CAPTCHA completion..."
                )
                print(
                    "\n" + "=" * 60 + "\n"
                    + "  archive.is 需要人工完成 CAPTCHA 验证\n"
                    + "  正在打开可见浏览器窗口...\n"
                    + "  请在新窗口中完成验证，完成后程序将自动继续\n"
                    + "=" * 60 + "\n"
                )
                browser2 = _launch_browser(p, headless=False)
                context2 = Fetcher._create_stealth_context(browser2, "https://archive.is/")
                page2 = context2.new_page()
                page2.set_default_timeout(30000)
                html_result, snap_url = _run_workflow(page2, headless=False)
                browser2.close()
                return html_result, snap_url
        except Exception as e:
            logger.warning(f"archive.is fetch failed: {e}")
            return None, None


# =============================================================================
# Special Site Handlers
# =============================================================================
# Sites that require special handling beyond regular HTML fetching.
# Each entry specifies the handler function and URL patterns.
#
# Structure:
#   'site_name': {
#       'patterns': [list of regex patterns to match URLs],
#       'handler': function(config, url, proxies) -> html_content or None
#   }
#
# If the handler returns None, falls back to regular fetching.
# =============================================================================

SPECIAL_SITE_HANDLERS = {
    "twitter": {
        "patterns": [
            r"^https?://(www\.)?twitter\.com/",
            r"^https?://(www\.)?x\.com/",
        ],
        "handler": Fetcher._fetch_twitter_content,
        "default_thread": True,
    },
    "wechat": {
        "patterns": [
            r"^https?://mp\.weixin\.qq\.com/s/",
            r"^https?://mp\.weixin\.qq\.com/.*__biz=",
        ],
        "handler": Fetcher._fetch_wechat_article,
        "default_no_proxy": True,  # Default: don't use proxy (can be overridden by command line)
        "default_no_translate": True,  # Default: don't translate (can be overridden by command line)
    },
    "zhihu": {
        "patterns": [
            r"^https?://(www\.)?zhihu\.com/question/\d+/answer/\d+",
            r"^https?://zhuanlan\.zhihu\.com/p/\d+",
            r"^https?://(www\.)?zhihu\.com/p/\d+",
        ],
        "handler": Fetcher._fetch_zhihu_content,
        "default_no_proxy": True,
        "default_no_translate": True,
        "no_generic_fallback": True,
    },
    "xiaohongshu": {
        "patterns": [
            r"^https?://(www\.)?xiaohongshu\.com/explore/",
            r"^https?://(www\.)?xiaohongshu\.com/discovery/item/",
            r"^https?://(www\.)?xiaohongshu\.com/user/profile/",
            r"^https?://xhslink\.com/",
        ],
        "handler": Fetcher._fetch_xiaohongshu,
        "default_no_proxy": True,  # Default: don't use proxy (can be overridden by command line)
        "default_no_translate": True,  # Default: don't translate (can be overridden by command line)
        "default_ocr_images": True,
    },
    "ncpssd": {
        "patterns": [
            r"^https?://ncpssd\.cn/Literature/",
            r"^https?://ncpssd\.org/Literature/",
        ],
        "handler": Fetcher._fetch_ncpssd_article,
        "default_no_proxy": True,
        "default_no_translate": True,
    },
    "github": {
        "patterns": [
            r"^https?://(www\.)?github\.com/[^/]+/[^/]+/?$",
            r"^https?://(www\.)?github\.com/[^/]+/[^/]+/.+\.md(?:[#?].*)?$",
            r"^https?://(www\.)?github\.com/[^/]+/[^/]+/(blob|raw|tree)/[^/]+/.+\.md(?:[#?].*)?$",
        ],
        "handler": Fetcher._fetch_github_readme,
        "skip_title_translation": True,  # Don't translate GitHub repo names, but content can be translated
    },
    "arxiv": {
        "patterns": [
            r"^https?://arxiv\.org/abs/\d{4}\.\d{4,5}",
            r"^https?://arxiv\.org/pdf/\d{4}\.\d{4,5}",
            r"^https?://arxiv\.org/html/\d{4}\.\d{4,5}",
        ],
        "handler": Fetcher._fetch_arxiv,
    },
    "wikipedia": {
        "patterns": [
            r"^https?://(www\.)?wikipedia\.org/wiki/",
            r"^https?://[a-z]{2}\.wikipedia\.org/wiki/",
        ],
        "handler": Fetcher._fetch_wikipedia,
    },
    "bluesky": {
        "patterns": [
            r"^https?://bsky\.app/profile/[^/]+/post/",
        ],
        "handler": Fetcher._fetch_bluesky,
        "default_thread": True,
    },
    "weibo": {
        "patterns": [
            r"^https?://(www\.)?weibo\.com/",
            r"^https?://m\.weibo\.cn/",
        ],
        "handler": Fetcher._fetch_social_thread_page,
        "default_thread": True,
    },
    "threads": {
        "patterns": [
            r"^https?://(www\.)?threads\.net/@[^/]+/post/",
        ],
        "handler": Fetcher._fetch_social_thread_page,
        "default_thread": True,
    },
    "reddit": {
        "patterns": [
            r"^https?://(www\.|old\.|new\.)?reddit\.com/r/[^/]+/comments/",
            r"^https?://(www\.|old\.|new\.)?reddit\.com/comments/",
            r"^https?://redd\.it/[A-Za-z0-9]+/?$",
        ],
        "handler": Fetcher._fetch_reddit_content,
    },
    "v2ex": {
        "patterns": [
            r"^https?://(www\.)?v2ex\.com/t/\d+",
        ],
        "handler": Fetcher._fetch_v2ex_topic,
        "force_proxy": True,
        "default_no_translate": True,
        "skip_title_translation": True,
        "no_generic_fallback": True,
    },
}

# Cache for compiled regex patterns (performance optimization)
_COMPILED_PATTERNS = {}


def _get_handler_for_url(url):
    """
    Get the appropriate handler for a URL from SPECIAL_SITE_HANDLERS.

    Args:
        url: The URL to check

    Returns:
        tuple: (handler_function, site_name, site_config) or (None, None, None) if no special handler
    """
    for site_name, config in SPECIAL_SITE_HANDLERS.items():
        patterns = config["patterns"]

        # Compile patterns on first use and cache them
        if site_name not in _COMPILED_PATTERNS:
            _COMPILED_PATTERNS[site_name] = [re.compile(p) for p in patterns]

        compiled_patterns = _COMPILED_PATTERNS[site_name]

        for pattern in compiled_patterns:
            if pattern.match(url):
                return config["handler"], site_name, config

    return None, None, None


class ContentProcessor:
    @staticmethod
    def _text_appears_to_match_target_language(text, target_lang):
        """Heuristic guard for mixed Markdown where langdetect sees badges/URLs first."""
        normalized_target = (target_lang or "").lower()
        if not normalized_target.startswith("zh"):
            return False

        sample = (text or "")[:4000]
        if not sample:
            return False

        cjk_count = len(re.findall(r"[\u3400-\u9fff]", sample))
        latin_count = len(re.findall(r"[A-Za-z]", sample))
        kana_hangul_count = len(re.findall(r"[\u3040-\u30ff\uac00-\ud7af]", sample))
        language_chars = cjk_count + latin_count + kana_hangul_count

        if language_chars == 0:
            return False

        # Chinese Markdown often starts with badges, URLs and English project names.
        # Require enough CJK text plus a modest share of the language-bearing sample
        # so mixed Chinese docs do not get sent through a slow, unnecessary translation.
        return cjk_count >= 80 and cjk_count / language_chars >= 0.10 and kana_hangul_count <= cjk_count

    @staticmethod
    def _preprocess_html(html):
        """
        Preprocesses HTML to normalize complex image structures (like <picture> or lazy-loaded images)
        so that extraction engines can find them more easily.
        """
        soup = BeautifulSoup(html, "html.parser")
        for img in soup.find_all("img"):
            # Normalize src
            src = img.get("src")
            should_replace_src = not src or (isinstance(src, str) and src.startswith("data:image"))
            if should_replace_src:
                for attr in [
                    "data-src",
                    "data-original",
                    "data-url",
                    "data-srcset",
                    "srcset",
                ]:
                    val = img.get(attr)
                    if not val:
                        continue
                    val = val.split(",")[0].split(" ")[0]
                    if val.startswith("//"):
                        val = f"https:{val}"
                    img["src"] = val
                    break

            # Flatten picture/source
            parent = img.parent
            while parent and parent.name in ["picture", "source", "figure"]:
                if parent.name in ["picture", "source"]:
                    # Replace the entire picture/source with just the img
                    parent.replace_with(img)
                    parent = img.parent
                else:
                    break
        return soup

    @staticmethod
    def _rescue_content(preprocessed_soup, summary_html):
        """
        Uses a fingerprint from Readability's summary to find the original,
        uncleaned container in the preprocessed soup, preserving images.
        """
        summary_soup = BeautifulSoup(summary_html, "html.parser")
        text = summary_soup.get_text(strip=True)
        if len(text) < 50:
            return summary_html

        # Take a fingerprint from the start of the text
        fingerprint = text[:100]

        # Find the node in the full preprocessed soup
        # Exclude head, title, script, style, etc. by searching only in body and main content areas
        node = None

        # First, try searching in body content (excluding head)
        body = preprocessed_soup.find("body")
        if body:
            node = body.find(string=lambda t: fingerprint in t if t else False)

        # If not found in body, try in main/article/section containers
        if not node:
            for container in preprocessed_soup.find_all(["article", "main", "section", "div"]):
                if container.get("id") and container.get("id").lower() in [
                    "content",
                    "main",
                    "article",
                    "body",
                ]:
                    continue
                node = container.find(string=lambda t: fingerprint in t if t else False)
                if node:
                    break

        # Last resort: try smaller fingerprint, still avoiding head elements
        if not node:
            fingerprint = text[:30]
            # Search only in body and article/main/section
            for container in [body] if body else []:
                node = container.find(string=lambda t: fingerprint in t if t else False)
                if node:
                    break
            if not node:
                for container in preprocessed_soup.find_all(["article", "main", "section"]):
                    node = container.find(string=lambda t: fingerprint in t if t else False)
                    if node:
                        break

        if not node:
            return summary_html

        # Traverse up to find a suitable article container
        curr = node.parent if hasattr(node, "parent") else node
        best_candidate = curr

        # Heuristic: go up until we hit a very broad container or body
        while curr and curr.name not in ["body", "html"]:
            # If this parent has images, it's a better candidate
            if curr.find_all("img"):
                best_candidate = curr

            # Stop if we hit a semantic article boundary
            if curr.name in ["article", "main"] or (
                curr.get("id") and curr.get("id").lower() in ["main", "content", "article"]
            ):
                best_candidate = curr
                break

            curr = curr.parent

        return str(best_candidate)

    @staticmethod
    def extract_content(html):
        """
        Extracts the main content using Readability and a custom rescue logic to preserve images.
        """
        logger.info("Extracting main content...")

        preprocessed_soup = ContentProcessor._preprocess_html(html)

        # Site-specific HTML that is already normalized should bypass Readability.
        # Xiaohongshu note pages are especially fragile here: short text + image galleries
        # often make Readability keep only a tiny text node and drop the images.
        source_site_tag = preprocessed_soup.find("meta", attrs={"name": "surf-source-site"})
        source_site = source_site_tag.get("content", "").strip().lower() if source_site_tag else ""
        if source_site in {"xiaohongshu", "twitter", "bluesky", "weibo", "threads"}:
            article = preprocessed_soup.find("article")
            preserved_html = str(article) if article else str(preprocessed_soup)
            img_count = preserved_html.count("<img")
            logger.info(
                f"Bypassing Readability for {source_site}. Preserved HTML length: {len(preserved_html)}, images: {img_count}"
            )
            return Document(str(preprocessed_soup)).title(), preserved_html

        # 1. Get Readability Summary
        try:
            doc = Document(str(preprocessed_soup))
            title = doc.title()
            summary_html = doc.summary()
            logger.info(f"Readability title: {title}")
            logger.info(f"Readability summary length: {len(summary_html)}")

            # 2. Rescue uncleaned content using fingerprint
            rescued_html = ContentProcessor._rescue_content(preprocessed_soup, summary_html)

            img_count = rescued_html.count("<img")
            logger.info(f"Extracted {img_count} images. Rescued HTML length: {len(rescued_html)}")

            # Debug: log first 200 chars of rescued HTML
            if rescued_html:
                logger.info(f"Rescued HTML preview: {rescued_html[:200]}")

            return title, rescued_html
        except Exception as e:
            logger.warning(f"Readability/Rescue failed: {e}. Falling back to Trafilatura.")

        # Final Fallback: Trafilatura
        try:
            content_html = trafilatura.extract(str(preprocessed_soup), output_format="html", include_images=True)
            if content_html:
                img_count = content_html.count("<img")
                logger.info(f"Trafilatura extracted {img_count} images. Content length: {len(content_html)}")
                logger.info(f"Trafilatura content preview: {content_html[:200]}")
                return Document(html).title(), content_html
        except Exception as e:
            logger.warning(f"Trafilatura extraction failed: {e}")

        logger.warning("All extraction methods failed, returning original HTML")
        return Document(html).title(), html

    @staticmethod
    def to_markdown(html):
        """
        Converts HTML to Markdown using markdownify.
        """
        logger.info("Converting to Markdown...")
        html = OutputHandler._strip_twitter_blockquote_wrapper(html)
        html = _prepare_inline_svgs_for_markdown(html)
        # strip=['a'] can be used to remove links if desired, but usually we keep them.
        # heading_style='ATX' ensures # style headings
        return markdownify.markdownify(html, heading_style="ATX")

    @staticmethod
    def _chunk_text(text, max_chars=4000):
        """
        Splits text into chunks by paragraphs, attempting to stay under max_chars.
        """
        chunks = []
        current_chunk = []
        current_len = 0

        # Split by double newlines to preserve paragraphs
        paragraphs = text.split("\n\n")

        for paragraph in paragraphs:
            para_len = len(paragraph)
            # If a single paragraph is huge, we might still overshoot, but this is a simple heuristic.
            if current_len + para_len + 2 > max_chars and current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_len = 0

            current_chunk.append(paragraph)
            current_len += para_len + 2

        if current_chunk:
            chunks.append("\n\n".join(current_chunk))

        return chunks

    @classmethod
    def translate_if_needed(cls, text, title=None, target_lang="zh-cn", config=None, llm_provider=None):
        """
        Detects language and translates (content + title) if necessary using chunking.

        Args:
            text (str): The text to translate
            title (str, optional): The title to translate
            target_lang (str): Target language code (default: 'zh-cn')
            config: Config object
            llm_provider (str, optional): Override the default LLM provider

        Returns:
            tuple: (translated_text, translated_title)
        """
        try:
            lang = detect(text[:1000])  # Detect based on first 1000 chars
            logger.info(f"Detected language: {lang}")
        except Exception as e:
            logger.warning(f"Language detection failed: {e}. Assuming translation needed.")
            lang = "unknown"

        if (
            target_lang.lower() in lang.lower()
            or lang == "zh-cn"
            or cls._text_appears_to_match_target_language(text, target_lang)
        ):
            logger.info("Language matches target or is already Chinese. Skipping translation.")
            return text, title

        logger.info(f"Translating to {target_lang} using LLM...")

        if not config:
            logger.error("No configuration provided for translation.")
            return text, title

        try:
            from openai import OpenAI

            # Get LLM configuration
            try:
                llm_config = config.get_llm_config(llm_provider)
                logger.info(f"Using LLM provider: {llm_provider or 'default'}")
            except ValueError as e:
                logger.error(f"LLM configuration error: {e}")
                return text, title

            client = OpenAI(base_url=llm_config["base_url"], api_key=llm_config["api_key"])

            # 1. Translate Title (if provided)
            translated_title = title
            if title:
                logger.info("Translating title...")
                try:
                    t_completion = client.chat.completions.create(
                        model=llm_config["model"],
                        messages=[
                            {
                                "role": "system",
                                "content": f"Translate the following title to {target_lang}. Output ONLY the translation.",
                            },
                            {"role": "user", "content": title},
                        ],
                    )
                    translated_title = t_completion.choices[0].message.content.strip()
                    logger.info(f"Translated title: {translated_title}")
                except Exception as e:
                    logger.error(f"Title translation failed: {e}")

            # 2. Translate Content (Chunked)
            chunks = cls._chunk_text(text)
            translated_chunks = []

            total_chunks = len(chunks)
            logger.info(f"Content split into {total_chunks} chunks for translation.")

            for i, chunk in enumerate(chunks):
                logger.info(f"Translating chunk {i + 1}/{total_chunks} ({len(chunk)} chars)...")
                completion = client.chat.completions.create(
                    model=llm_config["model"],
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                f"You are a helpful translator. Translate the following Markdown content to {target_lang}. "
                                "Preserve the Markdown formatting strictly. Output ONLY the translated markdown."
                            ),
                        },
                        {"role": "user", "content": chunk},
                    ],
                )
                translated_chunks.append(completion.choices[0].message.content)

            return "\n\n".join(translated_chunks), translated_title

        except Exception as e:
            logger.error(f"Translation failed: {e}")
            return text, title


class OcrHandler:
    @staticmethod
    def _is_enabled_for_site(site_name, site_config, args, config):
        if getattr(args, "no_ocr_images", False):
            return False
        if getattr(args, "ocr_images", False):
            return True
        if site_config and site_config.get("default_ocr_images"):
            return True
        value = config.get("OCR", "enabled", fallback="false")
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _get_int_config(config, key, fallback):
        try:
            return int(config.get("OCR", key, fallback=str(fallback)))
        except Exception:
            return fallback

    @staticmethod
    def _get_engine_setting(args, config):
        value = getattr(args, "ocr_engine", None) or config.get("OCR", "engine", fallback="rapidocr")
        value = str(value).strip().lower()
        return value or "rapidocr"

    @staticmethod
    def _get_engine_chain(args, config):
        engine = OcrHandler._get_engine_setting(args, config)
        if engine == "tesseract":
            return ["tesseract"]
        if engine in {"rapidocr", "auto"}:
            return ["rapidocr", "tesseract"]
        logger.warning("Unknown OCR engine '%s'; falling back to rapidocr", engine)
        return ["rapidocr", "tesseract"]

    @staticmethod
    def _normalize_ocr_text(text):
        if not text:
            return ""
        lines = []
        for line in text.splitlines():
            cleaned = re.sub(r"\s+", " ", line).strip()
            if cleaned:
                lines.append(cleaned)
        return "\n".join(lines).strip()

    @staticmethod
    def _prepare_image_for_ocr(image):
        from PIL import Image as PilImage  # type: ignore
        from PIL import ImageOps  # type: ignore

        prepared = image.convert("L")
        prepared = ImageOps.autocontrast(prepared)

        width, height = prepared.size
        max_side = max(width, height)
        if max_side < 1800:
            scale = max(2, int(1800 / max_side))
            prepared = prepared.resize(
                (width * scale, height * scale),
                resample=PilImage.Resampling.LANCZOS,
            )

        # A light threshold often helps screenshot-like text blocks.
        prepared = prepared.point(lambda px: 255 if px > 180 else 0)
        return prepared

    @staticmethod
    def _image_to_png_bytes(image):
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    @staticmethod
    def _init_rapidocr():
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
        except Exception as e:
            raise RuntimeError(f"RapidOCR is unavailable: {e}") from e
        try:
            return RapidOCR()
        except Exception as e:
            raise RuntimeError(f"RapidOCR init failed: {e}") from e

    @staticmethod
    def _extract_text_with_rapidocr(rapidocr_engine, image):
        ocr_input = OcrHandler._image_to_png_bytes(image)
        result = rapidocr_engine(ocr_input)
        if isinstance(result, tuple):
            lines = result[0]
        else:
            lines = result
        if not lines:
            return ""

        fragments = []
        for item in lines:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            text = OcrHandler._normalize_ocr_text(str(item[1]))
            if text:
                fragments.append(text)
        return "\n".join(fragments).strip()

    @staticmethod
    def _resolve_ocr_languages(pytesseract_module, requested_langs):
        try:
            available = set(pytesseract_module.get_languages(config=""))
        except Exception:
            available = set()

        requested = [lang.strip() for lang in requested_langs.split("+") if lang.strip()]
        if not requested:
            requested = ["eng"]

        supported = [lang for lang in requested if not available or lang in available]
        missing = [lang for lang in requested if available and lang not in available]

        if missing:
            logger.warning(
                "OCR language data missing for: %s. Available langs: %s",
                ", ".join(missing),
                ", ".join(sorted(available)) if available else "unknown",
            )

        if supported:
            return ["+".join(supported)]
        if available and "eng" in available:
            logger.warning("Falling back to OCR language: eng")
            return ["eng"]
        return [requested_langs]

    @staticmethod
    def _run_ocr(pytesseract_module, image, lang_candidates):
        best_text = ""
        configs = ["--psm 6", "--psm 11"]

        for lang in lang_candidates:
            for tesseract_config in configs:
                try:
                    text = pytesseract_module.image_to_string(image, lang=lang, config=tesseract_config)
                except Exception as e:
                    logger.debug(
                        "OCR attempt failed for lang=%s config=%s: %s",
                        lang,
                        tesseract_config,
                        e,
                    )
                    continue
                text = OcrHandler._normalize_ocr_text(text)
                if len(text) > len(best_text):
                    best_text = text
        return best_text

    @staticmethod
    def _init_tesseract(config):
        try:
            import pytesseract  # type: ignore
        except Exception as e:
            raise RuntimeError(f"pytesseract is unavailable: {e}") from e

        tesseract_cmd = config.get_path("OCR", "tesseract_cmd", fallback="").strip()
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

        try:
            pytesseract.get_tesseract_version()
        except Exception as e:
            raise RuntimeError(f"local tesseract is unavailable: {e}") from e

        return pytesseract

    @staticmethod
    def _create_ocr_runtime(args, config):
        runtime = {
            "chain": OcrHandler._get_engine_chain(args, config),
            "available": {},
        }

        ocr_lang = getattr(args, "ocr_lang", None) or config.get("OCR", "lang", fallback="chi_sim+eng")

        for engine_name in runtime["chain"]:
            if engine_name == "rapidocr":
                try:
                    runtime["available"]["rapidocr"] = OcrHandler._init_rapidocr()
                    logger.info("OCR engine ready: rapidocr")
                except Exception as e:
                    logger.warning("OCR engine unavailable: rapidocr (%s)", e)
            elif engine_name == "tesseract":
                try:
                    pytesseract = OcrHandler._init_tesseract(config)
                    runtime["available"]["tesseract"] = {
                        "module": pytesseract,
                        "langs": OcrHandler._resolve_ocr_languages(pytesseract, ocr_lang),
                    }
                    logger.info("OCR engine ready: tesseract")
                except Exception as e:
                    logger.warning("OCR engine unavailable: tesseract (%s)", e)

        return runtime

    @staticmethod
    def _run_ocr_with_engines(runtime, prepared_image, image_url):
        for engine_name in runtime["chain"]:
            engine = runtime["available"].get(engine_name)
            if engine is None:
                continue

            try:
                if engine_name == "rapidocr":
                    text = OcrHandler._extract_text_with_rapidocr(engine, prepared_image)
                elif engine_name == "tesseract":
                    text = OcrHandler._run_ocr(engine["module"], prepared_image, engine["langs"])
                else:
                    continue

                if text:
                    return text, engine_name
            except Exception as e:
                logger.debug(
                    "OCR engine failed for image %s via %s: %s",
                    image_url[:120],
                    engine_name,
                    e,
                )

        return "", None

    @staticmethod
    def _download_image(url, source_url, proxies, timeout=20):
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        }
        if source_url:
            parsed = urlparse(source_url)
            headers["Referer"] = source_url
            if parsed.scheme and parsed.netloc:
                headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
        response = _requests_get_interruptibly(url, headers=headers, proxies=proxies, timeout=timeout)
        response.raise_for_status()
        return response.content

    @staticmethod
    def _build_ocr_block(soup, ocr_text):
        block = soup.new_tag("div")
        block["class"] = "surf-ocr"

        label = soup.new_tag("p")
        strong = soup.new_tag("strong")
        strong.string = "OCR Text"
        label.append(strong)
        block.append(label)

        for line in ocr_text.splitlines():
            para = soup.new_tag("p")
            para.string = line
            block.append(para)

        return block

    @staticmethod
    def annotate_html_with_ocr(
        html_content,
        source_url,
        site_name,
        site_config,
        args,
        config,
        proxy_mode_override=None,
        custom_proxy_override=None,
    ):
        if not html_content:
            return html_content
        if not OcrHandler._is_enabled_for_site(site_name, site_config, args, config):
            return html_content

        try:
            from PIL import Image  # type: ignore
        except Exception as e:
            logger.warning(f"OCR disabled: missing optional OCR dependencies: {e}")
            return html_content

        runtime = OcrHandler._create_ocr_runtime(args, config)
        if not runtime["available"]:
            logger.warning(
                "OCR disabled: no usable OCR engine is available. Install rapidocr-onnxruntime or configure local tesseract."
            )
            return html_content

        req_proxies, _ = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)
        max_images = OcrHandler._get_int_config(config, "max_images", 8)
        min_width = OcrHandler._get_int_config(config, "min_width", 240)
        min_height = OcrHandler._get_int_config(config, "min_height", 120)
        min_text_length = OcrHandler._get_int_config(config, "min_text_length", 8)

        soup = BeautifulSoup(html_content, "html.parser")
        root = soup.find("article") or soup.find("main") or soup.body or soup
        seen_urls = set()
        processed = 0

        for img in root.find_all("img"):
            if processed >= max_images:
                break
            if img.find_next_sibling(class_="surf-ocr"):
                continue

            img_url = (img.get("src") or img.get("data-src") or img.get("data-original") or "").strip()
            if (
                not img_url
                or img_url.startswith("data:")
                or img_url in seen_urls
                or any(token in img_url.lower() for token in ("avatar", "logo", "emoji", "icon"))
            ):
                continue
            seen_urls.add(img_url)

            try:
                image_bytes = OcrHandler._download_image(img_url, source_url, req_proxies)
                image = Image.open(io.BytesIO(image_bytes))
                image.load()
                width, height = image.size
                if width < min_width or height < min_height:
                    continue
                prepared_image = OcrHandler._prepare_image_for_ocr(image)
                ocr_text, engine_used = OcrHandler._run_ocr_with_engines(runtime, prepared_image, img_url)
                if len(ocr_text) < min_text_length:
                    logger.info(
                        "OCR produced too little text for image: %s",
                        img_url[:120],
                    )
                    continue
                logger.info(
                    "OCR accepted text for image via %s: %s",
                    engine_used,
                    img_url[:120],
                )
                img.insert_after(OcrHandler._build_ocr_block(soup, ocr_text))
                processed += 1
            except Exception as e:
                logger.debug(f"OCR skipped for image {img_url}: {e}")

        if processed:
            logger.info(f"OCR annotated {processed} images")
        else:
            logger.warning(
                "OCR ran but produced no usable text. Check image quality, try RapidOCR, or use --ocr-engine tesseract --ocr-lang eng if Tesseract works better for the page."
            )
        return str(soup)


class OutputHandler:
    _MOJIBAKE_CHARS = "ÃÂâäåæçèéêëïðñøùœž€™�"

    @staticmethod
    def _mojibake_score(text):
        """Estimate how likely text contains UTF-8 mojibake."""
        if not text:
            return 0

        suspicious_pairs = ("Ã©", "Ã¨", "Ã ", "Â ", "â€™", "â€œ", "â€", "å", "æ", "ç", "ï", "ð")
        char_score = sum(text.count(ch) for ch in OutputHandler._MOJIBAKE_CHARS)
        pair_score = sum(text.count(pair) * 2 for pair in suspicious_pairs)
        replacement_penalty = text.count("\ufffd") * 3
        return char_score + pair_score + replacement_penalty

    @staticmethod
    def normalize_markdown_encoding(text):
        """
        Repair common mojibake patterns and normalize markdown text before UTF-8 output.
        """
        if not text:
            return text

        normalized = unicodedata.normalize("NFC", text)
        best = normalized
        changed = False

        for _ in range(3):
            best_score = OutputHandler._mojibake_score(best)
            improved = False

            for source_encoding in ("latin-1", "cp1252"):
                try:
                    candidate = best.encode(source_encoding).decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    continue

                candidate = unicodedata.normalize("NFC", candidate)
                candidate_score = OutputHandler._mojibake_score(candidate)
                if candidate_score < best_score:
                    best = candidate
                    best_score = candidate_score
                    improved = True
                    changed = True

            if not improved:
                break

        if changed:
            logger.info("Normalized markdown text encoding before UTF-8 output")

        return best

    @staticmethod
    def _extract_html_meta_description(html_content):
        """Extract normalized HTML meta description content when available."""
        if not html_content:
            return None
        soup = BeautifulSoup(html_content, "html.parser")
        description_tag = soup.find("meta", attrs={"name": "description"})
        if not description_tag:
            return None
        description_value = description_tag.get("content") or description_tag.get("value")
        if not description_value:
            return None
        return OutputHandler.normalize_markdown_encoding(description_value.strip())

    @staticmethod
    def _sanitize_filename(filename):
        return "".join([c for c in filename if c.alpha() or c.isdigit() or c in " ._-"]).rstrip()

    @staticmethod
    def _safe_filename_title(title, max_len=None):
        raw_title = str(title or "")
        # Keep all filename-safe punctuation (including CJK punctuation);
        # remove only characters that are invalid on Windows filesystems.
        safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", raw_title)
        # Windows does not allow trailing space/dot in path segments.
        safe_title = safe_title.strip().rstrip(". ")
        # Normalize whitespace while preserving punctuation.
        safe_title = re.sub(r"\s+", " ", safe_title)

        # Avoid reserved DOS device names.
        reserved = {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            "COM1",
            "COM2",
            "COM3",
            "COM4",
            "COM5",
            "COM6",
            "COM7",
            "COM8",
            "COM9",
            "LPT1",
            "LPT2",
            "LPT3",
            "LPT4",
            "LPT5",
            "LPT6",
            "LPT7",
            "LPT8",
            "LPT9",
        }
        if safe_title.upper() in reserved:
            safe_title = f"{safe_title}_"
        if max_len:
            safe_title = safe_title[:max_len]
        return safe_title or "Untitled"

    @staticmethod
    def _extract_html_title(html_content):
        if not html_content:
            return None
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            title_tag = soup.find("title")
            if title_tag:
                title = title_tag.get_text(strip=True)
                return title or None
        except Exception:
            return None
        return None

    @staticmethod
    def _is_twitter_non_article(source_url=None, html_content=None):
        if html_content:
            try:
                soup = BeautifulSoup(html_content, "html.parser")
                meta = soup.find("meta", attrs={"name": "surf-twitter-kind"})
                if meta:
                    kind = (meta.get("content") or "").strip().lower()
                    if kind == "tweet":
                        return True
                    if kind == "article":
                        return False
            except Exception:
                pass

        if not source_url or not Fetcher._is_twitter_url(source_url):
            return False
        if Fetcher._is_twitter_article_url(source_url):
            return False
        if html_content:
            try:
                soup = BeautifulSoup(html_content, "html.parser")
                meta = soup.find("meta", attrs={"name": "surf-twitter-kind"})
                if meta:
                    return (meta.get("content") or "").strip().lower() == "tweet"
            except Exception:
                pass
            html_title = OutputHandler._extract_html_title(html_content) or ""
            if "x post" in html_title.lower():
                return True
        return bool(re.search(r"/status/\d+", source_url))

    @staticmethod
    def _is_bluesky_post_url(source_url=None, html_content=None):
        if not source_url:
            return False
        if not re.search(r"^https?://bsky\.app/profile/[^/]+/post/[^/]+", source_url, re.IGNORECASE):
            return False
        if html_content:
            html_title = OutputHandler._extract_html_title(html_content) or ""
            if "bluesky post" in html_title.lower():
                return True
        return True

    @staticmethod
    def _get_social_source_site(html_content=None):
        if not html_content:
            return None
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            meta = soup.find("meta", attrs={"name": "surf-source-site"})
            if meta and meta.get("content"):
                site_name = (meta.get("content") or "").strip().lower()
                if site_name in {"twitter", "bluesky", "weibo", "threads"}:
                    return site_name
        except Exception:
            return None
        return None

    @staticmethod
    def _extract_first_sentence(text):
        if not text:
            return None
        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized:
            return None
        match = re.search(r"^(.+?[。！？!?\.])", normalized)
        if match:
            return match.group(1).strip()
        return normalized

    @staticmethod
    def _get_social_site_label(source_url=None, html_content=None):
        source_site = OutputHandler._get_social_source_site(html_content)
        if source_site == "twitter":
            if OutputHandler._is_twitter_non_article(source_url, html_content):
                return "X"
            return None
        if source_site == "bluesky":
            return "Bsky"
        if source_site == "weibo":
            return "Weibo"
        if source_site == "threads":
            return "Threads"
        if OutputHandler._is_twitter_non_article(source_url, html_content):
            return "X"
        if OutputHandler._is_bluesky_post_url(source_url, html_content):
            return "Bsky"
        if source_url and re.search(r"^https?://(www\.)?threads\.net/@[^/]+/post/[^/]+", source_url, re.IGNORECASE):
            return "Threads"
        if source_url and re.search(r"^https?://(m\.)?weibo\.cn/", source_url, re.IGNORECASE):
            return "Weibo"
        if source_url and re.search(r"^https?://(www\.)?weibo\.com/", source_url, re.IGNORECASE):
            return "Weibo"
        return None

    @staticmethod
    def _extract_social_first_sentence_title(html_content, source_url=None):
        site_label = OutputHandler._get_social_site_label(source_url, html_content)
        if not site_label:
            return None
        if not html_content:
            return None
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            author_name = None

            # Prefer the rendered current-post body when Surf has already normalized
            # a social thread page into structured sections.
            thread_posts = soup.find_all("section", class_="surf-thread-post")
            if thread_posts:
                current_section = None
                for section in thread_posts:
                    heading = section.find(["h1", "h2", "h3"])
                    if heading and heading.get_text(strip=True) == "Current Post":
                        current_section = section
                        break
                if not current_section:
                    # Merged Twitter/X replies are structured as thread posts while
                    # the original tweet remains plain article content. Do not let
                    # the first reply replace the source post as the title basis.
                    pass
                else:
                    meta_paragraph = current_section.find("p", recursive=False)
                    if meta_paragraph:
                        strong_tag = meta_paragraph.find("strong")
                        if strong_tag:
                            author_name = re.sub(r"\s+", " ", strong_tag.get_text(" ", strip=True)).strip() or None

                    for paragraph in current_section.find_all("p", recursive=False):
                        line = re.sub(r"\s+", " ", paragraph.get_text(" ", strip=True)).strip()
                        if not line:
                            continue
                        if line.lower().startswith("view "):
                            continue
                        if " | @" in line:
                            continue
                        sentence = OutputHandler._extract_first_sentence(line)
                        if sentence:
                            if author_name:
                                return f"{sentence} - {author_name} on {site_label}"
                            return sentence

            html_title = OutputHandler._extract_html_title(html_content) or ""
            skip_lines = set()
            if html_title:
                skip_lines.add(html_title.strip())
                if html_title.lower().endswith(" - x post"):
                    skip_lines.add(html_title[:-9].strip())
            skip_lines.update(
                {
                    "Bluesky Post",
                    "Current Post",
                    "Earlier Post",
                    "Later Reply",
                    "Earlier Posts",
                    "Replies",
                    "Thread Context",
                    "View post",
                    "View on Bluesky",
                    "Weibo Post",
                    "View on Weibo",
                    "Threads Post",
                    "View on Threads",
                    "X Post",
                    "View on X",
                }
            )

            text_lines = []
            for text in soup.stripped_strings:
                line = re.sub(r"\s+", " ", text).strip()
                if not line:
                    continue
                if source_url and line == source_url:
                    continue
                if line in skip_lines:
                    continue
                if re.fullmatch(r"https?://\S+", line):
                    continue
                if line.lower().startswith("author:"):
                    continue
                if not author_name and " | @" in line:
                    author_name = line.split("|", 1)[0].strip("* ").strip() or None
                text_lines.append(line)

            for line in text_lines:
                sentence = OutputHandler._extract_first_sentence(line)
                if sentence:
                    if author_name:
                        return f"{sentence} - {author_name} on {site_label}"
                    return sentence
        except Exception:
            return None
        return None

    @staticmethod
    def _strip_twitter_blockquote_wrapper(html):
        """Remove the outer Twitter/X embed blockquote so markdownify won't emit quote markers."""
        if not html:
            return html
        try:
            soup = BeautifulSoup(html, "html.parser")
            changed = False
            for blockquote in soup.find_all("blockquote"):
                classes = blockquote.get("class") or []
                if not any(cls in {"twitter-tweet", "twitter-video"} for cls in classes):
                    continue

                extracted = [child.extract() for child in list(blockquote.children)]
                for node in reversed(extracted):
                    blockquote.insert_after(node)
                blockquote.decompose()
                changed = True

            return str(soup) if changed else html
        except Exception:
            return html

    @staticmethod
    def _get_filename_title(title, source_url=None, html_content=None):
        """
        Select filename title based on source site rules.
        For GitHub URLs, prefer page <title>.
        """
        twitter_title = OutputHandler._extract_social_first_sentence_title(html_content, source_url=source_url)
        if twitter_title:
            return twitter_title
        if source_url and re.match(r"^https?://(www\.)?github\.com/", source_url, re.IGNORECASE):
            html_title = OutputHandler._extract_html_title(html_content)
            if html_title:
                return html_title
        return title

    @staticmethod
    def _convert_urls_to_absolute(html_content, base_url):
        """
        将HTML中的相对URL转换为绝对URL。

        Args:
            html_content: HTML内容
            base_url: 基础URL，用于解析相对URL

        Returns:
            处理后的HTML内容
        """
        from urllib.parse import urljoin

        soup = BeautifulSoup(html_content, "html.parser")

        # 需要处理的标签和属性映射
        tag_attr_map = {
            # 媒体和图片
            "img": ["src", "data-src", "data-srcset", "srcset"],
            "video": ["src", "poster", "data-src"],
            "audio": ["src", "data-src"],
            "source": ["src", "srcset"],
            "track": ["src"],
            "embed": ["src"],
            "object": ["data"],
            "iframe": ["src"],
            "svg": ["data", "href"],  # SVG引用
            # 链接和导航
            "a": ["href"],
            "area": ["href"],
            "base": ["href"],
            "link": ["href"],  # stylesheet, favicon等
            # 脚本和样式
            "script": ["src", "href"],
            "style": ["href"],
            # 表单
            "form": ["action"],
            "input": ["src"],
            "button": ["formaction"],
            # 其他
            "ins": ["cite"],
            "del": ["cite"],
            "blockquote": ["cite"],
        }

        for tag, attrs in tag_attr_map.items():
            for element in soup.find_all(tag):
                for attr in attrs:
                    url = element.get(attr)
                    if url and not url.startswith(
                        (
                            "http://",
                            "https://",
                            "data:",
                            "#",
                            "mailto:",
                            "tel:",
                            "javascript:",
                        )
                    ):
                        absolute_url = urljoin(base_url, url)
                        element[attr] = absolute_url
                        logger.debug(f"Converted relative URL: {url} -> {absolute_url}")

        return str(soup)

    @staticmethod
    def _rewrite_github_blob_asset_url(url):
        if not url:
            return url
        try:
            parsed = urlparse(url)
        except Exception:
            return url
        host = (parsed.netloc or "").split("@")[-1].split(":")[0].lower()
        if host.startswith("www."):
            host = host[4:]
        if host != "github.com":
            return url
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 5 or parts[2] != "blob":
            return url
        owner, repo, _, branch = parts[:4]
        asset_path = "/".join(parts[4:])
        if not asset_path:
            return url
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                f"/{owner}/{repo}/raw/{branch}/{asset_path}",
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )

    @staticmethod
    def _is_likely_media_url(url):
        if not url:
            return False
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        path = (parsed.path or "").lower()
        media_exts = {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".svg",
            ".bmp",
            ".ico",
            ".avif",
            ".mp4",
            ".webm",
            ".mov",
            ".m4v",
            ".mp3",
            ".wav",
            ".ogg",
            ".oga",
            ".m4a",
            ".flac",
            ".aac",
            ".opus",
            ".pdf",
        }
        return any(path.endswith(ext) for ext in media_exts)

    @staticmethod
    def _canonicalize_generic_source_url(url):
        if not url:
            return url
        try:
            parsed = urlparse(url)
        except Exception:
            return url
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
        cleaned_pairs = [(key, value) for key, value in query_pairs if not key.lower().startswith("utm_")]
        cleaned_query = urlencode(cleaned_pairs, doseq=True)
        return urlunparse(parsed._replace(query=cleaned_query))

    @staticmethod
    def _convert_markdown_urls_to_absolute(md_content, base_url):
        """
        将Markdown中的相对URL转换为绝对URL。

        Args:
            md_content: Markdown内容
            base_url: 基础URL，用于解析相对URL

        Returns:
            处理后的Markdown内容
        """
        from urllib.parse import urljoin
        from bs4 import BeautifulSoup

        # 处理图片链接: ![alt](url)
        def replace_image_url(match):
            alt_text = match.group(1)
            url = match.group(2)
            if url and url.startswith(("http://", "https://")):
                rewritten_url = OutputHandler._rewrite_github_blob_asset_url(url)
                if rewritten_url != url:
                    return f"![{alt_text}]({rewritten_url})"
            if url and not url.startswith(("http://", "https://", "data:")):
                absolute_url = urljoin(base_url, url)
                return f"![{alt_text}]({absolute_url})"
            return match.group(0)

        md_content = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replace_image_url, md_content)

        # 处理链接: [text](url)
        def replace_link_url(match):
            text = match.group(1)
            url = match.group(2)
            if url and url.startswith(("http://", "https://")):
                rewritten_url = OutputHandler._rewrite_github_blob_asset_url(url)
                if rewritten_url != url and OutputHandler._is_likely_media_url(rewritten_url):
                    return f"[{text}]({rewritten_url})"
            if url and not url.startswith(("http://", "https://", "data:", "#", "mailto:", "tel:", "javascript:")):
                absolute_url = urljoin(base_url, url)
                return f"[{text}]({absolute_url})"
            return match.group(0)

        md_content = re.sub(r"\[([^\]]*)\]\(([^)]+)\)", replace_link_url, md_content)

        # 处理内联HTML标签中的URL（如 <video src="...">、<audio src="..."> 等）
        def convert_html_attrs_in_md(match):
            html_tag = match.group(0)
            soup = BeautifulSoup(html_tag, "html.parser")
            tag = soup.find()

            if tag:
                tag_attr_map = {
                    "img": ["src", "data-src", "data-srcset", "srcset"],
                    "video": ["src", "poster", "data-src"],
                    "audio": ["src", "data-src"],
                    "source": ["src", "srcset"],
                    "track": ["src"],
                    "embed": ["src"],
                    "object": ["data"],
                    "iframe": ["src"],
                    "svg": ["data", "href"],
                    "script": ["src", "href"],
                    "a": ["href"],
                    "link": ["href"],
                }

                tag_name = tag.name
                if tag_name in tag_attr_map:
                    for attr in tag_attr_map[tag_name]:
                        url = tag.get(attr)
                        if not url:
                            continue
                        if url.startswith(("http://", "https://")):
                            rewritten_url = OutputHandler._rewrite_github_blob_asset_url(url)
                            if rewritten_url != url:
                                tag[attr] = rewritten_url
                            continue
                        if not url.startswith(
                            ("data:", "#", "mailto:", "tel:", "javascript:")
                        ):
                            tag[attr] = urljoin(base_url, url)

            return str(soup)

        # 匹配自闭合标签如 <img src="...">、<video src="..."> 等
        md_content = re.sub(r"<([a-zA-Z][a-zA-Z0-9]*)\s+[^>]*>", convert_html_attrs_in_md, md_content)

        return md_content

    @staticmethod
    def _extract_metadata(html_content, source_url=None, translator=None, archive_url=None, description_override=None):
        """
        从HTML中提取元数据用于YAML front matter。

        Args:
            html_content: 原始HTML内容
            source_url: 来源URL (可选)
            translator: 翻译模型名称 (可选)

        Returns:
            dict: 包含title, created, updated, tags, source, translator的字典
        """
        soup = BeautifulSoup(html_content, "html.parser")
        metadata = {
            "title": None,
            "description": None,
            "author": None,
            "created": None,
            "updated": None,
            "tags": [],
            "source": source_url,
            "translator": translator,
            "archive": archive_url,
        }
        if metadata["source"]:
            metadata["source"] = OutputHandler._canonicalize_generic_source_url(metadata["source"])

        source_site_tag = soup.find("meta", attrs={"name": "surf-source-site"})
        source_site = source_site_tag.get("content", "").strip().lower() if source_site_tag else ""
        if source_site == "xiaohongshu" and metadata["source"]:
            metadata["source"] = Fetcher._canonicalize_xiaohongshu_source_url(metadata["source"])
        elif source_site == "v2ex" and metadata["source"]:
            metadata["source"] = Fetcher._canonicalize_v2ex_source_url(metadata["source"])
        elif metadata["source"] and re.match(r"^https?://((www|cn)\.)?v2ex\.com/t/\d+", metadata["source"], re.IGNORECASE):
            metadata["source"] = Fetcher._canonicalize_v2ex_source_url(metadata["source"])
        elif source_site == "douban" and metadata["source"]:
            metadata["source"] = Fetcher._canonicalize_douban_source_url(metadata["source"])
        elif metadata["source"] and Fetcher._is_douban_url(metadata["source"]):
            metadata["source"] = Fetcher._canonicalize_douban_source_url(metadata["source"])

        twitter_author_tag = soup.find("meta", attrs={"name": "surf-author"})
        if source_site == "twitter" and twitter_author_tag:
            author_value = (twitter_author_tag.get("content") or "").strip()
            if author_value:
                metadata["author"] = OutputHandler.normalize_markdown_encoding(author_value)
        elif source_site == "zhihu":
            zhihu_author_tag = soup.find("meta", attrs={"name": "surf-author"})
            if zhihu_author_tag:
                author_value = (zhihu_author_tag.get("content") or "").strip()
                if author_value:
                    metadata["author"] = OutputHandler.normalize_markdown_encoding(author_value)

        def _format_front_matter_datetime(dt):
            """Format datetime values as local ISO timestamps without timezone suffix."""
            if dt is None:
                return None
            if getattr(dt, "tzinfo", None) is not None:
                dt = dt.astimezone().replace(tzinfo=None)
            return dt.isoformat(timespec="seconds")

        def _extract_direct_markdown_field(pattern):
            direct_markdown = soup.find(id="surf-direct-markdown")
            if not direct_markdown:
                return None
            match = re.search(pattern, direct_markdown.get_text(), re.MULTILINE)
            if not match:
                return None
            value = match.group(1).strip()
            return OutputHandler.normalize_markdown_encoding(value) if value else None

        if source_site == "v2ex" or (
            source_url and re.match(r"^https?://((www|cn)\.)?v2ex\.com/t/\d+", source_url, re.IGNORECASE)
        ):
            author = _extract_direct_markdown_field(r"^Author:\s*(.+)$")
            node_name = _extract_direct_markdown_field(r"^Node:\s*(.+)$")
            published = _extract_direct_markdown_field(r"^Published:\s*(.+)$")

            if not author:
                author_tag = soup.select_one("#Main .box .header small.gray a[href^='/member/']")
                if author_tag and author_tag.get_text(strip=True):
                    author = OutputHandler.normalize_markdown_encoding(author_tag.get_text(" ", strip=True))
            if not node_name:
                breadcrumb_tags = soup.select("#Main .box .header .flex-one-row a")
                if breadcrumb_tags:
                    node_name = OutputHandler.normalize_markdown_encoding(
                        breadcrumb_tags[-1].get_text(" ", strip=True)
                    )
            if not published:
                published_tag = soup.select_one("#Main .box .header small.gray span[title]")
                if published_tag:
                    published = OutputHandler.normalize_markdown_encoding((published_tag.get("title") or "").strip())

            if author:
                metadata["author"] = author
            if node_name:
                metadata["tags"] = [node_name]
            if published:
                try:
                    parsed_date = date_parser.parse(published)
                    metadata["created"] = _format_front_matter_datetime(parsed_date)
                except (ValueError, TypeError):
                    pass
        elif source_site == "zhihu":
            created_tag = soup.find("meta", attrs={"name": "surf-created"})
            updated_tag = soup.find("meta", attrs={"name": "surf-updated"})
            if created_tag:
                created_value = (created_tag.get("content") or "").strip()
                if created_value:
                    try:
                        metadata["created"] = _format_front_matter_datetime(date_parser.parse(created_value))
                    except (ValueError, TypeError):
                        pass
            if updated_tag:
                updated_value = (updated_tag.get("content") or "").strip()
                if updated_value:
                    try:
                        metadata["updated"] = _format_front_matter_datetime(date_parser.parse(updated_value))
                    except (ValueError, TypeError):
                        pass
        elif source_url and re.match(r"^https?://www\.douban\.com/topic/\d+/?(?:[?#].*)?$", source_url, re.IGNORECASE):
            author_tag = soup.select_one("div.article-main div.article-meta a.author-name, div.article-main div.article-meta a#author-name")
            if author_tag and author_tag.get_text(strip=True):
                metadata["author"] = OutputHandler.normalize_markdown_encoding(author_tag.get_text(strip=True))

            created_tag = soup.select_one("div.article-main div.article-meta div.topic-meta span.create-time")
            if created_tag and created_tag.get_text(strip=True):
                created_value = OutputHandler.normalize_markdown_encoding(created_tag.get_text(strip=True))
                try:
                    parsed_date = date_parser.parse(created_value)
                    metadata["created"] = _format_front_matter_datetime(parsed_date)
                except (ValueError, TypeError):
                    pass

        # 提取title元素
        title_tag = soup.find("title")
        if title_tag:
            metadata["title"] = OutputHandler.normalize_markdown_encoding(title_tag.get_text(strip=True))

        twitter_title = OutputHandler._extract_social_first_sentence_title(html_content, source_url=source_url)
        if twitter_title:
            metadata["title"] = OutputHandler.normalize_markdown_encoding(twitter_title)

        extracted_description = OutputHandler._extract_html_meta_description(html_content)
        if extracted_description:
            metadata["description"] = extracted_description
        if description_override:
            metadata["description"] = OutputHandler.normalize_markdown_encoding(description_override)

        # 提取发布日期 - 尝试多种常见的meta标签
        date_selectors = [
            ("meta", {"property": "article:published_time"}),
            ("meta", {"name": "publishdate"}),
            ("meta", {"name": "date"}),
            ("meta", {"property": "og:published_time"}),
            ("meta", {"name": "pubdate"}),
        ]

        for tag_name, attrs in date_selectors:
            date_tag = soup.find(tag_name, attrs)
            if date_tag:
                date_value = date_tag.get("content") or date_tag.get("value")
                if date_value:
                    try:
                        # 尝试解析日期
                        parsed_date = date_parser.parse(date_value)
                        metadata["created"] = _format_front_matter_datetime(parsed_date)
                        break
                    except (ValueError, TypeError):
                        pass

        # 提取keywords作为tags
        if not metadata["tags"]:
            keywords_tag = soup.find("meta", {"name": "keywords"})
            if keywords_tag:
                keywords_value = keywords_tag.get("content") or keywords_tag.get("value")
                if keywords_value:
                    # 分割关键词为列表
                    tags = [
                        OutputHandler.normalize_markdown_encoding(tag.strip())
                        for tag in keywords_value.split(",")
                        if tag.strip()
                    ]
                    metadata["tags"] = tags

        # 设置updated为当前日期（保持既有格式）
        if not metadata.get("updated"):
            metadata["updated"] = datetime.now().strftime("%Y-%m-%d")

        return metadata

    @staticmethod
    def _generate_yaml_frontmatter(metadata):
        """
        生成YAML front matter字符串。

        Args:
            metadata: 元数据字典

        Returns:
            str: YAML格式的front matter
        """
        lines = ["---"]

        if metadata.get("title"):
            lines.append(f"title: {metadata['title']}")

        if metadata.get("description"):
            lines.append(f"description: {metadata['description']}")

        if metadata.get("author"):
            lines.append(f"author: {metadata['author']}")

        if metadata.get("created"):
            lines.append(f"created: {metadata['created']}")

        if metadata.get("updated"):
            lines.append(f"updated: {metadata['updated']}")

        if metadata.get("tags") and len(metadata["tags"]) > 0:
            lines.append("tags:")
            for tag in metadata["tags"]:
                lines.append(f"  - {tag}")

        if metadata.get("source"):
            lines.append(f"source: {metadata['source']}")

        if metadata.get("archive"):
            lines.append(f"archive: {metadata['archive']}")

        if metadata.get("translator"):
            lines.append(f"translator: {metadata['translator']}")

        lines.append("---\n")

        return "\n".join(lines)

    @staticmethod
    def save_markdown(
        title,
        content,
        config,
        output_path=None,
        base_url=None,
        html_content=None,
        add_front_matter=True,
        translated_title=None,
        source_url=None,
        translator=None,
        archive_url=None,
    ):
        """
        Save content as Markdown file.

        Args:
            title: Document title (used for filename)
            content: Markdown content to save
            config: Config object
            output_path: Specific output file path (optional)
            base_url: Base URL for converting relative URLs
            html_content: Original HTML content for metadata extraction
            add_front_matter: Whether to add YAML front matter (default: True)
            translated_title: Translated title to use in YAML front matter (if translation was performed)
            source_url: Source URL to include in YAML front matter (default: None)
            translator: Translation model name to include in YAML front matter (default: None)
            archive_url: Wayback Machine snapshot URL to include in YAML front matter (default: None)
        """
        md_dir = config.get_path("Output", "md_dir", fallback="./notes")
        if not os.path.exists(md_dir):
            os.makedirs(md_dir)

        filename_title = OutputHandler._get_filename_title(title, source_url=source_url, html_content=html_content)

        # Determine filepath
        if output_path:
            # Expand user home directory (~) if present
            filepath = resolve_user_path(output_path)
            # Handle special cases: "." or "./" (current directory)
            if filepath == "." or filepath == "./":
                # Use current directory + default filename
                safe_title = OutputHandler._safe_filename_title(filename_title, max_len=120)
                filename = f"{safe_title}.md"
                filepath = os.path.join(".", filename)
            # Ensure directory exists
            filepath_dir = os.path.dirname(filepath)
            if filepath_dir and not os.path.exists(filepath_dir):
                os.makedirs(filepath_dir)
        else:
            # Simple sanitization
            safe_title = OutputHandler._safe_filename_title(filename_title, max_len=120)
            filename = f"{safe_title}.md"
            filepath = os.path.join(md_dir, filename)

        # Convert relative URLs to absolute if base_url is provided
        if base_url:
            content = OutputHandler._convert_markdown_urls_to_absolute(content, base_url)

        # Generate YAML front matter if html_content is provided and add_front_matter is True
        yaml_frontmatter = ""
        if html_content and add_front_matter:
            metadata = OutputHandler._extract_metadata(
                html_content,
                source_url=source_url,
                translator=translator,
                archive_url=archive_url,
            )
            # Use translated_title if provided (translation was performed)
            if translated_title:
                metadata["title"] = translated_title
            yaml_frontmatter = OutputHandler._generate_yaml_frontmatter(metadata)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(yaml_frontmatter)
                f.write(content)
            logger.info(f"Markdown saved to {filepath}")
        except Exception as e:
            logger.error(f"Failed to save markdown: {e}")

        return filepath

    @staticmethod
    def save_note(title, content, config, base_url=None):
        """Backward compatibility wrapper for save_markdown."""
        return OutputHandler.save_markdown(title, content, config, base_url=base_url)

    @staticmethod
    def _generate_with_playwright(full_html, filepath, config):
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.set_content(full_html)
                page.pdf(path=filepath, format="A4")
                browser.close()
            return True
        except Exception as e:
            logger.error(f"Playwright PDF failed: {e}")
            return False

    @staticmethod
    def generate_pdf(title, md_content, config, output_path=None):
        logger.info("Generating PDF...")

        import markdown  # type: ignore

        html_body = markdown.markdown(md_content)
        full_html = f"""
        <html>
        <head>
            <meta charset="utf-8">
            <title>{title}</title>
            <style>
                body {{ font-family: sans-serif; line-height: 1.6; padding: 2em; }}
                h1, h2, h3 {{ color: #333; }}
                pre {{ background: #f4f4f4; padding: 1em; overflow-x: auto; }}
                code {{ font-family: monospace; }}
                img {{ max-width: 100%; }}
            </style>
        </head>
        <body>
            <h1>{title}</h1>
            {html_body}
        </body>
        </html>
        """

        # Determine filepath
        if output_path:
            # Expand user home directory (~) if present
            filepath = resolve_user_path(output_path)
            # Ensure directory exists
            filepath_dir = os.path.dirname(filepath)
            if filepath_dir and not os.path.exists(filepath_dir):
                os.makedirs(filepath_dir)
        else:
            safe_title = OutputHandler._safe_filename_title(title)
            pdf_dir = config.get_path("Output", "pdf_dir", fallback=".")
            if not os.path.exists(pdf_dir):
                os.makedirs(pdf_dir)
            filepath = os.path.join(pdf_dir, f"{safe_title}.pdf")

        success = OutputHandler._generate_with_playwright(full_html, filepath, config)

        if success:
            logger.info(f"PDF saved to {filepath}")
        else:
            logger.error("Failed to generate PDF.")

        return filepath

    @staticmethod
    def save_html(title, html_content, config, inline=False, output_path=None, base_url=None):
        """
        Save content as HTML file.

        Args:
            title: Document title
            html_content: HTML content to save
            config: Config object
            inline: If True, inline CSS and JS for standalone HTML
            output_path: Specific output file path (optional)
            base_url: Base URL for converting relative URLs (used when inline=False)
        """
        html_dir = config.get_path("Output", "html_dir", fallback=".")
        if not os.path.exists(html_dir):
            os.makedirs(html_dir)

        # Sanitize filename
        safe_title = OutputHandler._safe_filename_title(title, max_len=100)

        if inline:
            html_content = OutputHandler._inline_resources(html_content)
        elif base_url:
            # Convert relative URLs to absolute for non-inline HTML
            html_content = OutputHandler._convert_urls_to_absolute(html_content, base_url)

        # Wrap content in complete HTML document if needed
        if (
            html_content
            and not html_content.strip().lower().startswith("<!doctype")
            and not html_content.strip().lower().startswith("<html")
        ):
            html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
</head>
<body>
{html_content}
</body>
</html>"""

        # Determine filepath
        if output_path == "-":
            # Write to stdout
            _configure_stdout_utf8()
            sys.stdout.write(html_content)
            sys.stdout.flush()
            logger.info(f"HTML output to stdout (inline={inline})")
            return None
        elif output_path:
            # Expand user home directory (~) if present
            filepath = resolve_user_path(output_path)
            # Ensure directory exists
            filepath_dir = os.path.dirname(filepath)
            if filepath_dir and not os.path.exists(filepath_dir):
                os.makedirs(filepath_dir)
        else:
            filepath = os.path.join(html_dir, f"{safe_title}.html")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html_content)

        logger.info(f"HTML saved to {filepath} (inline={inline})")
        return filepath

    @staticmethod
    def _inline_resources(html_content):
        """
        Inline CSS and JS resources in HTML content.

        Args:
            html_content: Raw HTML content

        Returns:
            HTML with inlined CSS and JS
        """
        soup = BeautifulSoup(html_content, "html.parser")

        # Inline CSS
        for link in soup.find_all("link", rel="stylesheet"):
            href = link.get("href")
            if href:
                try:
                    if not href.startswith(("http://", "https://", "data:")):
                        logger.warning(f"Skipping relative CSS: {href}")
                        continue

                    response = _requests_get_interruptibly(href, timeout=10)
                    if response.status_code == 200:
                        style = soup.new_tag("style")
                        style.string = response.text
                        link.replace_with(style)
                        logger.info(f"Inlined CSS: {href}")
                except Exception as e:
                    logger.warning(f"Failed to inline CSS {href}: {e}")

        # Inline JS
        for script in soup.find_all("script", src=True):
            src = script.get("src")
            if src:
                try:
                    if not src.startswith(("http://", "https://", "data:")):
                        logger.warning(f"Skipping relative JS: {src}")
                        continue

                    response = _requests_get_interruptibly(src, timeout=10)
                    if response.status_code == 200:
                        new_script = soup.new_tag("script")
                        new_script.string = response.text
                        script.replace_with(new_script)
                        logger.info(f"Inlined JS: {src}")
                except Exception as e:
                    logger.warning(f"Failed to inline JS {src}: {e}")

        # Add meta charset if missing
        if not soup.find("meta", charset=True):
            head = soup.find("head")
            if head:
                meta = soup.new_tag("meta")
                meta["charset"] = "utf-8"
                head.insert(0, meta)

        return str(soup)


class PublishHandler:
    """Handler for publishing content to various platforms."""

    @staticmethod
    def publish_to_pastebin(title, content, config, proxy_mode_override=None, custom_proxy_override=None):
        """
        Publish content to pastebin.com.

        Args:
            title: Paste title
            content: Content to paste (Markdown without YAML front matter)
            config: Config object
            proxy_mode_override: Override proxy_mode from command line
            custom_proxy_override: Override custom_proxy from command line

        Returns:
            str: URL of the created paste, or None if failed
        """
        api_dev_key = config.get("publish.pastebin", "api_dev_key")
        if not api_dev_key:
            logger.error("Pastebin API key not found in config [publish.pastebin] section.")
            return None

        logger.info(f"Publishing to pastebin.com as '{title}'...")

        # Get proxies for the request
        req_proxies, _ = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)

        # Prepare POST data (equivalent to curl command)
        data = {
            "api_dev_key": api_dev_key,
            "api_option": "paste",
            "api_paste_code": content,
            "api_paste_name": title,
        }

        # Optional: get additional parameters from config
        paste_expire = config.get("publish.pastebin", "api_paste_expire", fallback=None)
        paste_private = config.get("publish.pastebin", "api_paste_private", fallback="0")

        if paste_expire:
            data["api_paste_expire_date"] = paste_expire
        if paste_private:
            data["api_paste_private"] = paste_private

        # Verbose logging of POST data
        logger.debug("Pastebin POST URL: https://pastebin.com/api/api_post.php")
        logger.debug("Pastebin POST data:")
        for key, value in data.items():
            if key == "api_paste_code":
                logger.debug(f"  {key}: <{len(value)} chars>")
                logger.debug(f"  Content preview: {value[:200]}...")
            elif key == "api_paste_name":
                logger.debug(f"  {key}: {value}")
            elif key == "api_dev_key":
                logger.debug(f"  {key}: <hidden>")
            else:
                logger.debug(f"  {key}: {value}")

        try:
            response = _requests_post_interruptibly(
                "https://pastebin.com/api/api_post.php",
                data=data,
                proxies=req_proxies,
                timeout=30,
            )

            # Check for errors in response
            if response.text.startswith("Bad API request"):
                logger.error(f"Pastebin API error: {response.text}")
                return None

            # Success: response is the paste URL
            paste_url = response.text.strip()
            logger.info(f"Successfully published to: {paste_url}")
            return paste_url

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to publish to pastebin: {e}")
            return None


def get_data_dir():
    """
    Get the base directory for storing application data.
    Priority:
    1. Windows: %LOCALLAPPDATA%\\surf
    2. Linux/Mac: ~/.local/cache/surf
    """
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return os.path.join(local_app_data, "surf")

    # Fallback to ~/.local/cache/surf for Linux/Mac
    return os.path.join(os.path.expanduser("~"), ".local", "cache", "surf")


def migrate_data():
    """
    Migrate data from ~/.surf to the new data directory if necessary.
    """
    old_dir = os.path.join(os.path.expanduser("~"), ".surf")
    new_dir = get_data_dir()

    if old_dir == new_dir:
        return

    if os.path.exists(old_dir) and not os.path.exists(new_dir):
        try:
            import shutil

            # Create parent directory for new_dir if it doesn't exist
            parent_dir = os.path.dirname(new_dir)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)

            shutil.move(old_dir, new_dir)
            logger.info(f"Successfully migrated data from {old_dir} to {new_dir}")
        except Exception as e:
            logger.warning(f"Failed to migrate data from {old_dir} to {new_dir}: {e}")
    elif os.path.exists(old_dir) and os.path.exists(new_dir):
        logger.info(f"Both {old_dir} and {new_dir} exist. Please manually merge if needed.")


# Perform migration on import/startup
migrate_data()


class AuthHandler:
    """
    Handler for browser authentication state management.
    Supports persistent login state for sites requiring authentication.
    """

    # Directory to store authentication states
    AUTH_STATE_DIR = os.path.join(get_data_dir(), "auth")
    LOGIN_URLS = {
        "xiaohongshu": "https://www.xiaohongshu.com",
        "twitter": "https://x.com/i/flow/login",
        "x": "https://x.com/i/flow/login",
        "reddit": "https://www.reddit.com/login/",
        "zhihu": "https://www.zhihu.com/",
        "douban": "https://accounts.douban.com/passport/login",
        "ncpssd": "https://www.ncpssd.cn/",
    }

    @staticmethod
    def normalize_site_name(site_name):
        """Normalize site aliases used by auth-related commands."""
        normalized = site_name.lower()
        if normalized in {"twitter", "x"}:
            return "twitter"
        if normalized in {"reddit"}:
            return "reddit"
        if normalized in {"ncp", "ncpssd"}:
            return "ncpssd"
        return normalized

    @staticmethod
    def get_login_url(site_name):
        """Return the interactive login URL for a supported site."""
        normalized_site_name = AuthHandler.normalize_site_name(site_name)
        return AuthHandler.LOGIN_URLS.get(normalized_site_name)

    @staticmethod
    def can_launch_headed_browser():
        """Best-effort check for whether a headed browser can be launched."""
        if sys.platform.startswith("linux"):
            return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        return True

    @staticmethod
    def get_twitter_profile_dir():
        """Persistent browser profile directory for Twitter/X login session."""
        if not os.path.exists(AuthHandler.AUTH_STATE_DIR):
            os.makedirs(AuthHandler.AUTH_STATE_DIR)
        return os.path.join(AuthHandler.AUTH_STATE_DIR, "twitter_profile")

    @staticmethod
    def _get_state_file(site_name):
        """Get the path to the state file for a specific site."""
        site_name = AuthHandler.normalize_site_name(site_name)
        if not os.path.exists(AuthHandler.AUTH_STATE_DIR):
            os.makedirs(AuthHandler.AUTH_STATE_DIR)
        return os.path.join(AuthHandler.AUTH_STATE_DIR, f"{site_name}_state.json")

    @staticmethod
    def load_state(site_name, log_load=True):
        """
        Load saved browser state for a site.

        Args:
            site_name: Identifier for the site (e.g., 'xiaohongshu', 'twitter')
            log_load: If False, skip the info log (for frequent reads e.g. cookie injection)

        Returns:
            dict: Browser state dictionary, or None if not found
        """
        normalized_site_name = AuthHandler.normalize_site_name(site_name)
        state_file = AuthHandler._get_state_file(normalized_site_name)
        if os.path.exists(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    state = json.load(f)
                    if log_load:
                        logger.info(f"Loaded auth state for {normalized_site_name}")
                    return state
            except Exception as e:
                logger.warning(f"Failed to load auth state for {normalized_site_name}: {e}")
        return None

    @staticmethod
    def cookie_header_for_zhihu():
        """
        Build a Cookie header value from saved Playwright storage for zhihu.com domains.
        Zhihu's v4 API often returns 403 without logged-in cookies.
        """
        return AuthHandler.cookie_header_for_domains("zhihu", {"zhihu.com"})

    @staticmethod
    def cookie_header_for_reddit():
        """Build a Cookie header value from saved Playwright storage for reddit.com domains."""
        return AuthHandler.cookie_header_for_domains("reddit", {"reddit.com"})

    @staticmethod
    def cookie_header_for_douban():
        """Build a Cookie header value from saved Playwright storage for douban.com domains."""
        return AuthHandler.cookie_header_for_domains("douban", {"douban.com"})

    @staticmethod
    def cookie_header_for_domains(site_name, domains):
        state = AuthHandler.load_state(site_name, log_load=False)
        if not state:
            return None
        cookies = state.get("cookies") or []
        by_name = {}
        for c in cookies:
            domain = (c.get("domain") or "").lower()
            if not any(allowed_domain in domain for allowed_domain in domains):
                continue
            name = c.get("name")
            if not name:
                continue
            by_name[name] = c.get("value", "")
        if not by_name:
            return None
        return "; ".join(f"{k}={v}" for k, v in by_name.items())

    @staticmethod
    def twitter_cookie_values():
        """Return saved Twitter/X auth_token, ct0, and full cookie string from Surf auth state."""
        state = AuthHandler.load_state("twitter", log_load=False)
        if not state:
            return None
        cookies = state.get("cookies") or []
        by_name = {}
        for cookie in cookies:
            domain = (cookie.get("domain") or "").lower()
            if "x.com" not in domain and "twitter.com" not in domain:
                continue
            name = cookie.get("name")
            value = cookie.get("value")
            if name and value and name not in by_name:
                by_name[name] = value
        auth_token = by_name.get("auth_token")
        ct0 = by_name.get("ct0")
        if not auth_token or not ct0:
            return None
        return {
            "auth_token": auth_token,
            "ct0": ct0,
            "cookie_string": "; ".join(f"{name}={value}" for name, value in by_name.items()),
        }

    @staticmethod
    def apply_twitter_cookie_env(env):
        """Inject saved Surf Twitter/X cookies into a subprocess environment."""
        if env.get("TWITTER_AUTH_TOKEN") and env.get("TWITTER_CT0"):
            return env
        values = AuthHandler.twitter_cookie_values()
        if not values:
            return env
        env["TWITTER_AUTH_TOKEN"] = values["auth_token"]
        env["TWITTER_CT0"] = values["ct0"]
        if values.get("cookie_string"):
            env["TWITTER_COOKIE_STRING"] = values["cookie_string"]
        return env

    @staticmethod
    def save_state(site_name, state):
        """
        Save browser state for a site.

        Args:
            site_name: Identifier for the site
            state: Browser state dictionary from browser_context.storage_state()
        """
        normalized_site_name = AuthHandler.normalize_site_name(site_name)
        state_file = AuthHandler._get_state_file(normalized_site_name)
        try:
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            logger.info(f"Saved auth state for {normalized_site_name} to {state_file}")
        except Exception as e:
            logger.error(f"Failed to save auth state for {normalized_site_name}: {e}")

    @staticmethod
    def export_state(site_name, export_path):
        """Export a saved auth state JSON to a user-specified path."""
        normalized_site_name = AuthHandler.normalize_site_name(site_name)
        state = AuthHandler.load_state(normalized_site_name, log_load=False)
        if not state:
            raise FileNotFoundError(
                f"No saved auth state found for {normalized_site_name}. Run `surf --login {normalized_site_name}` first."
            )

        export_path = resolve_user_path(export_path)
        export_dir = os.path.dirname(os.path.abspath(export_path))
        if export_dir:
            os.makedirs(export_dir, exist_ok=True)

        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        logger.info(f"Exported auth state for {normalized_site_name} to {export_path}")

    @staticmethod
    def import_state(site_name, import_path):
        """Import an auth state JSON from a user-specified path."""
        normalized_site_name = AuthHandler.normalize_site_name(site_name)
        import_path = resolve_user_path(import_path)
        with open(import_path, "r", encoding="utf-8") as f:
            state = json.load(f)

        if not isinstance(state, dict):
            raise ValueError("Invalid auth state file: expected a JSON object")

        AuthHandler.save_state(normalized_site_name, state)

    @staticmethod
    def _twitter_storage_state_from_cookie_string(cookie_string):
        """Build a Playwright storage_state payload from a browser-style Twitter cookie string."""
        if not cookie_string:
            return None

        cookies = []
        seen = set()
        for part in cookie_string.split(";"):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            name = name.strip()
            value = value.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            cookies.append(
                {
                    "name": name,
                    "value": value,
                    "domain": ".x.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": name in {"auth_token", "ct0"},
                    "secure": True,
                    "sameSite": "Lax",
                }
            )
            cookies.append(
                {
                    "name": name,
                    "value": value,
                    "domain": ".twitter.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": name in {"auth_token", "ct0"},
                    "secure": True,
                    "sameSite": "Lax",
                }
            )

        names = {cookie["name"] for cookie in cookies}
        if not {"auth_token", "ct0"}.issubset(names):
            return None
        return {"cookies": cookies, "origins": []}

    @staticmethod
    def import_twitter_cookies_from_cli(
        config,
        proxy_mode_override=None,
        custom_proxy_override=None,
        browser=None,
        profile=None,
    ):
        """Use twitter-cli/browser-cookie3 to import real-browser X cookies into Surf auth state."""
        env_auth_token = (os.environ.get("TWITTER_AUTH_TOKEN") or "").strip()
        env_ct0 = (os.environ.get("TWITTER_CT0") or "").strip()
        if env_auth_token and env_ct0:
            return AuthHandler.import_twitter_cookie_values(env_auth_token, env_ct0)

        cli_command = Fetcher._resolve_twitter_cli_command(config.get("Twitter", "cli_bin", fallback=""))
        if not cli_command:
            logger.info("uvx not found; cannot import Twitter cookies via twitter-cli")
            return False

        req_proxies, _ = Fetcher._get_twitter_forced_proxies(config, proxy_mode_override, custom_proxy_override)
        proxy_url = None
        if req_proxies:
            proxy_url = req_proxies.get("https") or req_proxies.get("http")

        env = os.environ.copy()
        env.setdefault("UV_CACHE_DIR", os.path.join(os.getcwd(), ".uv-cache"))
        env.setdefault("PYTHONIOENCODING", "utf-8")
        AuthHandler.apply_twitter_cookie_env(env)
        browser = (browser or config.get("Twitter", "browser", fallback="") or "").strip()
        profile = (profile or config.get("Twitter", "profile", fallback="") or "").strip()
        if browser:
            env["TWITTER_BROWSER"] = browser
        if profile:
            env["TWITTER_CHROME_PROFILE"] = profile
        if proxy_url:
            env["TWITTER_PROXY"] = proxy_url

        script = r"""
import json
from twitter_cli.auth import get_cookies

cookies = get_cookies()
print(json.dumps(cookies, ensure_ascii=False))
"""
        command = cli_command[:-1] + ["python", "-c", script] if cli_command[-1] == "twitter" else []
        if not command:
            return False

        try:
            completed = _run_subprocess_interruptibly(
                command,
                timeout=60,
                env=env,
                check=False,
            )
        except Exception as e:
            logger.info(f"Twitter cookie import via twitter-cli failed to run: {e}")
            return False

        if completed.returncode != 0:
            message = (
                _decode_subprocess_output(completed.stderr)
                or _decode_subprocess_output(completed.stdout)
                or ""
            ).strip()
            logger.info(
                "Twitter cookie import via twitter-cli failed (%s): %s",
                completed.returncode,
                message or "no output",
            )
            if message:
                print("Twitter/X browser cookie import failed:")
                print(message)
            return False

        try:
            cookies = json.loads(_decode_subprocess_output(completed.stdout) or "")
        except Exception as e:
            logger.info(f"Twitter cookie import output could not be parsed: {e}")
            return False

        cookie_string = cookies.get("cookie_string")
        if not cookie_string and cookies.get("auth_token") and cookies.get("ct0"):
            cookie_string = f"auth_token={cookies['auth_token']}; ct0={cookies['ct0']}"
        state = AuthHandler._twitter_storage_state_from_cookie_string(cookie_string)
        if not state:
            logger.info("Twitter cookie import did not produce auth_token/ct0")
            return False

        AuthHandler.save_state("twitter", state)
        logger.info("Imported Twitter/X cookies from real browser into Surf auth state")
        return True

    @staticmethod
    def import_twitter_cookie_values(auth_token, ct0, cookie_string=None):
        """Import manually supplied Twitter/X auth_token and ct0 values."""
        auth_token = (auth_token or "").strip()
        ct0 = (ct0 or "").strip()
        if not auth_token or not ct0:
            return False
        cookie_string = cookie_string or f"auth_token={auth_token}; ct0={ct0}"
        state = AuthHandler._twitter_storage_state_from_cookie_string(cookie_string)
        if not state:
            return False
        AuthHandler.save_state("twitter", state)
        logger.info("Imported Twitter/X cookies from auth_token/ct0 values")
        return True

    @staticmethod
    def clear_state(site_name=None):
        """
        Clear saved authentication state.

        Args:
            site_name: Site identifier, or None to clear all states
        """
        if site_name:
            site_name = AuthHandler.normalize_site_name(site_name)
            state_file = AuthHandler._get_state_file(site_name)
            if os.path.exists(state_file):
                os.remove(state_file)
                logger.info(f"Cleared auth state for {site_name}")
            if site_name.lower() in {"twitter", "x"}:
                profile_dir = AuthHandler.get_twitter_profile_dir()
                if os.path.exists(profile_dir):
                    import shutil

                    shutil.rmtree(profile_dir)
                    logger.info("Cleared persistent profile for twitter")
        else:
            if os.path.exists(AuthHandler.AUTH_STATE_DIR):
                import shutil

                shutil.rmtree(AuthHandler.AUTH_STATE_DIR)
                logger.info("Cleared all auth states")

    @staticmethod
    def interactive_login(
        site_name,
        login_url,
        config,
        proxy_mode_override=None,
        custom_proxy_override=None,
        twitter_browser=None,
        twitter_profile=None,
    ):
        """
        Perform interactive login for a site.
        Opens a visible browser window for user to manually log in.

        Args:
            site_name: Site identifier
            login_url: URL to open for login
            config: Config object
            proxy_mode_override: Optional proxy mode override
            custom_proxy_override: Optional custom proxy override

        Returns:
            bool: True if login was successful, False otherwise
        """
        from playwright.sync_api import sync_playwright

        normalized_site_name = AuthHandler.normalize_site_name(site_name)
        if not AuthHandler.can_launch_headed_browser():
            login_url = AuthHandler.get_login_url(normalized_site_name) or login_url
            logger.error(
                "Interactive login requires a graphical desktop session. No DISPLAY/WAYLAND_DISPLAY was detected."
            )
            print(f"Login URL: {login_url}")
            print("Recommended workflow:")
            print(f"1. Run `surf --login {normalized_site_name}` on a desktop machine with a browser.")
            print(f"2. Export the saved state with `surf --export-auth {normalized_site_name} <FILE>`.")
            print(
                f"3. Copy the file to this server and import it with `surf --import-auth {normalized_site_name} <FILE>`."
            )
            return False

        if normalized_site_name == "twitter":
            _, pw_proxy = Fetcher._get_twitter_forced_proxies(config, proxy_mode_override, custom_proxy_override)
        else:
            _, pw_proxy = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)

        print(f"\n{'=' * 60}")
        print(f"Interactive Login for {normalized_site_name}")
        print(f"{'=' * 60}")
        print("A browser window will open. Please:")
        print(f"1. Log in to {normalized_site_name} manually")
        print("2. Complete any CAPTCHA or verification if needed")
        print("3. Once logged in, press Enter in this terminal to save the session")
        print(f"{'=' * 60}\n")

        if normalized_site_name == "twitter":
            print("Trying to import Twitter/X cookies from your real browser first...")
            if AuthHandler.import_twitter_cookies_from_cli(
                config,
                proxy_mode_override,
                custom_proxy_override,
                browser=twitter_browser,
                profile=twitter_profile,
            ):
                print("Imported Twitter/X login cookies from your real browser.")
                return True
            print("Browser cookie import was unavailable.")
            print("If the X login window loops, paste cookie values instead.")
            manual = input("Paste auth_token now? [y/N]: ").strip().lower()
            if manual in {"y", "yes"}:
                auth_token = getpass.getpass("auth_token: ").strip()
                ct0 = getpass.getpass("ct0: ").strip()
                if AuthHandler.import_twitter_cookie_values(auth_token, ct0):
                    print("Imported Twitter/X login cookies from auth_token/ct0.")
                    return True
                print("auth_token/ct0 import failed; falling back to an interactive login window.")
            else:
                print("Falling back to an interactive login window.")

        with sync_playwright() as p:
            browser = None
            context = None
            profile_dir = None
            if normalized_site_name == "twitter":
                # Use persistent profile and minimal fingerprint modifications for login.
                profile_dir = AuthHandler.get_twitter_profile_dir()
                os.makedirs(profile_dir, exist_ok=True)
                persistent_args = {
                    "headless": False,
                }
                if pw_proxy:
                    persistent_args["proxy"] = pw_proxy

                try:
                    context = p.chromium.launch_persistent_context(profile_dir, channel="chrome", **persistent_args)
                except Exception as e:
                    logger.info(f"Chrome channel unavailable for login, fallback to Chromium: {e}")
                    context = p.chromium.launch_persistent_context(profile_dir, **persistent_args)
            else:
                launch_args = {"headless": False}
                if pw_proxy:
                    launch_args["proxy"] = pw_proxy
                try:
                    browser = p.chromium.launch(channel="chrome", **launch_args)
                except Exception as e:
                    logger.info(f"Chrome channel unavailable for login, fallback to Chromium: {e}")
                    browser = p.chromium.launch(**launch_args)

                if normalized_site_name == "zhihu":
                    context = Fetcher._create_stealth_context(browser, login_url)
                else:
                    context = browser.new_context(
                        viewport={"width": 1280, "height": 800},
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    )

            page = context.pages[0] if context.pages else context.new_page()

            try:
                page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
                print(f"Browser opened. Please log in to {normalized_site_name}...")
                input("Press Enter after you have logged in...")

                # Save the state
                if normalized_site_name == "twitter" and profile_dir:
                    # For persistent Twitter context, the profile itself is primary session storage.
                    # Attempt to export storage_state for compatibility; tolerate target-closed cases.
                    try:
                        state = context.storage_state()
                        AuthHandler.save_state(normalized_site_name, state)
                    except Exception as state_error:
                        logger.warning(
                            f"Unable to export twitter storage_state, will rely on persistent profile: {state_error}"
                        )
                else:
                    state = context.storage_state()
                    AuthHandler.save_state(normalized_site_name, state)
                print(f"Login state saved for {normalized_site_name}")
                return True

            except KeyboardInterrupt:
                logger.warning("Interactive login cancelled by user.")
                return False
            except Exception as e:
                logger.error(f"Interactive login failed: {e}")
                return False
            finally:
                try:
                    context.close()
                except Exception as close_error:
                    logger.debug(f"Ignoring context close error: {close_error}")
                if browser:
                    try:
                        browser.close()
                    except Exception as close_error:
                        logger.debug(f"Ignoring browser close error: {close_error}")

    @staticmethod
    def create_context_with_auth(browser, site_name, **context_options):
        """
        Create a browser context with authentication state if available.

        Args:
            browser: Playwright browser instance
            site_name: Site identifier
            **context_options: Additional options for new_context()

        Returns:
            BrowserContext: Context with auth state if available
        """
        state = AuthHandler.load_state(site_name)
        if state:
            logger.info(f"Using saved auth state for {site_name}")
            return browser.new_context(storage_state=state, **context_options)
        else:
            logger.info(f"No saved auth state for {site_name}")
            return browser.new_context(**context_options)


class TTSHandler:
    @staticmethod
    async def generate_speech(text, output_file, config):
        voice = config.get("TTS", "voice", fallback="zh-CN-XiaoxiaoNeural")
        rate = config.get("TTS", "rate", fallback="+0%")
        volume = config.get("TTS", "volume", fallback="+0%")

        logger.info(f"Generating TTS audio with voice: {voice}, rate: {rate}, volume: {volume}...")
        communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
        await communicate.save(output_file)
        logger.info(f"Audio saved to {output_file}")

    @staticmethod
    def play_audio(file_path):
        logger.info(f"Playing audio: {file_path}")
        # playsound handles the blocking playback
        playsound(file_path)
        logger.info("Playback finished.")

    @staticmethod
    def run_tts(title, content, config, speak=False, save_path=None):
        # Professional cleanup for TTS: remove markdown artifacts, images, and only keep link text
        # Remove ![alt](url)
        clean_text = re.sub(r"!\[.*?\]\(.*?\)", "", content)
        # Remove [link text](url) -> keep 'link text'
        clean_text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", clean_text)
        # Remove other common MD artifacts
        clean_text = clean_text.replace("#", "").replace("*", "").replace("`", "").replace("---", "")

        # If output file not specified but speak is needed, use temp
        temp_file = "tts_temp.mp3"
        filename = resolve_user_path(save_path) if save_path else temp_file

        try:
            asyncio.run(TTSHandler.generate_speech(clean_text, filename, config))

            if speak:
                TTSHandler.play_audio(filename)

            # If we used a temp file and didn't ask to save, clean it up
            if speak and not save_path and os.path.exists(temp_file):
                os.remove(temp_file)

        except Exception as e:
            logger.error(f"TTS operation failed: {e}")


def main():
    _install_interrupt_handler()
    parser = argparse.ArgumentParser(
        prog="uv run surf",
        description="Surf - Convert URL to Markdown/PDF/HTML/Audio",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
        epilog="""
Examples:
  surf https://example.com                      # Translate to Chinese
  surf -p https://example.com                   # Generate PDF
  surf -h https://example.com                   # Save as HTML
  surf -a https://example.com                   # Save as audio
  surf -r https://example.com                   # Keep original language
  surf -b https://example.com                   # Bilingual output
  surf -x win https://example.com               # Use Windows proxy
  surf -x env https://example.com               # Use proxy from environment variables
  surf -c http://127.0.0.1:7890 https://example.com
  surf -n https://example.com                   # No proxy
  surf -hrn https://example.com                 # Combined short flags
  surf -t https://x.com/user/status/123         # Fetch later replies in the thread
  surf --thread before https://x.com/user/status/123
  surf --thread both --thread-author same https://x.com/user/status/123

Special Sites:
  WeChat & Xiaohongshu: Default to no proxy and no translation.
                        Override with -x/--proxy and -l/--lang if needed.
  Twitter/X:           Uses the same proxy order as Surf.
                        If the proxy path fails, Surf retries direct access automatically.
                        Default backend prefers `uvx --from twitter-cli twitter`.
                        `auto` backend tries CLI first, then native fallback.
  Twitter/X, Bluesky, Weibo, Threads: thread expansion defaults to `after`
                        with `--thread-author all`.
  Twitter/X, Bluesky, Weibo, Threads: short-post titles and default filenames
                        use `First sentence - Author on Site`.
  V2EX:                Forces configured proxy by default, keeps the original
                        language, and downloads only the main topic unless
                        `-t/--thread` is used to include replies.

Authentication:
  surf --login xiaohongshu                   # Login to Xiaohongshu
  surf --export-auth xiaohongshu STATE.json  # Export saved auth state
  surf --import-auth xiaohongshu STATE.json  # Import auth state on another machine
  surf --login ncpssd                        # Login to NCPSSD (for full-text PDF download)
  surf --login twitter                       # Login to Twitter/X
  surf --login reddit                        # Login to Reddit
  surf --login douban                        # Login to Douban
  surf --clear-auth xiaohongshu              # Clear auth for Xiaohongshu
  surf --clear-auth ncpssd                   # Clear auth for NCPSSD
  surf --clear-auth twitter                  # Clear auth for Twitter/X
  surf --clear-auth reddit                   # Clear auth for Reddit
  surf --clear-auth douban                   # Clear auth for Douban

OCR:
  surf --ocr-images URL                      # Run local OCR on article images
  surf --no-ocr-images URL                   # Disable OCR, including Xiaohongshu default
  surf --ocr-engine rapidocr URL             # Prefer RapidOCR, fallback to Tesseract
  surf --ocr-engine tesseract URL            # Force Tesseract only
  Xiaohongshu:                              OCR on images is enabled by default

Twitter/X Backend:
  surf --twitter-backend cli URL             # Prefer uvx --from twitter-cli twitter
  surf --twitter-backend auto URL            # CLI first, then native fallback
  surf --twitter-browser chrome URL          # Prefer Chrome cookies for twitter-cli
        """,
    )
    # Position argument
    parser.add_argument(
        "url",
        nargs="?",
        help="URL to process (not required for auth-management commands such as --login, --export-auth, --import-auth, or --clear-auth)",
    )

    # Output format
    format_group = parser.add_mutually_exclusive_group()
    format_group.add_argument(
        "-f",
        "--format",
        choices=["md", "pdf", "html", "audio", "publish"],
        help="Output format: md/pdf/html/audio/publish (default: md)",
    )
    format_group.add_argument("-h", action="store_true", help="Shorthand for --format html")
    format_group.add_argument("-p", action="store_true", help="Shorthand for --format pdf")
    format_group.add_argument("-a", action="store_true", help="Shorthand for --format audio")
    format_group.add_argument("-P", action="store_true", help="Shorthand for --format publish")

    # Output path
    parser.add_argument(
        "-o",
        "--output",
        help="Output file path (use '-' for stdout, overrides config; on Windows, accepts '~/Note' and '/' and maps '~' to %%USERPROFILE%%)",
    )
    parser.add_argument("-O", action="store_true", help="Shorthand for --output - (output to stdout)")

    # Language mode
    lang_group = parser.add_mutually_exclusive_group()
    lang_group.add_argument(
        "-l",
        "--lang",
        choices=["trans", "raw", "both"],
        help="Language mode: trans=translate, raw=original, both=bilingual (default: trans)",
    )
    lang_group.add_argument("-r", action="store_true", help="Shorthand for --lang raw")
    lang_group.add_argument("-b", action="store_true", help="Shorthand for --lang both")

    # TTS
    parser.add_argument("-s", "--speak", action="store_true", help="Speak the content")

    # Authentication
    parser.add_argument(
        "--login",
        metavar="SITE",
        help="Interactive login for a site (e.g., xiaohongshu, twitter/x, reddit, zhihu, douban, ncpssd). Opens a browser for manual login on machines with a desktop session.",
    )
    parser.add_argument(
        "--clear-auth",
        metavar="SITE",
        help="Clear saved authentication for a site (use 'all' to clear all)",
    )
    parser.add_argument(
        "--export-auth",
        nargs=2,
        metavar=("SITE", "FILE"),
        help="Export saved authentication state to a JSON file for transfer to another machine",
    )
    parser.add_argument(
        "--import-auth",
        nargs=2,
        metavar=("SITE", "FILE"),
        help="Import authentication state from a JSON file exported on another machine",
    )
    parser.add_argument(
        "--twitter-backend",
        choices=["auto", "cli", "native"],
        help="Twitter/X backend: cli=prefer uvx --from twitter-cli twitter, auto=CLI first then native fallback, native=surf built-in only",
    )
    parser.add_argument(
        "--twitter-cli-bin",
        help="Deprecated; ignored because Surf always invokes uvx --from twitter-cli twitter",
    )
    parser.add_argument(
        "--twitter-browser",
        choices=["arc", "chrome", "edge", "firefox", "brave"],
        help="Preferred browser cookie source for twitter-cli",
    )
    parser.add_argument(
        "--twitter-profile",
        help="Preferred browser profile for twitter-cli (for example: Default or Profile 2)",
    )

    # HTML options
    parser.add_argument("--html-inline", action="store_true", help="Inline CSS/JS in HTML output")

    ocr_group = parser.add_mutually_exclusive_group()
    ocr_group.add_argument(
        "--ocr-images",
        action="store_true",
        help="Run local OCR on article images (experimental)",
    )
    ocr_group.add_argument(
        "--no-ocr-images",
        action="store_true",
        help="Disable image OCR, including sites that enable it by default",
    )
    parser.add_argument(
        "--ocr-lang",
        help="OCR language(s) for Tesseract OCR, e.g. chi_sim+eng",
    )
    parser.add_argument(
        "--ocr-engine",
        choices=["rapidocr", "tesseract", "auto"],
        help="OCR engine: rapidocr (default), tesseract, or auto (rapidocr then tesseract)",
    )

    # Network
    proxy_choices = ["env", "custom", "no"]
    if Fetcher._is_windows():
        proxy_choices.insert(1, "win")
    parser.add_argument(
        "-x",
        "--proxy",
        choices=proxy_choices,
        help="Proxy mode: env=environment vars, custom=use --set-proxy or config custom_proxy, no=no proxy"
        + (", win=Windows Internet Settings" if Fetcher._is_windows() else ""),
    )
    parser.add_argument(
        "-c",
        nargs="?",
        const="",
        metavar="PROXY",
        help="Shorthand for --proxy custom; optionally provide PROXY to override config custom_proxy",
    )
    parser.add_argument("-n", action="store_true", help="Shorthand for --proxy no")
    parser.add_argument("--set-proxy", help="Custom proxy URL for --proxy custom (overrides config custom_proxy)")

    # LLM
    parser.add_argument("--llm", help="Override the default LLM provider (case-insensitive)")

    # Other options
    parser.add_argument("--browser", action="store_true", help="Force use of browser (Playwright)")
    thread_group = parser.add_mutually_exclusive_group()
    thread_group.add_argument(
        "-t",
        "--thread",
        nargs="?",
        const="after",
        choices=["after", "before", "both", "off"],
        help="Thread range: after=later posts (default), before=earlier posts, both=both directions, off=disabled",
    )
    thread_group.add_argument(
        "--no-thread",
        action="store_true",
        help="Disable thread expansion for supported social sites",
    )
    parser.add_argument(
        "--thread-author",
        choices=["same", "all"],
        default="all",
        help="Thread author scope: all=all authors (default), same=only the current post author",
    )
    parser.add_argument(
        "--config",
        help="Path to config file (on Windows, accepts '~/Note' and '/' and maps '~' to %%USERPROFILE%%)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument(
        "--no-front-matter",
        action="store_true",
        help="Disable YAML front matter in markdown output",
    )
    parser.add_argument(
        "--archive",
        action="store_true",
        help="Save the final source URL to the Wayback Machine and write the snapshot URL to front matter",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--help", action="help", help="Show this help message")

    args = parser.parse_args(_normalize_thread_argv(sys.argv[1:]))
    _raise_if_interrupted()

    # Enable verbose logging if requested
    if args.verbose:
        setup_verbose_logging()
    _raise_if_interrupted()

    # Determine config path
    config = Config(args.config) if args.config else Config()

    # Handle --clear-auth
    if args.clear_auth:
        clear_site = args.clear_auth.lower()
        if clear_site == "all":
            AuthHandler.clear_state()
        else:
            if clear_site == "x":
                clear_site = "twitter"
            AuthHandler.clear_state(clear_site)
        return

    if args.export_auth:
        site_name, export_path = args.export_auth
        export_path = resolve_user_path(export_path)
        normalized_site_name = AuthHandler.normalize_site_name(site_name)
        if normalized_site_name not in {"xiaohongshu", "twitter", "reddit", "zhihu", "douban", "ncpssd"}:
            parser.error("Unsupported site for --export-auth. Supported: xiaohongshu, twitter/x, reddit, zhihu, douban, ncpssd")
        try:
            AuthHandler.export_state(normalized_site_name, export_path)
        except Exception as e:
            logger.error(f"Failed to export auth state: {e}")
            sys.exit(1)
        print(f"Exported auth state for {normalized_site_name} to {export_path}")
        return

    if args.import_auth:
        site_name, import_path = args.import_auth
        import_path = resolve_user_path(import_path)
        normalized_site_name = AuthHandler.normalize_site_name(site_name)
        if normalized_site_name not in {"xiaohongshu", "twitter", "reddit", "zhihu", "douban", "ncpssd"}:
            parser.error("Unsupported site for --import-auth. Supported: xiaohongshu, twitter/x, reddit, zhihu, douban, ncpssd")
        try:
            AuthHandler.import_state(normalized_site_name, import_path)
        except Exception as e:
            logger.error(f"Failed to import auth state: {e}")
            sys.exit(1)
        print(f"Imported auth state for {normalized_site_name} from {import_path}")
        return

    # Resolve proxy args (shared by normal fetch and --login)
    proxy_mode, custom_proxy = _resolve_proxy_args(args, parser, config)

    # Handle --login
    if args.login:
        site_name = args.login.lower()
        login_url = AuthHandler.get_login_url(site_name)
        if not login_url:
            parser.error(f"Unsupported site: {site_name}. Supported: {', '.join(AuthHandler.LOGIN_URLS.keys())}")
        login_site = AuthHandler.normalize_site_name(site_name)
        success = AuthHandler.interactive_login(
            login_site,
            login_url,
            config,
            proxy_mode,
            custom_proxy,
            twitter_browser=args.twitter_browser,
            twitter_profile=args.twitter_profile,
        )
        sys.exit(0 if success else 1)

    # Check if url is required but not provided
    if not args.url:
        parser.error("URL is required (unless using --login or --clear-auth)")

    args.url = Fetcher._resolve_common_short_url(
        args.url,
        config,
        proxy_mode_override=proxy_mode,
        custom_proxy_override=custom_proxy,
    )

    # Determine final format
    output_format = "md"  # default
    if args.format:
        output_format = args.format
    elif args.h:
        output_format = "html"
    elif args.p:
        output_format = "pdf"
    elif args.a:
        output_format = "audio"
    elif args.P:
        output_format = "publish"

    # Determine final language mode
    lang_mode = "trans"  # default
    if args.lang:
        lang_mode = args.lang
    elif args.r:
        lang_mode = "raw"
    elif args.b:
        lang_mode = "both"

    # Determine proxy mode override
    # None means implicit resolution (env -> INI -> WinHTTP).

    fetch_thread = None
    if args.thread:
        fetch_thread = False if args.thread == "off" else args.thread
    elif args.no_thread:
        fetch_thread = False
    fetch_thread_author = args.thread_author

    # Check if URL matches special site handlers for default policies
    handler, site_name, site_config = _get_handler_for_url(args.url)
    explicit_output_format_selected = bool(args.format or args.h or args.p or args.a or args.P)
    if site_config:
        # Apply special site defaults (can be overridden by command line)
        if site_config.get("default_no_proxy") and proxy_mode is None and not Fetcher._is_windows():
            logger.info(f"{site_name}: Applying default 'no proxy' policy (can be overridden with -x)")
            proxy_mode = "no"

        if site_config.get("default_no_translate") and lang_mode == "trans":
            logger.info(f"{site_name}: Applying default 'no translate' policy (can be overridden with -l)")
            lang_mode = "raw"

        if site_config.get("default_thread") and fetch_thread is None:
            logger.info(f"{site_name}: Applying default thread expansion policy")
            fetch_thread = "after"

    # NCPSSD secure article pages default to PDF output (same as implicit `-p`).
    # This only applies when user did not explicitly choose an output format.
    if (
        not explicit_output_format_selected
        and site_name == "ncpssd"
        and Fetcher._is_ncpssd_secure_article_url(args.url)
    ):
        output_format = "pdf"
        logger.info("ncpssd: Applying default output format 'pdf' for secure article page")

    # Determine output path
    output_path = resolve_user_path(args.output) if args.output else None
    if args.O:
        output_path = "-"

    # For NCPSSD secure article pages, PDF mode should download the original full-text PDF
    # by simulating a click on the page's "全文下载" button.
    if output_format == "pdf" and site_name == "ncpssd" and Fetcher._is_ncpssd_secure_article_url(args.url):
        downloaded_pdf = Fetcher.download_ncpssd_pdf(
            args.url,
            config=config,
            output_path=output_path,
            proxy_mode_override=proxy_mode,
            custom_proxy_override=custom_proxy,
        )
        if downloaded_pdf:
            print(downloaded_pdf)
            return
        logger.warning(
            "NCPSSD original PDF download was unavailable. Falling back to generated PDF from extracted content."
        )

    # 1. Fetch
    _raise_if_interrupted()
    try:
        html_content = Fetcher.fetch(
            args.url,
            config=config,
            use_browser=args.browser,
            proxy_mode_override=proxy_mode,
            custom_proxy_override=custom_proxy,
            fetch_thread=fetch_thread,
            fetch_thread_author=fetch_thread_author,
            twitter_backend=args.twitter_backend,
            twitter_cli_bin=args.twitter_cli_bin,
            twitter_browser=args.twitter_browser,
            twitter_profile=args.twitter_profile,
        )
    except Exception as e:
        logger.error(f"Failed to fetch {args.url}: {e}")
        diagnosis = _diagnose_network_fetch_failure(args.url, e)
        if diagnosis:
            logger.error(diagnosis)
        sys.exit(1)

    if not html_content:
        if Fetcher._is_twitter_url(args.url):
            logger.error("Failed to fetch usable Twitter/X content (likely login wall or proxy/session issue).")
        else:
            logger.error(f"Failed to fetch usable content from {args.url}.")
        sys.exit(1)

    # ── Paywall detection and archive.is fallback ──
    archive_is_url = None
    paywall_result = Fetcher._detect_paywall(html_content, url=args.url)
    if paywall_result and paywall_result.get("detected"):
        logger.warning(
            f"Paywall detected (confidence: {paywall_result['confidence']:.0%}): "
            f"{paywall_result.get('reason', 'unknown')}"
        )
        logger.info("Attempting to fetch from archive.is...")
        archived_html, snapshot_url = Fetcher._fetch_archiveis_snapshot(
            args.url,
            config=config,
            proxy_mode_override=proxy_mode,
            custom_proxy_override=custom_proxy,
        )
        if archived_html:
            logger.info("archive.is snapshot fetched successfully, using it as content source.")
            archive_is_url = snapshot_url
            html_content = archived_html
        else:
            logger.error("内容受付费墙控制，未抓取全文")
            logger.error(f"  原始 URL: {args.url}")
            logger.error("  提示：可手动访问 https://archive.is/ 搜索该页面获取快照。")
            sys.exit(1)

    _raise_if_interrupted()
    try:
        processed = _process_fetched_content(
            html_content,
            args.url,
            config,
            site_name=site_name,
            site_config=site_config,
            lang_mode=lang_mode,
            ocr_args=args,
            proxy_mode_override=proxy_mode,
            custom_proxy_override=custom_proxy,
            llm_provider=args.llm if hasattr(args, "llm") else None,
        )
    except Exception as e:
        logger.error(f"Failed to process fetched content: {e}")
        sys.exit(1)

    source_url = processed["source_url"]
    content_base_url = processed["content_base_url"]
    title = processed["title"]
    cleaned_html = processed["cleaned_html"]
    md_content = processed["markdown"]
    original_md = processed["raw_markdown"]
    original_title = processed["original_title"]
    translated_title = processed["translated_title"]
    translation_performed = processed["translation_performed"]

    # 5. Output
    # Use the source URL from meta tag if available (for xhslink resolution)
    if source_url != args.url:
        logger.info(f"Using resolved source URL for metadata: {source_url}")

    # Handle output based on format
    _raise_if_interrupted()
    if output_format == "pdf":
        if output_path:
            OutputHandler.generate_pdf(title, md_content, config, output_path)
        else:
            OutputHandler.generate_pdf(title, md_content, config)

    elif output_format == "html":
        if output_path:
            OutputHandler.save_html(
                title,
                cleaned_html,
                config,
                inline=args.html_inline,
                output_path=output_path,
            )
        else:
            OutputHandler.save_html(title, cleaned_html, config, inline=args.html_inline)

    elif output_format == "audio":
        if output_path:
            TTSHandler.run_tts(title, md_content, config, speak=args.speak, save_path=output_path)
        else:
            TTSHandler.run_tts(title, md_content, config, speak=args.speak)

    elif output_format == "publish":
        # Publish to pastebin (md content without YAML front matter)
        paste_url = PublishHandler.publish_to_pastebin(
            title,
            md_content,
            config,
            proxy_mode_override=proxy_mode,
            custom_proxy_override=custom_proxy,
        )
        if paste_url:
            print(f"\nPublished to: {paste_url}")
        else:
            logger.error("Failed to publish content.")
            sys.exit(1)

    else:  # md (default)
        title = OutputHandler.normalize_markdown_encoding(title)
        md_content = OutputHandler.normalize_markdown_encoding(md_content)
        if translated_title:
            translated_title = OutputHandler.normalize_markdown_encoding(translated_title)

        # Get translator model name if translation was performed
        translator = None
        if translation_performed:
            try:
                llm_provider = args.llm if hasattr(args, "llm") else None
                llm_config = config.get_llm_config(llm_provider)
                translator = llm_config["model"]
            except Exception as e:
                logger.warning(f"Could not get LLM config for translator: {e}")

        # archive_url: prioritize archive.is snapshot (paywall fallback),
        # then explicit Wayback Machine --archive flag.
        archive_url = archive_is_url
        if not archive_url and args.archive and not args.no_front_matter and output_path != "-":
            archive_url = Fetcher.save_wayback_snapshot(
                source_url,
                config=config,
                proxy_mode_override=proxy_mode,
                custom_proxy_override=custom_proxy,
            )

            if output_path:
                if output_path == "-":
                    # Output to stdout
                    _configure_stdout_utf8()
                    sys.stdout.write(f"# {title}\n\n")
                    sys.stdout.write(md_content)
                    if not md_content.endswith("\n"):
                        sys.stdout.write("\n")
            else:
                OutputHandler.save_markdown(
                    title,
                    md_content,
                    config,
                    output_path,
                    html_content=html_content,
                    add_front_matter=not args.no_front_matter,
                    translated_title=translated_title if translation_performed else None,
                    source_url=source_url,
                    translator=translator,
                    archive_url=archive_url,
                )
        elif args.speak:
            TTSHandler.run_tts(title, md_content, config, speak=True)
        else:
            # Default: Save to default md_dir
            OutputHandler.save_markdown(
                title,
                md_content,
                config,
                html_content=html_content,
                add_front_matter=not args.no_front_matter,
                translated_title=translated_title if translation_performed else None,
                source_url=source_url,
                translator=translator,
                archive_url=archive_url,
            )


def run_cli():
    """Run the CLI entrypoint with consistent Ctrl+C exit behavior."""
    try:
        main()
    except KeyboardInterrupt:
        logger.warning("Interrupted by user.")
        sys.exit(130)


if __name__ == "__main__":
    run_cli()
