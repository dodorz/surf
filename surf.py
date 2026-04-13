#!/usr/bin/env -S uv run
import argparse
import configparser
import os
import sys
import logging
import json
import shutil
import subprocess
import tempfile
import io
import requests  # type: ignore
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
from html import escape
import trafilatura
import re
import unicodedata
from urllib.parse import urlparse


def _get_version():
    """从 pyproject.toml [project] 小节读取版本号"""
    try:
        with open("pyproject.toml", "r", encoding="utf-8") as f:
            content = f.read()
            # 匹配 [project] 小节后的 version = "..."
            match = re.search(
                r'\[project\].*?version\s*=\s*["\']([^"\']+)["\']',
                content,
                re.DOTALL
            )
            return match.group(1) if match else "0.0.0"
    except Exception:
        return "0.0.0"


__version__ = _get_version()

# Suppress warnings
warnings.filterwarnings("ignore")

# Configure logging (default: WARNING level, no timestamps)
logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def setup_verbose_logging():
    """Enable verbose logging with timestamps."""
    logging.getLogger().setLevel(logging.INFO)
    # Update existing handlers
    for handler in logging.getLogger().handlers:
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )


class Config:
    def __init__(self, config_path="config.ini"):
        # Disable interpolation to allow '%' in values (e.g. for TTS rate/voltage)
        self.config = configparser.ConfigParser(interpolation=None)
        if not os.path.exists(config_path):
            logger.warning(f"Config file {config_path} not found. Using defaults.")
        self.config.read(config_path)

        # Set default LLM provider
        self.llm_provider = self.get("LLM", "provider", fallback="L1")

    def get(self, section, key, fallback=None):
        return self.config.get(section, key, fallback=fallback)

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
        section = f"LLM.{provider}"

        if not self.config.has_section(section):
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
        return [
            section.split(".")[1]
            for section in self.config.sections()
            if section.startswith("LLM.")
        ]


