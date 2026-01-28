# 网站适配器（Special Parsers）

## 概述

请针对以下多个流行网站实现特殊的内容获取策略，而不是通用的网页爬虫。这些策略利用官方API、开放的数据格式或特定的HTML结构来获取更准确的内容。

## 网站适配器列表

| 网站 | 策略 | 关键实现 |
|------|------|---------|
| **Twitter/X** | Twitter oEmbed API | `https://publish.twitter.com/oembed?url=...` 获取结构化数据 |
| **YouTube** | 官方API (可选) + DOM解析 | Google Cloud API 或提取 `ytInitialData` 变量 |
| **YouTube频道** | 官方API (可选) + DOM解析 | 同上，额外支持handle格式 (@username) |
| **Bilibili** | User-Agent欺骗 + DOM解析 | `og:title` 元标签提取标题 |
| **Vimeo** | JSON-LD Schema提取 | HTML中 `<script type="application/ld+json">` |
| **GitHub** | DOM提取Readme | 定位 `article.markdown-body` 元素 |
| **Wikipedia** | DOM清洗优化 | 移除引用标记、修复表格标题显示 |
| **Stack Overflow/Exchange** | Desktop User-Agent + 自定义解析 | microdata (`[itemprop]`) + DOMPurify消毒 |
| **TikTok** | User-Agent + 元标签 | `og:description/og:url` 提取内容 |
| **Bluesky** | 官方API (xrpc) | `https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread` 获取完整数据+回复 |
| **Mastodon** | 动态API发现 + 官方API | 自动检测实例 (`/api/v2/instance`) → 调用 `/api/v1/statuses/{id}` |
| **Pinterest** | User-Agent + JSON嵌入解析 | 扫描 `data-relay-response='true'` 元素提取GraphQL响应 |
| **通用网站** | HTML转Markdown | Turndown + HTML清理 |

## 获取策略优先级

1. **优先：官方API**
   - Twitter oEmbed
   - YouTube Data API
   - Bluesky xRPC API
   - Mastodon REST API

2. **次选：开放数据格式**
   - JSON-LD Schema (`<script type="application/ld+json">`)
   - microdata (`[itemprop]` 属性)
   - Open Graph 元标签

3. **最后：User-Agent + DOM解析**
   - 伪装成桌面浏览器
   - 定向抓取特定CSS类或ID
   - DOMPurify 清理恶意内容

## 特殊处理案例

### Twitter/X
不用传统爬虫，而是调用 Twitter 官方的 oEmbed 端点，可以获取：
- 作者名称
- 推文内容（HTML格式）
- 发布日期
- 嵌入式播放器

### YouTube
两种模式：
- **有API密钥**：调用 Google Cloud API，获取视频元数据、章节、统计数据
- **无API密钥**：解析HTML中的 `ytInitialData` JSON对象

### Mastodon
动态适配任意 Mastodon 实例：
1. 先探测 `/api/v2/instance` 确认是否为 Mastodon
2. 调用实例的 `/api/v1/statuses/{id}` 获取帖子
3. 可选获取对话回复

### Pinterest
抓取 GraphQL 响应而非DOM：
- 查找 `data-relay-response='true'` 的script标签
- 解析其中的 `v3GetPinQuery` 数据
- 提取图片、描述、点赞数等
