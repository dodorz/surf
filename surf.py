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

# Version: 1.0.0.2
__version__ = "1.0.0.3"

# Suppress warnings
warnings.filterwarnings("ignore")

# Configure logging (default: WARNING level, no timestamps)
logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def setup_verbose_logging():
    """Enable verbose logging with timestamps."""
    logging.getLogger().setLevel(logging.INFO)
    # Update existing handlers
    for handler in logging.getLogger().handlers:
        handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

class Config:
    def __init__(self, config_path='config.ini'):
        # Disable interpolation to allow '%' in values (e.g. for TTS rate/voltage)
        self.config = configparser.ConfigParser(interpolation=None)
        if not os.path.exists(config_path):
            logger.warning(f"Config file {config_path} not found. Using defaults.")
        self.config.read(config_path)
        
        # Set default LLM provider
        self.llm_provider = self.get('LLM', 'provider', fallback='L1')

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
        section = f'LLM.{provider}'
        
        if not self.config.has_section(section):
            raise ValueError(f"LLM provider '{provider}' not found in config. "
                           f"Available providers: {self._get_available_llm_providers()}")
            
        return {
            'base_url': self.get(section, 'base_url'),
            'api_key': self.get(section, 'api_key'),
            'model': self.get(section, 'model')
        }
        
    def _get_available_llm_providers(self):
        """Get a list of available LLM provider names from the config."""
        return [section.split('.')[1] for section in self.config.sections() 
                if section.startswith('LLM.')]

