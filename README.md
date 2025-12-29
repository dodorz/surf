# Surf - URL to Markdown/PDF Converter

**Surf** is a powerful Python CLI tool that converts web pages into clean Markdown or PDF files. It handles dynamic content, removes clutter (ads, sidebars), and uses LLMs to translate content if needed.

---

## Features

*   **Smart Fetching**: Automatically switches between standard `requests` and `Playwright` (headless browser) for dynamic JavaScript-heavy sites.
*   **Clean Extraction**: Uses `readability` to extract only the main article content.
*   **Markdown & PDF**: Converts content to Standard Markdown or PDF.
*   **Auto Translation**: Detects non-Chinese content and auto-translates it using a configured LLM (e.g., OpenAI, DeepSeek). Supports **smart chunking** for long documents to avoid context limits.
*   **Note Integration**: Automatically saves files to your designated notes folder.
*   **TTS Support**: Text-to-Speech support using `edge-tts`. Can save to audio file or read aloud.
*   **Flexible Proxy**: Configurable proxy settings (System Default, Custom, or None) via `config.ini`.

## Installation

We recommend using `uv` for a clean environment.

1.  **Install uv**:
    ```bash
    pip install uv
    # or follow https://github.com/astral-sh/uv
    ```

2.  **Setup Environment**:
    ```bash
    uv venv
    uv pip install -r requirements.txt
    uv run playwright install
    ```

## Configuration

Copy `config.ini.example` to `config.ini` and edit it to set your API keys and paths.

```bash
cp config.ini.example config.ini
```

```ini
[LLM]
base_url = https://api.openai.com/v1
api_key = your_api_key_here
model = gpt-3.5-turbo

[Output]
note_dir = ./notes
target_language = zh-cn
; Directory to save PDF files (default: current directory)
pdf_dir = .
; Directory to save Audio files (default: current directory)
audio_dir = .

[TTS]
engine = edge-tts
voice = zh-CN-XiaoxiaoNeural
rate = +0%
volume = +0%

[Network]
; Proxy mode: default (env/system), none, custom
proxy_mode = default
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
Generate a PDF file in the current directory.
```bash
uv run surf.py "https://example.com" -p
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

## Help

```bash
uv run surf.py --help
```
