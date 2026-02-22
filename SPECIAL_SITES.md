# 特殊网站处理文档

本文档记录了 surf 在处理 URL 时对特定网站的特殊匹配方式和处理动作。

---

## 概述

surf 使用 `SPECIAL_SITE_HANDLERS` 字典来管理特殊网站的处理逻辑。每个网站配置包含：
- `patterns`: URL 匹配的正则表达式列表
- `handler`: 处理函数，接收 `(url, config, proxy_mode_override, custom_proxy_override)` 参数
- `default_no_proxy` (可选): 默认不使用代理，可被命令行参数覆盖
- `default_no_translate` (可选): 默认不翻译，可被命令行参数覆盖

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

**处理函数**: `Fetcher._fetch_twitter_oembed`

**处理流程**:
1. 使用 Twitter oEmbed API (`https://publish.twitter.com/oembed?url=...`) 获取嵌入内容
2. 检查返回内容是否为登录占位文案（如 "Don't miss what's happening"），若是则改为浏览器抓取
3. 检查返回的 HTML 是否为纯链接（Twitter Article 标志）
4. 如果是 Article（只有 t.co 链接），先解析 t.co 跳转，优先获取 `/i/article/...` 真实地址，再调用 `fetch_with_browser`
5. 浏览器抓取后，优先从 JSON-LD / `og:description` 等结构化数据提取正文；若失败再回退 DOM 清理逻辑
6. 如果是普通 Tweet，直接返回 oEmbed HTML
7. **强制代理**: Twitter/X 始终使用代理，优先级：命令行 > 强制代理 > 配置文件

**支持的内容类型**:
- 普通推文 (Tweet): `/username/status/123456`
- Twitter 文章 (Article): `/i/article/123456` 或推文中包含文章链接

**特殊说明**:
- Twitter Article 页面是动态加载的单页应用（SPA）
- 使用 `domcontentloaded` 等待策略（非 networkidle）
- 等待 5 秒让 JavaScript 完成内容加载
- 自动识别并过滤登录引导占位文案，避免把占位文本当正文
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
- **默认不使用代理**: 微信默认不使用代理，优先级：命令行参数 > 默认策略 > 配置文件
- **默认不翻译**: 微信默认不进行翻译，优先级：命令行参数 > 默认策略 > 配置文件

**使用命令行覆盖**:
```bash
# 强制使用代理（覆盖默认策略）
surf "https://mp.weixin.qq.com/s/..." -x custom --set-proxy http://proxy:port

# 强制翻译（覆盖默认策略）
surf "https://mp.weixin.qq.com/s/..." -l trans
```

---

### 3. 小红书 (Xiaohongshu)

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

2. **正常使用**:
   ```bash
   surf "https://www.xiaohongshu.com/explore/NOTE_ID"
   ```
   系统会自动使用保存的登录状态

3. **清除登录状态**:
   ```bash
   surf --clear-auth xiaohongshu
   # 或清除所有网站的登录状态
   surf --clear-auth all
   ```

**技术实现**:
- 使用 Playwright 的 `storage_state()` 保存/恢复登录状态
- 状态文件存储在 `~/.surf/auth/xiaohongshu_state.json`
- 如果检测到登录页面，提示用户使用 `--login` 参数

**内容提取**:
- 标题：从 `h1` 或页面标题提取
- 正文：从 `.note-content`, `.content`, `.desc` 等选择器提取
- 图片：提取 `xhscdn.com` 域名的图片
- 头像处理：来自 `sns-avatar-qc.xhscdn.com` 的头像图片显示为 60x60 圆形

**默认策略**:
- **默认不使用代理**: 小红书默认不使用代理，优先级：命令行参数 > 默认策略 > 配置文件
- **默认不翻译**: 小红书默认不进行翻译，优先级：命令行参数 > 默认策略 > 配置文件

**使用命令行覆盖**:
```bash
# 强制使用代理（覆盖默认策略）
surf "https://www.xiaohongshu.com/..." -x custom --set-proxy http://proxy:port

# 强制翻译（覆盖默认策略）
surf "https://www.xiaohongshu.com/..." -l trans
```

---

### 4. GitHub

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

---

### 5. Wikipedia

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

### 6. Bluesky

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
6. 处理嵌入媒体（图片、外部链接卡片）
7. 提取回复内容（最多 20 条）
8. 构建 HTML 文档返回

**特殊说明**:
- 使用 Bluesky 官方公共 API (xrpc)
- 获取完整帖子数据包括回复
- 支持提取嵌入图片和外部链接卡片
- 显示点赞、转发、回复统计数据

---

### 7. 国家哲学社会科学文献中心 (NCPSSD)

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
3. **默认不使用代理**: 提高访问速度（除非使用命令行参数 `-x` 强制启用）。
4. **标题提取**: 强制使用页面中的 `<h1>` 标签内容作为标题（用于文件名和 Markdown 元数据）。
5. **完整内容获取**: 专门提取包括中英文摘要、作者信息、出版物信息、页码范围、总页数、关键词和学科分类等详细元数据。

**技术细节**:
- 使用 JavaScript `evaluate` 脚本精准提取页面元素。
- 构建结构化的 HTML 文章，包含标题、作者、机构、出版物、摘要等部分。
- 处理完成后，由系统转换为标准的 Markdown 格式。

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

**最后更新日期**: 2026-02-13
**文档版本**: 1.4