class Fetcher:
    @staticmethod
    def _get_system_proxy_win():
        """
        Get Windows system proxy from WinINET (registry or IE settings).
        Returns dict or None.
        """
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, 
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
            # Enable proxy
            enable = winreg.QueryValueEx(key, "ProxyEnable")[0]
            if enable:
                proxy = winreg.QueryValueEx(key, "ProxyServer")[0]
                if proxy:
                    # Check for override (per-protocol)
                    override = winreg.QueryValueEx(key, "ProxyOverride")[0] if False else None
                    return {'http': proxy, 'https': proxy}
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
            proxy_mode_override: Override proxy_mode from command line (auto/none/win/custom)
            custom_proxy_override: Override custom_proxy from command line
        """
        # Use command line override if provided, otherwise use config
        mode = proxy_mode_override.lower() if proxy_mode_override else config.get('Network', 'proxy_mode', fallback='auto').lower()
        
        if mode == 'no':
            return None, None
            
        if mode == 'set':
            # Use command line override if provided, otherwise use config
            custom = custom_proxy_override if custom_proxy_override else config.get('Network', 'custom_proxy')
            if custom:
                req_proxies = {'http': custom, 'https': custom}
                pw_proxy = {'server': custom}
                return req_proxies, pw_proxy
            else:
                logger.warning("Custom proxy mode selected but no custom_proxy defined. Falling back to auto.")
                mode = 'auto'
        
        if mode == 'win':
            # Use WinINet API from Windows registry
            try:
                import winreg
                proxy_config = None
                try:
                    # Try to read proxy settings from Internet Explorer/Edge
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
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
                    for part in proxy_config.split(';'):
                        if '=' in part:
                            k, v = part.split('=', 1)
                            proxies[k.strip()] = v.strip()
                            if k.strip() == 'http':
                                pw_proxy_server = v.strip()
                        else:
                            # Single proxy for all protocols
                            proxies['http'] = part.strip()
                            proxies['https'] = part.strip()
                            pw_proxy_server = part.strip()
                    
                    # Build requests proxies dict
                    req_proxies = {}
                    if 'http' in proxies: req_proxies['http'] = proxies['http']
                    if 'https' in proxies: req_proxies['https'] = proxies['https']
                    if not req_proxies: req_proxies = None
                    
                    # Playwright proxy
                    pw_proxy = None
                    if pw_proxy_server:
                        pw_proxy = {"server": pw_proxy_server}
                    
                    logger.info(f"Using Windows proxy: {proxy_config}")
                    return req_proxies, pw_proxy
                else:
                    logger.info("Windows proxy not enabled. Falling back to auto.")
                    mode = 'auto'
            except ImportError:
                logger.warning("winreg not available. Falling back to auto.")
                mode = 'auto'
        
        # mode == 'auto' (Check env vars, fallback to no proxy if not set)
        http_proxy = os.environ.get('http_proxy') or os.environ.get('HTTP_PROXY')
        https_proxy = os.environ.get('https_proxy') or os.environ.get('HTTPS_PROXY')
        no_proxy = os.environ.get('no_proxy') or os.environ.get('NO_PROXY')
        
        req_proxies = {}
        if http_proxy: req_proxies['http'] = http_proxy
        if https_proxy: req_proxies['https'] = https_proxy
        if not req_proxies: req_proxies = None
        
        # Playwright prefers HTTPS, fallback HTTP
        server = https_proxy or http_proxy
        pw_proxy = None
        if server:
            pw_proxy = {"server": server}
            if no_proxy:
                pw_proxy["bypass"] = no_proxy
                
        return req_proxies, pw_proxy

    @staticmethod
    def fetch(url, config, use_browser=False, proxy_mode_override=None, custom_proxy_override=None):
        """
        Fetches the content of a URL.
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
        
        req_proxies, pw_proxy = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)
        
        if not use_browser:
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                logger.info(f"Requests Proxies: {req_proxies if req_proxies else 'None'}")
                response = requests.get(url, headers=headers, proxies=req_proxies, timeout=10)
                response.raise_for_status()
                
                # Check if likely dynamic (heuristic: very short content or explicit noscript)
                if len(response.text) < 1000 or '<noscript>' in response.text:
                    logger.info("Content seems short or requires JS. Switching to browser...")
                    return Fetcher.fetch_with_browser(url, config, proxy_mode_override, custom_proxy_override)
                    
                return response.text
            except Exception as e:
                logger.warning(f"Requests failed: {e}. Switching to browser...")
                return Fetcher.fetch_with_browser(url, config, proxy_mode_override, custom_proxy_override)
        else:
            return Fetcher.fetch_with_browser(url, config, proxy_mode_override, custom_proxy_override)

    @staticmethod
    def fetch_with_browser(url, config, proxy_mode_override=None, custom_proxy_override=None):
        logger.info("Launching browser...")
        from playwright.sync_api import sync_playwright
        
        _, pw_proxy = Fetcher._get_proxies(config, proxy_mode_override, custom_proxy_override)
        
        if pw_proxy:
             logger.info(f"Playwright Proxy: {pw_proxy}")
        else:
             logger.info("Playwright Proxy: None")
        
        with sync_playwright() as p:
            if pw_proxy:
                browser = p.chromium.launch(headless=True, proxy=pw_proxy)
            else:
                browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.goto(url, wait_until="networkidle", timeout=60000)
                # Wait a bit for any late hydration
                page.wait_for_timeout(2000)
                content = page.content()
                return content
            except Exception as e:
                logger.error(f"Browser fetch failed: {e}")
                raise
            finally:
                browser.close()