class Fetcher:
    _HTML_META_CHARSET_RE = re.compile(
        rb'<meta\s+charset\s*=\s*["\']?([^"\'>\s]+)', re.IGNORECASE
    )
    _HTML_CONTENT_CHARSET_RE = re.compile(
        rb'content\s*=\s*["\'][^"\']*charset\s*=\s*([^"\';\s]+)', re.IGNORECASE
    )

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
            if (
                not header_has_charset
                and encoding.upper() == "ISO-8859-1"
            ):
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
    def _get_system_proxy_win():
        """
        Get Windows system proxy from WinINET (registry or IE settings).
        Returns dict or None.
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
                    # Check for override (per-protocol)
                    winreg.QueryValueEx(key, "ProxyOverride")[0] if False else None
                    return {"http": proxy, "https": proxy}
            return None
        except Exception as e:
            logger.warning(f"Failed to get Windows proxy: {e}")
            return None

    @staticmethod
    def _get_proxies(config, proxy_mode_override=None, custom_proxy_override=None):
        """
        Returns a dictionary of proxies based on configuration.
        For requests: {'http': '...', 'https': '...'} or None
        For playwright: {'server': '...'} or None

        Args:
            config: Config object
            proxy_mode_override: Override proxy_mode from command line (auto/no/win/custom)
            custom_proxy_override: Override custom_proxy from command line
        """
        # Use command line override if provided, otherwise use config
        mode = (
            proxy_mode_override.lower()
            if proxy_mode_override
            else config.get("Network", "proxy_mode", fallback="auto").lower()
        )

        if mode == "no":
            return None, None

        if mode == "custom":
            # Use command line override if provided, otherwise use config
            custom = (
                custom_proxy_override
                if custom_proxy_override
                else config.get("Network", "custom_proxy")
            )
            if custom:
                req_proxies = {"http": custom, "https": custom}
                pw_proxy = {"server": custom}
                return req_proxies, pw_proxy
            else:
                logger.warning(
                    "Custom proxy mode selected but no custom_proxy defined. Falling back to auto."
                )
                mode = "auto"

        if mode == "win":
            # Use WinINet API from Windows registry
            try:
                import winreg

                proxy_config = None
                try:
                    # Try to read proxy settings from Internet Explorer/Edge
                    key = winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                    )
                    proxy_enable = winreg.QueryValueEx(key, "ProxyEnable")[0]
                    if proxy_enable:
                        proxy_config = winreg.QueryValueEx(key, "ProxyServer")[0]
                    key.Close()
                except Exception as e:
                    logger.debug(f"Could not read Windows proxy registry: {e}")

                if proxy_config:
                    # Parse proxy config (can be "http=proxy:8080;https=proxy:8080" format)
                    proxies = {}
                    pw_proxy_server = None
                    for part in proxy_config.split(";"):
                        if "=" in part:
                            k, v = part.split("=", 1)
                            proxies[k.strip()] = v.strip()
                            if k.strip() == "http":
                                pw_proxy_server = v.strip()
                        else:
                            # Single proxy for all protocols
                            proxies["http"] = part.strip()
                            proxies["https"] = part.strip()
                            pw_proxy_server = part.strip()

                    # Build requests proxies dict
                    req_proxies = {}
                    if "http" in proxies:
                        req_proxies["http"] = proxies["http"]
                    if "https" in proxies:
                        req_proxies["https"] = proxies["https"]
                    if not req_proxies:
                        req_proxies = None

                    # Playwright proxy
                    pw_proxy = None
                    if pw_proxy_server:
                        pw_proxy = {"server": pw_proxy_server}

                    logger.info(f"Using Windows proxy: {proxy_config}")
                    return req_proxies, pw_proxy
                else:
                    logger.info("Windows proxy not enabled. Falling back to auto.")
                    mode = "auto"
            except ImportError:
                logger.warning("winreg not available. Falling back to auto.")
                mode = "auto"

        # mode == 'auto' (Check env vars, fallback to no proxy if not set)
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

        # Playwright prefers HTTPS, fallback HTTP
        server = https_proxy or http_proxy
        pw_proxy = None
        if server:
            pw_proxy = {"server": server}
            if no_proxy:
                pw_proxy["bypass"] = no_proxy

        return req_proxies, pw_proxy

    @staticmethod
    def fetch(
        url,
        config,
        use_browser=False,
        proxy_mode_override=None,
        custom_proxy_override=None,
        fetch_thread=None,
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
        logger.info(f"Fetching {url}...")

        handler, site_name, site_config = _get_handler_for_url(url)

        # Apply site defaults for proxy if not overridden
        if (
            site_config
            and site_config.get("default_no_proxy")
            and proxy_mode_override is None
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
                    backend=twitter_backend,
                    cli_bin=twitter_cli_bin,
                    browser=twitter_browser,
                    profile=twitter_profile,
                )
            elif site_name in {"bluesky", "weibo", "threads"}:
                html_content = handler(
                    url,
                    config,
                    proxy_mode_override,
                    custom_proxy_override,
                    fetch_thread=fetch_thread,
                )
            else:
                html_content = handler(url, config, proxy_mode_override, custom_proxy_override)
            if html_content:
                return html_content
            if site_config and site_config.get("no_generic_fallback"):
                logger.info(f"{site_name}: Special handler failed; skipping generic fallback")
                return None

        req_proxies, pw_proxy = Fetcher._get_proxies(
            config, proxy_mode_override, custom_proxy_override
        )

        if not use_browser:
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
                }
                logger.info(
                    f"Requests Proxies: {req_proxies if req_proxies else 'None'}"
                )
                response = requests.get(
                    url, headers=headers, proxies=req_proxies, timeout=10
                )
                response.raise_for_status()
                decoded_text = Fetcher._decode_response_text(response)

                # Check if likely dynamic (heuristic: very short content or explicit noscript)
                if len(decoded_text) < 1000 or "<noscript>" in decoded_text:
                    logger.info(
                        "Content seems short or requires JS. Switching to browser..."
                    )
                    return Fetcher.fetch_with_browser(
                        url, config, proxy_mode_override, custom_proxy_override
                    )

                return decoded_text
            except Exception as e:
                logger.warning(f"Requests failed: {e}. Switching to browser...")
                return Fetcher.fetch_with_browser(
                    url, config, proxy_mode_override, custom_proxy_override
                )
        else:
            return Fetcher.fetch_with_browser(
                url, config, proxy_mode_override, custom_proxy_override
            )

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
            "backend": (
                backend or config.get("Twitter", "backend", fallback="cli") or "cli"
            ).strip().lower(),
            "cli_bin": (cli_bin or config.get("Twitter", "cli_bin", fallback="") or "").strip(),
            "browser": (browser or config.get("Twitter", "browser", fallback="") or "").strip(),
            "profile": (profile or config.get("Twitter", "profile", fallback="") or "").strip(),
        }

    @staticmethod
    def _tag_twitter_html_content(html_content, kind):
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
        name = (
            author.get("name")
            or data.get("authorName")
            or data.get("userName")
            or ""
        )
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
                entity.get("expanded_url")
                or entity.get("expandedUrl")
                or entity.get("url")
                or entity.get("href")
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
                    node.get("entities")
                    or node.get("facets")
                    or node.get("annotations")
                    or node.get("formatting")
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
                    href = (
                        data.get("url")
                        or data.get("href")
                        or data.get("expanded_url")
                        or data.get("expandedUrl")
                    )
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
        entity_map = ((article.get("content") or {}).get("entityMap") or {})
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
                    blocks_html.append(
                        f"<p><img src='{escape(media_url)}' alt='tweet media'></p>"
                    )

        content = article.get("content") or {}
        blocks = content.get("blocks") or []
        if isinstance(blocks, list) and blocks:
            pending_list_type = None
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                block_fragments, media_refs = Fetcher._render_fx_block_html(
                    block, entity_map=entity_map
                )
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
                                blocks_html.append(
                                    f"<p><img src='{escape(next_url)}' alt='tweet media'></p>"
                                )
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
        if parent_link and "/photo/" not in parent_link.get("href", "") and "/status/" not in parent_link.get("href", ""):
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
    def _normalize_twitter_markup_fragment(node):
        fragment = BeautifulSoup(str(node), "html.parser")
        root = fragment.find()
        if not root:
            return ""

        for img in root.find_all("img"):
            img.decompose()

        for tag in root.find_all(True):
            style = (tag.get("style") or "").lower()
            classes = " ".join(tag.get("class") or []).lower()
            is_bold = (
                tag.name in {"strong", "b"}
                or "font-weight" in style and any(token in style for token in ("bold", "600", "700", "800", "900"))
                or any(token in classes for token in ("bold", "semibold", "fontbold", "font-semibold"))
            )
            is_italic = (
                tag.name in {"em", "i"}
                or "font-style:italic" in style
                or "italic" in classes
            )

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
    def _extract_twitter_dom_sequence(best_article):
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
            elif node.name == "div" and node.get_text(" ", strip=True) and not Fetcher._twitter_node_has_block_children(node):
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

            html = Fetcher._normalize_twitter_markup_fragment(node)
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

        blocks = Fetcher._extract_twitter_dom_sequence(best_article)
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

        html_parts = [
            "<html><head><meta charset='utf-8'>",
            f"<title>{escape(title)}</title>",
            "</head><body><article>",
        ]
        if source_url:
            html_parts.append(f"<p><a href='{escape(source_url)}'>{escape(source_url)}</a></p>")
        for kind_name, value in blocks:
            if kind_name == "image":
                html_parts.append(f"<p><img src='{escape(value)}' alt='tweet media'></p>")
            else:
                html_parts.append(value)
        html_parts.append("</article></body></html>")
        return Fetcher._tag_twitter_html_content("".join(html_parts), kind)

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

        article_title = (
            data.get("articleTitle")
            or data.get("article_title")
            or data.get("title")
            or ""
        )
        author_name, screen_name = Fetcher._extract_twitter_author_info(data)
        kind = "article" if str(article_title).strip() else "tweet"
        title_base = (
            str(article_title).strip()
            or author_name
            or (f"@{screen_name}" if screen_name else "X Post")
        )
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
            f"<p><a href='{escape(source_url)}'>{escape(source_url)}</a></p>",
        ]
        if screen_name:
            html_parts.append(f"<p>Author: @{escape(screen_name)}</p>")
        elif author_name:
            html_parts.append(f"<p>Author: {escape(author_name)}</p>")

        for line in lines:
            html_parts.append(f"<p>{line}</p>")

        for media_url in Fetcher._extract_twitter_media_urls(data):
            html_parts.append(f"<p><img src='{escape(media_url)}' alt='tweet media'></p>")

        html_parts.append("</article></body></html>")
        return Fetcher._tag_twitter_html_content("".join(html_parts), kind)

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

        req_proxies, _ = Fetcher._get_twitter_forced_proxies(
            config, proxy_mode_override, custom_proxy_override
        )
        proxy_url = None
        if req_proxies:
            proxy_url = req_proxies.get("https") or req_proxies.get("http")

        env = os.environ.copy()
        env.setdefault("UV_CACHE_DIR", os.path.join(os.getcwd(), ".uv-cache"))
        if browser:
            env["TWITTER_BROWSER"] = browser
        if profile:
            env["TWITTER_CHROME_PROFILE"] = profile
        if proxy_url:
            env["TWITTER_PROXY"] = proxy_url

        commands = [
            cli_command + ["article", url, "--json"],
            cli_command + ["tweet", url, "--json", "--full-text"],
        ]

        for command in commands:
            tmp_path = None
            completed = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", delete=False, encoding="utf-8"
                ) as tmp_file:
                    tmp_path = tmp_file.name
                command_with_output = command + ["--output", tmp_path]
                logger.info("Trying twitter-cli backend: %s", " ".join(command))
                completed = subprocess.run(
                    command_with_output,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                    env=env,
                    check=False,
                )
            except Exception as e:
                logger.warning(f"twitter-cli execution failed: {e}")
                return None
            finally:
                if tmp_path and os.path.exists(tmp_path) and (
                    completed is None or completed.returncode != 0
                ):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

            stderr = (completed.stderr or "").strip()
            if completed.returncode != 0:
                logger.info(
                    "twitter-cli command failed (%s): %s",
                    completed.returncode,
                    stderr or "no output",
                )
                continue

            if not tmp_path or not os.path.exists(tmp_path):
                logger.info("twitter-cli did not produce an output file")
                continue

            try:
                with open(tmp_path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception as e:
                logger.info(f"twitter-cli output could not be parsed: {e}")
                continue
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

            html = Fetcher._convert_twitter_cli_json_to_html(payload, url)
            if html:
                return html

        return None

    @staticmethod
    def _fetch_twitter_content(
        url,
        config,
        proxy_mode_override=None,
        custom_proxy_override=None,
        fetch_thread=None,
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
                html = Fetcher._fetch_twitter_oembed(
                    url, config, proxy_mode_override, custom_proxy_override
                )
            if html:
                thread_mode = Fetcher._normalize_thread_mode(fetch_thread)
                if thread_mode and Fetcher._extract_twitter_status_id(url):
                    thread_items, thread_current_index = Fetcher._get_twitter_thread_items(
                        url, config, proxy_mode_override, custom_proxy_override, thread_mode
                    )
                    html = Fetcher._merge_thread_context_into_html(
                        html, "twitter", thread_items, thread_current_index
                    )
                return html
        req_proxies, _ = Fetcher._get_twitter_forced_proxies(
            config, proxy_mode_override, custom_proxy_override
        )
        fallback_html = Fetcher._fetch_twitter_status_fallbacks(
            url, proxies=req_proxies
        )
        if fallback_html:
            thread_mode = Fetcher._normalize_thread_mode(fetch_thread)
            if thread_mode and Fetcher._extract_twitter_status_id(url):
                thread_items, thread_current_index = Fetcher._get_twitter_thread_items(
                    url, config, proxy_mode_override, custom_proxy_override, thread_mode
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
        raw = (
            raw.replace("\u2019", "'")
            .replace("&#39;", "'")
            .replace("&rsquo;", "'")
            .replace("&apos;", "'")
        )
        raw = re.sub(r"\s+", " ", raw)
        if (
            re.search(r"don'?t miss what'?s happening", raw)
            and "people on x are the first to know" in raw
        ):
            return True
        soup = BeautifulSoup(html_content, "html.parser")
        text = soup.get_text(" ", strip=True)
        return Fetcher._is_twitter_placeholder_text(text)

    @staticmethod
    def _resolve_url_with_redirects(url, proxies=None, timeout=30):
        """Resolve short links (for example t.co) to their final destination URL."""
        if not url:
            return None
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
            }
            response = requests.get(
                url, headers=headers, proxies=proxies, timeout=timeout, allow_redirects=True
            )
            return response.url or url
        except Exception as e:
            logger.debug(f"Failed to resolve redirect URL {url}: {e}")
            return None

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
            response = requests.get(api_url, headers=headers, proxies=proxies, timeout=30)
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
        title_base = user_name or (f"@{screen_name}" if screen_name else "X Post")
        title = f"{title_base} - X Post"

        html_parts = [
            "<html><head><meta charset='utf-8'>",
            f"<title>{escape(title)}</title>",
            "</head><body><article>",
            f"<h1>{escape(title_base)}</h1>",
            f"<p><a href='{escape(url)}'>{escape(url)}</a></p>",
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
        return Fetcher._tag_twitter_html_content("".join(html_parts), "tweet")

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
            response = requests.get(api_url, headers=headers, proxies=proxies, timeout=30)
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
            f"<p><a href='{escape(url)}'>{escape(url)}</a></p>",
        ]
        if screen_name:
            html_parts.append(f"<p>Author: @{escape(screen_name)}</p>")

        html_parts.extend(body_blocks)

        html_parts.append("</article></body></html>")
        return Fetcher._tag_twitter_html_content("".join(html_parts), kind)

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
        if source_url:
            html_parts.append(f"<p><a href='{escape(source_url)}'>{escape(source_url)}</a></p>")
        for para in paragraphs:
            html_parts.append(f"<p>{escape(para)}</p>")
        html_parts.append("</article></body></html>")
        return "".join(html_parts)

    @staticmethod
    def _get_twitter_forced_proxies(
        config, proxy_mode_override=None, custom_proxy_override=None
    ):
        """
        Get proxies for Twitter/X with forced proxy usage.
        Priority: command-line args > WinINET > env vars > config custom_proxy
        Twitter/X must always use proxy regardless of INI configuration.

        Returns:
            tuple: (req_proxies, pw_proxy) - always returns valid proxy config for Twitter
        """
        # If command-line proxy args provided, respect them first
        if proxy_mode_override:
            mode = proxy_mode_override.lower()
            if mode == "no":
                logger.info("Twitter: Command-line specified no proxy")
                return None, None
            elif mode == "custom" and custom_proxy_override:
                logger.info(
                    f"Twitter: Using command-line custom proxy: {custom_proxy_override}"
                )
                req_proxies = {
                    "http": custom_proxy_override,
                    "https": custom_proxy_override,
                }
                pw_proxy = {"server": custom_proxy_override}
                return req_proxies, pw_proxy
            elif mode == "win":
                logger.info("Twitter: Command-line specified Windows proxy")
            else:
                logger.info(
                    "Twitter: Defaulting forced proxy handling to Windows proxy (same as -x win)"
                )

        # Priority 1: WinINET (same default behavior as `surf -x win`)
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            )
            proxy_enable = winreg.QueryValueEx(key, "ProxyEnable")[0]
            if proxy_enable:
                proxy_config = winreg.QueryValueEx(key, "ProxyServer")[0]
                key.Close()
                if proxy_config:
                    # Parse proxy config
                    proxies = {}
                    pw_proxy_server = None
                    for part in proxy_config.split(";"):
                        if "=" in part:
                            k, v = part.split("=", 1)
                            proxies[k.strip()] = v.strip()
                            if k.strip() == "http":
                                pw_proxy_server = v.strip()
                        else:
                            proxies["http"] = part.strip()
                            proxies["https"] = part.strip()
                            pw_proxy_server = part.strip()

                    req_proxies = {}
                    if "http" in proxies:
                        req_proxies["http"] = proxies["http"]
                    if "https" in proxies:
                        req_proxies["https"] = proxies["https"]

                    pw_proxy = None
                    if pw_proxy_server:
                        pw_proxy = {"server": pw_proxy_server}

                    logger.info(f"Twitter: Using WinINET proxy: {proxy_config}")
                    return req_proxies, pw_proxy
            key.Close()
        except Exception as e:
            logger.debug(f"Could not read WinINET proxy for Twitter: {e}")

        # Priority 2: Environment variables
        http_proxy = os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY")
        https_proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
        no_proxy = os.environ.get("no_proxy") or os.environ.get("NO_PROXY")

        if http_proxy or https_proxy:
            server = https_proxy or http_proxy
            req_proxies = {}
            if http_proxy:
                req_proxies["http"] = http_proxy
            if https_proxy:
                req_proxies["https"] = https_proxy
            pw_proxy = {"server": server}
            if no_proxy:
                pw_proxy["bypass"] = no_proxy
            logger.info(f"Twitter: Using environment proxy fallback: {server}")
            return req_proxies, pw_proxy

        # Priority 3: Config custom_proxy
        custom_proxy = config.get("Network", "custom_proxy")
        if custom_proxy:
            logger.info(f"Twitter: Using config custom_proxy: {custom_proxy}")
            req_proxies = {"http": custom_proxy, "https": custom_proxy}
            pw_proxy = {"server": custom_proxy}
            return req_proxies, pw_proxy

        # Last resort: no proxy (should not happen for Twitter, but need to handle)
        logger.warning(
            "Twitter: No proxy configured! Twitter/X may block requests without proxy."
        )
        return None, None

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
        for img in soup.find_all(
            "img", src=re.compile(r"sns-avatar-qc\.xhscdn\.com", re.IGNORECASE)
        ):
            img["style"] = (
                "width: 60px; height: 60px; object-fit: cover; border-radius: 50%;"
            )
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
        content_html = (content_html or "").strip()
        if not content_html:
            return None

        metadata_parts = [f"<p><a href='{escape(source_url)}'>{escape(source_url)}</a></p>"]
        if question_title and question_title.strip() and question_title.strip() != display_title:
            metadata_parts.append(f"<p>Question: {escape(question_title.strip())}</p>")
        if author_name and author_name.strip():
            metadata_parts.append(f"<p>Author: {escape(author_name.strip())}</p>")
        if created_time:
            metadata_parts.append(f"<p>Created: {escape(created_time)}</p>")
        if updated_time and updated_time != created_time:
            metadata_parts.append(f"<p>Updated: {escape(updated_time)}</p>")
        stats = []
        if isinstance(voteup_count, int):
            stats.append(f"Upvotes: {voteup_count}")
        if isinstance(comment_count, int):
            stats.append(f"Comments: {comment_count}")
        if stats:
            metadata_parts.append(f"<p>{escape(' | '.join(stats))}</p>")

        return "".join(
            [
                "<html><head><meta charset='utf-8'>",
                "<meta name='referrer' content='no-referrer-when-downgrade'>",
                f"<title>{escape(display_title)}</title>",
                "</head><body><article>",
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
        if (
            "安全验证" in page_text
            or "验证你是否是真人" in page_text
            or "captcha" in page_text.lower()
        ):
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
                response = requests.get(
                    candidate_url, headers=headers, proxies=proxies, timeout=20
                )
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
    def _fetch_zhihu_content(
        url, config, proxy_mode_override=None, custom_proxy_override=None
    ):
        """
        Fetch Zhihu answer/article content via public API first.
        Falls back to browser-rendered DOM extraction when the API is blocked.
        """
        if proxy_mode_override is None:
            proxy_mode_override = "no"
            custom_proxy_override = None
            logger.info("zhihu: Forcing 'no proxy' inside special handler")

        req_proxies, _ = Fetcher._get_proxies(
            config, proxy_mode_override, custom_proxy_override
        )
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
                response = requests.get(
                    api_url, headers=headers, proxies=req_proxies, timeout=30
                )
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
                response = requests.get(
                    api_url, headers=headers, proxies=req_proxies, timeout=30
                )
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

        try:
            browser_html = Fetcher.fetch_with_browser(
                url, config, proxy_mode_override, custom_proxy_override
            )
            return Fetcher._extract_zhihu_dom_content(browser_html, url)
        except Exception as e:
            logger.warning(f"Zhihu browser fallback failed: {e}")
            return None

    @staticmethod
    def _fetch_twitter_oembed(
        url, config, proxy_mode_override=None, custom_proxy_override=None
    ):
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
            logger.info(
                "Direct Twitter Article URL detected; skipping oEmbed and fetching canonical article URL"
            )
            article_html = Fetcher.fetch_with_browser(
                article_url,
                config,
                proxy_mode_override,
                custom_proxy_override,
                is_twitter_article=True,
            )
            if article_html and not Fetcher._is_twitter_placeholder_content(article_html):
                return Fetcher._tag_twitter_html_content(article_html, "article")
            return article_html

        # oEmbed API endpoint for Twitter
        oembed_url = f"https://publish.twitter.com/oembed?url={url}"

        logger.info(f"Fetching Twitter content via oEmbed API: {oembed_url}")

        # Use forced proxy for Twitter (always use proxy regardless of INI config)
        req_proxies, _ = Fetcher._get_twitter_forced_proxies(
            config, proxy_mode_override, custom_proxy_override
        )

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        try:
            response = requests.get(
                oembed_url, headers=headers, proxies=req_proxies, timeout=30
            )
            response.raise_for_status()

            data = response.json()
            html_content = data.get("html", "")

            # Log metadata from oEmbed response
            author_name = data.get("author_name", "")
            provider_name = data.get("provider_name", "")

            logger.info(
                f"oEmbed response: author={author_name}, provider={provider_name}"
            )

            # oEmbed occasionally returns login-wall placeholder copy; do not trust it.
            if Fetcher._is_twitter_placeholder_content(html_content):
                logger.info(
                    "oEmbed returned placeholder/login text, trying syndication fallback"
                )
                fallback_html = Fetcher._fetch_twitter_status_fallbacks(
                    url, proxies=req_proxies
                )
                if fallback_html:
                    return fallback_html

                logger.info(
                    "Syndication fallback unavailable, falling back to browser fetch"
                )
                browser_html = Fetcher.fetch_with_browser(
                    url,
                    config,
                    proxy_mode_override,
                    custom_proxy_override,
                    is_twitter_article=True,
                )
                if browser_html and not Fetcher._is_twitter_placeholder_content(
                    browser_html
                ):
                    return Fetcher._tag_twitter_html_content(browser_html, "article")
                logger.warning(
                    "Browser fallback still returned placeholder/empty content for Twitter/X"
                )
                return None

            # Check if this is a Twitter Article (oEmbed only returns link)
            if Fetcher._is_twitter_article_only_link(html_content):
                logger.info(
                    "oEmbed returned link-only content (Twitter Article), fetching with browser..."
                )
                article_target_url = Fetcher._extract_twitter_article_target(
                    url, html_content, proxies=req_proxies
                )
                # Fetch directly with browser and clean content
                article_html = Fetcher.fetch_with_browser(
                    article_target_url,
                    config,
                    proxy_mode_override,
                    custom_proxy_override,
                    is_twitter_article=True,
                )
                if article_html and not Fetcher._is_twitter_placeholder_content(
                    article_html
                ):
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
            logger.warning(f"oEmbed API failed ({e}), trying status-id fallbacks")
            fallback_html = Fetcher._fetch_twitter_status_fallbacks(
                url, proxies=req_proxies
            )
            if fallback_html:
                return fallback_html
            logger.warning("Status-id fallbacks unavailable, falling back to browser fetch")
            return None

    @staticmethod
    def _fetch_wechat_article(
        url, config, proxy_mode_override=None, custom_proxy_override=None
    ):
        from playwright.sync_api import sync_playwright

        req_proxies, pw_proxy = Fetcher._get_proxies(
            config, proxy_mode_override, custom_proxy_override
        )
        with sync_playwright() as p:
            browser = (
                p.chromium.launch(headless=True, proxy=pw_proxy)
                if pw_proxy
                else p.chromium.launch(headless=True)
            )
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
        return bool(
            re.match(r"^https?://(www\.)?(twitter|x)\.com/", url, re.IGNORECASE)
        )

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
            context = AuthHandler.create_context_with_auth(
                browser, auth_site_name, **context_options
            )
        else:
            context = browser.new_context(**context_options)

        # Add script to hide webdriver property
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            window.chrome = { runtime: {} };
        """)

        return context

    @staticmethod
    def fetch_with_browser(
        url,
        config,
        proxy_mode_override=None,
        custom_proxy_override=None,
        is_twitter_article=False,
    ):
        logger.info("Launching browser...")
        from playwright.sync_api import sync_playwright

        twitter_target_url = (
            Fetcher._normalize_twitter_article_url(url)
            if Fetcher._is_twitter_url(url) and Fetcher._is_twitter_article_url(url)
            else url
        )

        is_zhihu_url = bool(
            re.match(r"^https?://((www\.)?zhihu\.com|zhuanlan\.zhihu\.com)/", url, re.IGNORECASE)
        )

        # For Twitter/X URLs, always use forced proxy regardless of INI config
        is_twitter_url = Fetcher._is_twitter_url(url)
        if is_twitter_url:
            _, pw_proxy = Fetcher._get_twitter_forced_proxies(
                config, proxy_mode_override, custom_proxy_override
            )
            logger.info("Twitter/X URL detected - using forced proxy settings")
        else:
            _, pw_proxy = Fetcher._get_proxies(
                config, proxy_mode_override, custom_proxy_override
            )

        if pw_proxy:
            logger.info(f"Playwright Proxy: {pw_proxy}")
        else:
            logger.info("Playwright Proxy: None")

        with sync_playwright() as p:
            browser = None
            profile_context = None
            twitter_profile_dir = (
                AuthHandler.get_twitter_profile_dir() if is_twitter_url else None
            )

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
                    logger.info(
                        f"Chrome channel unavailable for twitter profile fetch, fallback to Chromium: {e}"
                    )
                    profile_context = p.chromium.launch_persistent_context(
                        twitter_profile_dir, **persistent_args
                    )
                context = profile_context
                page = context.pages[0] if context.pages else context.new_page()
            else:
                # Launch browser with stealth args
                launch_args = {
                    "headless": True,
                    "args": [
                        "--disable-blink-features=AutomationControlled",
                        "--disable-features=IsolateOrigins,site-per-process",
                        "--disable-web-security",
                        "--disable-features=BlockInsecurePrivateNetworkRequests",
                    ],
                }
                if pw_proxy:
                    launch_args["proxy"] = pw_proxy

                browser = p.chromium.launch(**launch_args)

                # Attach persisted auth state for sites with saved login sessions.
                auth_site_name = None
                if is_twitter_url:
                    auth_site_name = "twitter"
                elif is_zhihu_url:
                    auth_site_name = "zhihu"
                context = Fetcher._create_stealth_context(
                    browser, url, auth_site_name=auth_site_name
                )
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
                    logger.info("Using domcontentloaded strategy for Zhihu")
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(4000)
                else:
                    page.goto(url, wait_until="networkidle", timeout=60000)
                    page.wait_for_timeout(2000)

                content = page.content()

                if is_twitter_url:
                    if Fetcher._is_twitter_placeholder_content(content):
                        logger.info(
                            "Browser returned placeholder/login content, trying syndication fallback"
                        )
                        req_proxies, _ = Fetcher._get_twitter_forced_proxies(
                            config, proxy_mode_override, custom_proxy_override
                        )
                        syndication_html = Fetcher._fetch_twitter_syndication_html(
                            twitter_target_url, proxies=req_proxies
                        )
                        if syndication_html:
                            logger.info("Using Twitter/X syndication fallback content")
                            return syndication_html
                        fxapi_html = Fetcher._fetch_twitter_fxapi_html(
                            twitter_target_url, proxies=req_proxies
                        )
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

                    dom_content = Fetcher._extract_twitter_dom_content(
                        content, source_url=twitter_target_url
                    )
                    if dom_content:
                        logger.info("Using preserved Twitter/X DOM content")
                        return dom_content

                    structured = Fetcher._extract_twitter_structured_content(
                        content, source_url=twitter_target_url
                    )
                    if structured:
                        logger.info("Using structured Twitter/X content extraction")
                        return Fetcher._tag_twitter_html_content(
                            structured, "article" if is_twitter_article else "tweet"
                        )

                return content
            except Exception as e:
                if is_twitter_url:
                    logger.warning(
                        f"Browser fetch failed for Twitter/X, trying fxTwitter fallback: {e}"
                    )
                    req_proxies, _ = Fetcher._get_twitter_forced_proxies(
                        config, proxy_mode_override, custom_proxy_override
                    )
                    fxapi_html = Fetcher._fetch_twitter_fxapi_html(
                        twitter_target_url, proxies=req_proxies
                    )
                    if fxapi_html:
                        logger.info("Using fxTwitter API fallback content")
                        return fxapi_html
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
    def _render_thread_post_block(item, heading):
        author = escape(item.get("author") or "Unknown")
        handle = escape(item.get("handle") or "")
        timestamp = escape(item.get("timestamp") or "")
        permalink = escape(item.get("permalink") or "")
        text_html = Fetcher._escape_thread_text(item.get("text") or "")

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
        if permalink:
            html_parts.append(f"<p><a href=\"{permalink}\">View post</a></p>")
        html_parts.append("</section>")
        return "".join(html_parts)

    @staticmethod
    def _normalize_thread_mode(fetch_thread, default_mode="backward"):
        if fetch_thread is False:
            return None
        if isinstance(fetch_thread, str):
            mode = fetch_thread.strip().lower()
            if mode in {"forward", "backward", "both"}:
                return mode
        return default_mode

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

            article.insert(0, section)
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
            "<html lang=\"en\"><head>",
            "<meta charset=\"utf-8\">",
            f"<title>{site_label} Post by {author}</title>",
            f"<meta name=\"surf-source-site\" content=\"{escape(site_name)}\">",
            "</head><body><article>",
            f"<h1>{site_label} Post</h1>",
        ]

        if before_items or after_items:
            html_parts.append("<section class='surf-thread-context'><h2>Thread Context</h2>")
            for item in before_items:
                html_parts.append(Fetcher._render_thread_post_block(item, "Earlier Post"))
            for item in after_items:
                html_parts.append(Fetcher._render_thread_post_block(item, "Later Reply"))
            html_parts.append("</section>")

        html_parts.append(Fetcher._render_thread_post_block(current, "Current Post"))
        html_parts.append(f"<p><a href=\"{escape(page_url)}\">View on {site_label}</a></p>")
        html_parts.append("</article></body></html>")
        return "".join(html_parts)

    @staticmethod
    def _extract_same_author_thread_items(items, current_index, mode):
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

        if mode in {"forward", "both"} and current_author_key:
            while start_index > 0:
                previous = items[start_index - 1]
                if previous.get("author_key", "") != current_author_key:
                    break
                start_index -= 1
        if mode in {"backward", "both"} and current_author_key:
            while end_index + 1 < len(items):
                following = items[end_index + 1]
                if following.get("author_key", "") != current_author_key:
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
    ):
        site_name = "threads" if "threads.net" in url.lower() else "weibo"
        thread_mode = Fetcher._normalize_thread_mode(fetch_thread)

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
                        if ((threadMode === 'forward' || threadMode === 'both') && items[currentIndex].authorKey) {
                            while (startIndex > 0 && items[startIndex - 1].authorKey === items[currentIndex].authorKey) {
                                startIndex -= 1;
                            }
                        }
                        if ((threadMode === 'backward' || threadMode === 'both') && items[currentIndex].authorKey) {
                            while (endIndex + 1 < items.length && items[endIndex + 1].authorKey === items[currentIndex].authorKey) {
                                endIndex += 1;
                            }
                        }

                        return { items: items.slice(startIndex, endIndex + 1), currentIndex: currentIndex - startIndex };
                    }""",
                    {"site": site_name, "threadMode": thread_mode or ""},
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
        url, config, proxy_mode_override=None, custom_proxy_override=None, thread_mode="backward"
    ):
        try:
            from playwright.sync_api import sync_playwright

            _, pw_proxy = Fetcher._get_twitter_forced_proxies(
                config, proxy_mode_override, custom_proxy_override
            )

            with sync_playwright() as p:
                launch_args = {
                    "headless": True,
                    "args": ["--disable-blink-features=AutomationControlled"],
                }
                if pw_proxy:
                    launch_args["proxy"] = pw_proxy

                browser = p.chromium.launch(**launch_args)
                context = Fetcher._create_stealth_context(
                    browser, url, auth_site_name="twitter"
                )
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
                        return []

                    current_index = data.get("currentIndex", -1)
                    if current_index is None or current_index < 0:
                        current_index = len(items) - 1

                    return Fetcher._extract_same_author_thread_items(
                        items, current_index, thread_mode
                    )
                finally:
                    context.close()
                    browser.close()
        except Exception as e:
            logger.warning(f"Failed to fetch Twitter/X thread items: {e}")
            return [], -1

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
            response = session.get(
                url, headers=headers, proxies=proxies, timeout=timeout, allow_redirects=True
            )
            final_url = response.url

            logger.info(f"Resolved to: {final_url}")

            # Check if we got a valid xiaohongshu URL
            if "xiaohongshu.com" not in final_url:
                logger.warning(f"Resolved URL does not contain xiaohongshu.com: {final_url}")
                # The URL might still be a redirect page, let's check the response content
                response_text_lower = response.text.lower()
                if "xiaohongshu" in response_text_lower or "redirect" in response_text_lower:
                    logger.warning("Response appears to contain xiaohongshu or redirect, trying to extract URL from content")
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
            cleaned_url = urlunparse(
                parsed._replace(query=cleaned_query)
            )

            logger.info(f"Cleaned URL: {cleaned_url}")
            return final_url, cleaned_url

        except Exception as e:
            logger.warning(f"Failed to resolve xhslink URL {url}: {e}")
            import traceback
            logger.debug(f"Traceback: {traceback.format_exc()}")
            return None, None

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
    def _fetch_xiaohongshu(
        url, config, proxy_mode_override=None, custom_proxy_override=None
    ):
        """
        Fetch Xiaohongshu (小红书) content with authentication support.
        Supports both direct URLs and xhslink.com short URLs.
        Requires prior login using --login xiaohongshu
        """
        from playwright.sync_api import sync_playwright

        req_proxies, pw_proxy = Fetcher._get_proxies(
            config, proxy_mode_override, custom_proxy_override
        )

        # Check if this is a short link and resolve it
        original_short_url = None
        if "xhslink.com" in url:
            original_short_url = url
            _, url = Fetcher._resolve_xhslink_short_url(url, proxies=req_proxies)
            if not url:
                logger.error("Failed to resolve xhslink short URL")
                return None
            logger.info(f"Using resolved URL for fetching: {url}")

        # Check if auth state exists before launching browser
        state = AuthHandler.load_state("xiaohongshu")
        if not state:
            logger.warning(
                "No saved auth state found for xiaohongshu. Starting interactive login..."
            )
            login_success = AuthHandler.interactive_login(
                "xiaohongshu",
                "https://www.xiaohongshu.com",
                config,
                proxy_mode_override,
                custom_proxy_override,
            )
            if not login_success:
                logger.error("Interactive login failed")
                return None
            logger.info("Login successful. Proceeding to fetch content...")

        with sync_playwright() as p:
            browser = (
                p.chromium.launch(headless=True, proxy=pw_proxy)
                if pw_proxy
                else p.chromium.launch(headless=True)
            )

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
                        "Saved auth state expired. Starting interactive login..."
                    )
                    browser.close()
                    # Automatically trigger interactive login
                    login_success = AuthHandler.interactive_login(
                        "xiaohongshu",
                        "https://www.xiaohongshu.com",
                        config,
                        proxy_mode_override,
                        custom_proxy_override,
                    )
                    if login_success:
                        # Retry fetching with new auth state
                        return Fetcher._fetch_xiaohongshu(
                            url, config, proxy_mode_override, custom_proxy_override
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
                    img_url = (
                        img.get("src")
                        or img.get("data-src")
                        or img.get("data-original")
                        or ""
                    ).strip()
                    canonical_img_url = Fetcher._canonicalize_xiaohongshu_image_url(
                        img_url
                    )
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
                    canonical_img_url = Fetcher._canonicalize_xiaohongshu_image_url(
                        img_url
                    )
                    match_key = Fetcher._xiaohongshu_image_match_key(img_url)
                    if (
                        canonical_img_url
                        and canonical_img_url in content_image_map
                        and canonical_img_url not in seen_ordered_images
                    ):
                        ordered_content_images.append(content_image_map[canonical_img_url])
                        seen_ordered_images.add(canonical_img_url)
                    elif (
                        match_key
                        and match_key in content_image_key_map
                    ):
                        matched_content_url = content_image_key_map[match_key]
                        matched_canonical = (
                            Fetcher._canonicalize_xiaohongshu_image_url(
                                matched_content_url
                            )
                        )
                        if matched_canonical and matched_canonical not in seen_ordered_images:
                            ordered_content_images.append(matched_content_url)
                            seen_ordered_images.add(matched_canonical)

                gallery_images = ordered_content_images[:]
                if not gallery_images and content_image_list:
                    # Prefer only images that were actually present in the extracted note body.
                    # This avoids unrelated JSON images and duplicate re-insertion.
                    gallery_images = content_image_list[:]
                    for img_url in gallery_images:
                        canonical_img_url = Fetcher._canonicalize_xiaohongshu_image_url(
                            img_url
                        )
                        if canonical_img_url:
                            seen_ordered_images.add(canonical_img_url)

                if not gallery_images:
                    seen_gallery_images = set()
                    for img_url in images:
                        canonical_img_url = Fetcher._canonicalize_xiaohongshu_image_url(
                            img_url
                        )
                        if (
                            canonical_img_url
                            and canonical_img_url not in seen_gallery_images
                        ):
                            gallery_images.append(img_url)
                            seen_gallery_images.add(canonical_img_url)

                gallery_images = Fetcher._normalize_xiaohongshu_gallery_order(
                    gallery_images
                )

                if gallery_images:
                    # Rebuild note images once and remove original in-content copies
                    # so the note body keeps exactly one gallery.
                    for img in content_soup.find_all("img"):
                        img_url = (
                            img.get("src")
                            or img.get("data-src")
                            or img.get("data-original")
                            or ""
                        ).strip()
                        canonical_img_url = Fetcher._canonicalize_xiaohongshu_image_url(
                            img_url
                        )
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
                    f'<meta name="source-url" content="{url}">',
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
                    "<html lang=\"en\"><head>",
                    "<meta charset=\"utf-8\">",
                    f"<title>{title}</title>",
                    "</head><body>",
                    f"<h1>{title}</h1>",
                ]

                if description:
                    html_parts.append(f"<p><strong>Description:</strong> {description}</p>")

                html_parts.append(f"<p><strong>Repository:</strong> <a href=\"{url}\">{url}</a></p>")
                html_parts.append(html_content)
                html_parts.append("</body></html>")

                return "".join(html_parts)

            except Exception as e:
                logger.warning(f"GitHub handler failed: {e}")
                return None
            finally:
                browser.close()

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
                    "<html lang=\"en\"><head>",
                    "<meta charset=\"utf-8\">",
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
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }

            # Resolve handle to DID
            resolve_url = f"https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle={handle}"
            resolve_resp = requests.get(resolve_url, headers=headers, proxies=req_proxies, timeout=30)

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
            resp = requests.get(api_url, headers=headers, proxies=req_proxies, timeout=30)

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
            original_author_key = (
                author.get("did")
                or author.get("handle")
                or author.get("displayName")
                or "unknown"
            )

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
                        "permalink": url if post_data is post else "",
                    }
                )

            thread_mode = Fetcher._normalize_thread_mode(fetch_thread)
            if thread_mode in {"forward", "both"}:
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
                    if parent_author_key != original_author_key:
                        break
                    ancestors.append(parent_post)
                    parent = parent.get("parent")
                for ancestor in reversed(ancestors):
                    append_post_item(ancestor)

            append_post_item(post)

            current_index = len(thread_items) - 1

            if thread_mode in {"backward", "both"}:
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

            html_content = Fetcher._render_social_thread_html(
                "bluesky", url, thread_items, current_index
            )

            embed = record.get("embed", {})
            extra_parts = []
            if embed.get("$type") == "app.bsky.embed.images":
                images = embed.get("images", [])
                for img in images:
                    img_ref = img.get("image", {}).get("ref", {}).get("$link", "")
                    if img_ref:
                        extra_parts.append(
                            f'<img src="https://cdn.bsky.app/img/feed_thumbnail/plain/{did}/{img_ref}@jpeg" style="max-width: 100%; margin: 0.5em 0;"><br>'
                        )
            elif embed.get("$type") == "app.bsky.embed.external":
                external = embed.get("external", {})
                uri = external.get("uri", "")
                title = external.get("title", "") or uri
                description = external.get("description", "")
                extra_parts.append("<div style='border: 1px solid #ccc; padding: 1em; margin: 1em 0;'>")
                extra_parts.append(f"<p><strong>Link:</strong> <a href=\"{escape(uri)}\">{escape(title)}</a></p>")
                if description:
                    extra_parts.append(f"<p>{escape(description)}</p>")
                extra_parts.append("</div>")

            if extra_parts and html_content:
                soup = BeautifulSoup(html_content, "html.parser")
                article = soup.find("article")
                if article:
                    fragment = BeautifulSoup("".join(extra_parts), "html.parser")
                    for child in fragment.contents:
                        article.append(child)
                html_content = str(soup)

            return html_content

        except Exception as e:
            logger.warning(f"Bluesky handler failed: {e}")
            return None


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
        ],
        "handler": Fetcher._fetch_github_readme,
        "skip_title_translation": True,  # Don't translate GitHub repo names, but content can be translated
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
    def _preprocess_html(html):
        """
        Preprocesses HTML to normalize complex image structures (like <picture> or lazy-loaded images)
        so that extraction engines can find them more easily.
        """
        soup = BeautifulSoup(html, "html.parser")
        for img in soup.find_all("img"):
            # Normalize src
            src = img.get("src")
            should_replace_src = not src or (
                isinstance(src, str) and src.startswith("data:image")
            )
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
            for container in preprocessed_soup.find_all(
                ["article", "main", "section", "div"]
            ):
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
                for container in preprocessed_soup.find_all(
                    ["article", "main", "section"]
                ):
                    node = container.find(
                        string=lambda t: fingerprint in t if t else False
                    )
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
                curr.get("id")
                and curr.get("id").lower() in ["main", "content", "article"]
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
        source_site_tag = preprocessed_soup.find(
            "meta", attrs={"name": "surf-source-site"}
        )
        source_site = (
            source_site_tag.get("content", "").strip().lower()
            if source_site_tag
            else ""
        )
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
            rescued_html = ContentProcessor._rescue_content(
                preprocessed_soup, summary_html
            )

            img_count = rescued_html.count("<img")
            logger.info(
                f"Extracted {img_count} images. Rescued HTML length: {len(rescued_html)}"
            )

            # Debug: log first 200 chars of rescued HTML
            if rescued_html:
                logger.info(f"Rescued HTML preview: {rescued_html[:200]}")

            return title, rescued_html
        except Exception as e:
            logger.warning(
                f"Readability/Rescue failed: {e}. Falling back to Trafilatura."
            )

        # Final Fallback: Trafilatura
        try:
            content_html = trafilatura.extract(
                str(preprocessed_soup), output_format="html", include_images=True
            )
            if content_html:
                img_count = content_html.count("<img")
                logger.info(
                    f"Trafilatura extracted {img_count} images. Content length: {len(content_html)}"
                )
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
    def translate_if_needed(
        cls, text, title=None, target_lang="zh-cn", config=None, llm_provider=None
    ):
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
            logger.warning(
                f"Language detection failed: {e}. Assuming translation needed."
            )
            lang = "unknown"

        if target_lang.lower() in lang.lower() or lang == "zh-cn":
            logger.info(
                "Language matches target or is already Chinese. Skipping translation."
            )
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

            client = OpenAI(
                base_url=llm_config["base_url"], api_key=llm_config["api_key"]
            )

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
                logger.info(
                    f"Translating chunk {i + 1}/{total_chunks} ({len(chunk)} chars)..."
                )
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
        value = getattr(args, "ocr_engine", None) or config.get(
            "OCR", "engine", fallback="rapidocr"
        )
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
                    text = pytesseract_module.image_to_string(
                        image, lang=lang, config=tesseract_config
                    )
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

        tesseract_cmd = config.get("OCR", "tesseract_cmd", fallback="").strip()
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

        ocr_lang = getattr(args, "ocr_lang", None) or config.get(
            "OCR", "lang", fallback="chi_sim+eng"
        )

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
                        "langs": OcrHandler._resolve_ocr_languages(
                            pytesseract, ocr_lang
                        ),
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
                    text = OcrHandler._run_ocr(
                        engine["module"], prepared_image, engine["langs"]
                    )
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
        response = requests.get(url, headers=headers, proxies=proxies, timeout=timeout)
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

        req_proxies, _ = Fetcher._get_proxies(
            config, proxy_mode_override, custom_proxy_override
        )
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

            img_url = (
                img.get("src")
                or img.get("data-src")
                or img.get("data-original")
                or ""
            ).strip()
            if (
                not img_url
                or img_url.startswith("data:")
                or img_url in seen_urls
                or any(token in img_url.lower() for token in ("avatar", "logo", "emoji", "icon"))
            ):
                continue
            seen_urls.add(img_url)

            try:
                image_bytes = OcrHandler._download_image(
                    img_url, source_url, req_proxies
                )
                image = Image.open(io.BytesIO(image_bytes))
                image.load()
                width, height = image.size
                if width < min_width or height < min_height:
                    continue
                prepared_image = OcrHandler._prepare_image_for_ocr(image)
                ocr_text, engine_used = OcrHandler._run_ocr_with_engines(
                    runtime, prepared_image, img_url
                )
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

        suspicious_pairs = (
            "Ã©", "Ã¨", "Ã ", "Â ", "â€™", "â€œ", "â€", "å", "æ", "ç", "ï", "ð"
        )
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
    def _sanitize_filename(filename):
        return "".join(
            [c for c in filename if c.alpha() or c.isdigit() or c in " ._-"]
        ).rstrip()

    @staticmethod
    def _safe_filename_title(title, max_len=None):
        safe_title = "".join(
            c for c in title if c.isalnum() or c in (" ", ".", "_", "-")
        ).strip()
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
    def _extract_first_sentence(text):
        if not text:
            return None
        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized:
            return None
        match = re.search(r"^(.+?[。！？!?\.]+)(?:\s|$)", normalized)
        if match:
            return match.group(1).strip()
        return normalized

    @staticmethod
    def _extract_twitter_first_sentence_title(html_content, source_url=None):
        if not OutputHandler._is_twitter_non_article(source_url, html_content):
            return None
        if not html_content:
            return None
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            html_title = OutputHandler._extract_html_title(html_content) or ""
            skip_lines = set()
            if html_title:
                skip_lines.add(html_title.strip())
                if html_title.lower().endswith(" - x post"):
                    skip_lines.add(html_title[:-9].strip())

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
                text_lines.append(line)

            for line in text_lines:
                sentence = OutputHandler._extract_first_sentence(line)
                if sentence:
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
        twitter_title = OutputHandler._extract_twitter_first_sentence_title(
            html_content, source_url=source_url
        )
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
            if url and not url.startswith(("http://", "https://", "data:")):
                absolute_url = urljoin(base_url, url)
                return f"![{alt_text}]({absolute_url})"
            return match.group(0)

        md_content = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replace_image_url, md_content)

        # 处理链接: [text](url)
        def replace_link_url(match):
            text = match.group(1)
            url = match.group(2)
            if url and not url.startswith(
                ("http://", "https://", "data:", "#", "mailto:", "tel:", "javascript:")
            ):
                absolute_url = urljoin(base_url, url)
                return f"[{text}]({absolute_url})"
            return match.group(0)

        md_content = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace_link_url, md_content)

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
                            tag[attr] = urljoin(base_url, url)

            return str(soup)

        # 匹配自闭合标签如 <img src="...">、<video src="..."> 等
        md_content = re.sub(
            r"<([a-zA-Z][a-zA-Z0-9]*)\s+[^>]*>", convert_html_attrs_in_md, md_content
        )

        return md_content

    @staticmethod
    def _extract_metadata(html_content, source_url=None, translator=None):
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
            "created": None,
            "updated": None,
            "tags": [],
            "source": source_url,
            "translator": translator,
        }

        # 提取title元素
        title_tag = soup.find("title")
        if title_tag:
            metadata["title"] = OutputHandler.normalize_markdown_encoding(
                title_tag.get_text(strip=True)
            )

        twitter_title = OutputHandler._extract_twitter_first_sentence_title(
            html_content, source_url=source_url
        )
        if twitter_title:
            metadata["title"] = OutputHandler.normalize_markdown_encoding(twitter_title)

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
                        metadata["created"] = parsed_date.strftime("%Y-%m-%d")
                        break
                    except (ValueError, TypeError):
                        pass

        # 提取keywords作为tags
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

        # 设置updated为当前时间
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
            # 转义特殊字符
            title = metadata["title"].replace('"', '\\"')
            lines.append(f'title: "{title}"')

        if metadata.get("created"):
            lines.append(f"created: {metadata['created']}")

        if metadata.get("updated"):
            lines.append(f"updated: {metadata['updated']}")

        if metadata.get("tags") and len(metadata["tags"]) > 0:
            lines.append("tags:")
            for tag in metadata["tags"]:
                lines.append(f'  - "{tag}"')

        if metadata.get("source"):
            lines.append(f"source: {metadata['source']}")

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
        """
        md_dir = config.get("Output", "md_dir", fallback="./notes")
        if not os.path.exists(md_dir):
            os.makedirs(md_dir)

        filename_title = OutputHandler._get_filename_title(
            title, source_url=source_url, html_content=html_content
        )

        # Determine filepath
        if output_path:
            # Expand user home directory (~) if present
            filepath = os.path.expanduser(output_path)
            # Handle special cases: "." or "./" (current directory)
            if filepath == "." or filepath == "./":
                # Use current directory + default filename
                safe_title = OutputHandler._safe_filename_title(filename_title)
                filename = f"{safe_title}.md"
                filepath = os.path.join(".", filename)
            # Ensure directory exists
            filepath_dir = os.path.dirname(filepath)
            if filepath_dir and not os.path.exists(filepath_dir):
                os.makedirs(filepath_dir)
        else:
            # Simple sanitization
            safe_title = OutputHandler._safe_filename_title(filename_title)
            filename = f"{safe_title}.md"
            filepath = os.path.join(md_dir, filename)

        # Convert relative URLs to absolute if base_url is provided
        if base_url:
            content = OutputHandler._convert_markdown_urls_to_absolute(
                content, base_url
            )

        # Generate YAML front matter if html_content is provided and add_front_matter is True
        yaml_frontmatter = ""
        if html_content and add_front_matter:
            metadata = OutputHandler._extract_metadata(
                html_content, source_url=source_url, translator=translator
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
            filepath = os.path.expanduser(output_path)
            # Ensure directory exists
            filepath_dir = os.path.dirname(filepath)
            if filepath_dir and not os.path.exists(filepath_dir):
                os.makedirs(filepath_dir)
        else:
            safe_title = "".join(
                c for c in title if c.isalnum() or c in (" ", ".", "_", "-")
            ).strip()
            pdf_dir = config.get("Output", "pdf_dir", fallback=".")
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
    def save_html(
        title, html_content, config, inline=False, output_path=None, base_url=None
    ):
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
        html_dir = config.get("Output", "html_dir", fallback=".")
        if not os.path.exists(html_dir):
            os.makedirs(html_dir)

        # Sanitize filename
        safe_title = "".join(
            c for c in title if c.isalnum() or c in (" ", ".", "_", "-")
        ).strip()
        safe_title = safe_title[:100]

        if inline:
            html_content = OutputHandler._inline_resources(html_content)
        elif base_url:
            # Convert relative URLs to absolute for non-inline HTML
            html_content = OutputHandler._convert_urls_to_absolute(
                html_content, base_url
            )

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
            sys.stdout.write(html_content)
            sys.stdout.flush()
            logger.info(f"HTML output to stdout (inline={inline})")
            return None
        elif output_path:
            # Expand user home directory (~) if present
            filepath = os.path.expanduser(output_path)
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

                    response = requests.get(href, timeout=10)
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

                    response = requests.get(src, timeout=10)
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
    def publish_to_pastebin(
        title, content, config, proxy_mode_override=None, custom_proxy_override=None
    ):
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
            logger.error(
                "Pastebin API key not found in config [publish.pastebin] section."
            )
            return None

        logger.info(f"Publishing to pastebin.com as '{title}'...")

        # Get proxies for the request
        req_proxies, _ = Fetcher._get_proxies(
            config, proxy_mode_override, custom_proxy_override
        )

        # Prepare POST data (equivalent to curl command)
        data = {
            "api_dev_key": api_dev_key,
            "api_option": "paste",
            "api_paste_code": content,
            "api_paste_name": title,
        }

        # Optional: get additional parameters from config
        paste_expire = config.get("publish.pastebin", "api_paste_expire", fallback=None)
        paste_private = config.get(
            "publish.pastebin", "api_paste_private", fallback="0"
        )

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
            response = requests.post(
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
        logger.info(
            f"Both {old_dir} and {new_dir} exist. Please manually merge if needed."
        )


# Perform migration on import/startup
migrate_data()


class AuthHandler:
    """
    Handler for browser authentication state management.
    Supports persistent login state for sites requiring authentication.
    """

    # Directory to store authentication states
    AUTH_STATE_DIR = os.path.join(get_data_dir(), "auth")

    @staticmethod
    def get_twitter_profile_dir():
        """Persistent browser profile directory for Twitter/X login session."""
        if not os.path.exists(AuthHandler.AUTH_STATE_DIR):
            os.makedirs(AuthHandler.AUTH_STATE_DIR)
        return os.path.join(AuthHandler.AUTH_STATE_DIR, "twitter_profile")

    @staticmethod
    def _get_state_file(site_name):
        """Get the path to the state file for a specific site."""
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
        state_file = AuthHandler._get_state_file(site_name)
        if os.path.exists(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    import json

                    state = json.load(f)
                    if log_load:
                        logger.info(f"Loaded auth state for {site_name}")
                    return state
            except Exception as e:
                logger.warning(f"Failed to load auth state for {site_name}: {e}")
        return None

    @staticmethod
    def cookie_header_for_zhihu():
        """
        Build a Cookie header value from saved Playwright storage for zhihu.com domains.
        Zhihu's v4 API often returns 403 without logged-in cookies.
        """
        state = AuthHandler.load_state("zhihu", log_load=False)
        if not state:
            return None
        cookies = state.get("cookies") or []
        by_name = {}
        for c in cookies:
            domain = (c.get("domain") or "").lower()
            if "zhihu.com" not in domain:
                continue
            name = c.get("name")
            if not name:
                continue
            by_name[name] = c.get("value", "")
        if not by_name:
            return None
        return "; ".join(f"{k}={v}" for k, v in by_name.items())

    @staticmethod
    def save_state(site_name, state):
        """
        Save browser state for a site.

        Args:
            site_name: Identifier for the site
            state: Browser state dictionary from browser_context.storage_state()
        """
        state_file = AuthHandler._get_state_file(site_name)
        try:
            with open(state_file, "w", encoding="utf-8") as f:
                import json

                json.dump(state, f, ensure_ascii=False, indent=2)
            logger.info(f"Saved auth state for {site_name} to {state_file}")
        except Exception as e:
            logger.error(f"Failed to save auth state for {site_name}: {e}")

    @staticmethod
    def clear_state(site_name=None):
        """
        Clear saved authentication state.

        Args:
            site_name: Site identifier, or None to clear all states
        """
        if site_name:
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

        normalized_site_name = "twitter" if site_name.lower() in {"twitter", "x"} else site_name.lower()
        if normalized_site_name == "twitter":
            _, pw_proxy = Fetcher._get_twitter_forced_proxies(
                config, proxy_mode_override, custom_proxy_override
            )
        else:
            _, pw_proxy = Fetcher._get_proxies(
                config, proxy_mode_override, custom_proxy_override
            )

        print(f"\n{'=' * 60}")
        print(f"Interactive Login for {normalized_site_name}")
        print(f"{'=' * 60}")
        print("A browser window will open. Please:")
        print(f"1. Log in to {normalized_site_name} manually")
        print("2. Complete any CAPTCHA or verification if needed")
        print("3. Once logged in, press Enter in this terminal to save the session")
        print(f"{'=' * 60}\n")

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
                    context = p.chromium.launch_persistent_context(
                        profile_dir, channel="chrome", **persistent_args
                    )
                except Exception as e:
                    logger.info(f"Chrome channel unavailable for login, fallback to Chromium: {e}")
                    context = p.chromium.launch_persistent_context(
                        profile_dir, **persistent_args
                    )
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

        logger.info(
            f"Generating TTS audio with voice: {voice}, rate: {rate}, volume: {volume}..."
        )
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
        clean_text = (
            clean_text.replace("#", "")
            .replace("*", "")
            .replace("`", "")
            .replace("---", "")
        )

        # If output file not specified but speak is needed, use temp
        temp_file = "tts_temp.mp3"
        filename = save_path if save_path else temp_file

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
  surf -n https://example.com                   # No proxy
  surf -hrn https://example.com                 # Combined short flags
  surf -t https://x.com/user/status/123         # Fetch same-author later replies in the thread
  surf --thread forward https://x.com/user/status/123

Special Sites:
  WeChat & Xiaohongshu: Default to no proxy and no translation.
                        Override with -x/--proxy and -l/--lang if needed.
  Twitter/X:           Always uses proxy settings.
                        Defaults to the same proxy behavior as `-x win`.
                        Default backend prefers `uvx --from twitter-cli twitter`.
                        `auto` backend tries CLI first, then native fallback.
  Twitter/X, Bluesky, Weibo, Threads: thread expansion defaults to `backward`.

Authentication:
  surf --login xiaohongshu                   # Login to Xiaohongshu
  surf --login twitter                       # Login to Twitter/X
  surf --clear-auth xiaohongshu              # Clear auth for Xiaohongshu
  surf --clear-auth twitter                  # Clear auth for Twitter/X

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
        help="URL to process (not required for --login or --clear-auth)",
    )

    # Output format
    format_group = parser.add_mutually_exclusive_group()
    format_group.add_argument(
        "-f",
        "--format",
        choices=["md", "pdf", "html", "audio", "publish"],
        help="Output format: md/pdf/html/audio/publish (default: md)",
    )
    format_group.add_argument(
        "-h", action="store_true", help="Shorthand for --format html"
    )
    format_group.add_argument(
        "-p", action="store_true", help="Shorthand for --format pdf"
    )
    format_group.add_argument(
        "-a", action="store_true", help="Shorthand for --format audio"
    )
    format_group.add_argument(
        "-P", action="store_true", help="Shorthand for --format publish"
    )

    # Output path
    parser.add_argument(
        "-o", "--output", help="Output file path (use '-' for stdout, overrides config)"
    )
    parser.add_argument(
        "-O", action="store_true", help="Shorthand for --output - (output to stdout)"
    )

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
        help="Interactive login for a site (e.g., xiaohongshu, twitter/x). Opens browser for manual login.",
    )
    parser.add_argument(
        "--clear-auth",
        metavar="SITE",
        help="Clear saved authentication for a site (use 'all' to clear all)",
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
    parser.add_argument(
        "--html-inline", action="store_true", help="Inline CSS/JS in HTML output"
    )

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
    parser.add_argument(
        "-x",
        "--proxy",
        choices=["win", "custom", "no"],
        help="Proxy mode: win=Windows, custom=use --set-proxy, no=no proxy (default: auto)",
    )
    parser.add_argument("-c", action="store_true", help="Shorthand for --proxy custom")
    parser.add_argument("-n", action="store_true", help="Shorthand for --proxy no")
    parser.add_argument("--set-proxy", help="Custom proxy URL (requires -x custom)")

    # LLM
    parser.add_argument("--llm", help="Override the default LLM provider")

    # Other options
    parser.add_argument(
        "--browser", action="store_true", help="Force use of browser (Playwright)"
    )
    thread_group = parser.add_mutually_exclusive_group()
    thread_group.add_argument(
        "-t",
        "--thread",
        nargs="?",
        const="backward",
        choices=["forward", "backward", "both"],
        help="Thread expansion mode: backward=follow same-author later replies (default), forward=earlier context, both=both directions",
    )
    thread_group.add_argument(
        "--no-thread",
        action="store_true",
        help="Disable thread expansion for supported social sites",
    )
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument(
        "--no-front-matter",
        action="store_true",
        help="Disable YAML front matter in markdown output",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument("--help", action="help", help="Show this help message")

    args = parser.parse_args()

    # Enable verbose logging if requested
    if args.verbose:
        setup_verbose_logging()

    # Determine config path
    config_path = args.config if args.config else "config.ini"
    config = Config(config_path)

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

    # Handle --login
    if args.login:
        site_name = args.login.lower()
        # Define login URLs for supported sites
        login_urls = {
            "xiaohongshu": "https://www.xiaohongshu.com",
            "twitter": "https://x.com/i/flow/login",
            "x": "https://x.com/i/flow/login",
            "zhihu": "https://www.zhihu.com/",
        }
        if site_name not in login_urls:
            parser.error(
                f"Unsupported site: {site_name}. Supported: {', '.join(login_urls.keys())}"
            )
        login_url = login_urls[site_name]
        login_site = "twitter" if site_name == "x" else site_name
        success = AuthHandler.interactive_login(
            login_site, login_url, config, args.proxy, args.set_proxy
        )
        sys.exit(0 if success else 1)

    # Validate proxy arguments
    if args.set_proxy and args.proxy != "custom":
        parser.error("--set-proxy requires -x custom")
    if args.proxy == "custom" and not args.set_proxy:
        parser.error("-x custom requires --set-proxy")

    # Check if url is required but not provided
    if not args.url:
        parser.error("URL is required (unless using --login or --clear-auth)")

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
    proxy_mode = None  # use config default (auto)
    if args.proxy:
        proxy_mode = args.proxy
    elif args.c:
        proxy_mode = "custom"
    elif args.n:
        proxy_mode = "no"

    # Determine custom proxy
    custom_proxy = args.set_proxy

    fetch_thread = None
    if args.thread:
        fetch_thread = args.thread
    elif args.no_thread:
        fetch_thread = False

    # Check if URL matches special site handlers for default policies
    handler, site_name, site_config = _get_handler_for_url(args.url)
    if site_config:
        # Apply special site defaults (can be overridden by command line)
        if site_config.get("default_no_proxy") and proxy_mode is None:
            logger.info(
                f"{site_name}: Applying default 'no proxy' policy (can be overridden with -x)"
            )
            proxy_mode = "no"

        if site_config.get("default_no_translate") and lang_mode == "trans":
            logger.info(
                f"{site_name}: Applying default 'no translate' policy (can be overridden with -l)"
            )
            lang_mode = "raw"

        if site_config.get("default_thread") and fetch_thread is None:
            logger.info(f"{site_name}: Applying default thread expansion policy")
            fetch_thread = "backward"

    # 1. Fetch
    try:
        html_content = Fetcher.fetch(
            args.url,
            config=config,
            use_browser=args.browser,
            proxy_mode_override=proxy_mode,
            custom_proxy_override=custom_proxy,
            fetch_thread=fetch_thread,
            twitter_backend=args.twitter_backend,
            twitter_cli_bin=args.twitter_cli_bin,
            twitter_browser=args.twitter_browser,
            twitter_profile=args.twitter_profile,
        )
    except Exception as e:
        logger.error(f"Failed to fetch {args.url}: {e}")
        sys.exit(1)

    if not html_content:
        if Fetcher._is_twitter_url(args.url):
            logger.error(
                "Failed to fetch usable Twitter/X content (likely login wall or proxy/session issue)."
            )
        else:
            logger.error(f"Failed to fetch usable content from {args.url}.")
        sys.exit(1)

    def _extract_source_url_from_html(html_blob, default_url):
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

    source_url = _extract_source_url_from_html(html_content, args.url)

    # 2. Extract
    try:
        title, cleaned_html = ContentProcessor.extract_content(html_content)
        if not title:
            title = "Untitled"
        logger.info(f"Title: {title}")
    except Exception as e:
        logger.error(f"Failed to extract content: {e}")
        sys.exit(1)

    # 2.5 OCR images
    try:
        cleaned_html = OcrHandler.annotate_html_with_ocr(
            cleaned_html,
            source_url=source_url,
            site_name=site_name,
            site_config=site_config,
            args=args,
            config=config,
            proxy_mode_override=proxy_mode,
            custom_proxy_override=custom_proxy,
        )
    except Exception as e:
        logger.warning(f"Image OCR failed and was skipped: {e}")

    # 3. Convert
    try:
        md_content = ContentProcessor.to_markdown(cleaned_html)
    except Exception as e:
        logger.error(f"Failed to convert to markdown: {e}")
        sys.exit(1)

    twitter_title = OutputHandler._extract_twitter_first_sentence_title(
        html_content, source_url=args.url
    )
    if twitter_title:
        title = twitter_title

    # 4. Translate
    target_lang = config.get("Output", "target_language", fallback="zh-cn")
    translated_title = None  # Initialize for raw mode

    # Check if this is a site that should skip title translation
    _, site_name, site_config = _get_handler_for_url(args.url) if args.url else (None, None, None)
    skip_title_translation = site_config.get("skip_title_translation", False) if site_config else False

    if lang_mode == "raw":
        logger.info("Language mode set to 'raw'. Skipping translation.")
    else:
        original_md = md_content
        original_title = title

        # Get LLM provider from command line if specified
        llm_provider = args.llm if hasattr(args, "llm") else None

        # For sites like GitHub, don't translate the title (keep repo name as-is)
        title_to_translate = None if skip_title_translation else title

        translated_md, translated_title = ContentProcessor.translate_if_needed(
            md_content,
            title=title_to_translate,
            target_lang=target_lang,
            config=config,
            llm_provider=llm_provider,
        )

        # Use original title if title translation was skipped
        if skip_title_translation:
            translated_title = original_title

        if lang_mode == "both":
            logger.info(
                "Language mode set to 'both'. Combining original and translation."
            )
            # Only combine if translation actually happened
            if translated_md != original_md:
                title = f"{translated_title} ({original_title})"
                md_content = f"{translated_md}\n\n---\n\n### Original Content / 原文内容\n\n{original_md}"
            else:
                title = translated_title
                md_content = translated_md
        else:
            # translated (default)
            md_content = translated_md
            title = translated_title

    # 4.5 Convert relative URLs to absolute (after translation, before output)
    if args.url:
        md_content = OutputHandler._convert_markdown_urls_to_absolute(
            md_content, args.url
        )
        # Also convert HTML URLs for HTML output
        cleaned_html = OutputHandler._convert_urls_to_absolute(cleaned_html, args.url)

    # 5. Output
    # Use the source URL from meta tag if available (for xhslink resolution)
    if source_url != args.url:
        logger.info(f"Using resolved source URL for metadata: {source_url}")

    # Determine output path
    output_path = args.output if args.output else None
    if args.O:
        output_path = "-"

    # Handle output based on format
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
            OutputHandler.save_html(
                title, cleaned_html, config, inline=args.html_inline
            )

    elif output_format == "audio":
        if output_path:
            TTSHandler.run_tts(
                title, md_content, config, speak=args.speak, save_path=output_path
            )
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

        # Determine if translation was performed
        translation_performed = lang_mode != "raw"

        # Get translator model name if translation was performed
        translator = None
        if translation_performed:
            try:
                llm_provider = args.llm if hasattr(args, "llm") else None
                llm_config = config.get_llm_config(llm_provider)
                translator = llm_config["model"]
            except Exception as e:
                logger.warning(f"Could not get LLM config for translator: {e}")

        if output_path:
            if output_path == "-":
                # Output to stdout
                print(f"# {title}\n")
                print(md_content)
            else:
                OutputHandler.save_markdown(
                    title,
                    md_content,
                    config,
                    output_path,
                    html_content=html_content,
                    add_front_matter=not args.no_front_matter,
                    translated_title=translated_title
                    if translation_performed
                    else None,
                    source_url=source_url,
                    translator=translator,
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
            )


if __name__ == "__main__":
    main()
