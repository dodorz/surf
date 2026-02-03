# Surf - 网页转 Markdown/PDF/HTML/Audio 转换器

**Surf** 是一个强大的 Python 命令行工具，可将网页转换为整洁的 Markdown、PDF、HTML 文件或音频。它能处理动态内容，移除杂乱信息（广告、侧边栏），并根据需要使用大语言模型（LLM）翻译内容。

---

## 功能

- **智能抓取**：针对动态 JavaScript 网站，自动在 `requests` 和 `Playwright`（无头浏览器）之间切换。
- **特殊网站处理**：针对 Twitter/X、微信公众号、小红书等网站优化处理，支持自动认证。
- **纯净提取**：使用 `readability` 仅提取主要文章内容。
- **多格式输出**：支持 Markdown、PDF、HTML 和音频。
- **自动翻译**：检测非中文内容并使用配置的 LLM（如 OpenAI, DeepSeek）自动翻译。支持**长文智能分段**翻译，避免上下文限制。
- **灵活代理**：通过 `config.ini` 配置代理设置（系统默认、自定义或不使用代理）。
- **TTS 支持**：使用 `edge-tts` 进行文本转语音。支持保存为音频文件或朗读。
- **认证管理**：支持需要登录的网站（如小红书）的交互式登录功能。

### 特殊网站策略

部分网站有默认策略，可通过命令行参数覆盖：

- **微信公众号 & 小红书**：默认不使用代理，不翻译（可用 `-x` 和 `-l` 覆盖）
- **Twitter/X**：使用强制代理设置

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
; Markdown 文件保存目录 (默认: ./notes)
md_dir = ./notes
; 目标翻译语言
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

转换 URL 并将 Markdown 打印到控制台（默认翻译为中文）：

```bash
surf "https://example.com"
```

### 输出路径 (-o / -O)

使用 `-o` 或 `--output` 指定输出文件路径，或使用 `-O` 直接输出到控制台：

```bash
surf "https://example.com" -o output.md     # 保存为 output.md
surf "https://example.com" -o -              # 输出到 stdout
surf "https://example.com" -O               # 输出到 stdout (简写)
surf -p "https://example.com" -o out.pdf     # PDF 保存为 out.pdf
```

### 语言模式 (-l)

使用 `-l` 或快捷方式控制翻译模式：

```bash
surf "https://example.com"           # 翻译为中文（默认）
surf -r "https://example.com"        # 仅原文（不翻译）
surf -b "https://example.com"        # 双语对比（原文 + 译文）
```

### 文本转语音 (-s)

朗读内容（需要本地音频输出）：

```bash
surf "https://example.com" -s
```

### HTML 选项

保存 HTML 时可使用以下选项：

```bash
surf "https://example.com" -h          # 保存 HTML 文件
surf "https://example.com" -h --html-inline  # 保存 HTML 并内联 CSS/JS
```

### 代理设置 (-x)

使用 `-x` 或快捷方式设置代理模式：

```bash
surf "https://example.com" -x win      # 使用 Windows 代理
surf "https://example.com" -c --set-proxy http://127.0.0.1:7890  # 自定义代理
surf "https://example.com" -n          # 不使用代理
```

### 其他选项

```bash
surf "https://example.com" --browser   # 强制使用浏览器
surf "https://example.com" --llm L2    # 指定 LLM 提供方
surf "https://example.com" --config myconfig.ini  # 指定配置文件
surf "https://example.com" --verbose   # 详细日志输出
surf --version                         # 查看版本
```

### 认证功能 (--login / --clear-auth)

对于需要登录的网站（如小红书），使用交互式登录功能：

```bash
# 首次登录小红书
surf --login xiaohongshu

# 登录后正常获取内容
surf "https://www.xiaohongshu.com/explore/..."

# 清除保存的认证
surf --clear-auth xiaohongshu
# 或清除所有网站的认证
surf --clear-auth all
```

**注意**：认证状态保存在 `~/.surf/auth/` 目录中，供后续使用。

## 单字符参数连写

单字符参数可以连写，顺序任意：

```bash
# 以下命令是等效的：
surf -h -r -n "https://example.com"
surf -hrn "https://example.com"
surf -nrh "https://example.com"
```

## 帮助

```bash
surf --help
```
