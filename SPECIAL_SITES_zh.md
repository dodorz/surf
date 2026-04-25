# 特殊网站处理文档（中文）

本文档记录了 surf 在处理 URL 时对特定网站的特殊匹配方式和处理动作。
英文版请见：`SPECIAL_SITES.md`。

---

## 概述

surf 使用 `SPECIAL_SITE_HANDLERS` 字典来管理特殊网站的处理逻辑。每个网站配置包含：
- `patterns`: URL 匹配的正则表达式列表
- `handler`: 处理函数，接收 `(url, config, proxy_mode_override, custom_proxy_override)` 参数
- `default_no_proxy` (可选): 默认不使用代理，可被命令行参数覆盖
- `force_proxy` (可选): 默认强制使用代理，可被命令行参数覆盖
- `default_no_translate` (可选): 默认不翻译，可被命令行参数覆盖
- `default_thread` (可选): 默认开启 thread 追溯；当前为 `backward`（向后取同作者连续回帖），可通过 `--thread` 或 `--no-thread` 覆盖

---

## 特殊网站列表

### 1. Twitter/X (推特)

**域名**:
- `twitter.com`
- `x.com`

**匹配规则**:
```regex
^https?://(www\.)?twitter\.com/
^https?://(www\.)?x\.com/
```

**处理函数**: `Fetcher._fetch_twitter_content`

**处理流程**:
1. 根据 `--twitter-backend` 或 `[Twitter].backend` 选择后端，默认 `cli`
2. 默认 `cli` 后端优先调用 `uvx --from twitter-cli twitter`，并尽量复用本机浏览器 Cookie
3. `uvx --from twitter-cli twitter` 若不可用、解析失败或无正文，则回退到 Surf 内置 native 链路
4. native 链路先使用 Twitter oEmbed API (`https://publish.twitter.com/oembed?url=...`) 获取嵌入内容
5. 检查返回内容是否为登录占位文案（支持多种引号/标点变体，如 "Don’t miss what’s happening"），若是优先尝试 syndication / fxTwitter API，再回退浏览器抓取
6. 检查返回的 HTML 是否为纯链接（Twitter Article 标志）
7. 如果是 Article（只有 t.co 链接），先解析 t.co 跳转，优先获取 `/i/article/...` 真实地址，再调用 `fetch_with_browser`
8. 直接访问的 Article URL 同时支持 `/i/article/<id>` 和 `/<user>/article/<id>`，内部会统一规范化到 `/i/article/<id>` 后再抓取
9. 若 `x.com` / oEmbed 本身因代理、TLS 或连接问题不可达，会优先直接尝试基于 status id 的 `cdn.syndication.twimg.com` / `api.fxtwitter.com` 兜底，减少对 `x.com` 页面加载的依赖
10. 浏览器抓取后，优先保留主 tweet/article 的 DOM 容器，以尽量保住粗体等行内样式和插图；仅在无法定位正文块时，才回退到 JSON-LD / `og:description` 等结构化文本提取
11. 若浏览器结果仍为登录占位页，尝试 `api.fxtwitter.com` 提取 tweet/article 内容；仍失败则返回 `None` 交由通用抓取链路处理（避免输出占位 HTML）
12. **代理策略**: Twitter/X 使用统一代理顺序：显式参数（`-x env/win/custom/no`）> 环境变量 > `config.ini` > WinHTTP；若属于隐式代理链路且连接失败，会自动回退到直连；显式传入 `-x win`、`-x custom` 或 `-n` 时严格遵循用户指定
13. 对非 Article 的短消息，标题统一使用 `正文第一句 - 作者名 on X`；该标题会同时用于最终标题、YAML/TOML 头中的 `title` 以及保存文件名
14. 对 Article 长文页面，保留文章自身 `<title>`（不使用“第一句 - 作者名 on 站点”规则）
15. 默认开启 `backward` thread 追溯：在成功取回当前贴文后，会继续补取当前贴文之后、且作者仍与当前贴文相同的连续回帖；遇到作者变化即停止
16. 可通过 `--thread forward` 改为向前取上文，或 `--thread both` 同时取前后

