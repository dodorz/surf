#!/usr/bin/env -S uv run
"""
Surf Web Interface - A web interface for the Surf URL to Markdown/PDF converter.

Usage:
    python surf_web.py [--host HOST] [--port PORT]

Example:
    python surf_web.py --host 0.0.0.0 --port 8080
"""

import argparse
import os
import re
import threading
import webbrowser
from html import escape
from types import SimpleNamespace

# Flask web framework
try:
    from flask import (
        Flask,
        render_template_string,
        request,
        jsonify,
        send_file,
    )
    from werkzeug.exceptions import HTTPException
except ImportError:
    print("Flask not installed. Installing...")
    os.system("pip install flask")
    from flask import (
        Flask,
        render_template_string,
        request,
        jsonify,
        send_file,
    )
    from werkzeug.exceptions import HTTPException

# Import surf modules
from surf import (
    Config,
    Fetcher,
    ContentProcessor,
    OcrHandler,
    OutputHandler,
    TTSHandler,
    _extract_direct_markdown_payload,
    _get_handler_for_url,
    _get_version,
    logger,
    _render_markdown_to_html,
    resolve_user_path,
)

app = Flask(__name__)


@app.errorhandler(Exception)
def handle_api_error(error):
    """Return JSON errors for API routes instead of Flask HTML error pages."""
    if not request.path.startswith("/api/"):
        raise error

    if isinstance(error, HTTPException):
        logger.error(f"API request failed: {error}")
        return jsonify({"success": False, "error": error.description}), error.code

    logger.exception("Unhandled API error")
    return jsonify({"success": False, "error": str(error)}), 500

