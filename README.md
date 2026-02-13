# Surf - URL to Markdown/PDF Converter

**Surf** is a powerful Python CLI tool that converts web pages into clean Markdown or PDF files. It handles dynamic content, removes clutter (ads, sidebars), and uses LLMs to translate content if needed.

---

## Features

- **Smart Fetching**: Automatically switches between standard `requests` and `Playwright` (headless browser) for dynamic JavaScript-heavy sites.
- **Special Site Handling**: Optimized handling for Twitter/X, WeChat Official Accounts, and Xiaohongshu (RED) with automatic authentication support.
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

### Special Site Policies

Some sites have default policies that can be overridden with command-line arguments:

- **WeChat & Xiaohongshu**: Default to no proxy and no translation (can be overridden with `-x` and `-l`)
- **Twitter/X**: Uses forced proxy settings

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

### Authentication (--login / --clear-auth)

For sites requiring authentication (e.g., Xiaohongshu), use interactive login:

```bash
# First-time login for Xiaohongshu
surf --login xiaohongshu

# After login, fetch content normally
surf "https://www.xiaohongshu.com/explore/..."

# Clear saved authentication
surf --clear-auth xiaohongshu
# Or clear all sites
surf --clear-auth all
```

**Note**: Authentication state and application data are saved in `%LOCALLAPPDATA%\surf\` on Windows, or `~/.local/cache/surf/` on Linux/macOS.

## Help

```bash
uv run surf.py --help
```