**支持的内容类型**:
- 普通推文 (Tweet): `/username/status/123456`
- Twitter 文章 (Article): `/i/article/123456`、`/<user>/article/123456`，或推文中包含文章链接

**特殊说明**:
- Twitter Article 页面是动态加载的单页应用（SPA）
- 使用 `domcontentloaded` 等待策略（非 networkidle）
- 等待 5 秒让 JavaScript 完成内容加载
- 自动识别并过滤登录引导占位文案，避免把占位文本当正文
- 支持复用 `--login twitter` 保存的登录态抓取（若已保存）
- Twitter/X 抓取会优先复用认证目录中的持久 profile（若存在）
- 如果系统可用 `uvx`，默认 `cli` 后端会优先使用 `uvx --from twitter-cli twitter` 读取本机浏览器 Cookie，再回退到 Surf 内置抓取链路
- Twitter/X 默认走隐式代理链路（环境变量 > `config.ini` > WinHTTP）；若该代理链路失败，会自动回退到直连
- 显式传入 `-x win`、`-x custom` 或 `-n` 时，不会再自动切换到其他代理模式
- 当 X 本站抓取失败时，支持 fxTwitter API JSON 兜底提取
- 对无法直接提取正文的页面，使用结构化元数据（JSON-LD / OpenGraph）兜底提取
- 自动清理 UI 元素（点赞、转发、统计等）

---

### 2. 微信公众号文章

**域名**: `mp.weixin.qq.com`

**匹配规则**:
```regex
^https?://mp\.weixin\.qq\.com/s/
^https?://mp\.weixin\.qq\.com/.*__biz=
```

**处理函数**: `Fetcher._fetch_wechat_article`

**处理流程**:
1. 使用 Playwright 启动 Chromium 浏览器
2. 模拟 iPhone 微信浏览器 User-Agent
3. 等待 `#js_content` 元素加载（最长 15 秒）
4. 提取文章标题和内容
5. 如果 HTML 内容为空，尝试从 JavaScript 变量中解析（支持 `JsDecode` 解密）
6. 返回完整的 HTML 文档

**User-Agent**:
```
Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile MicroMessenger/8.0.30
```

**内容提取策略**:
1. 优先从 DOM 获取：`#js_content` 或 `.rich_media_content`
2. 备选方案：从 JavaScript 变量中提取（支持 `desc: JsDecode("...")` 格式）

**默认策略**:
- **默认不使用代理（仅非 Windows）**: 微信在非 Windows 平台默认不使用代理，优先级：命令行参数 > 默认策略 > 环境变量 > 配置文件 > WinHTTP
- **默认不翻译**: 微信默认不进行翻译，优先级：命令行参数 > 默认策略 > 配置文件

**使用命令行覆盖**:
```bash
# 强制使用代理（覆盖默认策略）
surf "https://mp.weixin.qq.com/s/..." -x custom --set-proxy http://proxy:port

# 强制翻译（覆盖默认策略）
surf "https://mp.weixin.qq.com/s/..." -l trans
```

---

### 3. 知乎 (Zhihu)

**域名**:
- `zhihu.com`
- `zhuanlan.zhihu.com`

**匹配规则**:
```regex
^https?://(www\.)?zhihu\.com/question/\d+/answer/\d+
^https?://zhuanlan\.zhihu\.com/p/\d+
^https?://(www\.)?zhihu\.com/p/\d+
```

**处理函数**: `Fetcher._fetch_zhihu_content`

