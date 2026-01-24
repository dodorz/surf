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
import re
from bs4 import BeautifulSoup
import trafilatura

# Suppress warnings
warnings.filterwarnings("ignore")

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
    def _get_proxies(config, proxy_mode_override=None, custom_proxy_override=None):
        """
        Returns a dictionary of proxies based on configuration.
        For requests: {'http': '...', 'https': '...'} or None
        For playwright: {'server': '...'} or None
        
        Args:
            config: Config object
            proxy_mode_override: Override proxy_mode from command line (default/none/custom)
            custom_proxy_override: Override custom_proxy from command line
        """
        # Use command line override if provided, otherwise use config
        mode = proxy_mode_override.lower() if proxy_mode_override else config.get('Network', 'proxy_mode', fallback='default').lower()
        
        if mode == 'none':
            return None, None
            
        if mode == 'custom':
            # Use command line override if provided, otherwise use config
            custom = custom_proxy_override if custom_proxy_override else config.get('Network', 'custom_proxy')
            if custom:
                # requests format
                req_proxies = {'http': custom, 'https': custom}
                # playwright format
                pw_proxy = {'server': custom}
                return req_proxies, pw_proxy
            else:
                logger.warning("Custom proxy mode selected but no custom_proxy defined. Falling back to default.")
                
        # mode == 'default' (Check env vars)
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
        node = preprocessed_soup.find(string=lambda t: fingerprint in t if t else False)
        if not node:
            # Try a fuzzy/smaller search
            fingerprint = text[:30]
            node = preprocessed_soup.find(string=lambda t: fingerprint in t if t else False)
        
        if not node:
            return summary_html

        # Traverse up to find a suitable article container
        curr = node.parent
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
            
            # 2. Rescue uncleaned content using fingerprint
            rescued_html = ContentProcessor._rescue_content(preprocessed_soup, summary_html)
            
            img_count = rescued_html.count('<img')
            logger.info(f"Extracted {img_count} images.")
            
            return title, rescued_html
        except Exception as e:
            logger.warning(f"Readability/Rescue failed: {e}. Falling back to Trafilatura.")

        # Final Fallback: Trafilatura
        try:
            content_html = trafilatura.extract(str(preprocessed_soup), output_format='html', include_images=True)
            if content_html:
                img_count = content_html.count('<img')
                logger.info(f"Trafilatura extracted {img_count} images.")
                return Document(html).title(), content_html
        except:
             pass

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
            'img': ['src', 'data-src', 'data-srcset'],
            'link': ['href'],  # stylesheet
            'script': ['src'],
            'video': ['src', 'poster'],
            'audio': ['src'],
            'source': ['src'],
            'embed': ['src'],
            'object': ['data'],
            'iframe': ['src'],
            'a': ['href'],  # 链接也可能需要
            'area': ['href'],
            'base': ['href'],
            'form': ['action'],
            'input': ['src'],
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
        
        return md_content

    @staticmethod
    def save_note(title, content, config, base_url=None):
        """
        Save content as Markdown file, converting relative URLs to absolute.
        
        Args:
            title: Document title
            content: Markdown content to save
            config: Config object
            base_url: Base URL for converting relative URLs
        """
        note_dir = config.get('Output', 'note_dir', fallback='./notes')
        if not os.path.exists(note_dir):
            os.makedirs(note_dir)
        
        # Simple sanitization
        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '.', '_', '-')).strip()
        filename = f"{safe_title}.md"
        filepath = os.path.join(note_dir, filename)
        
        # Convert relative URLs to absolute if base_url is provided
        if base_url:
            content = OutputHandler._convert_markdown_urls_to_absolute(content, base_url)
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info(f"Note saved to {filepath}")
        except Exception as e:
            logger.error(f"Failed to save note: {e}")

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
    def generate_pdf(title, md_content, config):
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

    @staticmethod
    def save_html(title, html_content, config, inline=False, base_url=None):
        """
        Save content as HTML file.
        
        Args:
            title: Document title
            html_content: HTML content to save
            config: Config object
            inline: If True, inline CSS and JS for standalone HTML
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
        
        output_path = os.path.join(html_dir, f"{safe_title}.html")
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        logger.info(f"HTML saved to {output_path} (inline={inline})")
        return output_path

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
    parser = argparse.ArgumentParser(description="Convert URL to Markdown/PDF")
    parser.add_argument("url", help="The URL to process")
    parser.add_argument("-p", "--pdf", action="store_true", help="Generate PDF")
    parser.add_argument("-n", "--note", action="store_true", help="Save to note directory")
    parser.add_argument("-a", "--audio", action="store_true", help="Save as audio file | 保存为音频文件")
    parser.add_argument("-s", "--speak", action="store_true", help="Speak the content | 朗读内容")
    parser.add_argument("--browser", action="store_true", help="Force use of browser (Playwright)")
    parser.add_argument("--trans-mode", choices=['original', 'translated', 'both'], default='translated', 
                        help="Translation mode (default: translated)")
    parser.add_argument("-o", "--original", action="store_true", help="Only original content | 仅原文")
    parser.add_argument("-b", "--both", action="store_true", help="Bilingual: original + translated | 双语 (原文+译文)")
    parser.add_argument("-x", "--proxy-mode", choices=['default', 'none', 'custom'], 
                        help="Proxy mode: default (env/system), none (no proxy), custom (use --proxy) | 代理模式")
    parser.add_argument("--proxy", 
                        help="Custom proxy URL (e.g., http://127.0.0.1:7890). Requires --proxy-mode custom | 自定义代理地址")
    parser.add_argument("--llm", 
                        help="Override the default LLM provider (e.g., L1, L2) | 指定LLM提供方")
    parser.add_argument("--html", action="store_true", 
                        help="Save as HTML file | 保存为HTML文件")
    parser.add_argument("--html-inline", action="store_true", 
                        help="Save as HTML with inline CSS/JS | 保存HTML并将外部资源内联化")
    
    args = parser.parse_args()
    config = Config()
    
    # Validate proxy arguments
    if args.proxy and args.proxy_mode != 'custom':
        parser.error("--proxy requires --proxy-mode custom")
    if args.proxy_mode == 'custom' and not args.proxy:
        parser.error("--proxy-mode custom requires --proxy")

    # Determine final translation mode
    trans_mode = args.trans_mode
    if args.original:
        trans_mode = 'original'
    elif args.both:
        trans_mode = 'both'

    # 1. Fetch
    try:
        html_content = Fetcher.fetch(args.url, config=config, use_browser=args.browser, 
                                     proxy_mode_override=args.proxy_mode, custom_proxy_override=args.proxy)
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
    
    if trans_mode == 'original':
        logger.info("Translation mode set to 'original'. Skipping translation.")
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
        
        if trans_mode == 'both':
            logger.info("Translation mode set to 'both'. Combining original and translation.")
            # Only combine if translation actually happened (or skip logic in translate_if_needed was hit)
            # If translate_if_needed returns the same object, we might not want to double it up.
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

    # Output Actions
    if args.note:
        OutputHandler.save_note(title, md_content, config, base_url=args.url)
    
    if args.pdf:
        OutputHandler.generate_pdf(title, md_content, config)

    if args.html:
        OutputHandler.save_html(title, cleaned_html, config, inline=False, base_url=args.url)

    if args.html_inline:
        OutputHandler.save_html(title, cleaned_html, config, inline=True)

    if args.audio or args.speak:
        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '.', '_', '-')).strip()
        
        audio_filename = None
        if args.audio:
            audio_dir = config.get('Output', 'audio_dir', fallback='.')
            if not os.path.exists(audio_dir):
                os.makedirs(audio_dir)
            audio_filename = os.path.join(audio_dir, f"{safe_title}.mp3")
            
        TTSHandler.run_tts(title, md_content, config, speak=args.speak, save_path=audio_filename)

    if not args.note and not args.pdf and not args.audio and not args.speak and not args.html and not args.html_inline:
        # Default: Print content to stdout
        print(f"# {title}\n")
        print(md_content)


if __name__ == "__main__":
    main()
