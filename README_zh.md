# Surf

**一个链接，一份干净的文档。**

Surf 把一个网页、一个本地文件或一句搜索词，变成一份去掉噪音、按需翻译的文档：Markdown、HTML、PDF、音频，或者一个 Pastebin 帖子。同一套流水线有两个前端——给脚本用的命令行，和给交互操作用的本地网页。

```
输入 ─▶ 抓取 ─▶ 正文提取 ─▶ (OCR) ─▶ 翻译 ─▶ 渲染 ─▶ 保存
```

- **一条命令就是一份文档。** `surf https://example.com` 直接写出 Markdown 笔记。
- **静态 HTML 到重 JavaScript 页面。** 默认走普通 HTTP，需要时自动（或手动）启用无头浏览器（Obscura，或 Playwright）。
- **真正的翻译。** 检测语言，调用兼容 OpenAI 的 LLM 翻译，并把实际发生了什么写进 front matter。
- **一套流程五种产物。** Markdown、HTML、PDF、音频、Pastebin，PDF 甚至可以不依赖浏览器。
- **认识站点。** Twitter/X、Reddit、Bluesky、微博、Threads、V2EX、微信公众号、知乎、小红书、NCPSSD、Pocket Casts、小宇宙、GitHub、Wikipedia、Foreign Affairs。
- **失败不会凭空消失。** 抓不下来的 URL 会写到磁盘，之后可以重试。

---

## 安装