class ContentProcessor:
    @staticmethod
    def _preprocess_html(html):
        """
        Preprocesses HTML to normalize complex image structures (like <picture> or lazy-loaded images)
        so that extraction engines can find them more easily.
        """
        soup = BeautifulSoup(html, 'html.parser')
        for img in soup.find_all('img'):
            # Normalize src
            if not img.get('src'):
                for attr in ['data-src', 'data-original', 'data-url', 'data-srcset', 'srcset']:
                    if img.get(attr):
                        # For srcset, take the first URL
                        val = img[attr].split(',')[0].split(' ')[0]
                        img['src'] = val
                        break
            
            # Flatten picture/source
            parent = img.parent
            while parent and parent.name in ['picture', 'source', 'figure']:
                if parent.name in ['picture', 'source']:
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
        summary_soup = BeautifulSoup(summary_html, 'html.parser')
        text = summary_soup.get_text(strip=True)
        if len(text) < 50:
            return summary_html

        # Take a fingerprint from the start of the text
        fingerprint = text[:100]

        # Find the node in the full preprocessed soup
        # Exclude head, title, script, style, etc. by searching only in body and main content areas
        node = None

        # First, try searching in body content (excluding head)
        body = preprocessed_soup.find('body')
        if body:
            node = body.find(string=lambda t: fingerprint in t if t else False)

        # If not found in body, try in main/article/section containers
        if not node:
            for container in preprocessed_soup.find_all(['article', 'main', 'section', 'div']):
                if container.get('id') and container.get('id').lower() in ['content', 'main', 'article', 'body']:
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
                for container in preprocessed_soup.find_all(['article', 'main', 'section']):
                    node = container.find(string=lambda t: fingerprint in t if t else False)
                    if node:
                        break

        if not node:
            return summary_html

        # Traverse up to find a suitable article container
        curr = node.parent if hasattr(node, 'parent') else node
        best_candidate = curr

        # Heuristic: go up until we hit a very broad container or body
        while curr and curr.name not in ['body', 'html']:
            # If this parent has images, it's a better candidate
            if curr.find_all('img'):
                best_candidate = curr

            # Stop if we hit a semantic article boundary
            if curr.name in ['article', 'main'] or (curr.get('id') and curr.get('id').lower() in ['main', 'content', 'article']):
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
            rescued_html = ContentProcessor._rescue_content(preprocessed_soup, summary_html)

            img_count = rescued_html.count('<img')
            logger.info(f"Extracted {img_count} images. Rescued HTML length: {len(rescued_html)}")

            # Debug: log first 200 chars of rescued HTML
            if rescued_html:
                logger.info(f"Rescued HTML preview: {rescued_html[:200]}")

            return title, rescued_html
        except Exception as e:
            logger.warning(f"Readability/Rescue failed: {e}. Falling back to Trafilatura.")

        # Final Fallback: Trafilatura
        try:
            content_html = trafilatura.extract(str(preprocessed_soup), output_format='html', include_images=True)
            if content_html:
                img_count = content_html.count('<img')
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
        paragraphs = text.split('\n\n')
        
        for paragraph in paragraphs:
            para_len = len(paragraph)
             # If a single paragraph is huge, we might still overshoot, but this is a simple heuristic.
            if current_len + para_len + 2 > max_chars and current_chunk:
                chunks.append('\n\n'.join(current_chunk))
                current_chunk = []
                current_len = 0
            
            current_chunk.append(paragraph)
            current_len += para_len + 2
            
        if current_chunk:
            chunks.append('\n\n'.join(current_chunk))
            
        return chunks

    @classmethod
    def translate_if_needed(cls, text, title=None, target_lang='zh-cn', config=None, llm_provider=None):
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
            lang = detect(text[:1000]) # Detect based on first 1000 chars
            logger.info(f"Detected language: {lang}")
        except Exception as e:
            logger.warning(f"Language detection failed: {e}. Assuming translation needed.")
            lang = 'unknown'

        if target_lang.lower() in lang.lower() or lang == 'zh-cn': 
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
            
            client = OpenAI(
                base_url=llm_config['base_url'],
                api_key=llm_config['api_key']
            )
            
            # 1. Translate Title (if provided)
            translated_title = title
            if title:
                logger.info("Translating title...")
                try:
                    t_completion = client.chat.completions.create(
                        model=llm_config['model'],
                        messages=[
                            {"role": "system", "content": f"Translate the following title to {target_lang}. Output ONLY the translation."},
                            {"role": "user", "content": title}
                        ]
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
                logger.info(f"Translating chunk {i+1}/{total_chunks} ({len(chunk)} chars)...")
                completion = client.chat.completions.create(
                    model=llm_config['model'],
                    messages=[
                        {"role": "system", "content": f"You are a helpful translator. Translate the following Markdown content to {target_lang}. Preserve the Markdown formatting strictly. Output ONLY the translated markdown."},
                        {"role": "user", "content": chunk}
                    ]
                )
                translated_chunks.append(completion.choices[0].message.content)
            
            return '\n\n'.join(translated_chunks), translated_title
            
        except Exception as e:
            logger.error(f"Translation failed: {e}")
            return text, title

class OutputHandler:
    @staticmethod
    def _sanitize_filename(filename):
        return "".join([c for c in filename if c.alpha() or c.isdigit() or c in ' ._-']).rstrip()

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
        
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # 需要处理的标签和属性映射
        tag_attr_map = {
            # 媒体和图片
            'img': ['src', 'data-src', 'data-srcset', 'srcset'],
            'video': ['src', 'poster', 'data-src'],
            'audio': ['src', 'data-src'],
            'source': ['src', 'srcset'],
            'track': ['src'],
            'embed': ['src'],
            'object': ['data'],
            'iframe': ['src'],
            'svg': ['data', 'href'],  # SVG引用
            # 链接和导航
            'a': ['href'],
            'area': ['href'],
            'base': ['href'],
            'link': ['href'],  # stylesheet, favicon等
            # 脚本和样式
            'script': ['src', 'href'],
            'style': ['href'],
            # 表单
            'form': ['action'],
            'input': ['src'],
            'button': ['formaction'],
            # 其他
            'ins': ['cite'],
            'del': ['cite'],
            'blockquote': ['cite'],
        }
        
        for tag, attrs in tag_attr_map.items():
            for element in soup.find_all(tag):
                for attr in attrs:
                    url = element.get(attr)
                    if url and not url.startswith(('http://', 'https://', 'data:', '#', 'mailto:', 'tel:', 'javascript:')):
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
            if url and not url.startswith(('http://', 'https://', 'data:')):
                absolute_url = urljoin(base_url, url)
                return f'![{alt_text}]({absolute_url})'
            return match.group(0)

        md_content = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', replace_image_url, md_content)

        # 处理链接: [text](url)
        def replace_link_url(match):
            text = match.group(1)
            url = match.group(2)
            if url and not url.startswith(('http://', 'https://', 'data:', '#', 'mailto:', 'tel:', 'javascript:')):
                absolute_url = urljoin(base_url, url)
                return f'[{text}]({absolute_url})'
            return match.group(0)

        md_content = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', replace_link_url, md_content)

        # 处理内联HTML标签中的URL（如 <video src="...">、<audio src="..."> 等）
        def convert_html_attrs_in_md(match):
            html_tag = match.group(0)
            soup = BeautifulSoup(html_tag, 'html.parser')
            tag = soup.find()

            if tag:
                tag_attr_map = {
                    'img': ['src', 'data-src', 'data-srcset', 'srcset'],
                    'video': ['src', 'poster', 'data-src'],
                    'audio': ['src', 'data-src'],
                    'source': ['src', 'srcset'],
                    'track': ['src'],
                    'embed': ['src'],
                    'object': ['data'],
                    'iframe': ['src'],
                    'svg': ['data', 'href'],
                    'script': ['src', 'href'],
                    'a': ['href'],
                    'link': ['href'],
                }

                tag_name = tag.name
                if tag_name in tag_attr_map:
                    for attr in tag_attr_map[tag_name]:
                        url = tag.get(attr)
                        if url and not url.startswith(('http://', 'https://', 'data:', '#', 'mailto:', 'tel:', 'javascript:')):
                            tag[attr] = urljoin(base_url, url)

            return str(soup)

        # 匹配自闭合标签如 <img src="...">、<video src="..."> 等
        md_content = re.sub(r'<([a-zA-Z][a-zA-Z0-9]*)\s+[^>]*>', convert_html_attrs_in_md, md_content)

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
        soup = BeautifulSoup(html_content, 'html.parser')
        metadata = {
            'title': None,
            'created': None,
            'updated': None,
            'tags': [],
            'source': source_url,
            'translator': translator
        }
        
        # 提取title元素
        title_tag = soup.find('title')
        if title_tag:
            metadata['title'] = title_tag.get_text(strip=True)
        
        # 提取发布日期 - 尝试多种常见的meta标签
        date_selectors = [
            ('meta', {'property': 'article:published_time'}),
            ('meta', {'name': 'publishdate'}),
            ('meta', {'name': 'date'}),
            ('meta', {'property': 'og:published_time'}),
            ('meta', {'name': 'pubdate'}),
        ]
        
        for tag_name, attrs in date_selectors:
            date_tag = soup.find(tag_name, attrs)
            if date_tag:
                date_value = date_tag.get('content') or date_tag.get('value')
                if date_value:
                    try:
                        # 尝试解析日期
                        parsed_date = date_parser.parse(date_value)
                        metadata['created'] = parsed_date.strftime('%Y-%m-%d')
                        break
                    except (ValueError, TypeError):
                        pass
        
        # 提取keywords作为tags
        keywords_tag = soup.find('meta', {'name': 'keywords'})
        if keywords_tag:
            keywords_value = keywords_tag.get('content') or keywords_tag.get('value')
            if keywords_value:
                # 分割关键词为列表
                tags = [tag.strip() for tag in keywords_value.split(',') if tag.strip()]
                metadata['tags'] = tags
        
        # 设置updated为当前时间
        metadata['updated'] = datetime.now().strftime('%Y-%m-%d')
        
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
        lines = ['---']
        
        if metadata.get('title'):
            # 转义特殊字符
            title = metadata['title'].replace('"', '\\"')
            lines.append(f'title: "{title}"')
        
        if metadata.get('created'):
            lines.append(f'created: {metadata["created"]}')
        
        if metadata.get('updated'):
            lines.append(f'updated: {metadata["updated"]}')
        
        if metadata.get('tags') and len(metadata['tags']) > 0:
            lines.append('tags:')
            for tag in metadata['tags']:
                lines.append(f'  - "{tag}"')
        
        if metadata.get('source'):
            lines.append(f'source: {metadata["source"]}')
        
        if metadata.get('translator'):
            lines.append(f'translator: {metadata["translator"]}')
        
        lines.append('---\n')
        
        return '\n'.join(lines)
    
    @staticmethod
    def save_markdown(title, content, config, output_path=None, base_url=None, html_content=None, add_front_matter=True, translated_title=None, source_url=None, translator=None):
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
        md_dir = config.get('Output', 'md_dir', fallback='./notes')
        if not os.path.exists(md_dir):
            os.makedirs(md_dir)
        
        # Determine filepath
        if output_path:
            filepath = output_path
            # Handle special cases: "." or "./" (current directory)
            if filepath == "." or filepath == "./":
                # Use current directory + default filename
                safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '.', '_', '-')).strip()
                filename = f"{safe_title}.md"
                filepath = os.path.join(".", filename)
            # Ensure directory exists
            filepath_dir = os.path.dirname(filepath)
            if filepath_dir and not os.path.exists(filepath_dir):
                os.makedirs(filepath_dir)
        else:
            # Simple sanitization
            safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '.', '_', '-')).strip()
            filename = f"{safe_title}.md"
            filepath = os.path.join(md_dir, filename)
        
        # Convert relative URLs to absolute if base_url is provided
        if base_url:
            content = OutputHandler._convert_markdown_urls_to_absolute(content, base_url)
        
        # Generate YAML front matter if html_content is provided and add_front_matter is True
        yaml_frontmatter = ''
        if html_content and add_front_matter:
            metadata = OutputHandler._extract_metadata(html_content, source_url=source_url, translator=translator)
            # Use translated_title if provided (translation was performed)
            if translated_title:
                metadata['title'] = translated_title
            yaml_frontmatter = OutputHandler._generate_yaml_frontmatter(metadata)
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
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
            safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '.', '_', '-')).strip()
            pdf_dir = config.get('Output', 'pdf_dir', fallback='.')
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
        html_dir = config.get('Output', 'html_dir', fallback='.')
        if not os.path.exists(html_dir):
            os.makedirs(html_dir)
        
        # Sanitize filename
        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '.', '_', '-')).strip()
        safe_title = safe_title[:100]
        
        if inline:
            html_content = OutputHandler._inline_resources(html_content)
        elif base_url:
            # Convert relative URLs to absolute for non-inline HTML
            html_content = OutputHandler._convert_urls_to_absolute(html_content, base_url)

        # Wrap content in complete HTML document if needed
        if html_content and not html_content.strip().lower().startswith('<!doctype') and not html_content.strip().lower().startswith('<html'):
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
        if output_path == '-':
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
        
        with open(filepath, 'w', encoding='utf-8') as f:
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
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Inline CSS
        for link in soup.find_all('link', rel='stylesheet'):
            href = link.get('href')
            if href:
                try:
                    if not href.startswith(('http://', 'https://', 'data:')):
                        logger.warning(f"Skipping relative CSS: {href}")
                        continue
                    
                    response = requests.get(href, timeout=10)
                    if response.status_code == 200:
                        style = soup.new_tag('style')
                        style.string = response.text
                        link.replace_with(style)
                        logger.info(f"Inlined CSS: {href}")
                except Exception as e:
                    logger.warning(f"Failed to inline CSS {href}: {e}")
        
        # Inline JS
        for script in soup.find_all('script', src=True):
            src = script.get('src')
            if src:
                try:
                    if not src.startswith(('http://', 'https://', 'data:')):
                        logger.warning(f"Skipping relative JS: {src}")
                        continue
                    
                    response = requests.get(src, timeout=10)
                    if response.status_code == 200:
                        new_script = soup.new_tag('script')
                        new_script.string = response.text
                        script.replace_with(new_script)
                        logger.info(f"Inlined JS: {src}")
                except Exception as e:
                    logger.warning(f"Failed to inline JS {src}: {e}")
        
        # Add meta charset if missing
        if not soup.find('meta', charset=True):
            head = soup.find('head')
            if head:
                meta = soup.new_tag('meta')
                meta['charset'] = 'utf-8'
                head.insert(0, meta)
        
        return str(soup)