**处理流程**:
1. 优先识别知乎问答页回答链接和专栏文章链接
2. 对问答页调用知乎 API `https://www.zhihu.com/api/v4/answers/{answer_id}`（若存在 `surf --login zhihu` 保存的登录态，会将其中 `zhihu.com` 相关 Cookie 附加到 API 与镜像页请求，降低无 Cookie 时的 403）
3. 对专栏页调用知乎 API `https://www.zhihu.com/api/v4/articles/{article_id}`（同上）
4. 从 API 返回中提取标题、作者、创建/更新时间、点赞/评论统计和正文 HTML
5. 为输出 HTML 添加 `referrer` 元标签，减少知乎图片链路的 403 问题
6. 如果 API 被拒绝或返回异常，则尝试公开镜像页（如 `en.zhihu.com/answer/{id}`）提取正文
7. 如果镜像页也失败，再回退到 Playwright 浏览器抓取
8. 浏览器抓取时使用 `domcontentloaded` 等待策略，再从知乎 DOM 中提取正文容器
9. 如果知乎专用链路整体失败，则直接返回失败，不再进入通用 `requests -> browser` 回退，避免再次命中 403 和安全验证页

**特殊说明**:
- 主要解决知乎问答页直接 `requests` 抓取时常见的 `403 Forbidden`
- 问答页优先保留问题标题作为文档标题
- 专栏页支持 `zhuanlan.zhihu.com/p/...` 和 `www.zhihu.com/p/...`
- API 被限制时会额外尝试公开镜像页，减少对浏览器环境的依赖
- **默认不使用代理**
- **默认不翻译**
- 支持 `surf --login zhihu` 保存知乎登录态；保存的 Cookie 会同时用于 **API / 镜像页 `requests` 请求** 与 Playwright 浏览器抓取（未登录时 API 常见 403，属预期，将自动走镜像与浏览器回退）
- 知乎专用链路失败后不会再进入通用回退链路，避免把安全验证页当作正文

---

### 4. 小红书 (Xiaohongshu)

**域名**: `xiaohongshu.com`

**匹配规则**:
```regex
^https?://(www\.)?xiaohongshu\.com/explore/
^https?://(www\.)?xiaohongshu\.com/discovery/item/
^https?://(www\.)?xiaohongshu\.com/user/profile/
^https?://xhslink\.com/
```

**处理函数**: `Fetcher._fetch_xiaohongshu`

**特点**:
- 需要登录才能访问完整内容
- 支持短链接自动解析（`xhslink.com` → `xiaohongshu.com`）
- 对已由专用处理器整理好的笔记 HTML，跳过通用 Readability 正文抽取，直接保留 `<article>` 中的图文内容，避免短文本笔记在二次抽取时丢失插图/题图
- 笔记多图按正文相关容器中的 DOM 顺序提取；若正文块未直接包含图集，则按该顺序在正文前补齐缺失图片，减少图序错乱和重复图片
- 优先从笔记详情数据中的 `imageList` / `imagesList` 等字段提取图集；一旦确定最终图集，会移除正文中的小红书图片节点，只保留单份有序图集，避免重复和无关插图
- 当前对小红书图集额外应用一个经验性顺序修正：若检测到“最后一张图被提前到第一位”的常见偏移，则通过循环左移将第一张图放到末尾
- 默认开启文章插图 OCR；默认优先使用 RapidOCR，必要时回退到本地 Tesseract，并在图片下追加 OCR 文本块；可通过 `--no-ocr-images` 显式关闭

**短链接支持**:
- 自动识别 `http://xhslink.com/xxxxx` 格式的短链接
- 自动解析并重定向到完整的长链接
- 元数据中保存的长链接仅保留 `xsec_token` 参数
- 示例：
  - 短链接：`http://xhslink.com/o/44WPgb4b8J4`
  - 解析后：`https://www.xiaohongshu.com/explore/69765621000000000e00c91e?xsec_token=CB_OFtXIEBO6WUWQwEGJO26hp_JXiroO_HkoGjCmq9X7c=`

**使用流程**:

1. **首次登录**（必需）:
   ```bash
   surf --login xiaohongshu
   ```
   这会打开浏览器窗口，您需要：
   - 手动登录小红书账号
   - 完成任何验证码验证
   - 登录成功后按回车保存会话
   - 如果目标机器是无 GUI 的 Linux 服务器，请在有桌面的机器完成此步骤

