# 项目：Surf - 网页转 Markdown/PDF/音频转换器

创建一个名为 `surf` 的 Python 命令行工具，用于抓取网页内容、清理干扰项、使用 LLM 翻译，并将其输出为 Markdown、PDF 或音频文件。

## 技术约束
- **语言**: Python
- **环境管理**: 使用 `uv`。
- **操作系统**: 兼容 Windows。

## 核心功能

### 1. 内容抓取与处理
- **智能抓取**:
  - 优先尝试使用 `requests` 进行快速抓取。
  - 在以下情况自动降级使用 `Playwright` (无头模式):
    - 内容过短（可能被拦截或由 JS 渲染）。
    - 响应内容包含需要 JavaScript 的提示（如 `<noscript>`）。
  - 支持通过命令行标志 (`--browser`) 强制使用浏览器。
- **清理**: 使用 `html-text` 提取主要内容（移除广告、导航栏等）。
- **格式转换**: 使用 `markdownify` 将提取的 HTML 转换为 Markdown。

### 2. 翻译 (LLM 集成)
- **检测**: 自动检测内容语言。
- **翻译模式**: 支持三种模式 (`--trans-mode`) 及快捷键：
  - `original` / `-o`: 不进行翻译，直接输出。
  - `translated`: 翻译为目标语言并替换原文（默认）。
  - `both` / `-b`: 同时保留原文和译文。
- **自动翻译**: 如果内容不是目标语言（可配置，例如 'zh-cn'）且模式不是 `original`：
  - 使用兼容 OpenAI 接口的 API 翻译内容。
  - **分段处理**: 实现智能文本分段（按段落分割），以处理超过 LLM 上下文窗口限制的长文章。
  - **标题翻译**: 同时翻译标题，并使用翻译后的标题作为输出文件名。

### 3. 输出格式
- **Markdown 笔记 (`-n`)**: 将（已翻译的）Markdown 保存到可配置的笔记目录。
- **PDF (`-p`)**: 将 Markdown 转换为 PDF。在 `config.ini` 中支持多种引擎：
  - `playwright`: 默认，内置，质量高且稳定。
  - `weasyprint`: 高质量，Windows 上需要 GTK3。
  - `pandoc`: 功能强大，需系统安装 pandoc。
  - `wkhtmltopdf`: 经典方案，需系统安装相应二进制文件。
  - 保存到可配置的 PDF 目录。
- **音频/TTS (`-a`, `-s`)**:
  - 使用 `edge-tts` 支持文本转语音。
  - **保存 (`-a`)**: 保存为 MP3 到可配置的音频目录。
  - **朗读 (`-s`)**: 使用 `playsound` 即时朗读内容。
  - **可配置**: 支持设置语音、语速和音量。

### 4. 网络与代理
- **配置**: 支持配置文件中的 `[Network]` 部分。
  - 模式: `default` (使用系统/环境变量), `none` (直连), `custom` (自定义 URL)。
  - 逻辑需同时应用于 `requests` 和 `Playwright` 实例。

## 配置文件 (`config.ini`)
工具必须使用 `config.ini` 文件。

**必需部分**:
- `[LLM]`: `base_url`, `api_key`, `model`
- `[Output]`: `note_dir`, `pdf_dir`, `audio_dir`, `target_language`
- `[PDF]`: `css_file`
- `[TTS]`: `engine`, `voice`, `rate`, `volume`
- `[Network]`: `proxy_mode`, `custom_proxy`

## 文档与交付物
1.  **`surf.py`**: 实现核心逻辑的主脚本。
2.  **`requirements.txt`**: 列出依赖项（**注意**: 为保证 Windows 兼容性，固定 `playsound==1.2.2`）。
3.  **`README.md`**: 英文文档。
4.  **`README_zh.md`**: 中文文档。
5.  **`.gitignore`**: 标准 Python 忽略文件 + `config.ini`。
6.  **`config.ini.example`**: 配置文件模板。