Surf 是一个 Python 包，用 [uv](https://github.com/astral-sh/uv) 运行最省心。

```bash
git clone https://github.com/dodorz/surf.git && cd surf
uv sync
```

这会装上核心依赖，包含 OCR 引擎（PaddleOCR、RapidOCR、pytesseract；Tesseract 二进制本身属于系统包）。

只有确实需要时才装可选 extra：

| Extra | 提供 | 安装 |
| --- | --- | --- |
| `browser` | Playwright：交互式登录、特殊站点页面自动化、NCPSSD 全文 PDF、Playwright PDF 引擎 | `uv sync --extra browser && uv run playwright install chromium` |
| `pdf` | WeasyPrint：不依赖浏览器的 PDF 渲染 | `uv sync --extra pdf` |
| `transcribe` | 通过 transcribe.cpp 在本地转写播客 | `uv sync --extra transcribe` |
| `curlcffi` | 模拟浏览器 TLS 指纹，绕过部分 Cloudflare 拦截 | `uv sync --extra curlcffi` |

Surf 默认的无头后端是 **Obscura**，它是一个独立二进制。请从
[Obscura Releases](https://github.com/h4ckf0r0day/obscura/releases) 安装并确保它在 `PATH` 中（或设置 `[Browser] obscura_executable`）。Surf 会按需启动并复用受管理的 `obscura serve` 进程，通过 Chrome DevTools Protocol 通信，因此普通动态页面不需要安装 Playwright。

然后准备配置并至少填一个 LLM provider：

```bash
cp config.ini.example config.ini
```

Surf 先找脚本同目录的 `config.ini`，找不到再回退到 `$XDG_CONFIG_HOME/surf/config.ini`（默认 `~/.config/surf/config.ini`）。

---

## 快速开始

```bash
uv run surf "https://example.com"                  # Markdown，翻译为中文
uv run surf "https://example.com" -r               # 保留原文
uv run surf "https://example.com" -b               # 双语对照
uv run surf "https://example.com" -h               # HTML
uv run surf "https://example.com" -p               # PDF
uv run surf "https://example.com" -a               # 文本转语音音频
uv run surf "https://example.com" -P               # 发布到 Pastebin
uv run surf "https://example.com" -o ~/note.md     # 指定输出路径
uv run surf "https://example.com" -O               # 打印到 stdout
```

短参数可以连写：`surf -hrn URL` 等价于 `-h -r -n`（HTML、保留原文、不使用代理）。

Web 界面跑的是同一条流水线：

```bash
uv run python surf_web.py                # http://127.0.0.1:18473
```

---

## 输入

Surf 只有一个位置参数，它是什么由内容本身决定。

### 链接

```bash
uv run surf "https://example.com/article"
```

参数是带有 URL 的说明文字（比如小红书分享文案）时，会提取其中第一个 `http(s)` URL。常见短链（`t.co`、`bit.ly`、`tinyurl.com`、`xhslink.com`、`xhslink.cn`、`pca.st/episode/...`）会先解析成最终地址，front matter 里记录的也是解析后的地址。

命令行一次只处理一个 URL；批量请用 shell 循环或 `-S/--search`。Web 界面可以一次输入多个 URL，并为每个 URL 生成一张卡片。

完全没有 URL 的纯文字只在 Web 界面里有意义：它会被当成一篇帖子保存，第一句作为标题，这是随手记笔记的常规方式。命令行要求输入 URL、本地路径，或者配合 `-S` 的关键词。

### 本地文件

本地 HTML、Markdown、文本和 PDF 文件与网页走同一条流水线。

```bash
uv run surf /tmp/article.html          # 操作系统路径
uv run surf ~/docs/readme.md           # 和其它文档一样会被翻译
uv run surf file:///tmp/article.html   # file:// URI
uv run surf "C:\Temp\notes.txt"        # Windows 路径
```

识别类型：`.html`、`.htm`、`.md`、`.txt`、`.rst`、`.adoc`、`.pdf`，其它一律按纯文本读取。在 Windows 上，参数和配置中的 Unix 风格路径会被接受并归一化（`~/Note/x.md` 解析到 `%USERPROFILE%` 下）。

### PDF

输入指向 PDF 时（URL、`file://` 或本地路径皆可），由输出格式决定行为：

```bash
uv run surf "https://example.com/paper.pdf"        # 提取文本 → Markdown
uv run surf "https://example.com/paper.pdf" -h     # 提取文本 → HTML
uv run surf "https://example.com/paper.pdf" -p     # 原样保留 PDF，逐字节一致
```

判断依据是 `Content-Type`、响应体与 `Content-Disposition`，所以 URL 不必以 `.pdf` 结尾。文本提取使用 `pypdf`；扫描版 PDF 不做 OCR，需要时用 `-p` 保留原文件。若 PDF 站点已失效，Surf 会向 Wayback Machine 查询快照并使用归档的原始 PDF，同时把快照写入 front matter 的 `archive` 字段。

### 搜索词

配合 `-S/--search` 时，参数是查询词而不是 URL：

```bash
uv run surf -S "python asyncio 教程"
```

Surf 先搜索，打印带编号的「标题 + URL」列表，只抓取你选中的条目。详见[关键词搜索](#关键词搜索)。

### 社交线程

```bash
uv run surf -t               "https://x.com/user/status/123"   # 后续帖文（默认方向）
uv run surf --thread before  "https://x.com/user/status/123"
uv run surf --thread both --thread-author same "https://x.com/user/status/123"
uv run surf --no-thread "https://bsky.app/profile/user.bsky.social/post/abc123"
```

Twitter/X、Bluesky、微博、Threads 默认展开线程（`after`，所有作者）。V2EX 默认只保存主贴，只有传 `-t/--thread` 才包含回帖。

---

## 输出

`-f/--format` 决定输出类型，单字母参数是它的简写。

| 格式 | 参数 | 简写 |
| --- | --- | --- |
| Markdown | `-f md` | 默认 |
| HTML | `-f html` | `-h` |
| PDF | `-f pdf` | `-p` |
| 音频 | `-f audio` | `-a` |
| Pastebin | `-f publish` | `-P` |

### Markdown

默认格式。文件写入 `[Output] md_dir`，以标题命名，并带一段 YAML front matter，记录 `title`、`source`、`created`、`description`，以及——仅在翻译确实改动了内容时——`translator`。

```bash
uv run surf "https://example.com" --no-front-matter   # 不写 front matter
uv run surf "https://example.com" --archive           # 同时归档原始链接
```

标题作为文件名时保留中文标点和合法符号，只过滤文件系统不允许的字符。`--archive` 会把最终的 `source` URL 提交到 Internet Archive，并把快照写进 front matter；默认值来自 `[Output] archive`。

### HTML

```bash
uv run surf "https://example.com" -h
uv run surf "https://example.com" -h --html-inline     # 自包含单文件
```

`--html-inline` 会把外部 CSS/JS 内联进文件，离线也能独立打开（默认值由 `[Output] html_inline` 控制）。

### PDF

PDF 由文档的 HTML 渲染而来，因此并不强制需要浏览器。`[Output] pdf_engine` 引擎链按顺序尝试：

| 引擎 | 使用什么 | 依赖 |
| --- | --- | --- |
| `playwright` | 通过 Playwright 调用已安装的 Chromium | `--extra browser` |
| `chrome` | 本机 `google-chrome` / `chromium` / Edge 的 `--headless=new --print-to-pdf` | 一个 Chrome 二进制 |
| `weasyprint` | 纯 Python 的 HTML/CSS 渲染器，远程图片会下载后内嵌 | `--extra pdf`、系统 Pango |
| `typst` | 把 HTML 转成 Typst 标记后调用 `typst` | `typst` 二进制 |
| `pandoc` | 把 HTML 交给 `pandoc`，由它驱动 typst/weasyprint/xelatex… | `pandoc` 加一个引擎 |

```ini
[Output]
pdf_engine = auto          ; playwright, chrome, weasyprint, typst, pandoc
typst_executable = typst   ; 环境变量: SURF_TYPST_EXECUTABLE
typst_fonts =              ; 追加字体目录，逗号或 ':' 分隔
pandoc_executable = pandoc ; 环境变量: SURF_PANDOC_EXECUTABLE
pandoc_pdf_engine =        ; 留空 = 自动探测
pandoc_args =              ; 例如 --toc -V CJKmainfont="Noto Serif CJK SC"
```

引擎可用逗号列表挑选或排序，例如 `pdf_engine = typst,weasyprint`。Chrome 会自动探测，也可以用 `[Browser] chrome_executable` / `SURF_CHROME_EXECUTABLE` 指定。CommonMark 表格与围栏代码块可以正确渲染；中文只要系统装了任一 CJK 字体即可。

### 音频与朗读

```bash
uv run surf "https://example.com" -a                              # 保存 MP3
uv run surf "https://example.com" -s                              # 直接朗读
uv run surf "https://example.com" -a --tts-voice en-US-AriaNeural
uv run surf "https://example.com" -a --tts-rate +10% --tts-volume -20%
```

语音由 `edge-tts` 提供；语音、语速、音量默认取 `[TTS]` 小节，可按次覆盖。对 Pocket Casts 和小宇宙剧集，`-a` 改为下载原始音频（保留 `.m4a` 等源扩展名），受 `[Transcription] max_audio_mb` 与 `audio_timeout` 限制。

### Pastebin

```bash
uv run surf "https://example.com" -P
```

发布不写本地文件，成功后打印 paste 链接。需要在 `[publish.pastebin] api_dev_key` 填好 key；`api_paste_expire_date` 和 `api_paste_private` 可选。

### 决定存到哪里

```bash
uv run surf "https://example.com" -o output.md      # 指定文件
uv run surf "https://example.com" -o ~/Note/out.md  # ~ 在 Windows 上同样有效
uv run surf "https://example.com" -o -              # 标准输出
uv run surf "https://example.com" -O                # 等价于 -o -
uv run surf -p "https://example.com" -o out.pdf
```

`-o` 会覆盖配置目录。不给 `-o` 时，各格式分别使用 `md_dir`、`html_dir`、`pdf_dir`、`audio_dir`。

---

## 翻译

```bash
uv run surf "https://example.com"          # -l trans（默认）
uv run surf "https://example.com" -r       # -l raw，不翻译
uv run surf "https://example.com" -b       # -l both，原文 + 译文
uv run surf "https://example.com" --llm l2 # 换一个已配置的 provider
```

只有当检测到的语言与 `[Output] target_language` 不同时才会翻译；长文会切分成段以适配模型上下文。只有正文或标题真的被改写时，front matter 才写入 `translator`；单纯的语言判断不算翻译。

`title` 和 `description` 会被压成单行——换行、制表符、不换行空格与连续空白合并，中文字符之间的空格去掉——保证 YAML 始终有效。`title` 末尾的句号会被删除；GitHub 自动生成的 "Contribute to … development by creating an account on GitHub." 会在翻译前从 `title` 和 `description` 中移除，避免译文里出现被翻译过的这句话。

---

## 抓取

### 代理

```bash
uv run surf "https://example.com" -x env               # http_proxy / https_proxy / no_proxy
uv run surf "https://example.com" -x win               # Windows Internet Settings（仅 Windows）
uv run surf "https://example.com" -c http://127.0.0.1:7890
uv run surf "https://example.com" -x custom --set-proxy http://127.0.0.1:7890
uv run surf "https://example.com" -n                   # 直连
```

模式有 `env`、`win`、`custom`、`no`；`-c` 就是带可选内联地址的 `-x custom`，`-n` 就是 `-x no`。不传参数时，CLI 会隐式解析代理——这在很多站点都需要本地代理的 Windows 桌面上是合理的。如果隐式代理因代理/TLS 握手失败，Surf 会先不带代理重试一次，再回退到浏览器；显式指定的模式绝不会被悄悄改掉。

[Web 界面](#web-界面)则默认 `no`，因为它通常跑在公网可直连的服务器上。

### 正文提取

```bash
uv run surf "https://example.com" -e trafilatura   # -T
uv run surf "https://example.com" -e readability   # -R
uv run surf "https://example.com" -e raw           # -U，保留原始 HTML
```

`auto`（默认，`[Content] extractor`）先试 Readability，失败再回退 Trafilatura，两者都会去掉广告、侧边栏和导航。带内容语义的内嵌 SVG 图表会转成 Markdown 图片引用，装饰性图标会被丢弃。

### 无头浏览器

```bash
uv run surf "https://example.com" --browser
uv run surf "https://example.com" -B playwright
uv run surf "https://example.com" -B obscura
uv run surf "https://example.com" -B auto          # 跟随 [Browser] backend
```

`--browser` 强制用无头浏览器渲染而不是普通 HTTP，`-B/--browser-backend` 选择用哪一个。Obscura 是默认后端，不需要任何 Python extra：

- `[Browser] obscura_fetch = auto` 走原生 CDP，协议出错时回退 Playwright 客户端；`native` 从不回退；`playwright` 使用旧版客户端。
- 配置的 CDP endpoint 若已在运行，会被视为外部服务：只连接，不启动也不停止。
- Web 服务器不同线程会协调访问；代理变化时，受管理的 Obscura 服务会被安全替换。
- Bloomberg 系列站点即使 `obscura_stealth = false` 也会强制开启 Obscura 隐身模式，因为默认无头配置过不了 PerimeterX。
- 知乎是例外：它的浏览器兜底始终打开可见的 Playwright 窗口，因为 `zse-ck` 挑战在无头会话中无法通过，Linux 上需要图形会话。

如果根本无法启动有头浏览器，archive.today 的 CAPTCHA 兜底现在会快速失败，而不是启动一个注定失败的浏览器。

### 插图 OCR

```bash
uv run surf "https://example.com/article" --ocr
uv run surf "https://example.com/article" --ocr --ocr-engine tesseract --ocr-lang eng
uv run surf "https://www.xiaohongshu.com/explore/..." --no-ocr
uv run surf --ocr /path/to/image.png                 # 直接对本地图片 OCR
```

OCR 会把识别文本附在每张插图下方。默认引擎是 `auto`（PaddleOCR → RapidOCR → Tesseract）；`--ocr-engine` 可强制其中一个——即使选 `rapidocr`，Tesseract 仍是回退项。`--ocr-lang` 只对 Tesseract 生效。小红书默认开启 OCR，其它站点需要 `--ocr` 或 `[OCR] enabled = true`。单张图片失败只会跳过它，不会中断抓取；引擎在短生命周期子进程中运行，内存不会被 Web worker 长期占住。

---

## 播客与转写

Pocket Casts 和小宇宙的剧集链接会解析出 Show Notes、发布时间、时长与音频地址：

```bash
uv run surf "https://pca.st/episode/ef99d8d4-a16c-48c3-a844-9a88163450d3" -r
uv run surf "https://www.xiaoyuzhoufm.com/episode/69018660740634ca4785c3b8" -a
```

front matter 的标题是 `剧集标题 - 播客名称`，默认文件名带 `[播客]` 前缀。Show Notes 输出在 `## Show Notes` 下；能取到日期时写入 `created`。即使 Show Notes 被翻译，播客元信息标签也保持不变。若剧集页面被拦截，分享链接里的基本信息仍会被保留。

本地转写需要可选后端和 GGUF 模型：

```bash
uv sync --extra transcribe
uv run surf "https://pca.st/episode/..." -w
uv run surf "https://pca.st/episode/..." -w --transcript-timestamps segment
```

```ini
[Transcription]
backend = auto
model_path = /path/to/whisper-small-Q8_0.gguf
language = auto
timestamps = none        ; none = 纯段落，segment = 每行 [HH:MM:SS]
ffmpeg_path = ffmpeg
max_audio_mb = 2048
audio_timeout = 120
```

转写文本遵循当前语言模式；Show Notes 与转写稿分别独立翻译。

---

## 特殊站点

Surf 内置 Twitter/X、Reddit、Bluesky、微博、Threads、V2EX、微信公众号、知乎、小红书、NCPSSD、Pocket Casts、小宇宙、GitHub、Wikipedia 和 Foreign Affairs 的处理器。要点：

站点默认值也是同一套机制的一部分：微信、知乎、小红书、NCPSSD、V2EX、BBC 默认不使用代理、不翻译（小红书还默认开启插图 OCR），这些默认值都可以被显式参数覆盖。

- **Twitter/X** 优先 `uvx --from twitter-cli twitter` 并复用本机浏览器 Cookie，解析 `t.co` 链接，把 `/user/article/<id>` 规范化为 `/i/article/<id>`，尽量保留主 tweet 的 DOM 以留住行内样式与媒体，X 本身连不通时依次回退 syndication、fxTwitter 和 `api.fxtwitter.com`。
- **短帖子**（Twitter/X、Bluesky、微博、Threads）标题为 `第一句 - 作者名 on 站点`；长文保留文章自身标题。
- **Reddit** 读 JSON 评论端点并复用已保存 Cookie；只有显式传 `--thread after|both` 或 `-t` 才包含回复。
- **小红书** 先用已保存 Cookie 抓服务端渲染的笔记页，失败才回退浏览器；登录态过期与风控响应会作为错误报告，而不会被当作正文输出。
- **GitHub** 从解析后的 README/blob 文件取内容，但 front matter 的 `source` 保留你输入的页面 URL；Markdown 里内嵌的 HTML 片段会转回 Markdown，围栏代码示例原样保留。
- **Foreign Affairs** 会移除分享/引用弹窗和订阅推销，避免可免费阅读的文章被误判为付费墙。
- **付费墙** 触发归档优先的回退：先试 `archive.today/newest/<url>` 的普通 HTTP 跳转，再查 Wayback availability API 并下载无工具栏的 `id_` 文档，最后通过浏览器依次尝试 `archive.ph` / `archive.is` / `archive.fo` / …，仍失败则打开可见窗口等待人工过 CAPTCHA。查询快照时会去掉跟踪参数与 fragment；若 Readability 只抓到摘要，还会再跑一次 Trafilatura。

完整的匹配规则、处理动作和维护要求见 `SPECIAL_SITES_zh.md`（中文）与 `SPECIAL_SITES.md`（English），请与代码保持同步。

---

## 认证

有些站点需要登录。Surf 按站点保存一份 Playwright 登录态并复用。

```bash
uv run surf --login xiaohongshu
uv run surf --export-auth xiaohongshu ~/Note/xhs_state.json   # 搬到另一台机器
uv run surf --import-auth xiaohongshu ~/Note/xhs_state.json
uv run surf --clear-auth xiaohongshu
uv run surf --clear-auth all
```

支持的站点：`xiaohongshu`、`twitter`（别名 `x`）、`reddit`、`zhihu`、`douban`、`ncpssd`。登录态在 Windows 上位于 `%LOCALLAPPDATA%\surf\`，在 Linux/macOS 上位于 `~/.local/cache/surf/`。

`--login` 需要图形会话。在无头 Linux 上它会快速失败而不是尝试打开浏览器——请在桌面机器登录，再用 `--export-auth` / `--import-auth` 迁移登录态。

各站点说明：

- **Twitter/X** 先通过 `twitter-cli` 从真实浏览器（或 `TWITTER_AUTH_TOKEN` / `TWITTER_CT0`）导入 Cookie，这通常比 X 的自动登录页可靠；不可用时才打开可见的 Playwright 窗口并保留持久 profile。
- **Twitter/X** 还接受 `--twitter-backend auto|cli|native`、`--twitter-browser`、`--twitter-profile` 控制 Cookie 导入；`--twitter-cli-bin` 已废弃并被忽略。
- **知乎** 的 Cookie 会用于 API 与镜像请求，并帮助通过浏览器验证。
- **NCPSSD** 的部分安全全文 PDF 下载必须登录。

### Cloudflare Cookie

被 Cloudflare WAF 拦截的站点，可以复用自己浏览器里的 `cf_clearance`：

1. 用 Chrome 打开目标站点并通过验证。
2. DevTools → Application → Cookies → 选中该域名。
3. 复制 `cf_clearance` 的值。

```bash
uv run surf --set-cf-clearance example.com "cf_clearance=VALUE"
uv run surf --clear-cf-clearance example.com
uv run surf --clear-cf-clearance all
```

也可以写进配置：

```ini
[Cloudflare]
example.com = cf_clearance=VALUE
```

Cookie 必须来自 Chrome 或其它 Chromium 内核浏览器——Surf 模拟的是 Chrome 的 TLS 指纹，Firefox 和 Safari 的 Cookie 不会生效。这类 Cookie 通常在 30 分钟到 24 小时内失效，过期后由常规的挑战处理流程接管。

---

## 关键词搜索

```bash
uv run surf -S "python asyncio 教程"                        # 交互选择
uv run surf -S "python asyncio 教程" --search-top 3         # 前 3 条，不询问
uv run surf -S "python asyncio 教程" --feel-lucky           # 只要第一条
uv run surf -S "python asyncio 教程" --search-merge         # 合并成一个文档
uv run surf -S "python asyncio 教程" --search-prefix asyncio
uv run surf -S "python asyncio 教程" --search-preview 3 --search-delay 2
```

`-S` 把位置参数变成关键词。Surf 先搜索，把前 `--search-preview` 条结果打印成编号列表，再让选中的 URL 走完整的 抓取 → 提取 → 翻译 → 保存 流程。单条失败只记日志并跳过，不会中断整批。

**引擎** —— `--search-engine auto|duckduckgo|bing|searxng|metaso|deepseek`（默认取 `[Search] engine`，即 `auto`）。`auto` 依次尝试 DuckDuckGo、Bing，再尝试已配置的 DeepSeek/SearXNG/Metaso API。

```ini
[Search]
engine = auto
limit = 5             ; 每次获取的结果数，1-20
preview = 5           ; 交互列表显示的行数
searxng_url =         ; 已开启 JSON API 的自建实例
metaso_api_key =      ; https://metaso.cn 开放 API
deepseek_api_key =    ; POST https://api.deepseek.com/v1/tools/search
deepseek_url =        ; 可选接口覆盖
```

DeepSeek 搜索支持 `site:`、`lang:`、`after:`、`before:`、精确短语和排除语法。

**选择** —— 输入 `1`、`1,3-5`、`1 3 5` 或 `all`；空行表示取消。非法输入最多重试三次。stdin 不是终端时（脚本、CI）会处理全部已获取结果；脚本里建议用 `--search-top N` 或 `--feel-lucky` 保证行为确定。

**输出** —— 默认每条结果一个文件，`--search-merge` 合并成一个文档。不会创建搜索专用目录；文件进常规 `[Output]` 目录，名字固定为 `[<前缀>]<标题>`。前缀默认取关键词（超长截到 40 字符），`--search-prefix` 可覆盖，末尾分隔符会被规范化，已经带中括号的前缀不会重复包裹。搜索模式下 `-o/--output` 只接受 `-`（标准输出）。每个文件都会在 front matter 记录 `search_query`，结果 URL 在抓取前以及写入 front matter `source` 时会去掉 `utm_*`、`fbclid`、`gclid`、`dclid`、`spm` 等跟踪参数。

其它选项（`-l`、`-p`、`-h`、`-a`、`-e`、代理相关、`--archive` 等）对每条结果均生效。

---

## Web 界面

```bash
uv run python surf_web.py                              # http://127.0.0.1:18473
uv run python surf_web.py --port 8080
uv run python surf_web.py --host 0.0.0.0 --root /surf
```

整个页面就是一屏：顶部输入区，下面可折叠的选项面板，再往下是结果卡片。输入需要的东西都直接可见；每次运行才需要的偏好都在可折叠面板里，面板标题会实时汇总当前选择（语言 · 代理 · OCR · 格式）。

### 页面能做什么

- **两种输入模式。** *链接 / 文本* 抓取 URL，或把粘贴的文字当作帖子保存；*关键词搜索* 走和 `-S` 相同的搜索流程，列出可勾选的结果，只抓你要的。
- **提取，或直接保存。** *提取* 生成预览卡片；*直接保存* 跳过预览，把文件放进后台队列。
- **全文模式。** 勾选 *全文保存* 后，即使文字里含 URL 也会整段保留。
- **每份文档一张卡片。** 卡片上有可编辑的保存目录和文件名，然后是格式按钮（MD / HTML / PDF / 音频 / Pastebin），同一份结果不用重新抓取就能导出成任意格式。*朗读* 按钮当场生成语音。
- **三个预览页签。** Markdown、HTML 和原始提取文本并排。
- **合并卡片。** 一次运行产生多份结果时，会多出一张卡片把它们合并成一个 Markdown/HTML/PDF/音频文档。
- **实时翻译。** 开启翻译时卡片先显示原文，后台翻译任务完成后原地刷新。
- **保存队列。** 后台保存任务列出状态（排队中 / 保存中 / 已完成 / 失败），顶栏有任务计数；队列清空后面板自动隐藏。
- **站点感知默认值。** 输入 URL 会应用该站点默认值——默认保留原文的站点会隐藏 LLM provider，直达你手动选择翻译模式；有效 OCR 默认为关闭时隐藏引擎选择；播客转写开关只在 Pocket Casts 和小宇宙剧集 URL 下出现。
- **代理默认值。** Web 界面默认 `no` 代理（见[代理](#代理)），部署确实需要时再切换。
- **键盘。** `Ctrl`/`Cmd` + `Enter` 提交输入区。

GitHub 仓库页和 Markdown 文件 URL 在 Web 界面上使用普通语言默认值：非中文内容默认翻译为中文，除非你选择原文模式。

### 部署

内置服务器是 Flask 的开发服务器，它提示不要用于生产是正常的。需要长期运行时，请让 WSGI 服务器加载 `surf_web:app`：

```bash
uv run waitress-serve --listen=0.0.0.0:18473 --threads=4 surf_web:app
```

waitress 在内存里只保留一份应用（线程池而非多进程 worker）。这在小型主机上很关键：每个 gunicorn worker 都要各付一份约 70 MB 的 Python 基线内存。

如果暴露到公网，请在前面加一层带 HTTPS 的反向代理，并只开放必要端口。

### 反向代理后面运行

需要在路径前缀下提供服务（`http://IP:8000/surf`）时，使用 `--root`：

```bash
uv run python surf_web.py --host 127.0.0.1 --port 18473 --root /surf
```

```nginx
location /surf/ {
    proxy_pass http://127.0.0.1:18473/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

`proxy_pass` 末尾的 `/` 会在转发前剥掉前缀（`/surf/api/process` → `/api/process`），这种情况下不需要 `--root`。如果 `proxy_pass` 末尾没有 `/` 且路径原样转发，则必须 `--root /surf`。

---

## 配置

`config.ini.example` 记录了全部可选项，最常改的是这些：

```ini
[LLM]
provider = LLM1                     ; 默认 provider 名称

[LLM.LLM1]
base_url = https://api.openai.com/v1
api_key = your_api_key_here
model = gpt-3.5-turbo

[Output]
target_language = zh-cn             ; 翻译目标语言
md_dir = ./notes
pdf_dir = ./pdf
html_dir = ./web
audio_dir = ./audio
pdf_engine = auto                   ; playwright, chrome, weasyprint, typst, pandoc
archive = false                     ; --archive 的默认值
no_front_matter = false             ; --no-front-matter 的默认值
html_inline = false                 ; --html-inline 的默认值

[Content]
extractor = auto                    ; auto, readability, trafilatura, raw

[Network]
proxy_mode = env                    ; env, win, custom, no
custom_proxy =

[TTS]
voice = zh-CN-XiaoxiaoNeural
rate = +0%
volume = +0%

[Browser]
backend = obscura                   ; obscura 或 playwright
obscura_executable = obscura
obscura_endpoint = http://127.0.0.1:9222
obscura_fetch = auto                ; auto, native, playwright
obscura_stealth = false
obscura_navigation_timeout = 60
chrome_executable =                 ; 无 Playwright 时的 PDF 回退
curlcffi_mode = auto                ; false, true, auto

[OCR]
enabled = false                     ; 小红书会覆盖为 true
engine = auto                       ; auto, paddleocr, rapidocr, tesseract
lang = chi_sim+eng
max_images = 8

[publish.pastebin]
api_dev_key = your_pastebin_api_key_here
```

`SURF_LOG_LEVEL`（例如 `SURF_LOG_LEVEL=INFO`）可提高长期运行时的日志详细度；单次 CLI 运行可用 `--verbose`。默认只输出 WARNING 级日志。

---

## 命令行参考

按 `--help` 的分组排列。

**输入**

| 参数 | 含义 |
| --- | --- |
| `URL \| PATH \| KEYWORDS` | 唯一的位置参数。认证类命令不需要它。 |

**输出**

| 参数 | 含义 |
| --- | --- |
| `-f, --format {md,pdf,html,audio,publish}` | 输出格式（默认 `md`） |
| `-h` `-p` `-a` `-P` | `-f html/pdf/audio/publish` 的简写 |
| `-o, --output PATH` | 输出路径，`-` 表示标准输出 |
| `-O` | 等价于 `-o -` |
| `--html-inline` | HTML 中内联 CSS/JS |
| `--no-front-matter` | 不写 YAML front matter |
| `--archive` | 把源链接提交到 Wayback Machine |

**语言与音频**

| 参数 | 含义 |
| --- | --- |
| `-l, --lang {trans,raw,both}` | 翻译模式（默认 `trans`） |
| `-r` `-b` | `-l raw` / `-l both` 的简写 |
| `--llm PROVIDER` | 本次运行使用的 LLM provider |
| `-s, --speak` | 朗读结果 |
| `--tts-voice` `--tts-rate` `--tts-volume` | 覆盖本次运行的 `[TTS]` |
| `-w, --transcribe` | 本地转写播客剧集 |
| `--transcript-timestamps {none,segment}` | 转写稿时间戳样式 |

**提取与渲染**

| 参数 | 含义 |
| --- | --- |
| `-e, --extractor {auto,readability,trafilatura,raw}` | 正文提取策略 |
| `-R` `-T` `-U` | `readability` / `trafilatura` / `raw` 的简写 |
| `--browser` | 强制无头浏览器渲染 |
| `-B, --browser-backend {auto,obscura,playwright}` | 使用哪个无头后端 |

**网络**

| 参数 | 含义 |
| --- | --- |
| `-x, --proxy {env,win,custom,no}` | 代理模式（`win` 仅 Windows） |
| `-c [PROXY]` | 自定义代理，可内联地址 |
| `-n` | 不使用代理 |
| `--set-proxy URL` | `-x custom` 使用的代理地址 |

**线程与社交站点**

| 参数 | 含义 |
| --- | --- |
| `-t, --thread [{after,before,both,off}]` | 线程范围 |
| `--no-thread` | 关闭线程展开 |
| `--thread-author {same,all}` | 作者范围（默认 `all`） |
| `--twitter-backend {auto,cli,native}` | Twitter/X 后端 |
| `--twitter-browser` `--twitter-profile` | `twitter-cli` 的 Cookie 来源 |

**OCR**

| 参数 | 含义 |
| --- | --- |
| `--ocr` / `--no-ocr` | 开启或关闭插图 OCR |
| `--ocr-engine {rapidocr,tesseract,paddleocr,auto}` | 指定引擎或回退链 |
| `--ocr-lang LANGS` | Tesseract 语言 |

**搜索**（仅配合 `-S/--search`）

| 参数 | 含义 |
| --- | --- |
| `-S, --search` | 把输入当作关键词 |
| `--search-engine` | `auto`、`duckduckgo`、`bing`、`searxng`、`metaso`、`deepseek` |
| `--search-limit N` | 获取的结果数（上限 20） |
| `--search-preview N` | 交互列表显示的行数 |
| `--search-top N` / `--feel-lucky` | 跳过交互选择 |
| `--search-merge` | 合并为一个文档 |
| `--search-prefix PREFIX` | 文件名前缀 |
| `--search-delay SECONDS` | 两次抓取之间的等待 |

**认证与 Cookie**

| 参数 | 含义 |
| --- | --- |
| `--login SITE` | 交互式登录 |
| `--clear-auth SITE` | 删除某站点登录态（`all` 表示全部） |
| `--export-auth SITE FILE` / `--import-auth SITE FILE` | 在不同机器间迁移登录态 |
| `--set-cf-clearance DOMAIN COOKIE` | 保存 Cloudflare 验证 Cookie |
| `--clear-cf-clearance DOMAIN` | 删除它（`all` 表示全部域名） |

**运行时**

| 参数 | 含义 |
| --- | --- |
| `--config FILE` | 使用指定配置文件 |
| `--verbose` | 详细日志 |
| `--version` / `--help` | 版本 / 本参考 |

---

## 退出码与失败记录

成功时退出码为 `0`；输入无法处理时非零（抓取/转换/保存失败为 `1`，Ctrl+C 为 `130`）。

CLI 或 Web 界面无法抓取、转换或保存某个网页 URL（包括搜索结果）时，Surf 会把该 URL 本身写入 `[Output] md_dir`，文件名为 `获取失败_<标题>.md`（没有标题时用域名/路径生成的短标识）。同名的无关文件不会被覆盖；同一 URL 已有的记录会被复用，因此这些文件同时充当重试队列。

---

## 更多文档

| 文件 | 内容 |
| --- | --- |
| `SPECIAL_SITES_zh.md` / `SPECIAL_SITES.md` | 每条 URL 规则与处理动作的细节 |
| `config.ini.example` | 全部配置项及注释 |
| `surf.py --help` | 上面这份命令行参考的源码版本 |
| `surf_web.py --help` | Web 服务器参数 |