2. **迁移到无 GUI Linux（可选）**:
   ```bash
   # 在桌面机器导出登录态
   surf --export-auth xiaohongshu ./xiaohongshu_state.json

   # 在 Linux 服务器导入登录态
   surf --import-auth xiaohongshu ./xiaohongshu_state.json
   ```

3. **正常使用**:
   ```bash
   surf "https://www.xiaohongshu.com/explore/NOTE_ID"
   ```
   系统会自动使用保存的登录状态

4. **清除登录状态**:
   ```bash
   surf --clear-auth xiaohongshu
   # 或清除所有网站的登录状态
   surf --clear-auth all
   ```

**技术实现**:
- 使用 Playwright 的 `storage_state()` 保存/恢复登录状态
- 状态文件默认存储在 Windows 的 `%LOCALLAPPDATA%\surf\auth\` 或 Linux/macOS 的 `~/.local/cache/surf/auth/`
- 无 GUI Linux 上不会再在抓取流程中自动启动交互登录；如果状态缺失或过期，会提示用户先刷新并导入登录态
- 生成 front matter 的 `source` 时会规范化小红书分享链接，只保留主路径和 `xsec_token`，移除 `source=webshare`、`xhsshare=pc_web` 等分享跟踪参数

**内容提取**:
- 标题：从 `h1` 或页面标题提取
- 正文：从 `.note-content`, `.content`, `.desc` 等选择器提取
- 图片：提取 `xhscdn.com` 域名的图片
- 头像处理：来自 `sns-avatar-qc.xhscdn.com` 的头像图片显示为 60x60 圆形

**默认策略**:
- **默认不使用代理（仅非 Windows）**: 小红书在非 Windows 平台默认不使用代理，优先级：命令行参数 > 默认策略 > 环境变量 > 配置文件 > WinHTTP
- **默认不翻译**: 小红书默认不进行翻译，优先级：命令行参数 > 默认策略 > 配置文件

**使用命令行覆盖**:
```bash
# 强制使用代理（覆盖默认策略）
surf "https://www.xiaohongshu.com/..." -x custom --set-proxy http://proxy:port

# 强制翻译（覆盖默认策略）
surf "https://www.xiaohongshu.com/..." -l trans
```

---

### 5. GitHub

**域名**: `github.com`

**匹配规则**:
```regex
^https?://(www\.)?github\.com/[^/]+/[^/]+/?$
```

**处理函数**: `Fetcher._fetch_github_readme`

**处理流程**:
1. 解析 URL 提取仓库所有者 (owner) 和仓库名 (repo)
2. 加载仓库主页，查找语言特定的 README 文件（如 README_zh.md）
3. 根据配置的目标语言优先选择对应语言的 README
4. 导航到 README 文件页面（如有语言特定版本）
5. 提取 `<article class="markdown-body">` 元素内容
6. 移除所有 `aria-label^="Permalink:"` 的锚点元素（GitHub 标题旁的"¶"符号）
7. 构建完整的 HTML 文档返回

**特殊说明**:
- 优先提取语言特定的 README（如 README_zh.md）
- 自动移除 permalink 锚点链接，避免空链接显示
- 提取仓库标题和描述信息
- **标题不翻译**: 仓库名称保持原文（通过 `skip_title_translation` 配置），README 内容可以翻译
- **文件名策略**: 保存 Markdown 文件时，GitHub URL 使用页面 `<title>` 作为文件名基准

---

### 6. Wikipedia

**域名**:
- `wikipedia.org`
- `*.wikipedia.org` (如 `en.wikipedia.org`, `zh.wikipedia.org`)

**匹配规则**:
```regex
^https?://(www\.)?wikipedia\.org/wiki/
^https?://[a-z]{2}\.wikipedia\.org/wiki/
```

**处理函数**: `Fetcher._fetch_wikipedia`

**处理流程**:
1. 加载 Wikipedia 页面
2. 提取 `#mw-content-text .mw-parser-output` 内容区域
3. **DOM 清洗优化**:
   - 移除引用标记（`sup.reference`, `sup a[href^="#cite_note"]`）
   - 移除编辑链接（`.mw-editsection`）
   - 移除目录（`#toc`, `.toc`）
   - 修复表格标题显示
   - 移除文章消息框（`.ambox`, `.mbox`, `.tmbox`）
   - 移除导航模板（`.navbox`）
   - 移除提示框（`.hatnote`, `.dablink`, `.rellink`）
   - 清理空段落和空标题
