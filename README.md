# Surf - URL to Markdown/PDF Converter

**Surf** is a powerful Python CLI tool that converts web pages into clean Markdown or PDF files. It handles dynamic content, removes clutter (ads, sidebars), and uses LLMs to translate content if needed.

---

## Features

- **Smart Fetching**: Automatically switches between standard `requests` and `Playwright` (headless browser) for dynamic JavaScript-heavy sites.
- **Special Site Handling**: Optimized handling for Twitter/X, Bluesky, Weibo, Threads, V2EX, WeChat Official Accounts, Zhihu, Xiaohongshu (RED), and NCPSSD with reusable saved authentication support.
- **Improved X/Twitter Extraction**: Prefers `uvx --from twitter-cli twitter` by default, reuses local browser cookies when available, detects more X login-wall placeholder variants, resolves `t.co` article links, normalizes direct profile article URLs like `/user/article/<id>` to `/i/article/<id>`, preserves the main tweet/article DOM when possible so inline emphasis and media survive, falls back to structured metadata extraction only when necessary, uses status-id based syndication/fxTwitter fallbacks when `x.com` itself is unreachable, and uses `api.fxtwitter.com` as a final fallback when X content is blocked.
- **Same-Author Thread Expansion**: For Twitter/X, Bluesky, Weibo, and Threads, Surf now defaults to following later same-author replies in the thread until the author changes. You can switch to `forward` or `both` with `--thread`; V2EX uses `-t/--thread` to include topic replies and otherwise saves only the main post.
- **Short-Post Title Normalization**: For short posts on Twitter/X, Bluesky, Weibo, and Threads, Surf derives the title, front matter `title`, and default Markdown filename as `First sentence - Author on Site`. Long-form articles (for example X `/article/...`) keep the article's own title.
- **Filename Safety with Minimal Loss**: When titles are used as filenames, Surf preserves valid punctuation (including CJK punctuation) and only removes filesystem-illegal filename characters.
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
- **Flexible Proxy**: Unified proxy modes: `env` (environment variables), `win` (Windows Internet Settings), `custom`, and `no`.
- **Authentication Management**: Interactive login plus auth state import/export for sites requiring authentication (e.g., Xiaohongshu, NCPSSD).
- **Experimental Image OCR**: Optional local OCR on article images. RapidOCR is preferred by default, with Tesseract as fallback. Xiaohongshu enables image OCR by default; other sites require `--ocr-images` or `[OCR].enabled = true`.
- **Web Text Posts**: In `surf_web.py`, if you paste plain text without any URL, Surf treats it as a post, uses the first sentence as the title, and sends it through the normal translation/export pipeline.

### Special Site Policies

Surf includes site-specific handlers for platforms such as Twitter/X, WeChat, Zhihu, Xiaohongshu, Bluesky, Weibo, Threads, V2EX, NCPSSD, GitHub, and Wikipedia.

Detailed matching rules, handler behavior, default policy overrides, and maintenance notes are documented in:
- `SPECIAL_SITES.md` (English)
- `SPECIAL_SITES_zh.md` (Chinese)

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
    `uv sync` installs the Python dependencies declared in `pyproject.toml`, including `rapidocr-onnxruntime` for image OCR.

3.  **Optional: Install OCR engine(s) for image OCR**:
    `surf` prefers `RapidOCR` via the Python package dependency. If you need to install it separately, run:
    ```bash
    uv pip install rapidocr-onnxruntime
    ```
    If you also install local Tesseract, Surf can fall back to it automatically, or you can force it with `--ocr-engine tesseract`. Ensure `tesseract` is on `PATH`, or set `[OCR].tesseract_cmd` in `config.ini`.

## Web UI

`surf_web.py` provides a local Flask-based web interface for development and personal use. The built-in `app.run(...)` server is Flask's development server, so the warning about production deployment is expected.

The web form exposes the most commonly used Surf options directly, including:
- language mode (`trans` / `raw` / `both`)
- proxy mode and custom proxy (`win/env/custom/no` on Windows, `env/custom/no` on non-Windows)
- browser rendering
- image OCR on/off, with OCR engine and OCR language controls shown only when OCR is not disabled
- thread expansion (`forward` / `backward` / `both` / off; V2EX uses this as reply inclusion)
- optional LLM provider override for translation, shown only when language mode is not `raw`
- free-form URL or text input: you can paste share text and Surf will extract the first `http/https` URL automatically; if no URL is present, the text is saved as a post and the first sentence becomes the title

As you type a URL, the Web UI applies matching special-site defaults to the visible options. For example, sites that default to raw language hide the LLM provider unless you manually choose a translation mode, and sites where the effective OCR default is off hide the OCR engine unless you manually enable OCR.

Web proxy default selection is deterministic:
- Surf Web defaults to no proxy because it is usually deployed on a server with direct outbound access. Choose `env`, `custom`, or another proxy mode manually in the Web UI for the few sites or deployments that need it.

For local access:

```bash
uv run python surf_web.py --host 127.0.0.1 --port 18473
```

For external or public deployment, use a real WSGI server and point it at `surf_web:app`:

```bash
# Windows-friendly production server (installed by `uv sync` on Windows)
uv run waitress-serve --listen=0.0.0.0:18473 surf_web:app

# Common Linux production server (installed by `uv sync` on Linux/macOS)
uv run gunicorn -w 2 -b 0.0.0.0:18473 surf_web:app
```

If you expose it to the internet, put it behind a reverse proxy with HTTPS and open only the required port in your firewall or security group.

## Configuration

Copy `config.ini.example` to `config.ini` and edit it to set your API keys and paths.

On Windows, Surf accepts Unix-style path input in config values and CLI path arguments. For example, `~/Note/article.md` is resolved under `%USERPROFILE%`, and `/` is normalized to `\`.

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
; Proxy mode: env (environment vars), win (Windows Internet Settings), custom (custom_proxy), no (no proxy)
proxy_mode = env
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

On Windows, path-style arguments such as `--config`, `-o/--output`, `--export-auth`, and `--import-auth` also accept Unix-style input. For example, `~/Note/out.md` resolves under `%USERPROFILE%`.

### Proxy Modes (-x / -c / -n)

```bash
# Use environment variables (http_proxy / https_proxy / no_proxy)
uv run surf.py "https://example.com" -x env

# Windows only: use Windows Internet Settings proxy (WinINET)
uv run surf.py "https://example.com" -x win

# Custom proxy (CLI value overrides config `custom_proxy`)
uv run surf.py "https://example.com" -x custom --set-proxy http://127.0.0.1:7890
uv run surf.py "https://example.com" -c http://127.0.0.1:7890
uv run surf.py "https://example.com" -x custom
uv run surf.py "https://example.com" -c

# Disable proxy
uv run surf.py "https://example.com" -n
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
uv run surf.py "https://example.com" -p -o ~/Note/example.pdf
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

### Thread Expansion (-t / --thread / --no-thread)

Fetch the current post together with same-author posts from the same thread:

```bash
uv run surf.py "https://x.com/user/status/123" -t
uv run surf.py "https://x.com/user/status/123" --thread forward
uv run surf.py "https://x.com/user/status/123" --thread both
uv run surf.py "https://bsky.app/profile/user.bsky.social/post/abc123" --no-thread
```

For V2EX topics, Surf defaults to the main post only and uses the configured proxy automatically when available. Add `-t` or `--thread` to include replies:

```bash
uv run surf.py "https://v2ex.com/t/1208365" -r
uv run surf.py "https://v2ex.com/t/1208365" -r -t
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

### Authentication (--login / --export-auth / --import-auth / --clear-auth)

For sites requiring authentication (e.g., Xiaohongshu, Twitter/X, Zhihu, NCPSSD), prepare and reuse a saved Playwright auth state:

```bash
# First-time login for Xiaohongshu
surf --login xiaohongshu

# Export the saved state from a desktop machine
surf --export-auth xiaohongshu ./xiaohongshu_state.json
surf --export-auth xiaohongshu ~/Note/xiaohongshu_state.json

# Import that state on a headless Linux server
surf --import-auth xiaohongshu ./xiaohongshu_state.json
surf --import-auth xiaohongshu ~/Note/xiaohongshu_state.json

# Optional: login for Twitter/X (helps with login-wall pages)
surf --login twitter

# Prefer uvx --from twitter-cli twitter with local browser cookies
surf --twitter-backend cli "https://x.com/username/status/1234567890"
surf --twitter-backend cli --twitter-browser chrome "https://x.com/username/status/1234567890"

# Optional: login for Zhihu (feeds cookies to API/mirror requests and helps browser verification)
surf --login zhihu

# Optional: login for NCPSSD (required by some secure full-text downloads)
surf --login ncpssd

# After login/import, fetch content normally
surf "https://www.xiaohongshu.com/explore/..."
surf "https://www.xiaohongshu.com/discovery/item/..."
surf "https://x.com/username/status/1234567890"
surf "https://www.zhihu.com/question/349732913/answer/2008128917886751846"
surf -p "https://ncpssd.cn/Literature/secure/articleinfo?params=..."

# Clear saved authentication
surf --clear-auth xiaohongshu
surf --clear-auth twitter
surf --clear-auth zhihu
surf --clear-auth ncpssd
# Or clear all sites
surf --clear-auth all
```

**Note**: Authentication state and application data are saved in `%LOCALLAPPDATA%\surf\` on Windows, or `~/.local/cache/surf/` on Linux/macOS.
On headless Linux, `surf --login ...` now fails fast instead of trying to open a browser with no display. Run the login command on a desktop machine, then move the saved state with `--export-auth` and `--import-auth`.
For Twitter/X, Surf also keeps a persistent browser profile under the auth directory to improve login-wall handling. If `uvx` is available, the default backend prefers `uvx --from twitter-cli twitter` so Surf can reuse local browser cookies before touching the built-in Playwright/oEmbed chain. Twitter's forced proxy path defaults to the same behavior as `surf -x win`.
For site-specific behavior details (including NCPSSD download rules), refer to `SPECIAL_SITES.md` / `SPECIAL_SITES_zh.md`.

## Help

```bash
uv run surf.py --help
```
