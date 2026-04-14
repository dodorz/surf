# Surf - 网页转 Markdown/PDF/HTML/Audio 转换器

**Surf** 是一个强大的 Python 命令行工具，可将网页转换为整洁的 Markdown、PDF、HTML 文件或音频。它能处理动态内容，移除杂乱信息（广告、侧边栏），并根据需要使用大语言模型（LLM）翻译内容。

---

## 功能

- **智能抓取**：针对动态 JavaScript 网站，自动在 `requests` 和 `Playwright`（无头浏览器）之间切换。
- **特殊网站处理**：针对 Twitter/X、Bluesky、微博、Threads、微信公众号、知乎、小红书等网站优化处理，支持自动认证。
- **X/Twitter 提取增强**：默认优先使用 `uvx --from twitter-cli twitter` 并复用本机浏览器 Cookie，自动识别更多 X 登录引导占位文案变体、解析 `t.co` 跳转到真实 Article 链接，并将 `/<user>/article/<id>` 这类直链规范化为 `/i/article/<id>` 后再抓取；优先保留主 tweet/article 的 DOM，从而尽量保住粗体等行内样式和插图；仅在必要时再回退到结构化元数据提取；当 `x.com` 本身连不通时，会优先尝试基于 status id 的 syndication/fxTwitter 兜底；当 X 被登录墙拦截时会进一步回退到 `api.fxtwitter.com`。
- **同作者 Thread 追溯**：对 Twitter/X、Bluesky、微博、Threads，Surf 默认会向后抓取当前贴文之后、且作者仍与当前贴文相同的连续回帖；也可通过 `--thread forward|backward|both` 显式指定方向。
- **短帖子标题规范化**：对 Twitter/X、Bluesky、微博、Threads 这类短帖子，Surf 会将标题、front matter 中的 `title` 以及默认 Markdown 文件名统一生成为“第一句 - 作者名 on 站点”。
- **纯净提取**：使用 `readability` 仅提取主要文章内容。
- **多格式输出**：支持 Markdown、PDF、HTML 和音频。
- **自动翻译**：检测非中文内容并使用配置的 LLM（如 OpenAI, DeepSeek）自动翻译。支持**长文智能分段**翻译，避免上下文限制。
- **灵活代理**：通过 `config.ini` 配置代理设置（系统默认、自定义或不使用代理）。
- **TTS 支持**：使用 `edge-tts` 进行文本转语音。支持保存为音频文件或朗读。
- **认证管理**：支持需要登录的网站（如小红书）的交互式登录功能。
- **实验性插图 OCR**：可选地对文章插图执行本地 OCR。默认优先使用 RapidOCR，必要时回退到 Tesseract。小红书默认开启，其它网站需显式传 `--ocr-images` 或在 `[OCR].enabled = true` 中开启。

### 特殊网站策略

部分网站有默认策略，可通过命令行参数覆盖：

- **微信公众号 & 小红书**：默认不使用代理，不翻译（可用 `-x` 和 `-l` 覆盖）
- **小红书**：额外默认开启插图 OCR，可用 `--no-ocr-images` 关闭
- **Twitter/X**：默认使用等同于 `-x win` 的强制代理设置，并优先使用 `uvx --from twitter-cli twitter`；若需要保留 native 兜底可显式使用 `auto`
- **Twitter/X、Bluesky、微博、Threads**：默认开启 `backward` 方向的 thread 追溯；可用 `--thread forward|backward|both` 调整，或用 `--no-thread` 关闭
- **Twitter/X、Bluesky、微博、Threads**：短帖子标题和默认文件名统一使用“第一句 - 作者名 on 站点”
- **知乎**：默认不使用代理、默认不翻译；优先走知乎专用提取；若已执行 `surf --login zhihu`，保存的 Cookie 会用于 API/镜像页的 `requests`；避免再掉回通用抓取链路
- **GitHub**：保存 Markdown 时文件名使用页面 `<title>`

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

3.  **可选：安装图片 OCR 引擎**：
    `surf` 默认优先使用 Python 依赖中的 RapidOCR。如果您额外安装了本地 Tesseract，Surf 会在需要时自动回退，或者可通过 `--ocr-engine tesseract` 强制使用。请确保 `tesseract` 在 `PATH` 中，或在 `config.ini` 的 `[OCR].tesseract_cmd` 中指定路径。

## Web 界面

`surf_web.py` 提供的是一个基于 Flask 的本地 Web 界面，适合开发和个人使用。它内部调用的 `app.run(...)` 是 Flask 自带的开发服务器，所以你看到“不要用于生产部署”的提示是正常的。

本机访问时可以直接启动：

```bash
uv run python surf_web.py --host 127.0.0.1 --port 18473
```