4. 构建结构化的 HTML 文档

**特殊说明**:
- 适用于所有语言版本的 Wikipedia
- 保留信息框（infobox）但优化布局
- 添加表格样式美化输出

---

### 7. Bluesky

**域名**: `bsky.app`

**匹配规则**:
```regex
^https?://bsky\.app/profile/[^/]+/post/
```

**处理函数**: `Fetcher._fetch_bluesky`

**处理流程**:
1. 解析 URL 提取用户 handle 和帖子 ID
2. 调用 Bluesky 官方 API 解析 handle 为 DID
   - `https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle`
3. 使用 DID 构造 `at://` URI
4. 调用官方 API 获取帖子线程
   - `https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread`
5. 提取帖子内容、作者信息、发布时间、互动数据
6. 逐条提取 thread 中每条帖子自己的嵌入媒体（图片、外部链接卡片、视频封面/播放列表），并尽量优先使用 API 返回的原始媒体 URL
7. 默认开启 `backward` thread 追溯：沿 replies 链向后补取同作者的连续回帖，直到作者不再等于当前贴文作者
8. 按“前文 -> 当前帖子 -> 后续回复”的自然顺序构建 HTML 文档返回，媒体跟随各自帖子原位渲染

**特殊说明**:
- 使用 Bluesky 官方公共 API (xrpc)
- 获取当前帖子以及同作者的连续 thread 片段；默认取后续回帖，也支持 `forward` / `both`
- 支持按帖子顺序提取并渲染嵌入图片、外部链接卡片，以及部分视频卡片信息
- 短帖子标题统一使用 `正文第一句 - 作者名 on Bsky`，并同步用于 front matter `title` 与默认文件名
- 显示点赞、转发、回复统计数据

---

### 8. 微博 (Weibo)

**域名**:
- `weibo.com`
- `m.weibo.cn`

**匹配规则**:
```regex
^https?://(www\.)?weibo\.com/
^https?://m\.weibo\.cn/
```

**处理函数**: `Fetcher._fetch_social_thread_page`

**处理流程**:
1. 使用 Playwright 打开页面并等待动态内容加载
2. 从页面中的帖子卡片提取作者、时间、正文和帖子链接
3. 识别当前目标贴文
4. 默认开启 `backward` thread 追溯：只保留当前贴文以及它之后、且作者与当前贴文相同的连续回帖
5. 若前一条回帖作者变化，则停止继续向前追溯
6. 构建结构化 HTML 返回
7. 短帖子标题统一使用 `正文第一句 - 作者名 on Weibo`，并同步用于 front matter `title` 与默认文件名

---

### 9. Threads

**域名**: `threads.net`

**匹配规则**:
```regex
^https?://(www\.)?threads\.net/@[^/]+/post/
```

**处理函数**: `Fetcher._fetch_social_thread_page`

**处理流程**:
1. 使用 Playwright 打开 Threads 贴文页
2. 从页面中的帖子卡片提取作者、时间、正文和帖子链接
3. 识别当前目标贴文
4. 默认开启 `backward` thread 追溯：只保留当前贴文以及它之后、且作者与当前贴文相同的连续回帖
5. 若前一条回帖作者变化，则停止继续向前追溯
6. 构建结构化 HTML 返回
7. 短帖子标题统一使用 `正文第一句 - 作者名 on Threads`，并同步用于 front matter `title` 与默认文件名

---

### 10. V2EX

**域名**: `v2ex.com` / `www.v2ex.com`

**匹配规则**:
```regex
^https?://(www\.)?v2ex\.com/t/\d+
```

