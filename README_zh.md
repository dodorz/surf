# Surf - 网页转 Markdown/PDF 转换器

**Surf** 是一个强大的 Python 命令行工具，可将网页转换为整洁的 Markdown 或 PDF 文件。它能处理动态内容，移除杂乱信息（广告、侧边栏），并根据需要使用大语言模型（LLM）翻译内容。

---

## 功能

- **智能抓取**：针对动态 JavaScript 网站，自动在 `requests` 和 `Playwright`（无头浏览器）之间切换。
- **纯净提取**：使用 `readability` 仅提取主要文章内容。
- **Markdown & PDF**：将内容转换为标准 Markdown 或 PDF。
- **自动翻译**：检测非中文内容并使用配置的 LLM（如 OpenAI, DeepSeek）自动翻译。支持**长文智能分段**翻译，避免上下文限制。
- **笔记集成**：自动将文件保存到您指定的笔记文件夹。
- **TTS 支持**：使用 `edge-tts` 进行文本转语音。支持保存为音频文件或朗读。
- **灵活代理**：通过 `config.ini` 配置代理设置（系统默认、自定义或不使用代理）。

## 安装

我们推荐使用 `uv` 保持环境整洁。

1.  **安装 uv**:

    ```bash
    pip install uv
    # 或参考 https://github.com/astral-sh/uv
    ```

2.  **设置环境**:
    ```bash
    uv sync
    uv run playwright install
    ```

## 配置

复制 `config.ini.example` 为 `config.ini` 并编辑以设置您的 API 密钥和路径。

```bash
cp config.ini.example config.ini
```

```ini
[LLM]
; 默认 LLM 提供方名称
provider = L1

[LLM.L1]
; OpenAI 兼容 API 配置
base_url = https://api.openai.com/v1
api_key = your_api_key_here
model = gpt-3.5-turbo

[LLM.L2]
; 备用 LLM 提供方示例 (DeepSeek)
base_url = https://api.deepseek.com/v1
api_key = your_deepseek_api_key
model = deepseek-chat

[Output]
note_dir = ./notes
target_language = zh-cn
; PDF 文件保存目录 (默认: 当前目录)
pdf_dir = .
; 音频文件保存目录 (默认: 当前目录)
audio_dir = .
; HTML 文件保存目录 (默认: 当前目录)
html_dir = .

[TTS]
engine = edge-tts
voice = zh-CN-XiaoxiaoNeural
rate = +0%
volume = +0%

[Network]
; 代理模式: auto (环境变量), no (不使用), win (Windows注册表), custom (自定义地址)
proxy_mode = auto
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

生成 PDF 文件。使用 Playwright 作为唯一引擎：

- **playwright**: 非常可靠，使用已安装的浏览器引擎。

```bash
uv run surf.py "https://example.com" -p
```

### 保存 HTML (--html / --html-inline)

保存为 HTML 文件：

- `--html`: 保存原始 HTML
- `--html-inline`: 将外部 CSS/JS 资源内联化，生成自包含的 HTML 文件

```bash
# 保存 HTML 文件
uv run surf.py "https://example.com" --html

# 保存 HTML 并内联 CSS/JS（生成自包含文件）
uv run surf.py "https://example.com" --html-inline
```

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