如果要对外网或局域网正式部署，请改用真正的 WSGI 服务器，并让它加载 `surf_web:app`：

```bash
# Windows 上更适合的生产服务器
uv add waitress
uv run waitress-serve --listen=0.0.0.0:18473 surf_web:app

# Linux 上常见的生产服务器
uv add gunicorn
uv run gunicorn -w 2 -b 0.0.0.0:18473 surf_web:app
```

如果要暴露到公网，建议再前置反向代理并启用 HTTPS，同时只开放必要端口。

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

[Twitter]
; 后端选择: cli（优先 uvx --from twitter-cli twitter）, auto（先 CLI，再 native 回退）, native（仅 Surf 内置实现）
backend = cli
; 已弃用：Surf 固定通过 `uvx --from twitter-cli twitter` 调用 twitter-cli
cli_bin =
; 可选：twitter-cli 读取 Cookie 时优先使用的浏览器：chrome、edge、brave、firefox、arc
browser =
; 可选：twitter-cli 的浏览器 profile，例如 Default 或 Profile 2
profile =

[OCR]
; 默认是否对文章插图执行 OCR（默认 false；小红书站点会覆盖为 true）
enabled = false
; OCR 引擎：rapidocr（默认）、tesseract，或 auto（先 rapidocr 再 tesseract）
engine = rapidocr
; Tesseract 语言，例如：chi_sim+eng、eng、jpn+eng
; RapidOCR 不使用这个参数，仅在选择/回退到 Tesseract 时生效
lang = chi_sim+eng
; 可选：本地 tesseract 可执行文件路径
tesseract_cmd =
; 每篇文章最多 OCR 这么多张图
max_images = 8
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
surf "https://x.com/user/status/123" -t   # 默认抓取同作者 thread 后续回帖
surf "https://x.com/user/status/123" --thread forward
surf "https://x.com/user/status/123" --thread both
surf "https://bsky.app/profile/user.bsky.social/post/abc123" --no-thread
surf "https://example.com" --llm L2    # 指定 LLM 提供方
surf "https://example.com" --config myconfig.ini  # 指定配置文件
surf "https://example.com" --verbose   # 详细日志输出
surf --version                         # 查看版本
```

### 插图 OCR (--ocr-images / --no-ocr-images)

对文章插图执行本地 OCR，并把识别文本追加到图片下方：

```bash
surf "https://example.com/article" --ocr-images
surf "https://www.xiaohongshu.com/explore/..." --no-ocr-images
surf "https://example.com/article" --ocr-images --ocr-lang eng
surf "https://example.com/article" --ocr-images --ocr-engine tesseract --ocr-lang eng
```

说明：

- OCR 默认优先使用 `rapidocr-onnxruntime`。
- 如果 RapidOCR 不可用或没有产出可用文本，Surf 会自动回退到本地 Tesseract；如需强制使用，可传 `--ocr-engine tesseract`。
- `--ocr-lang` 仅对 Tesseract 生效。
- 小红书默认开启插图 OCR。
- OCR 某一张图片失败时只会跳过该图，不会中断整篇文章抓取。

### 认证功能 (--login / --clear-auth)

对于需要登录的网站（如小红书、Twitter/X、知乎），使用交互式登录功能：

```bash
# 首次登录小红书
surf --login xiaohongshu

# 可选：登录 Twitter/X（可提高登录墙页面抓取成功率）
surf --login twitter

# 优先使用 twitter-cli 和本机浏览器 Cookie
surf --twitter-backend cli "https://x.com/username/status/1234567890"
surf --twitter-backend cli --twitter-browser chrome "https://x.com/username/status/1234567890"

# 可选：登录知乎（Cookie 会用于 API/镜像页请求，并提高浏览器验证页成功率）
surf --login zhihu

# 登录后正常获取内容
surf "https://www.xiaohongshu.com/explore/..."
surf "https://www.xiaohongshu.com/discovery/item/..."
surf "https://x.com/username/status/1234567890"
surf "https://www.zhihu.com/question/349732913/answer/2008128917886751846"

# 清除保存的认证
surf --clear-auth xiaohongshu
surf --clear-auth twitter
surf --clear-auth zhihu
# 或清除所有网站的认证
surf --clear-auth all
```

**注意**：认证状态和应用数据保存在 Windows 的 `%LOCALLAPPDATA%\surf\` 或 Linux/macOS 的 `~/.local/cache/surf/` 目录中。
对于 Twitter/X，Surf 还会在认证目录下保存持久浏览器 profile，以提高登录墙场景的可用性。如果系统可用 `uvx`，默认后端会优先调用 `uvx --from twitter-cli twitter` 复用本机浏览器 Cookie，尽量避免先落到 Surf 现有的 Playwright/oEmbed 链路。Twitter 的强制代理默认等同于 `surf -x win`。

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