**处理函数**: `Fetcher._fetch_v2ex_topic`

**处理流程**:
1. 默认强制使用代理访问 V2EX；若 `[Network].custom_proxy` 已配置，则优先使用该自定义代理；显式传入 `-x/--proxy` 或 `-n` 时仍严格遵循用户指定。
2. 使用 `requests` 获取主题页，并通过 V2EX 专用 DOM 解析器提取标题、作者、节点、发布时间和主贴正文。
3. 默认只输出主贴，避免通用 readability 将回帖误判为正文。
4. 使用 `-t` / `--thread` 时包含回帖；若页面存在 `?p=...` 分页，会继续抓取后续回帖页并去重。
5. 默认不翻译，保留 V2EX 原文语言；如需翻译可显式使用 `-l trans`。
6. 返回 direct Markdown payload，绕过通用正文抽取流程，保证主贴和回帖边界稳定。

**示例**:
```bash
surf "https://v2ex.com/t/1208365" -r
surf "https://v2ex.com/t/1208365" -r -t
```

---

### 11. 国家哲学社会科学文献中心 (NCPSSD)

**域名**:
- `ncpssd.cn`
- `ncpssd.org`

**匹配规则**:
```regex
^https?://ncpssd\.cn/Literature/
^https?://ncpssd\.org/Literature/
```

**处理函数**: `Fetcher._fetch_ncpssd_article`

**处理要求**:
1. **强制使用浏览器**: 使用 Playwright 浏览器渲染以获取动态加载的内容。
2. **默认不翻译**: 满足用户对学术文献原文查阅的需求。
3. **默认不使用代理（仅非 Windows）**: 提高访问速度（除非使用命令行参数 `-x` 强制启用）。
4. **标题提取**: 强制使用页面中的 `<h1>` 标签内容作为标题（用于文件名和 Markdown 元数据）。
5. **完整内容获取**: 专门提取包括中英文摘要、作者信息、出版物信息、页码范围、总页数、关键词和学科分类等详细元数据。
6. **认证复用**: 支持 `surf --login ncpssd` 保存登录态，并在后续浏览器会话中自动复用。
7. **PDF 直链下载优先**: 对 `https://ncpssd.cn/Literature/secure/articleinfo?params=...`（含 `.org`）在 `-p/--format pdf` 模式下，优先模拟点击页面“全文下载”按钮，保存站点原始全文 PDF；失败时回退为 Surf 生成的 PDF。
8. **PDF 命名规则**: 当下载原始全文 PDF 成功时，文件名优先使用 `论文标题-作者-期刊.pdf`（来自 articleinfo 页面字段），便于归档检索。
9. **默认输出为 PDF**: 对受保护文章详情页（`/Literature/secure/articleinfo?params=...`）若用户未显式指定输出格式，则默认按 PDF 输出（等价于隐式 `-p`）。

**技术细节**:
- 使用 JavaScript `evaluate` 脚本精准提取页面元素。
- 构建结构化的 HTML 文章，包含标题、作者、机构、出版物、摘要等部分。
- 处理完成后，由系统转换为标准的 Markdown 格式。
- PDF 直链下载通过 Playwright `expect_download` + 点击“全文下载”按钮触发，并使用保存的 `ncpssd` 登录态处理受保护资源。

---

## 通用处理逻辑

### 处理器查找流程

当用户输入 URL 时，系统执行以下步骤：

1. **URL 分发**: 调用 `Fetcher.fetch()` 方法
2. **特殊处理检查**: 调用 `_get_handler_for_url(url)` 遍历 `SPECIAL_SITE_HANDLERS`
3. **模式匹配**: 使用编译后的正则表达式匹配 URL
4. **调用处理器**: 如果匹配成功，调用对应的 `handler` 函数
5. **回退机制**: 如果处理器返回 `None`，回退到常规抓取（requests 或 Playwright）

### 回退处理

