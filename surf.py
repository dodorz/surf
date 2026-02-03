#!/usr/bin/env -S uv run
import argparse
import configparser
import os
import sys
import logging
import requests
from readability import Document
import markdownify
from langdetect import detect
import warnings
import asyncio
import edge_tts
from playsound import playsound
from datetime import datetime
from dateutil import parser as date_parser
from bs4 import BeautifulSoup
import trafilatura
import re

__version__ = "1.1.0.20"

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
                    override = (
                        winreg.QueryValueEx(key, "ProxyOverride")[0] if False else None
                    )
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

        if mode == "set":
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
        if handler:
            logger.info(f"Using special handler for {site_name}")
            html_content = handler(
                url, config, proxy_mode_override, custom_proxy_override
            )
            if html_content:
                return html_content

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

                # Check if likely dynamic (heuristic: very short content or explicit noscript)
                if len(response.text) < 1000 or "<noscript>" in response.text:
                    logger.info(
                        "Content seems short or requires JS. Switching to browser..."
                    )
                    return Fetcher.fetch_with_browser(
                        url, config, proxy_mode_override, custom_proxy_override
                    )

                return response.text
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
    def _get_twitter_forced_proxies(
        config, proxy_mode_override=None, custom_proxy_override=None
    ):
        """
        Get proxies for Twitter/X with forced proxy usage.
        Priority: command-line args > env vars > WinINET > config custom_proxy
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
            elif mode == "set" and custom_proxy_override:
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
                # Use WinINET
                pass  # Fall through to WinINET logic below
            else:
                # auto or other - fall through to auto logic
                pass

        # Priority 1: Environment variables
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
            logger.info(f"Twitter: Using environment proxy: {server}")
            return req_proxies, pw_proxy

        # Priority 2: WinINET (Windows registry)
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
        import json

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
            author_url = data.get("author_url", "")
            provider_name = data.get("provider_name", "")

            logger.info(
                f"oEmbed response: author={author_name}, provider={provider_name}"
            )

            # Check if this is a Twitter Article (oEmbed only returns link)
            if Fetcher._is_twitter_article_only_link(html_content):
                logger.info(
                    "oEmbed returned link-only content (Twitter Article), fetching with browser..."
                )
                # Fetch directly with browser and clean content
                article_html = Fetcher.fetch_with_browser(
                    url,
                    config,
                    proxy_mode_override,
                    custom_proxy_override,
                    is_twitter_article=True,
                )
                return article_html

            return html_content
        except requests.exceptions.RequestException as e:
            logger.warning(f"oEmbed API failed ({e}), falling back to browser fetch")
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
    def _create_stealth_context(browser, url=None):
        """
        Create a browser context with anti-detection measures.
        Uses stealth settings to avoid being detected as automation.
        """
        # Base stealth args for all sites
        args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-web-security",
            "--disable-features=BlockInsecurePrivateNetworkRequests",
        ]

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
        else:
            viewport = {"width": 1920, "height": 1080}
            user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            locale = "en-US"
            timezone = "America/New_York"

        context = browser.new_context(
            viewport=viewport,
            user_agent=user_agent,
            locale=locale,
            timezone_id=timezone,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            },
        )

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

        # For Twitter/X URLs, always use forced proxy regardless of INI config
        if Fetcher._is_twitter_url(url):
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

            # Create context with anti-detection measures
            context = Fetcher._create_stealth_context(browser, url)
            page = context.new_page()

            try:
                # For Twitter/X, use domcontentloaded + timeout instead of networkidle
                # because X has persistent connections that never reach networkidle
                if Fetcher._is_twitter_url(url):
                    logger.info("Using domcontentloaded strategy for Twitter/X")
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    # Wait longer for content to hydrate
                    page.wait_for_timeout(5000)
                else:
                    page.goto(url, wait_until="networkidle", timeout=60000)
                    page.wait_for_timeout(2000)

                content = page.content()

                # Clean Twitter Article content if needed
                if Fetcher._is_twitter_url(url) and is_twitter_article:
                    logger.info("Cleaning Twitter Article content...")
                    content = Fetcher._clean_twitter_article_content(content)

                return content
            except Exception as e:
                logger.error(f"Browser fetch failed: {e}")
                raise
            finally:
                context.close()
                browser.close()

    @staticmethod
    def _fetch_xiaohongshu(
        url, config, proxy_mode_override=None, custom_proxy_override=None
    ):
        """
        Fetch Xiaohongshu (小红书) content with authentication support.
        Requires prior login using --login xiaohongshu
        """
        from playwright.sync_api import sync_playwright

        req_proxies, pw_proxy = Fetcher._get_proxies(
            config, proxy_mode_override, custom_proxy_override
        )

        # Check if auth state exists before launching browser
        state = AuthHandler.load_state("xiaohongshu")
        if not state:
            logger.warning("No saved auth state found for xiaohongshu. Starting interactive login...")
            login_success = AuthHandler.interactive_login(
                "xiaohongshu", "https://www.xiaohongshu.com",
                config, proxy_mode_override, custom_proxy_override
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
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)

                # Check if we're still on login page (auth failed or not logged in)
                current_url = page.url
                if "/login" in current_url or "/signin" in current_url:
                    logger.warning("Saved auth state expired. Starting interactive login...")
                    browser.close()
                    # Automatically trigger interactive login
                    login_success = AuthHandler.interactive_login(
                        "xiaohongshu", "https://www.xiaohongshu.com", 
                        config, proxy_mode_override, custom_proxy_override
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

                # Extract images
                images = page.evaluate("""() => {
                    const imgs = Array.from(document.querySelectorAll('img'));
                    return imgs
                        .map(img => img.src)
                        .filter(src => src && src.includes('xhscdn.com'));
                }
                """)

                # Build HTML with full page structure (for cleaning)
                html_parts = [
                    "<html><head><meta charset='utf-8'>",
                    f"<title>{title}</title>",
                    "</head><body><article>",
                    f"<h1>{title}</h1>",
                    content,
                ]

                # Add images if found (excluding avatars which are already in content)
                if images:
                    html_parts.append("<div class='images'>")
                    for img_url in images:
                        # Skip avatar images (they're already styled separately)
                        if 'sns-avatar-qc.xhscdn.com' not in img_url:
                            html_parts.append(f'<img src="{img_url}" />')
                    html_parts.append("</div>")

                html_parts.append("</article></body></html>")
                html_content = ''.join(html_parts)

                # Clean the content to remove unrelated UI elements
                html_content = Fetcher._clean_xiaohongshu_content(html_content)

                return html_content

            except Exception as e:
                logger.warning(f"Xiaohongshu handler failed: {e}")
                return None
            finally:
                browser.close()


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
        "handler": Fetcher._fetch_twitter_oembed,
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
    "xiaohongshu": {
        "patterns": [
            r"^https?://(www\.)?xiaohongshu\.com/explore/",
            r"^https?://(www\.)?xiaohongshu\.com/user/profile/",
        ],
        "handler": Fetcher._fetch_xiaohongshu,
        "default_no_proxy": True,  # Default: don't use proxy (can be overridden by command line)
        "default_no_translate": True,  # Default: don't translate (can be overridden by command line)
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
                            "content": f"You are a helpful translator. Translate the following Markdown content to {target_lang}. Preserve the Markdown formatting strictly. Output ONLY the translated markdown.",
                        },
                        {"role": "user", "content": chunk},
                    ],
                )
                translated_chunks.append(completion.choices[0].message.content)

            return "\n\n".join(translated_chunks), translated_title

        except Exception as e:
            logger.error(f"Translation failed: {e}")
            return text, title


class OutputHandler:
    @staticmethod
    def _sanitize_filename(filename):
        return "".join(
            [c for c in filename if c.alpha() or c.isdigit() or c in " ._-"]
        ).rstrip()

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
        from urllib.parse import urljoin, urlparse

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
            metadata["title"] = title_tag.get_text(strip=True)

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
                tags = [tag.strip() for tag in keywords_value.split(",") if tag.strip()]
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

        # Determine filepath
        if output_path:
            filepath = output_path
            # Handle special cases: "." or "./" (current directory)
            if filepath == "." or filepath == "./":
                # Use current directory + default filename
                safe_title = "".join(
                    c for c in title if c.isalnum() or c in (" ", ".", "_", "-")
                ).strip()
                filename = f"{safe_title}.md"
                filepath = os.path.join(".", filename)
            # Ensure directory exists
            filepath_dir = os.path.dirname(filepath)
            if filepath_dir and not os.path.exists(filepath_dir):
                os.makedirs(filepath_dir)
        else:
            # Simple sanitization
            safe_title = "".join(
                c for c in title if c.isalnum() or c in (" ", ".", "_", "-")
            ).strip()
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

        import markdown

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
            filepath = output_path
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
            filepath = output_path
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
        logger.debug(f"Pastebin POST URL: https://pastebin.com/api/api_post.php")
        logger.debug(f"Pastebin POST data:")
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


class AuthHandler:
    """
    Handler for browser authentication state management.
    Supports persistent login state for sites requiring authentication.
    """

    # Directory to store authentication states
    AUTH_STATE_DIR = os.path.join(os.path.expanduser("~"), ".surf", "auth")

    @staticmethod
    def _get_state_file(site_name):
        """Get the path to the state file for a specific site."""
        if not os.path.exists(AuthHandler.AUTH_STATE_DIR):
            os.makedirs(AuthHandler.AUTH_STATE_DIR)
        return os.path.join(AuthHandler.AUTH_STATE_DIR, f"{site_name}_state.json")

    @staticmethod
    def load_state(site_name):
        """
        Load saved browser state for a site.

        Args:
            site_name: Identifier for the site (e.g., 'xiaohongshu', 'twitter')

        Returns:
            dict: Browser state dictionary, or None if not found
        """
        state_file = AuthHandler._get_state_file(site_name)
        if os.path.exists(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    import json
                    state = json.load(f)
                    logger.info(f"Loaded auth state for {site_name}")
                    return state
            except Exception as e:
                logger.warning(f"Failed to load auth state for {site_name}: {e}")
        return None

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
        else:
            if os.path.exists(AuthHandler.AUTH_STATE_DIR):
                import shutil
                shutil.rmtree(AuthHandler.AUTH_STATE_DIR)
                logger.info("Cleared all auth states")

    @staticmethod
    def interactive_login(site_name, login_url, config, proxy_mode_override=None, custom_proxy_override=None):
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

        req_proxies, pw_proxy = Fetcher._get_proxies(
            config, proxy_mode_override, custom_proxy_override
        )

        print(f"\n{'='*60}")
        print(f"Interactive Login for {site_name}")
        print(f"{'='*60}")
        print(f"A browser window will open. Please:")
        print(f"1. Log in to {site_name} manually")
        print(f"2. Complete any CAPTCHA or verification if needed")
        print(f"3. Once logged in, press Enter in this terminal to save the session")
        print(f"{'='*60}\n")

        with sync_playwright() as p:
            browser = (
                p.chromium.launch(headless=False, proxy=pw_proxy)
                if pw_proxy
                else p.chromium.launch(headless=False)
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            try:
                page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
                print(f"Browser opened. Please log in to {site_name}...")
                input("Press Enter after you have logged in...")

                # Save the state
                state = context.storage_state()
                AuthHandler.save_state(site_name, state)
                print(f"Login state saved for {site_name}")
                return True

            except Exception as e:
                logger.error(f"Interactive login failed: {e}")
                return False
            finally:
                browser.close()

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

Special Sites:
  WeChat & Xiaohongshu: Default to no proxy and no translation.
                        Override with -x/--proxy and -l/--lang if needed.
  Twitter/X:           Always uses proxy settings.

Authentication:
  surf --login xiaohongshu                   # Login to Xiaohongshu
  surf --clear-auth xiaohongshu              # Clear auth for Xiaohongshu
        """,
    )
    # Position argument
    parser.add_argument("url", nargs="?", help="URL to process (not required for --login or --clear-auth)")

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
        help="Interactive login for a site (e.g., xiaohongshu). Opens browser for manual login.",
    )
    parser.add_argument(
        "--clear-auth",
        metavar="SITE",
        help="Clear saved authentication for a site (use 'all' to clear all)",
    )

    # HTML options
    parser.add_argument(
        "--html-inline", action="store_true", help="Inline CSS/JS in HTML output"
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
        if args.clear_auth.lower() == "all":
            AuthHandler.clear_state()
        else:
            AuthHandler.clear_state(args.clear_auth)
        return

    # Handle --login
    if args.login:
        site_name = args.login.lower()
        # Define login URLs for supported sites
        login_urls = {
            "xiaohongshu": "https://www.xiaohongshu.com",
        }
        if site_name not in login_urls:
            parser.error(f"Unsupported site: {site_name}. Supported: {', '.join(login_urls.keys())}")
        login_url = login_urls[site_name]
        success = AuthHandler.interactive_login(
            site_name, login_url, config, args.proxy, args.set_proxy
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

    # Check if URL matches special site handlers for default policies
    handler, site_name, site_config = _get_handler_for_url(args.url)
    if site_config:
        # Apply special site defaults (can be overridden by command line)
        # Priority: Command line > Special site default > Config file
        if site_config.get("default_no_proxy") and proxy_mode is None:
            logger.info(f"{site_name}: Applying default 'no proxy' policy (can be overridden with -x)")
            proxy_mode = "no"

        if site_config.get("default_no_translate") and lang_mode == "trans":
            logger.info(f"{site_name}: Applying default 'no translate' policy (can be overridden with -l)")
            lang_mode = "raw"

    # 1. Fetch
    try:
        html_content = Fetcher.fetch(
            args.url,
            config=config,
            use_browser=args.browser,
            proxy_mode_override=proxy_mode,
            custom_proxy_override=custom_proxy,
        )
    except Exception as e:
        logger.error(f"Failed to fetch {args.url}: {e}")
        sys.exit(1)

    # 2. Extract
    try:
        title, cleaned_html = ContentProcessor.extract_content(html_content)
        if not title:
            title = "Untitled"
        logger.info(f"Title: {title}")
    except Exception as e:
        logger.error(f"Failed to extract content: {e}")
        sys.exit(1)

    # 3. Convert
    try:
        md_content = ContentProcessor.to_markdown(cleaned_html)
    except Exception as e:
        logger.error(f"Failed to convert to markdown: {e}")
        sys.exit(1)

    # 4. Translate
    target_lang = config.get("Output", "target_language", fallback="zh-cn")
    translated_title = None  # Initialize for raw mode

    if lang_mode == "raw":
        logger.info("Language mode set to 'raw'. Skipping translation.")
    else:
        original_md = md_content
        original_title = title

        # Get LLM provider from command line if specified
        llm_provider = args.llm if hasattr(args, "llm") else None

        translated_md, translated_title = ContentProcessor.translate_if_needed(
            md_content,
            title=title,
            target_lang=target_lang,
            config=config,
            llm_provider=llm_provider,
        )

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
                    source_url=args.url,
                    translator=translator,
                )
        elif args.speak:
            TTSHandler.run_tts(title, md_content, config, speak=True)
        else:
            # Default: Save to default md_dir
            md_dir = config.get("Output", "md_dir", fallback="./notes")
            OutputHandler.save_markdown(
                title,
                md_content,
                config,
                html_content=html_content,
                add_front_matter=not args.no_front_matter,
                translated_title=translated_title if translation_performed else None,
                source_url=args.url,
                translator=translator,
            )


if __name__ == "__main__":
    main()