# HTML Template
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Surf - URL转Markdown/PDF/HTML/Audio</title>
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0%25' y1='0%25' x2='100%25' y2='100%25'%3E%3Cstop offset='0%25' stop-color='%23667eea'/%3E%3Cstop offset='100%25' stop-color='%23764ba2'/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect width='64' height='64' rx='14' fill='url(%23g)'/%3E%3Cpath d='M12 38c6-8 10-12 16-12 7 0 10 8 16 8 4 0 7-2 12-8v10c-5 6-8 8-12 8-6 0-9-8-16-8-6 0-10 4-16 12V38z' fill='white' fill-opacity='.92'/%3E%3Cpath d='M12 24c6-8 10-12 16-12 7 0 10 8 16 8 4 0 7-2 12-8v10c-5 6-8 8-12 8-6 0-9-8-16-8-6 0-10 4-16 12V24z' fill='white' fill-opacity='.72'/%3E%3C/svg%3E">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 900px;
            margin: 0 auto;
        }
        
        .header {
            text-align: center;
            color: white;
            margin-bottom: 30px;
        }
        
        .header h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
        }
        
        .header p {
            opacity: 0.9;
        }
        
        .card {
            background: white;
            border-radius: 12px;
            padding: 30px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            margin-bottom: 20px;
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        
        label {
            display: block;
            font-weight: 600;
            margin-bottom: 8px;
            color: #333;
        }
        
        input[type="text"], input[type="url"], textarea, select {
            width: 100%;
            padding: 12px 15px;
            border: 2px solid #e1e5e9;
            border-radius: 8px;
            font-size: 16px;
            transition: border-color 0.3s;
        }
        
        input[type="text"]:focus, input[type="url"]:focus, textarea:focus, select:focus {
            outline: none;
            border-color: #667eea;
        }
        
        .url-input {
            font-size: 18px;
            min-height: 96px;
            resize: vertical;
        }
        
        .options-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 18px 20px;
            align-items: start;
        }

        .form-group.wide {
            grid-column: 1 / -1;
        }
        
        .checkbox-group {
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .checkbox-group label {
            margin-bottom: 0;
        }

        .checkbox-group input[type="checkbox"] {
            width: 18px;
            height: 18px;
        }

        .radio-group {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }

        .radio-option {
            position: relative;
        }

        .radio-option input[type="radio"] {
            position: absolute;
            opacity: 0;
            pointer-events: none;
        }

        .radio-option label {
            display: inline-flex;
            align-items: center;
            min-height: 44px;
            padding: 10px 14px;
            margin-bottom: 0;
            border: 2px solid #e1e5e9;
            border-radius: 999px;
            background: #fff;
            cursor: pointer;
            transition: all 0.2s ease;
        }

        .radio-option input[type="radio"]:checked + label {
            border-color: #667eea;
            background: #eef2ff;
            color: #4c63d2;
        }

        .radio-option input[type="radio"]:disabled + label {
            opacity: 0.5;
            cursor: not-allowed;
        }
        
        .btn {
            display: inline-block;
            padding: 15px 40px;
            border: none;
            border-radius: 8px;
            font-size: 18px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .btn-primary {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        
        .btn-primary:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 20px rgba(102, 126, 234, 0.4);
        }
        
        .btn-primary:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }
        
        .btn-secondary {
            background: #6c757d;
            color: white;
            padding: 10px 20px;
            font-size: 14px;
        }
        
        .result-card {
            display: none;
        }
        
        .result-card.show {
            display: block;
        }
        
        .result-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 2px solid #e1e5e9;
        }
        
        .result-title {
            font-size: 1.5em;
            color: #333;
        }
        
        .save-links {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }

        .save-actions {
            display: flex;
            align-items: center;
            justify-content: flex-end;
            gap: 12px;
            flex-wrap: wrap;
        }

        .play-btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
        }

        .save-btn {
            padding: 8px 16px;
            border: none;
            border-radius: 6px;
            text-decoration: none;
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.3s;
        }

        .save-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 3px 10px rgba(0,0,0,0.2);
        }

        .save-btn:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }

        .save-md { background: #28a745; color: white; }
        .save-html { background: #e34c26; color: white; }
        .save-pdf { background: #f40f02; color: white; }
        .save-audio { background: #6f42c1; color: white; }
        
        .content-preview {
            background: #f8f9fa;
            padding: 20px;
            border-radius: 8px;
            max-height: 500px;
            overflow-y: auto;
            white-space: pre-wrap;
            font-family: 'Consolas', 'Monaco', monospace;
            font-size: 14px;
            line-height: 1.6;
        }
        
        .status {
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
            display: none;
        }
        
        .status.show {
            display: block;
        }
        
        .status-processing {
            background: #fff3cd;
            color: #856404;
            border: 1px solid #ffeeba;
        }
        
        .status-success {
            background: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        
        .status-error {
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        
        .spinner {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 3px solid rgba(0,0,0,0.1);
            border-radius: 50%;
            border-top-color: #667eea;
            animation: spin 1s ease-in-out infinite;
            margin-right: 10px;
            vertical-align: middle;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        
        .tabs {
            display: flex;
            gap: 5px;
            margin-bottom: 15px;
        }
        
        .tab {
            padding: 10px 20px;
            border: none;
            background: #e1e5e9;
            border-radius: 6px 6px 0 0;
            cursor: pointer;
            font-size: 14px;
            font-weight: 500;
        }
        
        .tab.active {
            background: #667eea;
            color: white;
        }
        
        .tab-content {
            display: none;
        }
        
        .tab-content.active {
            display: block;
        }
        
        .section-title {
            font-size: 1.1em;
            font-weight: 600;
            color: #667eea;
            margin: 20px 0 15px;
            padding-bottom: 8px;
            border-bottom: 2px solid #e1e5e9;
        }

        .field-hint {
            margin-top: 6px;
            font-size: 13px;
            color: #6c757d;
            line-height: 1.5;
        }
        
        .version-info {
            text-align: center;
            color: rgba(255,255,255,0.7);
            font-size: 12px;
            margin-top: 20px;
        }

        @media (max-width: 768px) {
            .result-header {
                flex-direction: column;
                align-items: stretch;
                gap: 12px;
            }

            .save-actions {
                justify-content: flex-start;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🌊 Surf</h1>
            <p>将网页转换为 Markdown / PDF / HTML / Audio</p>
        </div>
        
        <div class="card">
            <form id="surfForm">
                <div class="form-group">
                    <label for="url">URL 地址或纯文本</label>
                    <textarea id="url" name="url" class="url-input" rows="4"
                              placeholder="粘贴链接，或直接输入一段没有 URL 的文字"></textarea>
                    <div class="field-hint">如果包含链接，Surf 会自动提取其中第一个 http/https URL；如果没有链接，会直接把这段文字保存为帖子，第一句作为标题。</div>
                </div>
                
                <div class="section-title">获取内容</div>
                <div class="options-grid">
                    <div class="form-group">
                        <label>代理模式</label>
                        <div class="radio-group" id="proxyGroup">
                            {% if is_windows %}
                            <div class="radio-option">
                                <input type="radio" id="proxy-win" name="proxy" value="win" {% if default_proxy_mode == 'win' %}checked{% endif %}>
                                <label for="proxy-win">Windows 系统代理</label>
                            </div>
                            {% endif %}
                            <div class="radio-option">
                                <input type="radio" id="proxy-env" name="proxy" value="env" {% if default_proxy_mode == 'env' %}checked{% endif %}>
                                <label for="proxy-env">环境变量</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="proxy-custom" name="proxy" value="custom" {% if default_proxy_mode == 'custom' %}checked{% endif %}>
                                <label for="proxy-custom">自定义代理</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="proxy-no" name="proxy" value="no" {% if default_proxy_mode == 'no' %}checked{% endif %}>
                                <label for="proxy-no">不使用代理</label>
                            </div>
                        </div>
                    </div>
                    
                    <div class="form-group" id="customProxyGroup" style="display: none;">
                        <label for="customProxy">自定义代理地址</label>
                        <input type="text" id="customProxy" name="custom_proxy" 
                               value="{{ default_custom_proxy }}"
                               placeholder="http://127.0.0.1:7890">
                    </div>

                    <div class="form-group checkbox-group">
                        <input type="checkbox" id="browser" name="browser">
                        <label for="browser">使用浏览器渲染 (JavaScript)</label>
                    </div>

                    <div class="form-group wide">
                        <label>线程抓取</label>
                        <div class="radio-group">
                            <div class="radio-option">
                                <input type="radio" id="thread-default" name="thread_mode" value="default" checked>
                                <label for="thread-default">跟随站点默认</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="thread-off" name="thread_mode" value="off">
                                <label for="thread-off">关闭</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="thread-backward" name="thread_mode" value="backward">
                                <label for="thread-backward">向后抓取同作者后续回复</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="thread-forward" name="thread_mode" value="forward">
                                <label for="thread-forward">向前抓取上下文</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="thread-both" name="thread_mode" value="both">
                                <label for="thread-both">双向抓取</label>
                            </div>
                        </div>
                        <div class="field-hint">对应 CLI 的 `--thread/--no-thread`。仅支持的社交站点会生效。</div>
                    </div>
                </div>

                <div class="section-title">内容处理</div>
                <div class="options-grid">
                    <div class="form-group">
                        <label>语言模式</label>
                        <div class="radio-group">
                            <div class="radio-option">
                                <input type="radio" id="lang-trans" name="lang" value="trans" checked>
                                <label for="lang-trans">翻译为中文</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="lang-raw" name="lang" value="raw">
                                <label for="lang-raw">保留原文</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="lang-both" name="lang" value="both">
                                <label for="lang-both">双语对照</label>
                            </div>
                        </div>
                    </div>

                    <div class="form-group" id="llmProviderGroup">
                        <label>LLM Provider</label>
                        <div class="radio-group">
                            {% for provider in llm_providers %}
                            <div class="radio-option">
                                <input type="radio" id="llm-{{ loop.index }}" name="llm" value="{{ provider }}" {% if provider == default_llm_provider %}checked{% endif %}>
                                <label for="llm-{{ loop.index }}">{{ provider }}</label>
                            </div>
                            {% endfor %}
                        </div>
                    </div>

                    <div class="form-group">
                        <label>图片 OCR</label>
                        <div class="radio-group">
                            <div class="radio-option">
                                <input type="radio" id="ocr-mode-default" name="ocr_mode" value="default" checked>
                                <label for="ocr-mode-default">跟随站点默认</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="ocr-mode-on" name="ocr_mode" value="on">
                                <label for="ocr-mode-on">启用</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="ocr-mode-off" name="ocr_mode" value="off">
                                <label for="ocr-mode-off">关闭</label>
                            </div>
                        </div>
                    </div>

                    <div class="form-group" id="ocrEngineGroup" {% if not default_ocr_enabled %}style="display: none;"{% endif %}>
                        <label>OCR 引擎</label>
                        <div class="radio-group">
                            <div class="radio-option">
                                <input type="radio" id="ocr-engine-rapidocr" name="ocr_engine" value="rapidocr" {% if default_ocr_engine == 'rapidocr' %}checked{% endif %}>
                                <label for="ocr-engine-rapidocr">RapidOCR</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="ocr-engine-tesseract" name="ocr_engine" value="tesseract" {% if default_ocr_engine == 'tesseract' %}checked{% endif %}>
                                <label for="ocr-engine-tesseract">Tesseract</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="ocr-engine-auto" name="ocr_engine" value="auto" {% if default_ocr_engine == 'auto' %}checked{% endif %}>
                                <label for="ocr-engine-auto">自动回退</label>
                            </div>
                        </div>
                    </div>

                    <div class="form-group" id="ocrLangGroup" style="display: none;">
                        <label>OCR 语言</label>
                        <div class="radio-group">
                            {% for option in ocr_lang_options %}
                            <div class="radio-option">
                                <input type="radio" id="ocr-lang-{{ loop.index }}" name="ocr_lang" value="{{ option.value }}" {% if option.checked %}checked{% endif %}>
                                <label for="ocr-lang-{{ loop.index }}">{{ option.label }}</label>
                            </div>
                            {% endfor %}
                        </div>
                    </div>
                </div>

                <div class="section-title">输出设置</div>
                <div class="options-grid">
                    <div class="form-group wide">
                        <label>格式</label>
                        <div class="radio-group">
                            <div class="radio-option">
                                <input type="radio" id="format-md" name="format" value="md" checked>
                                <label for="format-md">Markdown (.md)</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="format-html" name="format" value="html">
                                <label for="format-html">HTML (.html)</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="format-pdf" name="format" value="pdf">
                                <label for="format-pdf">PDF (.pdf)</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="format-audio" name="format" value="audio">
                                <label for="format-audio">Audio (.mp3)</label>
                            </div>
                        </div>
                    </div>
                    
                    <div class="form-group checkbox-group" id="htmlInlineGroup" style="display: none;">
                        <input type="checkbox" id="htmlInline" name="html_inline">
                        <label for="htmlInline">内联 CSS/JS (HTML)</label>
                    </div>

                    <div class="form-group checkbox-group" id="noFrontMatterGroup">
                        <input type="checkbox" id="noFrontMatter" name="no_front_matter">
                        <label for="noFrontMatter">禁用 YAML Front Matter</label>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 25px;">
                    <button type="submit" class="btn btn-primary" id="submitBtn">
                        开始获取
                    </button>
                </div>
            </form>
        </div>
        
        <div class="card result-card" id="resultCard">
            <div class="result-header">
                <h2 class="result-title" id="resultTitle">转换结果</h2>
                <div class="save-actions">
                    <div class="save-links" id="saveLinks">
                        <!-- Save buttons will be added here -->
                    </div>
                </div>
            </div>
            
            <div class="tabs">
                <button class="tab active" data-tab="markdown">Markdown</button>
                <button class="tab" data-tab="html">HTML</button>
                <button class="tab" data-tab="raw">原始内容</button>
            </div>
            
            <div class="tab-content active" id="tab-markdown">
                <pre class="content-preview" id="markdownContent"></pre>
            </div>
            <div class="tab-content" id="tab-html">
                <pre class="content-preview" id="htmlContent"></pre>
            </div>
            <div class="tab-content" id="tab-raw">
                <pre class="content-preview" id="rawContent"></pre>
            </div>
        </div>
        
        <div class="version-info">
            Surf v{{ version }} | 运行本地服务器
        </div>
    </div>
    
    <script>
        let proxyModeTouched = false;
        let proxyModeProgrammaticUpdate = false;
        let langModeTouched = false;
        let langModeProgrammaticUpdate = false;
        let currentSiteDefaults = {
            site_name: null,
            lang_mode: 'trans',
            ocr_enabled: {{ 'true' if default_ocr_enabled else 'false' }},
        };
        let siteDefaultsRequestId = 0;
        let siteDefaultsTimer = null;
        const urlInput = document.getElementById('url');

        function getCheckedRadioValue(name) {
            const checked = document.querySelector(`input[name="${name}"]:checked`);
            return checked ? checked.value : '';
        }

        function setCheckedRadioValue(name, value) {
            const radio = Array.from(document.querySelectorAll(`input[name="${name}"]`))
                .find((input) => input.value === value);
            if (radio) {
                radio.checked = true;
            }
            return radio;
        }

        // Show/hide custom proxy input
        function updateCustomProxyVisibility() {
            const customGroup = document.getElementById('customProxyGroup');
            customGroup.style.display = getCheckedRadioValue('proxy') === 'custom' ? 'block' : 'none';
        }

        document.querySelectorAll('input[name="proxy"]').forEach((input) => {
            input.addEventListener('change', function() {
                updateCustomProxyVisibility();
                if (!proxyModeProgrammaticUpdate) {
                    proxyModeTouched = true;
                }
            });
        });
        proxyModeProgrammaticUpdate = true;
        updateCustomProxyVisibility();
        proxyModeProgrammaticUpdate = false;

        async function refreshProxyDefault(force = false) {
            if (!force && proxyModeTouched) {
                return;
            }
            try {
                const url = (urlInput?.value || '').trim();
                const query = url ? ('?url=' + encodeURIComponent(url)) : '';
                const response = await fetch('/api/proxy-default' + query);
                const result = await parseJsonResponse(response);
                if (result.success && result.proxy_mode) {
                    proxyModeProgrammaticUpdate = true;
                    setCheckedRadioValue('proxy', result.proxy_mode);
                    updateCustomProxyVisibility();
                    proxyModeProgrammaticUpdate = false;
                }
            } catch (error) {
                // Ignore default-proxy refresh errors and keep current selection.
                proxyModeProgrammaticUpdate = false;
            }
        }

        if (urlInput) {
            urlInput.addEventListener('blur', () => refreshProxyDefault(false));
        }
        refreshProxyDefault(true);

        async function refreshSiteDefaults(force = false) {
            const requestId = ++siteDefaultsRequestId;
            try {
                const url = (urlInput?.value || '').trim();
                const query = url ? ('?url=' + encodeURIComponent(url)) : '';
                const response = await fetch('/api/site-defaults' + query);
                const result = await parseJsonResponse(response);
                if (requestId !== siteDefaultsRequestId || !result.success) {
                    return;
                }

                currentSiteDefaults = {
                    site_name: result.site_name || null,
                    lang_mode: result.lang_mode || 'trans',
                    ocr_enabled: !!result.ocr_enabled,
                };

                if ((force || !langModeTouched) && currentSiteDefaults.lang_mode) {
                    langModeProgrammaticUpdate = true;
                    setCheckedRadioValue('lang', currentSiteDefaults.lang_mode);
                    updateLanguageControls();
                    langModeProgrammaticUpdate = false;
                }
                updateOcrControls();
            } catch (error) {
                langModeProgrammaticUpdate = false;
            }
        }

        function scheduleSiteDefaultsRefresh() {
            window.clearTimeout(siteDefaultsTimer);
            siteDefaultsTimer = window.setTimeout(() => {
                refreshSiteDefaults(false);
                refreshProxyDefault(false);
            }, 250);
        }

        if (urlInput) {
            urlInput.addEventListener('input', scheduleSiteDefaultsRefresh);
            urlInput.addEventListener('blur', () => refreshSiteDefaults(false));
        }

        // Show/hide format-specific options
        function updateFormatSpecificOptions() {
            const format = getCheckedRadioValue('format');

            // YAML Front Matter option - only for markdown
            const noFrontMatterGroup = document.getElementById('noFrontMatterGroup');
            if (noFrontMatterGroup) {
                noFrontMatterGroup.style.display = format === 'md' ? 'flex' : 'none';
            }

            // HTML inline option - only for html
            const htmlInlineGroup = document.getElementById('htmlInlineGroup');
            if (htmlInlineGroup) {
                htmlInlineGroup.style.display = format === 'html' ? 'flex' : 'none';
            }
        }

        document.querySelectorAll('input[name="format"]').forEach((input) => {
            input.addEventListener('change', updateFormatSpecificOptions);
        });

        // Initialize format-specific options visibility on page load
        updateFormatSpecificOptions();

        function updateOcrControls() {
            const ocrMode = getCheckedRadioValue('ocr_mode');
            const effectiveOcrEnabled = ocrMode === 'on' || (
                ocrMode === 'default' && currentSiteDefaults.ocr_enabled
            );
            const disabled = !effectiveOcrEnabled;
            const useTesseract = getCheckedRadioValue('ocr_engine') === 'tesseract';
            const ocrEngineGroup = document.getElementById('ocrEngineGroup');
            if (ocrEngineGroup) {
                ocrEngineGroup.style.display = disabled ? 'none' : 'block';
            }
            document.querySelectorAll('input[name="ocr_engine"]').forEach((input) => {
                input.disabled = disabled;
            });
            const ocrLangGroup = document.getElementById('ocrLangGroup');
            const showOcrLang = !disabled && useTesseract;
            if (ocrLangGroup) {
                ocrLangGroup.style.display = showOcrLang ? 'block' : 'none';
            }
            document.querySelectorAll('input[name="ocr_lang"]').forEach((input) => {
                input.disabled = !showOcrLang;
            });
        }

        document.querySelectorAll('input[name="ocr_mode"], input[name="ocr_engine"]').forEach((input) => {
            input.addEventListener('change', updateOcrControls);
        });

        function updateLanguageControls() {
            const keepOriginal = getCheckedRadioValue('lang') === 'raw';
            const llmProviderGroup = document.getElementById('llmProviderGroup');
            if (llmProviderGroup) {
                llmProviderGroup.style.display = keepOriginal ? 'none' : 'block';
            }
            document.querySelectorAll('input[name="llm"]').forEach((input) => {
                input.disabled = keepOriginal;
            });
        }

        document.querySelectorAll('input[name="lang"]').forEach((input) => {
            input.addEventListener('change', function() {
                updateLanguageControls();
                if (!langModeProgrammaticUpdate) {
                    langModeTouched = true;
                }
            });
        });

        refreshSiteDefaults(true);

        // Tab switching
        document.querySelectorAll('.tab').forEach(tab => {
            tab.addEventListener('click', function() {
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                
                this.classList.add('active');
                document.getElementById('tab-' + this.dataset.tab).classList.add('active');
            });
        });

        async function parseJsonResponse(response) {
            const text = await response.text();
            let result = null;

            try {
                result = text ? JSON.parse(text) : {};
            } catch (error) {
                const snippet = text.trim().slice(0, 240) || ('HTTP ' + response.status);
                throw new Error(`HTTP ${response.status}: ${snippet}`);
            }

            if (!response.ok) {
                const message = result?.error || result?.message || (`HTTP ${response.status}`);
                throw new Error(message);
            }

            return result;
        }
        
        // Form submission
        document.getElementById('surfForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            
            const submitBtn = document.getElementById('submitBtn');
            const statusDiv = document.getElementById('status') || createStatusDiv();
            const resultCard = document.getElementById('resultCard');
            
            submitBtn.disabled = true;
            submitBtn.innerHTML = '<span class="spinner"></span>处理中...';
            currentResult = null;
            if (resultCard) {
                resultCard.classList.remove('show');
            }
            
            showStatus('processing', '正在获取网页内容...');
            
            const formData = new FormData(this);
            const data = Object.fromEntries(formData.entries());
            if (!data.proxy) {
                data.proxy = getCheckedRadioValue('proxy') || 'env';
            }
             
            // Convert checkboxes to booleans
            data.browser = data.browser === 'on';
            data.lang_touched = langModeTouched;
            data.html_inline = data.html_inline === 'on';
            data.no_front_matter = data.no_front_matter === 'on';
            
            try {
                const response = await fetch('/api/process', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(data)
                });
                
                const result = await parseJsonResponse(response);
                
                if (result.success) {
                    showStatus('success', '获取完成！');

                    // Update result card
                    document.getElementById('resultTitle').textContent = result.title;
                    document.getElementById('markdownContent').textContent = result.markdown;
                    document.getElementById('htmlContent').textContent = result.html;
                    document.getElementById('rawContent').textContent = result.raw;

                    // Store result data for saving
                    currentResult = result;
                    window.defaultDirs = result.defaultDirs || {};
                    
                    // Update save buttons - always show all buttons
                    const saveLinks = document.getElementById('saveLinks');
                    saveLinks.innerHTML = '';

                    // Markdown button
                    const mdBtn = document.createElement('button');
                    mdBtn.className = 'save-btn save-md';
                    mdBtn.innerHTML = '📄 保存 Markdown';
                    mdBtn.onclick = () => saveFile('md');
                    saveLinks.appendChild(mdBtn);

                    // HTML button
                    const htmlBtn = document.createElement('button');
                    htmlBtn.className = 'save-btn save-html';
                    htmlBtn.innerHTML = '🌐 保存 HTML';
                    htmlBtn.onclick = () => saveFile('html');
                    saveLinks.appendChild(htmlBtn);

                    // PDF button
                    const pdfBtn = document.createElement('button');
                    pdfBtn.className = 'save-btn save-pdf';
                    pdfBtn.innerHTML = '📕 保存 PDF';
                    pdfBtn.onclick = () => saveFile('pdf');
                    saveLinks.appendChild(pdfBtn);

                    // Audio button
                    const audioBtn = document.createElement('button');
                    audioBtn.className = 'save-btn save-audio';
                    audioBtn.innerHTML = '🔊 保存 Audio';
                    audioBtn.onclick = () => saveFile('audio');
                    saveLinks.appendChild(audioBtn);

                    // Speak button
                    const playBtn = document.createElement('button');
                    playBtn.className = 'save-btn save-audio play-btn';
                    playBtn.innerHTML = '▶ 播放语音';
                    playBtn.onclick = () => saveFile('audio', { speak: true, promptForDir: false });
                    saveLinks.appendChild(playBtn);
                    
                    document.getElementById('resultCard').classList.add('show');
                } else {
                    showStatus('error', '错误: ' + result.error);
                }
            } catch (error) {
                showStatus('error', '请求失败: ' + error.message);
            } finally {
                submitBtn.disabled = false;
                submitBtn.textContent = '开始获取';
            }
        });
        
        function createStatusDiv() {
            const div = document.createElement('div');
            div.id = 'status';
            div.className = 'card';
            div.style.display = 'none';
            document.querySelector('.card').after(div);
            return div;
        }
        
        function showStatus(type, message) {
            let statusDiv = document.getElementById('status');
            if (!statusDiv) {
                statusDiv = createStatusDiv();
            }

            statusDiv.className = 'card status status-' + type + ' show';
            statusDiv.innerHTML = message;
            statusDiv.style.display = 'block';
        }

        // Store result data for saving
        let currentResult = null;

        async function saveFile(fileType, options = {}) {
            if (!currentResult) {
                showStatus('error', '没有可保存的内容');
                return;
            }

            // Get default directory from config
            const defaultDir = window.defaultDirs[fileType] || '';
            const promptForDir = options.promptForDir !== false;
            const speak = !!options.speak;

            let saveDir = '';
            if (promptForDir) {
                saveDir = prompt(
                    `保存 ${fileType.toUpperCase()} 文件\n\n输入保存目录（留空使用默认目录 ${defaultDir}）：`,
                    ''
                );

                // If user cancelled
                if (saveDir === null) {
                    return;
                }
            }

            try {
                const response = await fetch('/api/save', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        fileType,
                        saveDir: saveDir.trim(),
                        data: currentResult,
                        speak
                    })
                });

                const result = await parseJsonResponse(response);

                if (result.success) {
                    if (speak) {
                        showStatus('success', `语音已播放，音频文件已保存到: ${result.savePath}`);
                    } else {
                        showStatus('success', `${fileType.toUpperCase()} 文件已保存到: ${result.savePath}`);
                    }
                } else {
                    showStatus('error', '保存失败: ' + result.error);
                }
            } catch (error) {
                showStatus('error', '请求失败: ' + error.message);
            }
        }
    </script>
</body>
</html>
"""


def get_config():
    """Get config object."""
    config_path = resolve_user_path("config.ini")
    if os.path.exists(config_path):
        return Config(config_path)
    return Config()


def normalize_web_proxy_mode(mode):
    normalized = Fetcher._normalize_proxy_mode(mode)
    if normalized == "win" and not Fetcher._is_windows():
        return "env"
    if normalized in {"env", "win", "custom", "no"}:
        return normalized
    return "env"


def resolve_web_proxy_mode_default(config, url=None):
    """
    Resolve Web UI default proxy mode.

    Surf Web is commonly deployed on a server where direct outbound access is
    expected. Keep proxy usage opt-in from the Web UI instead of inheriting CLI
    or special-site proxy defaults such as V2EX's local-machine preference.
    """
    return "no"


def get_runtime_version():
    """Read the current project version at request time."""
    return _get_version()


def extract_url_from_text(value):
    """Extract the first http/https URL from free-form text."""
    text = (value or "").strip()
    if not text:
        return None

    match = re.search(r"https?://\S+", text, re.IGNORECASE)
    if not match:
        return None

    candidate = match.group(0).strip()
    candidate = candidate.rstrip('\'"<>)]}.,;!?:')
    return candidate or None


def extract_text_post_title(value):
    """Derive a post title from free-form text using the first sentence."""
    text = (value or "").strip()
    if not text:
        return "Untitled"
    return OutputHandler._extract_first_sentence(text) or "Untitled"


def build_text_post_html(text, title):
    """Wrap free-form text in a minimal article HTML document."""
    normalized = (text or "").strip()
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
    if not paragraphs:
        paragraphs = [normalized] if normalized else []

    body_parts = []
    for paragraph in paragraphs:
        escaped_paragraph = escape(paragraph).replace("\n", "<br>")
        body_parts.append(f"<p>{escaped_paragraph}</p>")

    body_html = "\n".join(body_parts)
    escaped_title = escape(title or "Untitled")
    return (
        "<html><head><meta charset='utf-8'>"
        f"<title>{escaped_title}</title>"
        "</head><body><article>"
        f"{body_html}"
        "</article></body></html>"
    )


def extract_source_url_from_html(html_blob, default_url):
    if not html_blob:
        return default_url
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html_blob, "html.parser")
        meta_tag = soup.find("meta", attrs={"name": "source-url"})
        if meta_tag and meta_tag.get("content"):
            return meta_tag["content"]
    except Exception:
        pass
    return default_url


def build_web_ocr_args(data):
    """Build an args-like object for OCR settings reused from the CLI pipeline."""
    ocr_mode = str(data.get("ocr_mode", "default")).strip().lower()
    return SimpleNamespace(
        ocr_images=ocr_mode == "on",
        no_ocr_images=ocr_mode == "off",
        ocr_lang=(data.get("ocr_lang") or "").strip() or None,
        ocr_engine=(data.get("ocr_engine") or "").strip() or None,
    )


def get_web_ui_context(config):
    default_custom_proxy = (config.get("Network", "custom_proxy", fallback="") or "").strip()
    default_llm_provider = config.llm_provider
    llm_providers = config._get_available_llm_providers()
    if default_llm_provider and default_llm_provider not in llm_providers:
        llm_providers.insert(0, default_llm_provider)

    default_ocr_engine = (
        config.get("OCR", "engine", fallback="rapidocr").strip().lower() or "rapidocr"
    )
    if default_ocr_engine not in {"rapidocr", "tesseract", "auto"}:
        default_ocr_engine = "rapidocr"

    default_ocr_lang = (config.get("OCR", "lang", fallback="chi_sim+eng") or "").strip()
    if not default_ocr_lang:
        default_ocr_lang = "chi_sim+eng"

    ocr_lang_values = [default_ocr_lang, "chi_sim+eng", "eng", "jpn+eng"]
    seen = set()
    ocr_lang_options = []
    for value in ocr_lang_values:
        if value in seen:
            continue
        seen.add(value)
        label = value
        if value == default_ocr_lang:
            label = f"默认 ({value})"
        ocr_lang_options.append(
            {"value": value, "label": label, "checked": value == default_ocr_lang}
        )

    return {
        "default_custom_proxy": default_custom_proxy,
        "default_llm_provider": default_llm_provider,
        "llm_providers": llm_providers,
        "default_ocr_engine": default_ocr_engine,
        "ocr_lang_options": ocr_lang_options,
    }


def build_output_path(title, extension, target_dir, source_url=None, html_content=None):
    filename_title = OutputHandler._get_filename_title(
        title, source_url=source_url, html_content=html_content
    )
    safe_title = OutputHandler._safe_filename_title(filename_title, max_len=100)
    return os.path.join(
        resolve_user_path(target_dir),
        f"{safe_title}.{extension}",
    )


def resolve_web_thread_mode(data, site_name, site_config):
    """Resolve web thread settings using the same semantics as CLI thread flags."""
    thread_mode = str(data.get("thread_mode", "default")).strip().lower()
    if thread_mode == "off":
        return False
    if thread_mode in {"forward", "backward", "both"}:
        return thread_mode
    if site_config and site_config.get("default_thread"):
        logger.info(f"Web: {site_name} using default thread expansion")
        return "backward"
    return None


def resolve_web_site_defaults(config, url=None):
    """Resolve URL-sensitive Web UI defaults without fetching page content."""
    site_name = None
    site_config = None
    if url:
        _, site_name, site_config = _get_handler_for_url(url)

    args = SimpleNamespace(ocr_images=False, no_ocr_images=False)
    ocr_enabled = OcrHandler._is_enabled_for_site(site_name, site_config, args, config)
    lang_mode = "raw" if site_config and site_config.get("default_no_translate") else "trans"

    return {
        "site_name": site_name,
        "lang_mode": lang_mode,
        "ocr_enabled": bool(ocr_enabled),
    }


@app.route("/")
def index():
    """Serve the main page."""
    config = get_config()
    ui_context = get_web_ui_context(config)
    return render_template_string(
        HTML_TEMPLATE,
        version=get_runtime_version(),
        is_windows=Fetcher._is_windows(),
        default_proxy_mode=resolve_web_proxy_mode_default(config),
        default_ocr_enabled=resolve_web_site_defaults(config)["ocr_enabled"],
        **ui_context,
    )


@app.route("/api/proxy-default", methods=["GET"])
def proxy_default():
    """Resolve the default proxy mode for current URL/context."""
    config = get_config()
    url = extract_url_from_text(request.args.get("url"))
    mode = resolve_web_proxy_mode_default(config, url=url)
    return jsonify({"success": True, "proxy_mode": mode})


@app.route("/api/site-defaults", methods=["GET"])
def site_defaults():
    """Resolve URL-sensitive defaults for Web UI option visibility."""
    config = get_config()
    url = extract_url_from_text(request.args.get("url"))
    defaults = resolve_web_site_defaults(config, url=url)
    return jsonify({"success": True, **defaults})


@app.route("/api/process", methods=["POST"])
def process_url():
    """Process a URL or free-form text post and return the result."""

    data = request.get_json(silent=True) or {}
    raw_url_input = data.get("url")
    url = extract_url_from_text(raw_url_input)
    raw_text = (raw_url_input or "").strip()
    is_text_post = bool(raw_text and not url)

    config = get_config()

    try:
        original_md = ""
        original_title = "Untitled"
        translated_title = None
        content_base_url = None

        # Fetch content
        proxy_mode = normalize_web_proxy_mode(data.get("proxy", "env"))
        custom_proxy = (data.get("custom_proxy") or "").strip() or None
        if proxy_mode == "custom" and not custom_proxy:
            return jsonify({"success": False, "error": "custom 代理模式需要填写自定义代理地址"})
        
        # Language mode
        lang_mode = data.get("lang", "trans")
        if is_text_post:
            source_url = None
            site_name = "text"
            site_config = {}
            title = extract_text_post_title(raw_text)
            cleaned_html = build_text_post_html(raw_text, title)
            html_content = cleaned_html
            md_content = ContentProcessor.to_markdown(cleaned_html)
            original_md = md_content
            original_title = title
        else:
            if not url:
                return jsonify({"success": False, "error": "A valid http/https URL is required"})

            url = Fetcher._resolve_common_short_url(
                url,
                config,
                proxy_mode_override=proxy_mode,
                custom_proxy_override=custom_proxy,
            )
            _, site_name, site_config = _get_handler_for_url(url)
            fetch_thread = resolve_web_thread_mode(data, site_name, site_config)
            if site_config:
                if (
                    site_config.get("default_no_translate")
                    and lang_mode == "trans"
                    and not data.get("lang_touched", False)
                ):
                    logger.info(f"Web: {site_name} using default 'no translate'")
                    lang_mode = "raw"

            html_content = Fetcher.fetch(
                url,
                config=config,
                use_browser=data.get("browser", False),
                proxy_mode_override=proxy_mode,
                custom_proxy_override=custom_proxy,
                fetch_thread=fetch_thread,
            )
            if not html_content:
                return jsonify({"success": False, "error": f"Failed to fetch usable content from {url}"})

            source_url = extract_source_url_from_html(html_content, url)
            content_base_url = source_url
            direct_markdown_payload = _extract_direct_markdown_payload(html_content)

            if direct_markdown_payload:
                title = direct_markdown_payload.get("title") or "Untitled"
                md_content = direct_markdown_payload.get("markdown") or ""
                content_base_url = direct_markdown_payload.get("base_url") or source_url
                cleaned_html = _render_markdown_to_html(md_content)
            else:
                # Extract content
                title, cleaned_html = ContentProcessor.extract_content(html_content)
                if not title:
                    title = "Untitled"

            if not direct_markdown_payload:
                try:
                    cleaned_html = OcrHandler.annotate_html_with_ocr(
                        cleaned_html,
                        source_url=source_url,
                        site_name=site_name,
                        site_config=site_config,
                        args=build_web_ocr_args(data),
                        config=config,
                        proxy_mode_override=proxy_mode,
                        custom_proxy_override=custom_proxy,
                    )
                except Exception as e:
                    logger.warning(f"Web OCR failed and was skipped: {e}")

            if not direct_markdown_payload:
                # Convert to markdown
                md_content = ContentProcessor.to_markdown(cleaned_html)

            social_title = OutputHandler._extract_social_first_sentence_title(
                html_content, source_url=source_url
            )
            if social_title:
                title = social_title

            original_md = md_content
            original_title = title

        # Handle language mode
        target_lang = config.get("Output", "target_language", fallback="zh-cn")

        skip_title_translation = site_config.get("skip_title_translation", False) if site_config else False

        if lang_mode != "raw":
            llm_provider = (data.get("llm") or "").strip() or None
            md_content, translated_title = ContentProcessor.translate_if_needed(
                md_content,
                title=None if skip_title_translation else title,
                target_lang=target_lang,
                config=config,
                llm_provider=llm_provider,
            )
            if skip_title_translation:
                translated_title = original_title

            if lang_mode == "both" and translated_title != original_title:
                title = f"{translated_title} ({original_title})"
                md_content = f"{md_content}\n\n---\n\n### Original Content / 原文内容\n\n{original_md}"
            else:
                title = translated_title

        # Convert relative URLs to absolute
        base_url_for_links = content_base_url or source_url or url
        if base_url_for_links:
            md_content = OutputHandler._convert_markdown_urls_to_absolute(md_content, base_url_for_links)
            cleaned_html = OutputHandler._convert_urls_to_absolute(cleaned_html, base_url_for_links)

        # Determine if translation was performed for YAML front matter
        translation_performed = lang_mode != "raw"
        translator = None
        if translation_performed:
            try:
                llm_provider = (data.get("llm") or "").strip() or None
                llm_config = config.get_llm_config(llm_provider)
                translator = llm_config["model"]
            except Exception:
                pass

        # Get default directories for frontend
        defaultDirs = {
            "md": config.get_path("Output", "md_dir", fallback="notes"),
            "html": config.get_path("Output", "html_dir", fallback="web"),
            "pdf": config.get_path("Output", "pdf_dir", fallback="pdf"),
            "audio": config.get_path("Output", "audio_dir", fallback="audio"),
        }

        return jsonify(
            {
                "success": True,
                "title": title,
                "markdown": md_content,
                "html": cleaned_html,
                "raw": original_md,
                "defaultDirs": defaultDirs,
                # Store metadata for saving later
                "metadata": {
                    "title": title,
                    "html_content": html_content,
                    "add_front_matter": not data.get("no_front_matter", False),
                    "translated_title": translated_title
                    if translation_performed
                    else None,
                    "source_url": source_url,
                    "translator": translator,
                    "html_inline": data.get("html_inline", False),
                },
            }
        )

    except Exception as e:
        logger.error(f"Processing failed: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route("/download/<filename>")
def download_file(filename):
    """Download a generated file."""
    # Search in common output directories
    search_dirs = [".", "notes", "pdf", "audio", "web", "html"]

    for directory in search_dirs:
        filepath = os.path.join(directory, filename)
        if os.path.exists(filepath):
            return send_file(filepath, as_attachment=True)

    return jsonify({"error": "File not found"}), 404


@app.route("/api/save", methods=["POST"])
def save_file():
    """Generate and save a file to specified directory."""
    data = request.get_json(silent=True) or {}
    fileType = data.get("fileType")
    saveDir = data.get("saveDir", "").strip()
    resultData = data.get("data", {})
    speak = bool(data.get("speak"))

    if not fileType:
        return jsonify({"success": False, "error": "File type is required"})

    config = get_config()

    # Determine default directories based on config
    defaultDirs = {
        "md": config.get_path("Output", "md_dir", fallback="notes"),
        "html": config.get_path("Output", "html_dir", fallback="web"),
        "pdf": config.get_path("Output", "pdf_dir", fallback="pdf"),
        "audio": config.get_path("Output", "audio_dir", fallback="audio"),
    }

    # Use user-specified directory or default
    if saveDir:
        targetDir = resolve_user_path(saveDir)
    else:
        targetDir = defaultDirs.get(fileType, ".")

    # Create target directory if it doesn't exist
    os.makedirs(targetDir, exist_ok=True)

    try:
        title = resultData.get("title", "Untitled")
        metadata = resultData.get("metadata", {})
        html_content = metadata.get("html_content", "")
        source_url = metadata.get("source_url")

        if fileType == "md":
            md_content = resultData.get("markdown", "")
            output_path = build_output_path(
                title, "md", targetDir, source_url=source_url, html_content=html_content
            )
            md_path = OutputHandler.save_markdown(
                title,
                md_content,
                config,
                output_path=output_path,
                html_content=html_content,
                add_front_matter=metadata.get("add_front_matter", True),
                translated_title=metadata.get("translated_title"),
                source_url=metadata.get("source_url"),
                translator=metadata.get("translator"),
            )
            return jsonify({"success": True, "savePath": md_path})

        elif fileType == "html":
            cleaned_html = resultData.get("html", "")
            output_path = build_output_path(
                title, "html", targetDir, source_url=source_url, html_content=html_content
            )
            html_path = OutputHandler.save_html(
                title,
                cleaned_html,
                config,
                inline=metadata.get("html_inline", False),
                output_path=output_path,
            )
            return jsonify({"success": True, "savePath": html_path})

        elif fileType == "pdf":
            md_content = resultData.get("markdown", "")
            output_path = build_output_path(
                title, "pdf", targetDir, source_url=source_url, html_content=html_content
            )
            pdf_path = OutputHandler.generate_pdf(
                title, md_content, config, output_path=output_path
            )
            return jsonify({"success": True, "savePath": pdf_path})

        elif fileType == "audio":
            md_content = resultData.get("markdown", "")
            output_path = build_output_path(
                title, "mp3", targetDir, source_url=source_url, html_content=html_content
            )
            TTSHandler.run_tts(
                title, md_content, config, speak=speak, save_path=output_path
            )

            return jsonify({"success": True, "savePath": output_path})

        else:
            return jsonify({"success": False, "error": "Unsupported file type"})

    except Exception as e:
        logger.error(f"Failed to save file: {e}")
        return jsonify({"success": False, "error": str(e)})


def run_server(host="127.0.0.1", port=18473, debug=False):
    """Run the web server."""
    # Open browser
    url = f"http://{host}:{port}"
    threading.Timer(1, lambda: webbrowser.open(url)).start()

    print(f"\nSurf Web Interface v{get_runtime_version()}")
    print("=====================================")
    print(f"Server running at: {url}")
    print("Press Ctrl+C to stop\n")

    app.run(host=host, port=port, debug=debug)


def main():
    parser = argparse.ArgumentParser(
        prog="python surf_web.py",
        description="Surf Web Interface - A web interface for URL to Markdown/PDF/HTML/Audio converter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=18473, help="Port to bind (default: 18473)"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("--bind", help="Bind address (deprecated, use --host)")

    args = parser.parse_args()

    host = args.bind if args.bind else args.host
    run_server(host=host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
