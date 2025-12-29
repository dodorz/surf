# Surf - 网页转 Markdown/PDF 转换器

**Surf** 是一个强大的 Python 命令行工具，可将网页转换为整洁的 Markdown 或 PDF 文件。它能处理动态内容，移除杂乱信息（广告、侧边栏），并根据需要使用大语言模型（LLM）翻译内容。

---

## 功能

*   **智能抓取**：针对动态 JavaScript 网站，自动在 `requests` 和 `Playwright`（无头浏览器）之间切换。
*   **纯净提取**：使用 `readability` 仅提取主要文章内容。
*   **Markdown & PDF**：将内容转换为标准 Markdown 或 PDF。
*   **自动翻译**：检测非中文内容并使用配置的 LLM（如 OpenAI, DeepSeek）自动翻译。支持**长文智能分段**翻译，避免上下文限制。
*   **笔记集成**：自动将文件保存到您指定的笔记文件夹。
*   **TTS 支持**：使用 `edge-tts` 进行文本转语音。支持保存为音频文件或朗读。
*   **灵活代理**：通过 `config.ini` 配置代理设置（系统默认、自定义或不使用代理）。

## 安装

我们推荐使用 `uv` 保持环境整洁。

1.  **安装 uv**:
    ```bash
    pip install uv
    # 或参考 https://github.com/astral-sh/uv
    ```

2.  **设置环境**:
    ```bash
    uv venv
    uv pip install -r requirements.txt
    uv run playwright install
    ```

## 配置

复制 `config.ini.example` 为 `config.ini` 并编辑以设置您的 API 密钥和路径。

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
; PDF 文件保存目录 (默认: 当前目录)
pdf_dir = .
; 音频文件保存目录 (默认: 当前目录)
audio_dir = .

[TTS]
engine = edge-tts
voice = zh-CN-XiaoxiaoNeural
rate = +0%
volume = +0%

[Network]
; 代理模式: default (使用系统/环境代理), none (不使用), custom (使用自定义地址)
proxy_mode = default
; 自定义代理地址 (例如 http://127.0.0.1:7890)
custom_proxy =
```

## 用法

### 基础
转换 URL 并将 Markdown 打印到控制台。
```bash
uv run surf.py "https://example.com"
```

### 保存笔记 (-n)
保存到配置的 `note_dir`。
```bash
uv run surf.py "https://example.com" -n
```

### 翻译模式
默认情况下，内容会被翻译为目标语言。使用 `--trans-mode` 或快捷参数更改此行为：
- `-o`, `--original`: 不进行翻译（仅原文）。
- `translated`: 仅保留译文（默认）。
- `-b`, `--both`: 双语对比（原文 + 译文）。

```bash
# 输出双语内容
uv run surf.py "https://example.com" -b

# 仅输出原文（不调用翻译）
uv run surf.py "https://example.com" -o
```

### 生成 PDF (-p)
生成 PDF 文件。您可以在 `config.ini` 中选择引擎：
*   **playwright** (默认): 非常可靠，使用已安装的浏览器引擎。
*   **weasyprint**: 高质量，Windows 上需要安装 GTK3。
*   **pandoc**: 需要系统中安装有 `pandoc` 和 `pdflatex`（或同类工具）。
*   **wkhtmltopdf**: 需要系统中安装有 `wkhtmltopdf` 二进制文件并加入 PATH。

```bash
uv run surf.py "https://example.com" -p
```

> **Windows 用户注意**：推荐使用默认的 **playwright** 引擎，因为它不需要安装像 GTK3 这样的外部系统库。如果您更倾向于使用 **weasyprint**，则必须从 [此处](https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases) 下载并安装 **GTK3 Runtime**。

### 文本转语音 (-a / -s)
将内容保存为 MP3 音频文件：
```bash
uv run surf.py "https://example.com" -a
```

朗读内容（需要本地音频输出）：
```bash
uv run surf.py "https://example.com" -s
```

### 强制浏览器
强制使用 Playwright（对复杂网站有用）。
```bash
uv run surf.py "https://example.com" --browser
```

## 帮助

```bash
uv run surf.py --help
```
