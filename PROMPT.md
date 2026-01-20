# Surf - 网页转 Markdown/PDF/音频 CLI 工具

## 功能需求

1. **网页抓取**: 智能选择 requests 或 Playwright 处理动态页面
2. **内容清理**: 提取正文，移除广告/侧边栏
3. **格式转换**: HTML 转 Markdown
4. **翻译**: 使用 LLM API 自动翻译（支持原文/译文/双语三种模式）
5. **输出**: 支持 Markdown 笔记、PDF、音频文件
6. **代理**: 支持系统代理/自定义代理

## 命令行接口

```
surf.py <url> [选项]
```

**选项**:
- `--version`: 显示版本
- `-p`, `--pdf`: 生成 PDF
- `-n`, `--note`: 保存为 Markdown 笔记
- `-a`, `--audio`: 保存为音频
- `-s`, `--speak`: 朗读内容
- `--browser`: 强制使用浏览器
- `--trans-mode`: 翻译模式 (original/translated/both，默认 translated)
- `-o`: 等同于 `--trans-mode original`
- `-b`: 等同于 `--trans-mode both`
- `-x`, `--proxy`: 代理模式 (auto/win/no/set，默认 auto)
- `--set-proxy`: 自定义代理地址（配合 -x set 使用）

## 配置文件

使用 `config.ini`，需要配置 `[LLM]` (API)、`[Output]` (路径)、`[TTS]` (语音)、`[Network]` (代理)

## 版本管理

遵循语义化版本。每次 git 提交自动递增 patch 版本号。

## 开发规范

后续任何代码更改，如涉及命令行参数、功能特性或配置文件的修改，应同步更新以下文档：
1. **`--help` 输出**：确保帮助信息与实际参数一致
2. **`README.md` / `README_zh.md`**：用户文档需反映最新功能
3. **`PROMPT.md`**：功能需求和命令行接口说明需同步更新