class TTSHandler:
    @staticmethod
    async def generate_speech(text, output_file, config):
        voice = config.get('TTS', 'voice', fallback='zh-CN-XiaoxiaoNeural')
        rate = config.get('TTS', 'rate', fallback='+0%')
        volume = config.get('TTS', 'volume', fallback='+0%')
        
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
        clean_text = re.sub(r'!\[.*?\]\(.*?\)', '', content)
        # Remove [link text](url) -> keep 'link text'
        clean_text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', clean_text)
        # Remove other common MD artifacts
        clean_text = clean_text.replace('#', '').replace('*', '').replace('`', '').replace('---', '')
        
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
        prog='uv run surf',
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
        """
    )
    # Position argument
    parser.add_argument("url", help="URL to process")
    
    # Output format
    format_group = parser.add_mutually_exclusive_group()
    format_group.add_argument("-f", "--format", choices=['md', 'pdf', 'html', 'audio'], 
                        help="Output format: md/pdf/html/audio (default: md)")
    format_group.add_argument("-h", action="store_true", help="Shorthand for --format html")
    format_group.add_argument("-p", action="store_true", help="Shorthand for --format pdf")
    format_group.add_argument("-a", action="store_true", help="Shorthand for --format audio")
    
    # Output path
    parser.add_argument("-o", "--output", 
                        help="Output file path (use '-' for stdout, overrides config)")
    
    # Language mode
    lang_group = parser.add_mutually_exclusive_group()
    lang_group.add_argument("-l", "--lang", choices=['trans', 'raw', 'both'], 
                        help="Language mode: trans=translate, raw=original, both=bilingual (default: trans)")
    lang_group.add_argument("-r", action="store_true", help="Shorthand for --lang raw")
    lang_group.add_argument("-b", action="store_true", help="Shorthand for --lang both")
    
    # TTS
    parser.add_argument("-s", "--speak", action="store_true", 
                        help="Speak the content")
    
    # HTML options
    parser.add_argument("--html-inline", action="store_true", 
                        help="Inline CSS/JS in HTML output")
    
    # Network
    parser.add_argument("-x", "--proxy", choices=['win', 'custom', 'no'], 
                        help="Proxy mode: win=Windows, custom=use --set-proxy, no=no proxy (default: auto)")
    parser.add_argument("-c", action="store_true", help="Shorthand for --proxy custom")
    parser.add_argument("-n", action="store_true", help="Shorthand for --proxy no")
    parser.add_argument("--set-proxy", 
                        help="Custom proxy URL (requires -x custom)")
    
    # LLM
    parser.add_argument("--llm", 
                        help="Override the default LLM provider")
    
    # Other options
    parser.add_argument("--browser", action="store_true", 
                        help="Force use of browser (Playwright)")
    parser.add_argument("--config", 
                        help="Path to config file")
    parser.add_argument("--verbose", action="store_true", 
                        help="Enable verbose logging")
    parser.add_argument("--no-front-matter", action="store_true",
                        help="Disable YAML front matter in markdown output")
    parser.add_argument("--version", action="version", 
                        version=f"%(prog)s {__version__}")
    parser.add_argument("--help", action="help", help="Show this help message")
    
    args = parser.parse_args()
    
    # Enable verbose logging if requested
    if args.verbose:
        setup_verbose_logging()
    
    # Determine config path
    config_path = args.config if args.config else 'config.ini'
    config = Config(config_path)
    
    # Validate proxy arguments
    if args.set_proxy and args.proxy != 'custom':
        parser.error("--set-proxy requires -x custom")
    if args.proxy == 'custom' and not args.set_proxy:
        parser.error("-x custom requires --set-proxy")
    
    # Determine final format
    output_format = 'md'  # default
    if args.format:
        output_format = args.format
    elif args.h:
        output_format = 'html'
    elif args.p:
        output_format = 'pdf'
    elif args.a:
        output_format = 'audio'
    
    # Determine final language mode
    lang_mode = 'trans'  # default
    if args.lang:
        lang_mode = args.lang
    elif args.r:
        lang_mode = 'raw'
    elif args.b:
        lang_mode = 'both'
    
    # Determine proxy mode override
    proxy_mode = None  # use config default (auto)
    if args.proxy:
        proxy_mode = args.proxy
    elif args.c:
        proxy_mode = 'custom'
    elif args.n:
        proxy_mode = 'no'
    
    # Determine custom proxy
    custom_proxy = args.set_proxy

    # Determine proxy mode
    proxy_mode = args.proxy
    custom_proxy = args.set_proxy

    # 1. Fetch
    try:
        html_content = Fetcher.fetch(
            args.url, 
            config=config, 
            use_browser=args.browser, 
            proxy_mode_override=proxy_mode, 
            custom_proxy_override=custom_proxy
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
    target_lang = config.get('Output', 'target_language', fallback='zh-cn')
    translated_title = None  # Initialize for raw mode
    
    if lang_mode == 'raw':
        logger.info("Language mode set to 'raw'. Skipping translation.")
    else:
        original_md = md_content
        original_title = title
        
        # Get LLM provider from command line if specified
        llm_provider = args.llm if hasattr(args, 'llm') else None
        
        translated_md, translated_title = ContentProcessor.translate_if_needed(
            md_content, 
            title=title, 
            target_lang=target_lang, 
            config=config,
            llm_provider=llm_provider
        )
        
        if lang_mode == 'both':
            logger.info("Language mode set to 'both'. Combining original and translation.")
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
        md_content = OutputHandler._convert_markdown_urls_to_absolute(md_content, args.url)
        # Also convert HTML URLs for HTML output
        cleaned_html = OutputHandler._convert_urls_to_absolute(cleaned_html, args.url)

    # 5. Output
    # Determine output path
    output_path = args.output if args.output else None
    
    # Handle output based on format
    if output_format == 'pdf':
        if output_path:
            OutputHandler.generate_pdf(title, md_content, config, output_path)
        else:
            OutputHandler.generate_pdf(title, md_content, config)

    elif output_format == 'html':
        if output_path:
            OutputHandler.save_html(title, cleaned_html, config, inline=args.html_inline, output_path=output_path)
        else:
            OutputHandler.save_html(title, cleaned_html, config, inline=args.html_inline)

    elif output_format == 'audio':
        if output_path:
            TTSHandler.run_tts(title, md_content, config, speak=args.speak, save_path=output_path)
        else:
            TTSHandler.run_tts(title, md_content, config, speak=args.speak)

    else:  # md (default)
        # Determine if translation was performed
        translation_performed = lang_mode != 'raw'
        
        # Get translator model name if translation was performed
        translator = None
        if translation_performed:
            try:
                llm_provider = args.llm if hasattr(args, 'llm') else None
                llm_config = config.get_llm_config(llm_provider)
                translator = llm_config['model']
            except Exception as e:
                logger.warning(f"Could not get LLM config for translator: {e}")
        
        if output_path:
            if output_path == '-':
                # Output to stdout
                print(f"# {title}\n")
                print(md_content)
            else:
                OutputHandler.save_markdown(
                    title, md_content, config, output_path, 
                    html_content=html_content, 
                    add_front_matter=not args.no_front_matter,
                    translated_title=translated_title if translation_performed else None,
                    source_url=args.url,
                    translator=translator
                )
        elif args.speak:
            TTSHandler.run_tts(title, md_content, config, speak=True)
        else:
            # Default: Save to default md_dir
            md_dir = config.get('Output', 'md_dir', fallback='./notes')
            OutputHandler.save_markdown(
                title, md_content, config, 
                html_content=html_content, 
                add_front_matter=not args.no_front_matter,
                translated_title=translated_title if translation_performed else None,
                source_url=args.url,
                translator=translator
            )


if __name__ == "__main__":
    main()
