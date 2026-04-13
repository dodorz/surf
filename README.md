# Surf - URL to Markdown/PDF Converter

**Surf** is a powerful Python CLI tool that converts web pages into clean Markdown or PDF files. It handles dynamic content, removes clutter (ads, sidebars), and uses LLMs to translate content if needed.

---

## Features

- **Smart Fetching**: Automatically switches between standard `requests` and `Playwright` (headless browser) for dynamic JavaScript-heavy sites.
- **Special Site Handling**: Optimized handling for Twitter/X, WeChat Official Accounts, Zhihu, and Xiaohongshu (RED) with automatic authentication support.
- **Improved X/Twitter Extraction**: Prefers `uvx --from twitter-cli twitter` by default, reuses local browser cookies when available, detects more X login-wall placeholder variants, resolves `t.co` article links, normalizes direct profile article URLs like `/user/article/<id>` to `/i/article/<id>`, preserves the main tweet/article DOM when possible so inline emphasis and media survive, falls back to structured metadata extraction only when necessary, uses status-id based syndication/fxTwitter fallbacks when `x.com` itself is unreachable, uses the first sentence as the title for non-article posts, and uses `api.fxtwitter.com` as a final fallback when X content is blocked.
- **Multilingual Support**: Auto-detects language and translates to the target language (default: Chinese) using LLM.
- **Translation Modes**: Choose between `trans` (translation), `raw` (no translation), or `both` (bilingual).

### Translation Mode

By default, the content is translated to the target language. Use `--lang` or shortcuts to change this:

- `--lang trans`: Translation (default).
- `--lang raw` or `-r`: No translation.
- `--lang both` or `-b`: Bilingual output.

```bash
# Bilingual output
surf "https://example.com" -b

# Original only (no translation)
surf "https://example.com" -r
```

- **PDF Generation**: Generate PDF files using Playwright.
- **Note Integration**: Automatically saves files to your designated notes folder.
- **TTS Support**: Text-to-Speech support using `edge-tts`. Can save to audio file or read aloud.
- **Flexible Proxy**: Configurable proxy settings via `config.ini` or `-x/--proxy` option (auto/win/no/set).
- **Authentication Management**: Interactive login support for sites requiring authentication (e.g., Xiaohongshu).
- **Experimental Image OCR**: Optional local OCR on article images. RapidOCR is preferred by default, with Tesseract as fallback. Xiaohongshu enables image OCR by default; other sites require `--ocr-images` or `[OCR].enabled = true`.

### Special Site Policies

Some sites have default policies that can be overridden with command-line arguments:

- **WeChat & Xiaohongshu**: Default to no proxy and no translation (can be overridden with `-x` and `-l`)
- **Xiaohongshu**: Also enables local image OCR by default unless you pass `--no-ocr-images`
- **Twitter/X**: Uses forced proxy settings equivalent to `-x win` by default and prefers `uvx --from twitter-cli twitter`; `auto` keeps native fallback available
- **Zhihu**: Defaults to no proxy and no translation; tries Zhihu-specific extraction first; reuses cookies from `surf --login zhihu` for API/mirror `requests` when saved; avoids the generic fallback chain
- **GitHub**: Saved Markdown filename uses the page `<title>`

## Installation

We recommend using `uv` for a clean environment.

1.  **Install uv**:

    ```bash
    pip install uv
    # or follow https://github.com/astral-sh/uv
    ```

2.  **Setup Environment**:
    ```bash
    uv sync
    uv run playwright install
    ```

3.  **Optional: Install OCR engine(s) for image OCR**:
    `surf` prefers `RapidOCR` via the Python package dependency. If you also install local Tesseract, Surf can fall back to it automatically, or you can force it with `--ocr-engine tesseract`. Ensure `tesseract` is on `PATH`, or set `[OCR].tesseract_cmd` in `config.ini`.

## Web UI

`surf_web.py` provides a local Flask-based web interface for development and personal use. The built-in `app.run(...)` server is Flask's development server, so the warning about production deployment is expected.

For local access:

```bash
uv run python surf_web.py --host 127.0.0.1 --port 18473
```

For external or public deployment, use a real WSGI server and point it at `surf_web:app`:

```bash
# Windows-friendly production server
uv add waitress
uv run waitress-serve --listen=0.0.0.0:18473 surf_web:app

# Common Linux production server
uv add gunicorn
uv run gunicorn -w 2 -b 0.0.0.0:18473 surf_web:app
```

If you expose it to the internet, put it behind a reverse proxy with HTTPS and open only the required port in your firewall or security group.

## Configuration

Copy `config.ini.example` to `config.ini` and edit it to set your API keys and paths.

```bash
cp config.ini.example config.ini
```

