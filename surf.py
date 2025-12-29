import argparse
import configparser
import os
import sys
import logging
from urllib.parse import urlparse
import requests
from readability import Document
import markdownify
from langdetect import detect
import warnings
import asyncio
import edge_tts
from playsound import playsound

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

    def get(self, section, key, fallback=None):
        return self.config.get(section, key, fallback=fallback)

class Fetcher:
    @staticmethod
    def _get_proxies(config):
        """
        Returns a dictionary of proxies based on configuration.
        For requests: {'http': '...', 'https': '...'} or None
        For playwright: {'server': '...'} or None
        """
        mode = config.get('Network', 'proxy_mode', fallback='default').lower()
        
        if mode == 'none':
            return None, None
            
        if mode == 'custom':
            custom = config.get('Network', 'custom_proxy')
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
    def fetch(url, config, use_browser=False):
        """
        Fetches the content of a URL.
        If use_browser is True, uses Playwright.
        Otherwise, uses requests.
        """
        logger.info(f"Fetching {url}...")
        
        req_proxies, pw_proxy = Fetcher._get_proxies(config)
        
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
                    return Fetcher.fetch_with_browser(url, config)
                    
                return response.text
            except Exception as e:
                logger.warning(f"Requests failed: {e}. Switching to browser...")
                return Fetcher.fetch_with_browser(url, config)
        else:
            return Fetcher.fetch_with_browser(url, config)

    @staticmethod
    def fetch_with_browser(url, config):
        logger.info("Launching browser...")
        from playwright.sync_api import sync_playwright
        
        _, pw_proxy = Fetcher._get_proxies(config)
        
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
    def extract_content(html):
        """
        Extracts the main content using Readability.
        Returns title and cleaned HTML content.
        """
        logger.info("Extracting main content...")
        doc = Document(html)
        return doc.title(), doc.summary()

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

    @staticmethod
    def translate_if_needed(text, title=None, target_lang='zh-cn', config=None):
        """
        Detects language and translates (content + title) if necessary using chunking.
        Returns: (translated_text, translated_title)
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
            
            client = OpenAI(
                base_url=config.get('LLM', 'base_url'),
                api_key=config.get('LLM', 'api_key'),
            )
            
            # 1. Translate Title (if provided)
            translated_title = title
            if title:
                logger.info("Translating title...")
                try:
                    t_completion = client.chat.completions.create(
                        model=config.get('LLM', 'model'),
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
            chunks = ContentProcessor._chunk_text(text)
            translated_chunks = []
            
            total_chunks = len(chunks)
            logger.info(f"Content split into {total_chunks} chunks for translation.")
            
            for i, chunk in enumerate(chunks):
                logger.info(f"Translating chunk {i+1}/{total_chunks} ({len(chunk)} chars)...")
                completion = client.chat.completions.create(
                    model=config.get('LLM', 'model'),
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
    def save_note(title, content, config):
        note_dir = config.get('Output', 'note_dir', fallback='./notes')
        if not os.path.exists(note_dir):
            os.makedirs(note_dir)
        
        # Simple sanitization
        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '.', '_', '-')).strip()
        filename = f"{safe_title}.md"
        filepath = os.path.join(note_dir, filename)
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info(f"Note saved to {filepath}")
        except Exception as e:
            logger.error(f"Failed to save note: {e}")

    @staticmethod
    def generate_pdf(title, md_content, config):
        try:
            import markdown
            from weasyprint import HTML, CSS
        except ImportError:
            logger.error("Missing dependencies for PDF generation (markdown, weasyprint).")
            return

        logger.info("Generating PDF...")
        # Convert MD to HTML for PDF generation
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
            
        filename = f"{safe_title}.pdf"
        filepath = os.path.join(pdf_dir, filename)
        
        try:
            # Check for custom CSS
            css_file = config.get('PDF', 'css_file')
            stylesheets = []
            if css_file and os.path.exists(css_file):
                HTML(string=full_html).write_pdf(filepath, stylesheets=[CSS(css_file)])
            else:
                HTML(string=full_html).write_pdf(filepath)
            
            logger.info(f"PDF saved to {filepath}")
        except Exception as e:
            logger.error(f"Generate PDF failed: {e}")

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
        clean_text = content.replace('#', '').replace('*', '')  # Simple cleanup for TTS
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
    
    args = parser.parse_args()
    config = Config()

    # 1. Fetch
    try:
        html_content = Fetcher.fetch(args.url, config=config, use_browser=args.browser)
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
    md_content, title = ContentProcessor.translate_if_needed(md_content, title=title, target_lang=target_lang, config=config)

    # Output Actions
    if args.note:
        OutputHandler.save_note(title, md_content, config)
    
    if args.pdf:
        OutputHandler.generate_pdf(title, md_content, config)

    if args.audio or args.speak:
        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '.', '_', '-')).strip()
        
        audio_filename = None
        if args.audio:
            audio_dir = config.get('Output', 'audio_dir', fallback='.')
            if not os.path.exists(audio_dir):
                os.makedirs(audio_dir)
            audio_filename = os.path.join(audio_dir, f"{safe_title}.mp3")
            
        TTSHandler.run_tts(title, md_content, config, speak=args.speak, save_path=audio_filename)

    if not args.note and not args.pdf and not args.audio and not args.speak:
        # Default: Print content to stdout
        print("\n--- FINAL CONTENT PREVIEW ---\n")
        print(f"# {title}\n")
        print(md_content[:500] + "..." if len(md_content) > 500 else md_content)


if __name__ == "__main__":
    main()