如果特殊处理失败或返回 `None`，系统会：
1. 使用 `requests` 库抓取页面
2. 检测内容是否需要 JavaScript（长度 < 1000 或包含 `<noscript>`）
3. 如需要，切换到 Playwright 浏览器抓取

### 内容提取

无论使用何种方式获取 HTML，最终都会调用 `ContentProcessor.extract_content()`：
1. 预处理 HTML（标准化图片、移除懒加载）
2. 使用 Readability 库提取主要内容
3. 使用指纹匹配技术找回图片等元素
4. 备选方案：使用 Trafilatura 库

---

## 添加新的特殊网站处理

### 1. 定义匹配模式

在 `SPECIAL_SITE_HANDLERS` 字典中添加新条目：

```python
SPECIAL_SITE_HANDLERS = {
    "twitter": {
        "patterns": [
            r"^https?://(www\.)?twitter\.com/",
            r"^https?://(www\.)?x\.com/",
        ],
        "handler": Fetcher._fetch_twitter_oembed,
    },
    "wechat": {
        "patterns": [
            r"^https?://mp\.weixin\.qq\.com/s/",
            r"^https?://mp\.weixin\.qq\.com/.*__biz=",
        ],
        "handler": Fetcher._fetch_wechat_article,
    },
    # 新增网站示例
    "example_site": {
        "patterns": [
            r"^https?://example\.com/",
        ],
        "handler": Fetcher._fetch_example_site,
    },
}
```

### 2. 实现处理函数

在 `Fetcher` 类中实现静态方法：

```python
@staticmethod
def _fetch_example_site(url, config, proxy_mode_override=None, custom_proxy_override=None):
    """
    获取 example.com 的特殊处理
    
    Args:
        url: 目标 URL
        config: Config 对象
        proxy_mode_override: 命令行代理模式覆盖
        custom_proxy_override: 命令行自定义代理覆盖
    
    Returns:
        HTML 内容字符串，如果失败返回 None
    """
    req_proxies, pw_proxy = Fetcher._get_proxies(
        config, proxy_mode_override, custom_proxy_override
    )
    
    try:
        # 实现特殊抓取逻辑
        # ...
        return html_content
    except Exception as e:
        logger.warning(f"Example site handler failed: {e}")
        return None  # 返回 None 触发回退处理
```

### 3. 更新本文档

添加对应的文档说明，包括：
- 网站名称和域名
- 匹配规则（正则表达式）
- 处理函数
- 详细的处理流程
- 特殊说明（如代理策略、内容提取策略等）
- 依赖的配置项

---

## 文档维护要求

**重要**: 当修改以下内容时，必须同步更新本文档：

1. **新增特殊网站处理**: 在 `SPECIAL_SITE_HANDLERS` 中添加新条目时
2. **修改匹配规则**: 更新任何网站的 `patterns` 时
3. **变更处理逻辑**: 修改任何 `handler` 函数的实现时
4. **调整代理策略**: 修改网站的代理使用方式时
5. **添加配置依赖**: 引入新的配置项时

文档维护责任人：所有开发者

---

## 相关代码位置

| 功能 | 代码位置 |
|------|---------|
| 特殊网站处理器定义 | `surf.py:1604-1664` |
| 处理器查找逻辑 | `surf.py:1670-1695` |
| Twitter 处理函数 | `surf.py:627-695` |
| 微信处理函数 | `surf.py:697-767` |
| 小红书处理函数 | `surf.py:884-1023` |
| GitHub 处理函数 | `surf.py:1128-1244` |
| Wikipedia 处理函数 | `surf.py:1247-1412` |
| Bluesky 处理函数 | `surf.py:1415-1600` |
| 小红书内容清理 | `surf.py:558-603` |
| AuthHandler 认证管理 | `surf.py:2039-2194` |
| MediaHandler 媒体处理 | `surf.py:2196-2322` |
| 主函数策略应用 | `surf.py:2432-2463` |
| 内容提取逻辑 | `surf.py:1698-1750` |

---

**最后更新日期**: 2026-04-17
**文档版本**: 1.5