```ini
[LLM]
; Default LLM provider name
provider = L1

[LLM.L1]
; OpenAI-compatible API configuration for L1
base_url = https://api.openai.com/v1
api_key = your_api_key_here
model = gpt-3.5-turbo

[LLM.L2]
; Additional LLM provider example (DeepSeek)
base_url = https://api.deepseek.com/v1
api_key = your_deepseek_api_key
model = deepseek-chat

[Output]
note_dir = ./notes
target_language = zh-cn
; Directory to save PDF files (default: current directory)
pdf_dir = .
; Directory to save Audio files (default: current directory)
audio_dir = .
; Directory to save HTML files (default: current directory)
html_dir = .

[TTS]
engine = edge-tts
voice = zh-CN-XiaoxiaoNeural
rate = +0%
volume = +0%

[Network]
; Proxy mode: auto (env vars), no (no proxy), win (Windows registry), custom (custom_proxy)
proxy_mode = auto
; Custom proxy URL (e.g., http://127.0.0.1:7890)
custom_proxy =

[Twitter]
; Backend selection: cli (prefer uvx --from twitter-cli twitter), auto (CLI first, then native fallback), native (Surf built-in only)
backend = cli
; Deprecated: Surf always invokes `uvx --from twitter-cli twitter`
cli_bin =
; Optional browser hint for twitter-cli cookies: chrome, edge, brave, firefox, arc
browser =
; Optional browser profile hint for twitter-cli, for example: Default or Profile 2
profile =

[OCR]
; Enable OCR on article images by default (false by default; Xiaohongshu overrides to true)
enabled = false
; OCR engine: rapidocr (default), tesseract, or auto (rapidocr then tesseract)
engine = rapidocr
; Tesseract language(s), for example: chi_sim+eng, eng, jpn+eng
; Ignored by RapidOCR, used when Tesseract is selected or as fallback.
lang = chi_sim+eng
; Optional explicit path to the local tesseract executable
tesseract_cmd =
; OCR at most this many images per article
max_images = 8
```

## Usage

### Basic

Convert a URL and print Markdown to console.

```bash
uv run surf.py "https://example.com"
```

### Save Note (-n)

Save to the configured `note_dir`.

```bash
uv run surf.py "https://example.com" -n
```

### Generate PDF (-p)

Generate a PDF file using Playwright (the default and only engine):

- **playwright**: Highly reliable, uses the installed browser.

```bash
uv run surf.py "https://example.com" -p
```

### Save HTML (--html / --html-inline)

Save content as HTML files:

- `--html`: Save raw HTML
- `--html-inline`: Inline external CSS/JS resources for standalone HTML files

```bash
# Save HTML file
uv run surf.py "https://example.com" --html

# Save HTML with inlined CSS/JS (self-contained file)
uv run surf.py "https://example.com" --html-inline
```

### Text-to-Speech (-a / -s)

Save content as an MP3 audio file:

```bash
uv run surf.py "https://example.com" -a
```

Read content aloud (requires local audio output):

```bash
uv run surf.py "https://example.com" -s
```

### Force Browser

Force using Playwright (useful for tricky sites).

```bash
uv run surf.py "https://example.com" --browser
```

### Image OCR (--ocr-images / --no-ocr-images)

Run local OCR on article images and append recognized text below each image:

```bash
uv run surf.py "https://example.com/article" --ocr-images
uv run surf.py "https://www.xiaohongshu.com/explore/..." --no-ocr-images
uv run surf.py "https://example.com/article" --ocr-images --ocr-lang eng
uv run surf.py "https://example.com/article" --ocr-images --ocr-engine tesseract --ocr-lang eng
```

Notes:

- OCR prefers `rapidocr-onnxruntime` by default.
- If RapidOCR is unavailable or produces no usable text, Surf falls back to local Tesseract unless you force `--ocr-engine tesseract`.
- `--ocr-lang` only applies to Tesseract.
- Xiaohongshu enables image OCR by default.
- OCR failures only skip the affected image; they do not abort the article fetch.

### Authentication (--login / --clear-auth)

For sites requiring authentication (e.g., Xiaohongshu, Twitter/X, Zhihu), use interactive login:

```bash
# First-time login for Xiaohongshu
surf --login xiaohongshu

# Optional: login for Twitter/X (helps with login-wall pages)
surf --login twitter

# Prefer uvx --from twitter-cli twitter with local browser cookies
surf --twitter-backend cli "https://x.com/username/status/1234567890"
surf --twitter-backend cli --twitter-browser chrome "https://x.com/username/status/1234567890"

# Optional: login for Zhihu (feeds cookies to API/mirror requests and helps browser verification)
surf --login zhihu

# After login, fetch content normally
surf "https://www.xiaohongshu.com/explore/..."
surf "https://www.xiaohongshu.com/discovery/item/..."
surf "https://x.com/username/status/1234567890"
surf "https://www.zhihu.com/question/349732913/answer/2008128917886751846"

# Clear saved authentication
surf --clear-auth xiaohongshu
surf --clear-auth twitter
surf --clear-auth zhihu
# Or clear all sites
surf --clear-auth all
```

**Note**: Authentication state and application data are saved in `%LOCALLAPPDATA%\surf\` on Windows, or `~/.local/cache/surf/` on Linux/macOS.
For Twitter/X, Surf also keeps a persistent browser profile under the auth directory to improve login-wall handling. If `uvx` is available, the default backend prefers `uvx --from twitter-cli twitter` so Surf can reuse local browser cookies before touching the built-in Playwright/oEmbed chain. Twitter's forced proxy path defaults to the same behavior as `surf -x win`.

## Help

```bash
uv run surf.py --help
```
