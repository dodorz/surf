#!/usr/bin/env -S uv run
"""
Surf Web Interface - A web interface for the Surf URL to Markdown/PDF converter.

Usage:
    python surf_web.py [--host HOST] [--port PORT]

Example:
    python surf_web.py --host 0.0.0.0 --port 8080
"""

import argparse
import copy
import importlib.util
import os
import re
import sys
import threading
import time
import traceback
import uuid
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

def _ensure_local_surf_module():
    """Make surf_web import the sibling surf.py even when an older surf is installed."""
    local_surf_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "surf.py"))
    loaded = sys.modules.get("surf")
    loaded_path = os.path.abspath(getattr(loaded, "__file__", "") or "") if loaded else ""
    if loaded_path == local_surf_path:
        return

    spec = importlib.util.spec_from_file_location("surf", local_surf_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load local surf module from {local_surf_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["surf"] = module
    spec.loader.exec_module(module)


_ensure_local_surf_module()


# Import surf modules
from surf import (
    Config,
    Fetcher,
    ContentProcessor,
    OcrHandler,
    OutputHandler,
    TTSHandler,
    _convert_embedded_html_in_markdown,
    _get_handler_for_url,
    _get_version,
    logger,
    _process_fetched_content,
    resolve_user_path,
)

app = Flask(__name__)
_TRANSLATION_JOBS = {}
_TRANSLATION_JOBS_LOCK = threading.Lock()
_SAVE_JOBS = {}
_SAVE_JOBS_LOCK = threading.Lock()

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

        .input-toolbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            margin-top: 12px;
            flex-wrap: wrap;
        }

        .input-actions {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
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

        .aggregate-card {
            border: 2px solid #d6defa;
            background: linear-gradient(180deg, #f8faff 0%, #ffffff 100%);
        }

        .aggregate-card .result-title {
            font-size: 1.15em;
            font-weight: 700;
            color: #3047b0;
        }
        
        .result-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 2px solid #e1e5e9;
        }
        
        .save-links {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }

        .save-actions {
            display: flex;
            flex-direction: column;
            align-items: stretch;
            justify-content: flex-start;
            gap: 12px;
            width: 100%;
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

        .save-config {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 12px;
            margin: 0;
        }

        .result-source {
            margin-bottom: 14px;
        }

        .save-field {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .save-field label {
            font-size: 13px;
            font-weight: 600;
            color: #4a5568;
        }

        .save-input {
            width: 100%;
            padding: 10px 12px;
            border: 1px solid #cfd8e3;
            border-radius: 8px;
            font-size: 14px;
            font-family: inherit;
            color: #1f2937;
            background: #fff;
        }

        .save-input:focus {
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.15);
        }
        
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

        .toast-container {
            position: fixed;
            bottom: 24px;
            right: 24px;
            z-index: 9999;
            display: flex;
            flex-direction: column;
            gap: 10px;
            max-width: 380px;
        }

        .toast {
            padding: 12px 18px;
            border-radius: 8px;
            color: #fff;
            font-size: 14px;
            font-weight: 500;
            box-shadow: 0 4px 14px rgba(0, 0, 0, 0.18);
            animation: toastIn 0.3s ease-out;
            cursor: pointer;
            opacity: 1;
            transition: opacity 0.3s;
        }

        .toast.fade-out {
            opacity: 0;
        }

        .toast-success { background: #28a745; }
        .toast-error { background: #dc3545; }
        .toast-info { background: #17a2b8; }

        @keyframes toastIn {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
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

            .input-toolbar {
                align-items: stretch;
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
                    <div class="input-toolbar">
                        <div class="checkbox-group">
                            <input type="checkbox" id="saveFullText" name="save_full_text">
                            <label for="saveFullText">全文保存</label>
                        </div>
                        <div class="input-actions">
                            <button type="button" class="btn btn-secondary" id="pasteBtn">粘贴</button>
                            <button type="submit" class="btn btn-primary" id="submitBtn">开始获取</button>
                        </div>
                    </div>
                    <div class="field-hint">如果包含链接，Surf 会自动提取其中第一个 http/https URL；如果没有链接，会直接把这段文字保存为帖子，第一句作为标题。</div>
                    <div class="field-hint">勾选“全文保存”后，即使文本里包含 URL，也会整段作为正文保存，不再提取链接抓取网页。</div>
                </div>
                
                <div class="section-title">获取内容</div>
                <div class="options-grid">
                    <div class="form-group">
                        <label>代理模式</label>
                        <div class="radio-group" id="proxyGroup">
                            <div class="radio-option">
                                <input type="radio" id="proxy-auto" name="proxy" value="auto" {% if default_proxy_mode == 'auto' %}checked{% endif %}>
                                <label for="proxy-auto">自动（与 CLI 一致）</label>
                            </div>
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
                                <input type="radio" id="thread-after" name="thread_mode" value="after">
                                <label for="thread-after">向后</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="thread-before" name="thread_mode" value="before">
                                <label for="thread-before">向前</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="thread-both" name="thread_mode" value="both">
                                <label for="thread-both">前后</label>
                            </div>
                        </div>
                        <div class="field-hint">对应 CLI 的 `--thread/--no-thread`。仅支持的社交站点会生效。</div>
                    </div>

                    <div class="form-group wide">
                        <label>线程作者范围</label>
                        <div class="radio-group">
                            <div class="radio-option">
                                <input type="radio" id="thread-author-all" name="thread_author" value="all" checked>
                                <label for="thread-author-all">所有作者</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="thread-author-same" name="thread_author" value="same">
                                <label for="thread-author-same">仅同作者</label>
                            </div>
                        </div>
                        <div class="field-hint">对应 CLI 的 `--thread-author same|all`。</div>
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

                    <div class="form-group checkbox-group" id="archiveSourceGroup">
                        <input type="checkbox" id="archiveSource" name="archive_source">
                        <label for="archiveSource">保存 source 到 Internet Archive</label>
                    </div>
                </div>
                
            </form>
        </div>
        
        <div id="resultsContainer"></div>
        <div id="toastContainer" class="toast-container"></div>
        
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
        
        function extractUrlsFromInput(value) {
            const matches = (value || '').match(/https?:\\/\\/[^\\s<>"']+/g) || [];
            const seen = new Set();
            const urls = [];
            for (const match of matches) {
                const cleaned = match.replace(/[`'")\\]}>,.;!?，。；！？、）】》]+$/g, '');
                if (cleaned && !seen.has(cleaned)) {
                    seen.add(cleaned);
                    urls.push(cleaned);
                }
            }
            return urls;
        }

        function addSaveButtons(container, result) {
            const saveLinks = container.querySelector('.save-links');
            saveLinks.innerHTML = '';

            const buttons = [
                ['md', 'save-md', '📄 保存 Markdown', {}],
                ['html', 'save-html', '🌐 保存 HTML', {}],
                ['pdf', 'save-pdf', '📕 保存 PDF', {}],
                ['audio', 'save-audio', '🔊 保存 Audio', {}],
                ['audio', 'save-audio play-btn', '▶ 播放语音', { speak: true }],
            ];

            for (const [fileType, className, label, options] of buttons) {
                const btn = document.createElement('button');
                btn.className = 'save-btn ' + className;
                btn.innerHTML = label;
                btn.onclick = () => saveFile(container, result, fileType, options);
                saveLinks.appendChild(btn);
            }
        }

        function stripMarkdownHeading(markdownText, title) {
            const normalized = (markdownText || '').replace(/^﻿/, '');
            const escapedTitle = (title || '').replace(/[.*+?^${}()|[\\]\\\\]/g, '\\$&');
            const headingPattern = new RegExp(`^#\\s+${escapedTitle}\\s*(?:\\r?\\n){1,2}`);
            return normalized.replace(headingPattern, '');
        }

        function mergeResultsForSave(results) {
            if (!results || !results.length) {
                return null;
            }

            const title = `Surf Collection (${results.length} items)`;
            const markdownSections = [];
            const htmlSections = [];
            const rawSections = [];
            const sourceLines = [];

            results.forEach((result, index) => {
                const itemTitle = result.title || `Item ${index + 1}`;
                const source = result?.metadata?.source_url || result?.input_url || '';
                const markdownBody = stripMarkdownHeading(result.markdown || '', itemTitle).trim();
                const rawBody = stripMarkdownHeading(result.raw || '', itemTitle).trim();
                const htmlBody = (result.html || '').trim();

                markdownSections.push(`## ${itemTitle}\n\n${markdownBody || '_No content_'}`);
                rawSections.push(`## ${itemTitle}\n\n${rawBody || '_No content_'}`);
                htmlSections.push(`<section><h2>${itemTitle}</h2>${htmlBody || '<p><em>No content.</em></p>'}</section>`);
                if (source) {
                    sourceLines.push(`- ${itemTitle}: ${source}`);
                }
            });

            const metadataHtml = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${title}</title></head><body></body></html>`;
            return {
                title,
                markdown: markdownSections.join('\\n\\n---\\n\\n'),
                raw: rawSections.join('\\n\\n---\\n\\n'),
                html: `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${title}</title></head><body>${htmlSections.join('')}</body></html>`,
                defaultDirs: results[0].defaultDirs || {},
                defaultSaveTitle: title,
                metadata: {
                    title,
                    html_content: metadataHtml,
                    add_front_matter: false,
                    translated_title: null,
                    source_url: null,
                    archive_url: null,
                    translator: null,
                    html_inline: false,
                    combined_sources: sourceLines,
                },
            };
        }

        function renderAggregateSaveCard(results) {
            if (!results || results.length <= 1) {
                return;
            }

            const aggregateResult = mergeResultsForSave(results);
            if (!aggregateResult) {
                return;
            }

            const container = document.getElementById('resultsContainer');
            const existingAggregate = container.querySelector('.aggregate-card');
            if (existingAggregate) {
                existingAggregate.remove();
            }
            const card = document.createElement('div');
            card.className = 'card result-card show aggregate-card';
            card.innerHTML = `
                <div class="result-header">
                    <h2 class="result-title">合并保存全部结果</h2>
                    <div class="save-actions">
                        <div class="save-links"></div>
                        <div class="save-config">
                            <div class="save-field">
                                <label>保存文件夹</label>
                                <input class="save-input save-dir-input" type="text">
                            </div>
                            <div class="save-field">
                                <label>文件标题</label>
                                <input class="save-input save-title-input" type="text">
                            </div>
                        </div>
                    </div>
                </div>
                <div class="field-hint result-source"></div>
                <div class="tabs">
                    <button class="tab active" data-tab="markdown">Markdown</button>
                    <button class="tab" data-tab="html">HTML</button>
                    <button class="tab" data-tab="raw">原始内容</button>
                </div>
                <div class="tab-content active" data-tab-content="markdown">
                    <pre class="content-preview"></pre>
                </div>
                <div class="tab-content" data-tab-content="html">
                    <pre class="content-preview"></pre>
                </div>
                <div class="tab-content" data-tab-content="raw">
                    <pre class="content-preview"></pre>
                </div>
            `;

            card.querySelector('.result-source').textContent = `合并保存 ${results.length} 张结果卡`;
            card.dataset.defaultDirs = JSON.stringify(aggregateResult.defaultDirs || {});
            card.dataset.defaultSaveTitle = aggregateResult.defaultSaveTitle || aggregateResult.title || 'Untitled';
            card.querySelector('[data-tab-content="markdown"] pre').textContent = aggregateResult.markdown || '';
            card.querySelector('[data-tab-content="html"] pre').textContent = aggregateResult.html || '';
            card.querySelector('[data-tab-content="raw"] pre').textContent = aggregateResult.raw || '';
            card.querySelector('.save-dir-input').value = (aggregateResult.defaultDirs || {}).md || '';
            card.querySelector('.save-title-input').value = aggregateResult.defaultSaveTitle || aggregateResult.title || 'Untitled';

            addSaveButtons(card, aggregateResult);
            wireResultTabs(card);
            container.prepend(card);
        }

        function resultDefaultDirs(container) {
            try {
                return JSON.parse(container.dataset.defaultDirs || '{}');
            } catch (error) {
                return {};
            }
        }

        function getSaveFormState(container, fileType) {
            const dirInput = container.querySelector('.save-dir-input');
            const titleInput = container.querySelector('.save-title-input');
            const defaultDirs = resultDefaultDirs(container);
            const defaultTitle = (container.dataset.defaultSaveTitle || '').trim();

            return {
                saveDir: (dirInput?.value || '').trim() || (defaultDirs[fileType] || ''),
                customTitle: (titleInput?.value || '').trim() || defaultTitle || 'Untitled',
            };
        }

        function wireResultTabs(card) {
            card.querySelectorAll('.tab').forEach(tab => {
                tab.addEventListener('click', function() {
                    card.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                    card.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                    this.classList.add('active');
                    const target = card.querySelector(`[data-tab-content="${this.dataset.tab}"]`);
                    if (target) {
                        target.classList.add('active');
                    }
                });
            });
        }

        function populateResultCard(card, result, index, total) {
            const label = total > 1 ? ` (${index + 1}/${total})` : '';
            const source = result?.metadata?.source_url || result?.input_url || '';

            card.innerHTML = `
                <div class="result-header">
                    <div class="save-actions">
                        <div class="save-links"></div>
                        <div class="save-config">
                            <div class="save-field">
                                <label>保存文件夹</label>
                                <input class="save-input save-dir-input" type="text">
                            </div>
                            <div class="save-field">
                                <label>文件标题</label>
                                <input class="save-input save-title-input" type="text">
                            </div>
                        </div>
                    </div>
                </div>
                ${source ? `<div class="field-hint result-source"></div>` : ''}
                ${result.translation_pending ? '<div class="field-hint">翻译处理中，先显示原文结果。</div>' : ''}
                <div class="tabs">
                    <button class="tab active" data-tab="markdown">Markdown</button>
                    <button class="tab" data-tab="html">HTML</button>
                    <button class="tab" data-tab="raw">原始内容</button>
                </div>
                <div class="tab-content active" data-tab-content="markdown">
                    <pre class="content-preview"></pre>
                </div>
                <div class="tab-content" data-tab-content="html">
                    <pre class="content-preview"></pre>
                </div>
                <div class="tab-content" data-tab-content="raw">
                    <pre class="content-preview"></pre>
                </div>
            `;

            const hint = card.querySelector('.result-source');
            if (hint) {
                hint.textContent = `${source}${label}`;
            }
            card.dataset.defaultDirs = JSON.stringify(result.defaultDirs || {});
            card.dataset.defaultSaveTitle = result.defaultSaveTitle || result.title || 'Untitled';
            card.dataset.inputUrl = result.input_url || '';
            card.dataset.translationJobId = result.translation_job_id || '';
            card.querySelector('[data-tab-content="markdown"] pre').textContent = result.markdown || '';
            card.querySelector('[data-tab-content="html"] pre').textContent = result.html || '';
            card.querySelector('[data-tab-content="raw"] pre').textContent = result.raw || '';
            card.querySelector('.save-dir-input').value = (result.defaultDirs || {}).md || '';
            card.querySelector('.save-title-input').value = result.defaultSaveTitle || result.title || 'Untitled';

            addSaveButtons(card, result);
            wireResultTabs(card);
            return card;
        }

        function renderResultCard(result, index, total) {
            const container = document.getElementById('resultsContainer');
            const card = document.createElement('div');
            card.className = 'card result-card show';
            populateResultCard(card, result, index, total);
            container.appendChild(card);
            return card;
        }

        function renderErrorCard(input, message, index, total) {
            const container = document.getElementById('resultsContainer');
            const card = document.createElement('div');
            card.className = 'card result-card show status-error';
            const label = total > 1 ? ` (${index + 1}/${total})` : '';
            card.innerHTML = `
                <div class="result-header">
                    <h2 class="result-title"></h2>
                </div>
                <div class="field-hint"></div>
                <pre class="content-preview"></pre>
            `;
            card.querySelector('.result-title').textContent = `获取失败${label}`;
            card.querySelector('.field-hint').textContent = input;
            card.querySelector('pre').textContent = message || 'Unknown error';
            container.appendChild(card);
        }

        // Form submission
        document.getElementById('surfForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            
            const submitBtn = document.getElementById('submitBtn');
            const statusDiv = document.getElementById('status') || createStatusDiv();
            const resultsContainer = document.getElementById('resultsContainer');
            
            submitBtn.disabled = true;
            submitBtn.innerHTML = '<span class="spinner"></span>处理中...';
            currentResults = [];
            resultsContainer.innerHTML = '';
            
            showStatus('processing', '正在获取网页内容...');
            
            const formData = new FormData(this);
            const data = Object.fromEntries(formData.entries());
            if (!data.proxy) {
                data.proxy = getCheckedRadioValue('proxy') || 'no';
            }
             
            // Convert checkboxes to booleans
            data.browser = data.browser === 'on';
            data.lang_touched = langModeTouched;
            data.html_inline = data.html_inline === 'on';
            data.no_front_matter = data.no_front_matter === 'on';
            data.archive_source = data.archive_source === 'on';
            data.save_full_text = data.save_full_text === 'on';
            const rawInput = (data.url || '').trim();
            const inputs = data.save_full_text
                ? (rawInput ? [rawInput] : [])
                : (() => {
                    const urls = extractUrlsFromInput(rawInput);
                    return urls.length ? urls : (rawInput ? [rawInput] : []);
                })();

            if (!inputs.length) {
                showStatus('error', '请输入 URL 或文本');
                submitBtn.disabled = false;
                submitBtn.textContent = '开始获取';
                return;
            }
            
            try {
                let successCount = 0;
                for (let i = 0; i < inputs.length; i += 1) {
                    const input = inputs[i];
                    showStatus('processing', `正在处理 ${i + 1}/${inputs.length}: ${input}`);
                    const itemData = { ...data, url: input };
                    const response = await fetch('/api/process', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify(itemData)
                    });

                    const result = await parseJsonResponse(response);
                    if (result.success) {
                        result.input_url = input;
                        currentResults.push(result);
                        window.defaultDirs = result.defaultDirs || window.defaultDirs || {};
                        const card = renderResultCard(result, i, inputs.length);
                        if (result.translation_pending && result.translation_job_id) {
                            pollTranslationJob(result.translation_job_id, card, currentResults.length - 1, inputs.length);
                        }
                        successCount += 1;
                    } else {
                        renderErrorCard(input, result.error, i, inputs.length);
                    }
                }
                refreshAggregateCard();
                showStatus(successCount ? 'success' : 'error', `处理完成：成功 ${successCount}/${inputs.length}`);
            } catch (error) {
                showStatus('error', '请求失败: ' + error.message);
            } finally {
                submitBtn.disabled = false;
                submitBtn.textContent = '开始获取';
            }
        });

        document.getElementById('pasteBtn').addEventListener('click', async function() {
            try {
                const text = await navigator.clipboard.readText();
                if (!text) {
                    showStatus('error', '剪贴板为空');
                    return;
                }
                urlInput.value = text;
                await refreshProxyDefault(true);
                await refreshSiteDefaults(true);
            } catch (error) {
                showStatus('error', '读取剪贴板失败: ' + error.message);
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


        function showToast(type, message, duration = 5000) {
            const container = document.getElementById('toastContainer');
            if (!container) return;
            const toast = document.createElement('div');
            toast.className = 'toast toast-' + type;
            toast.textContent = message;
            toast.addEventListener('click', () => {
                toast.classList.add('fade-out');
                setTimeout(() => toast.remove(), 300);
            });
            container.appendChild(toast);
            if (duration > 0) {
                setTimeout(() => {
                    toast.classList.add('fade-out');
                    setTimeout(() => toast.remove(), 300);
                }, duration);
            }
        }
        // Store result data for saving
        let currentResults = [];
        const translationJobIds = new Set();
        const saveJobIds = new Set();

        function refreshAggregateCard() {
            renderAggregateSaveCard(currentResults);
        }

        function updateResultCard(card, result, index, total) {
            if (!card) {
                return;
            }
            populateResultCard(card, result, index, total);
        }

        async function pollTranslationJob(jobId, card, resultIndex, total) {
            if (!jobId || translationJobIds.has(jobId)) {
                return;
            }
            translationJobIds.add(jobId);

            const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

            while (true) {
                await delay(2000);
                try {
                    const response = await fetch(`/api/translation-jobs/${encodeURIComponent(jobId)}`);
                    const job = await parseJsonResponse(response);
                    if (!job.success) {
                        showStatus('error', '翻译任务失败: ' + (job.error || 'unknown error'));
                        translationJobIds.delete(jobId);
                        return;
                    }
                    if (job.status === 'done' && job.result) {
                        const updated = job.result;
                        updated.input_url = card.dataset.inputUrl || updated.input_url || '';
                        updated.translation_pending = false;
                        updated.translation_job_id = jobId;
                        currentResults[resultIndex] = updated;
                        updateResultCard(card, updated, resultIndex, total);
                        refreshAggregateCard();
                        translationJobIds.delete(jobId);
                        showStatus('success', `翻译完成：${updated.title || 'Untitled'}`);
                        return;
                    }
                    if (job.status === 'error') {
                        showStatus('error', '翻译失败: ' + (job.error || 'unknown error'));
                        translationJobIds.delete(jobId);
                        return;
                    }
                    showStatus('processing', '翻译处理中...');
                } catch (error) {
                    showStatus('processing', '翻译处理中...');
                }
            }
        }

        async function pollSaveJob(jobId) {
            if (!jobId || saveJobIds.has(jobId)) {
                return;
            }
            saveJobIds.add(jobId);

            const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

            while (true) {
                await delay(2000);
                try {
                    const response = await fetch(`/api/save-jobs/${encodeURIComponent(jobId)}`);
                    const job = await parseJsonResponse(response);
                    if (!job.success) {
                        showToast('error', '保存失败: ' + (job.error || 'unknown error'));
                        saveJobIds.delete(jobId);
                        return;
                    }
                    if (job.status === 'done') {
                        const fileTypeLabel = (job.fileType || '').toUpperCase();
                        showToast('success', `${fileTypeLabel} 已保存到: ${job.savePath}`, 8000);
                        saveJobIds.delete(jobId);
                        return;
                    }
                    if (job.status === 'error') {
                        showToast('error', '保存失败: ' + (job.error || 'unknown error'));
                        saveJobIds.delete(jobId);
                        return;
                    }
                } catch (error) {
                    // Silently retry
                }
            }
        }

        function getCurrentFormData(inputUrl) {
            // Collect all current form field values as a plain dict matching /api/process contract
            const form = document.getElementById('surfForm');
            const fd = new FormData(form);
            const data = Object.fromEntries(fd.entries());
            if (!data.proxy) {
                data.proxy = getCheckedRadioValue('proxy') || 'no';
            }
            data.url = inputUrl || (data.url || '').trim();
            data.lang_touched = langModeTouched;
            data.browser = data.browser === 'on';
            data.html_inline = data.html_inline === 'on';
            data.no_front_matter = data.no_front_matter === 'on';
            data.archive_source = data.archive_source === 'on';
            data.save_full_text = data.save_full_text === 'on';
            return data;
        }

        async function saveFile(container, resultData, fileType, options = {}) {
            if (!resultData) {
                showStatus('error', '没有可保存的内容');
                return;
            }

            const speak = !!options.speak;
            const { saveDir, customTitle } = getSaveFormState(container, fileType);
            const inputUrl = container.dataset.inputUrl || '';
            const formData = getCurrentFormData(inputUrl);
            const translationJobId = container.dataset.translationJobId || resultData.translation_job_id || '';
            const translationPending = !!(resultData.translation_pending && translationJobId);

            const body = {
                fileType,
                saveDir,
                customTitle,
                data: resultData,
                speak
            };
            if (translationPending) {
                body.translation_job_id = translationJobId;
            } else {
                body.formData = formData;
            }

            try {
                showStatus('processing', translationPending ? '翻译进行中，将在翻译完成后自动保存...' : '正在提交后台保存...');
                const response = await fetch('/api/save-async', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(body)
                });

                const result = await parseJsonResponse(response);

                if (result.success && result.job_id) {
                    showStatus('success', `${fileType.toUpperCase()} 已提交后台保存，可继续处理其他 URL`);
                    pollSaveJob(result.job_id);
                } else {
                    showStatus('error', '保存提交失败: ' + (result.error || 'unknown error'));
                }
            } catch (error) {
                showStatus('error', '请求失败: ' + error.message);
            }
        }
</body>
</html>
"""


def get_config():
    """Get config object."""
    return Config()


def normalize_web_proxy_mode(mode):
    raw_mode = str(mode or "").strip().lower()
    if raw_mode in {"", "auto", "default"}:
        return "auto"
    normalized = Fetcher._normalize_proxy_mode(mode)
    if normalized == "win" and not Fetcher._is_windows():
        return "auto"
    if normalized in {"env", "win", "custom", "no"}:
        return normalized
    return "auto"


def get_web_proxy_override(mode):
    """Return the Fetcher proxy override for a Web UI proxy mode."""
    normalized = normalize_web_proxy_mode(mode)
    return None if normalized == "auto" else normalized


def resolve_web_proxy_mode_default(config, url=None):
    """
    Resolve Web UI default proxy mode.

    Web UI commonly runs on a server where public sites are directly reachable,
    unlike the Windows-heavy CLI environment where implicit proxy discovery is
    often helpful. Keep Web's default direct and make auto an explicit choice.
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
    candidate = candidate.rstrip('`\'"<>)]}.,;!?:')
    return candidate or None


def extract_text_post_title(value):
    """Derive a post title from free-form text using the first sentence."""
    text = (value or "").strip()
    if not text:
        return "Untitled"
    return OutputHandler._extract_first_sentence(text) or "Untitled"


def _normalize_web_text_newlines(value):
    """Normalize user-entered text to LF for consistent Web processing."""
    return (value or "").replace("\r\n", "\n").replace("\r", "\n")


def build_text_post_html(text, title):
    """Wrap free-form text in a minimal article HTML document."""
    normalized = _normalize_web_text_newlines(text).strip()
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


def build_default_filename_stem(title, source_url=None, html_content=None):
    """Build the default filename stem shown in the Web save form."""
    filename_title = OutputHandler._get_filename_title(
        title, source_url=source_url, html_content=html_content
    )
    return OutputHandler._safe_filename_title(filename_title, max_len=100)


def _store_translation_job(job_id, payload):
    with _TRANSLATION_JOBS_LOCK:
        _TRANSLATION_JOBS[job_id] = payload


def _get_translation_job(job_id):
    with _TRANSLATION_JOBS_LOCK:
        job = _TRANSLATION_JOBS.get(job_id)
        return copy.deepcopy(job) if job else None


def _build_translated_web_result(base_result, translated_markdown, translated_title, translation_performed, lang_mode):
    result = copy.deepcopy(base_result)
    original_title = result.get("title") or "Untitled"
    original_markdown = result.get("markdown") or ""

    if lang_mode == "both":
        if translated_markdown != original_markdown:
            result["markdown"] = (
                f"{translated_markdown}\n\n---\n\n### Original Content / 原文内容\n\n{original_markdown}"
            )
            result["title"] = (
                f"{translated_title} ({original_title})"
                if translated_title and translated_title != original_title
                else original_title
            )
        else:
            result["markdown"] = translated_markdown
            result["title"] = translated_title or original_title
    else:
        if translated_markdown != original_markdown:
            result["markdown"] = translated_markdown
        if translated_title:
            result["title"] = translated_title

    result["translated_title"] = translated_title
    result["translation_performed"] = translation_performed
    result["defaultSaveTitle"] = result.get("title") or result.get("defaultSaveTitle") or "Untitled"
    result.setdefault("metadata", {})
    result["metadata"]["title"] = result["title"]
    result["metadata"]["translated_title"] = translated_title if translation_performed else None
    return result


def _run_web_translation_job(job_id):
    job = _get_translation_job(job_id)
    if not job:
        return

    _store_translation_job(job_id, {**job, "status": "running", "updated_at": time.time()})
    try:
        config = get_config()
        source = job["source"]
        raw_markdown = source.get("raw_markdown") or source.get("raw") or ""
        original_title = source.get("original_title") or source.get("title") or "Untitled"
        translator_model = None
        try:
            translator_model = config.get_llm_config(job.get("llm_provider"))["model"]
        except Exception:
            translator_model = None
        translated_markdown, translated_title = ContentProcessor.translate_if_needed(
            raw_markdown,
            title=original_title,
            target_lang=config.get("Output", "target_language", fallback="zh-cn"),
            config=config,
            llm_provider=job.get("llm_provider"),
        )
        translation_performed = (
            translated_markdown != raw_markdown
            or (translated_title or "") != original_title
        )
        result = _build_translated_web_result(
            source,
            translated_markdown,
            translated_title,
            translation_performed,
            job.get("lang_mode", "trans"),
        )
        result["metadata"]["translator"] = translator_model if translation_performed else None
        _store_translation_job(
            job_id,
            {
                **job,
                "status": "done",
                "updated_at": time.time(),
                "result": result,
            },
        )
    except Exception as exc:
        logger.error("Web translation job failed: %s", exc)
        _store_translation_job(
            job_id,
            {
                **job,
                "status": "error",
                "updated_at": time.time(),
                "error": str(exc),
            },
        )


def _enqueue_web_translation_job(source_result, llm_provider, lang_mode):
    job_id = uuid.uuid4().hex
    _store_translation_job(
        job_id,
        {
            "status": "pending",
            "created_at": time.time(),
            "updated_at": time.time(),
            "llm_provider": llm_provider,
            "lang_mode": lang_mode,
            "source": copy.deepcopy(source_result),
        },
    )
    threading.Thread(target=_run_web_translation_job, args=(job_id,), daemon=True).start()
    return job_id

def _store_save_job(job_id, payload):
    with _SAVE_JOBS_LOCK:
        _SAVE_JOBS[job_id] = payload


def _get_save_job(job_id):
    with _SAVE_JOBS_LOCK:
        job = _SAVE_JOBS.get(job_id)
        return copy.deepcopy(job) if job else None


def _run_web_save_job(job_id):
    job = _get_save_job(job_id)
    if not job:
        return

    _store_save_job(job_id, {**job, "status": "running", "updated_at": time.time()})
    try:
        config = get_config()
        data = job["data"]
        fileType = data.get("fileType")
        saveDir = (data.get("saveDir") or "").strip()
        customTitle = (data.get("customTitle") or "").strip()
        speak = bool(data.get("speak"))

        # Resolve resultData: explicit translation_job_id > resultData's own pending >
        # form re-process > cached data
        translation_job_id = data.get("translation_job_id")
        formData = data.get("formData")
        cached_pending = (data.get("data") or {}).get("translation_pending")
        cached_tjid = (data.get("data") or {}).get("translation_job_id")
        logger.warning(
            "Save worker resolving: tid=%s formData=%s dataPending=%s dataTid=%s",
            bool(translation_job_id), bool(formData), bool(cached_pending), bool(cached_tjid),
        )
        if translation_job_id:
            logger.warning("Save worker: using explicit translation_job_id=%s", translation_job_id)
            resultData = _wait_for_translation_and_get_result(translation_job_id)
        elif formData:
            cached = data.get("data", {})
            if cached.get("translation_pending") and cached.get("translation_job_id"):
                logger.warning("Save worker: resultData has pending translation, waiting for job %s", cached["translation_job_id"])
                resultData = _wait_for_translation_and_get_result(cached["translation_job_id"])
            else:
                logger.info("Save worker: re-processing with formData (lang=%s)", formData.get("lang"))
                result, _lang_mode, _translation_pending = _process_web_request(formData, translate_sync=True)
                resultData = result
        else:
            resultData = data.get("data", {})
            logger.info("Save worker: using cached resultData, pending=%s", bool(resultData.get("translation_pending")))
            if resultData.get("translation_pending") and resultData.get("translation_job_id"):
                logger.info("Save worker: cached resultData has pending translation, waiting for job %s", resultData["translation_job_id"])
                resultData = _wait_for_translation_and_get_result(resultData["translation_job_id"])
            if data.get("combine_all"):
                resultData = build_combined_result_payload(data.get("results", [])) or {}
        defaultDirs = {
            "md": config.get_path("Output", "md_dir", fallback="notes"),
            "html": config.get_path("Output", "html_dir", fallback="web"),
            "pdf": config.get_path("Output", "pdf_dir", fallback="pdf"),
            "audio": config.get_path("Output", "audio_dir", fallback="audio"),
        }

        if saveDir:
            targetDir = resolve_user_path(saveDir)
        else:
            targetDir = defaultDirs.get(fileType, ".")

        os.makedirs(targetDir, exist_ok=True)

        title = customTitle or resultData.get("title", "Untitled")
        metadata = resultData.get("metadata", {})
        html_content = metadata.get("html_content", "")
        source_url = metadata.get("source_url")

        if fileType == "md":
            md_content = resultData.get("markdown", "")
            output_path = build_output_path(
                title, "md", targetDir, source_url=source_url, html_content=html_content
            )
            save_path = OutputHandler.save_markdown(
                title,
                md_content,
                config,
                output_path=output_path,
                html_content=html_content,
                add_front_matter=metadata.get("add_front_matter", True),
                translated_title=metadata.get("translated_title"),
                source_url=metadata.get("source_url"),
                translator=metadata.get("translator"),
                archive_url=metadata.get("archive_url"),
            )
        elif fileType == "html":
            cleaned_html = resultData.get("html", "")
            output_path = build_output_path(
                title, "html", targetDir, source_url=source_url, html_content=html_content
            )
            save_path = OutputHandler.save_html(
                title,
                cleaned_html,
                config,
                inline=metadata.get("html_inline", False),
                output_path=output_path,
            )
        elif fileType == "pdf":
            md_content = resultData.get("markdown", "")
            output_path = build_output_path(
                title, "pdf", targetDir, source_url=source_url, html_content=html_content
            )
            save_path = OutputHandler.generate_pdf(
                title, md_content, config, output_path=output_path
            )
        elif fileType == "audio":
            md_content = resultData.get("markdown", "")
            output_path = build_output_path(
                title, "mp3", targetDir, source_url=source_url, html_content=html_content
            )
            TTSHandler.run_tts(
                title, md_content, config, speak=speak, save_path=output_path
            )
            save_path = output_path
        else:
            raise ValueError(f"Unsupported file type: {fileType}")

        _store_save_job(
            job_id,
            {
                **job,
                "status": "done",
                "updated_at": time.time(),
                "savePath": save_path,
                "fileType": fileType,
            },
        )
    except Exception as exc:
        logger.error("Web save job failed: %s", exc)
        _store_save_job(
            job_id,
            {
                **job,
                "status": "error",
                "updated_at": time.time(),
                "error": str(exc),
            },
        )


def _enqueue_web_save_job(save_data):
    job_id = uuid.uuid4().hex
    _store_save_job(
        job_id,
        {
            "status": "pending",
            "created_at": time.time(),
            "updated_at": time.time(),
            "data": save_data,
        },
    )
    threading.Thread(target=_run_web_save_job, args=(job_id,), daemon=True).start()
    return job_id

def _wait_for_translation_and_get_result(translation_job_id, timeout=300, poll_interval=2):
    """Poll an async translation job until completion, then return the translated result."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = _get_translation_job(translation_job_id)
        if not job:
            raise ValueError(f"Translation job {translation_job_id} not found")
        status = job.get("status")
        if status == "done":
            result = job.get("result")
            if not result:
                raise ValueError("Translation job completed but returned no result")
            return result
        if status == "error":
            raise ValueError(f"Translation failed: {job.get('error', 'unknown error')}")
        time.sleep(poll_interval)
    raise TimeoutError(f"Translation job {translation_job_id} did not complete within {timeout}s")


def build_combined_result_payload(results):
    valid_results = [item for item in (results or []) if isinstance(item, dict)]
    if not valid_results:
        return None

    title = f"Surf Collection ({len(valid_results)} items)"
    markdown_sections = []
    raw_sections = []
    html_sections = []
    combined_sources = []

    for index, result in enumerate(valid_results, start=1):
        item_title = result.get("title") or f"Item {index}"
        source = (result.get("metadata") or {}).get("source_url") or result.get("input_url") or ""
        markdown_body = (result.get("markdown") or "").strip()
        raw_body = (result.get("raw") or "").strip()
        html_body = (result.get("html") or "").strip()

        markdown_sections.append(f"## {item_title}\n\n{markdown_body or '_No content_'}")
        raw_sections.append(f"## {item_title}\n\n{raw_body or '_No content_'}")
        html_sections.append(f"<section><h2>{escape(item_title)}</h2>{html_body or '<p><em>No content.</em></p>'}</section>")
        if source:
            combined_sources.append(f"{item_title}: {source}")

    html_content = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{escape(title)}</title>"
        "</head><body>"
        + "".join(html_sections)
        + "</body></html>"
    )

    return {
        "title": title,
        "markdown": "\n\n---\n\n".join(markdown_sections),
        "raw": "\n\n---\n\n".join(raw_sections),
        "html": html_content,
        "metadata": {
            "title": title,
            "html_content": html_content,
            "add_front_matter": False,
            "translated_title": None,
            "source_url": None,
            "archive_url": None,
            "translator": None,
            "html_inline": False,
            "combined_sources": combined_sources,
        },
    }


def resolve_web_thread_mode(data, site_name, site_config):
    """Resolve web thread settings using the same semantics as CLI thread flags."""
    thread_mode = str(data.get("thread_mode", "default")).strip().lower()
    if thread_mode == "off":
        return False
    if thread_mode in {"after", "before", "both"}:
        return thread_mode
    if site_config and site_config.get("default_thread"):
        logger.info(f"Web: {site_name} using default thread expansion")
        return "after"
    return None


def resolve_web_thread_author(data):
    """Resolve web thread author scope using the same semantics as CLI."""
    thread_author = str(data.get("thread_author", "all")).strip().lower()
    if thread_author in {"same", "all"}:
        return thread_author
    return "all"


def resolve_web_site_defaults(config, url=None):
    """Resolve URL-sensitive Web UI defaults without fetching page content."""
    site_name = None
    site_config = None
    if url:
        _, site_name, site_config = _get_handler_for_url(url)

    args = SimpleNamespace(ocr_images=False, no_ocr_images=False)
    ocr_enabled = OcrHandler._is_enabled_for_site(site_name, site_config, args, config)
    lang_mode = (
        "raw"
        if site_config and site_config.get("default_no_translate")
        else "trans"
    )

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


@app.route("/api/translation-jobs/<job_id>", methods=["GET"])
def translation_job_status(job_id):
    """Fetch the current status of an async translation job."""
    job = _get_translation_job(job_id)
    if not job:
        return jsonify({"success": False, "error": "Translation job not found"}), 404

    payload = {
        "success": True,
        "job_id": job_id,
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }
    if job.get("status") == "done":
        payload["result"] = job.get("result")
    elif job.get("status") == "error":
        payload["error"] = job.get("error") or "Translation failed"
    return jsonify(payload)



def _process_web_request(data, translate_sync=False):
    """Core URL/text-post processing shared by /api/process and async save.

    When *translate_sync* is True, translation runs inline (suitable for
    background threads).  When False, the caller is responsible for spawning
    an async translation job — the returned result will have the raw content
    and set ``translation_pending`` to True.
    """

    raw_url_input = data.get("url")
    save_full_text = bool(data.get("save_full_text", False))
    url = None if save_full_text else extract_url_from_text(raw_url_input)
    raw_text = (raw_url_input or "").strip()
    is_text_post = bool(raw_text and not url)

    config = get_config()

    original_md = ""
    original_title = "Untitled"
    translated_title = None
    content_base_url = None
    archive_is_url = None
    translation_performed = False
    translation_pending = False

    # Fetch content
    proxy_mode = normalize_web_proxy_mode(data.get("proxy", "no"))
    proxy_override = get_web_proxy_override(proxy_mode)
    custom_proxy = (data.get("custom_proxy") or "").strip() or None
    if proxy_mode == "custom" and not custom_proxy:
        raise ValueError("custom 代理模式需要填写自定义代理地址")

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
        translation_pending = lang_mode in {"trans", "both"}
    else:
        if not url:
            raise ValueError("A valid http/https URL is required")

        url = Fetcher._resolve_common_short_url(
            url,
            config,
            proxy_mode_override=proxy_override,
            custom_proxy_override=custom_proxy,
        )
        _, site_name, site_config = _get_handler_for_url(url)
        fetch_thread = resolve_web_thread_mode(data, site_name, site_config)
        fetch_thread_author = resolve_web_thread_author(data)
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
            proxy_mode_override=proxy_override,
            custom_proxy_override=custom_proxy,
            fetch_thread=fetch_thread,
            fetch_thread_author=fetch_thread_author,
        )
        if not html_content:
            raise ValueError(f"Failed to fetch usable content from {url}")

        # Paywall detection and archive.is fallback
        archive_is_url = None
        paywall_result = Fetcher._detect_paywall(html_content, url=url)
        if paywall_result and paywall_result.get("detected"):
            logger.warning(
                f"Paywall detected (confidence: {paywall_result['confidence']:.0%}): "
                f"{paywall_result.get('reason', 'unknown')}"
            )
            logger.info("Attempting to fetch from archive.is...")
            archived_html, snapshot_url = Fetcher._fetch_archiveis_snapshot(
                url,
                config=config,
                proxy_mode_override=proxy_override,
                custom_proxy_override=custom_proxy,
            )
            if archived_html:
                logger.info("archive.is snapshot fetched successfully, using it as content source.")
                archive_is_url = snapshot_url
                html_content = archived_html
            else:
                raise ValueError("内容受付费墙控制，未抓取全文")

        pipeline_lang_mode = lang_mode
        if not translate_sync and lang_mode in {"trans", "both"}:
            pipeline_lang_mode = "raw"
        processed = _process_fetched_content(
            html_content,
            url,
            config,
            site_name=site_name,
            site_config=site_config,
            lang_mode=pipeline_lang_mode,
            ocr_args=build_web_ocr_args(data),
            proxy_mode_override=proxy_override,
            custom_proxy_override=custom_proxy,
            llm_provider=(data.get("llm") or "").strip() or None,
        )
        source_url = processed["source_url"]
        content_base_url = processed["content_base_url"]
        title = processed["title"]
        cleaned_html = processed["cleaned_html"]
        md_content = processed["markdown"]
        original_md = processed["raw_markdown"]
        original_title = processed["original_title"]
        translated_title = processed["translated_title"]
        translation_performed = processed["translation_performed"]
        translation_pending = (not translate_sync) and lang_mode in {"trans", "both"}
        if translation_pending:
            title = original_title
            md_content = original_md

    # archive_url: prioritize archive.is snapshot (from paywall fallback)
    archive_url = archive_is_url
    if not archive_url and data.get("archive_source") and not data.get("no_front_matter", False):
        archive_url = Fetcher.save_wayback_snapshot(
            source_url,
            config=config,
            proxy_mode_override=proxy_override,
            custom_proxy_override=custom_proxy,
        )

    # Get default directories for frontend
    defaultDirs = {
        "md": config.get_path("Output", "md_dir", fallback="notes"),
        "html": config.get_path("Output", "html_dir", fallback="web"),
        "pdf": config.get_path("Output", "pdf_dir", fallback="pdf"),
        "audio": config.get_path("Output", "audio_dir", fallback="audio"),
    }
    defaultSaveTitle = build_default_filename_stem(
        title,
        source_url=source_url,
        html_content=html_content,
    )

    result = {
        "title": title,
        "markdown": md_content,
        "html": cleaned_html,
        "raw": original_md,
        "raw_markdown": original_md,
        "original_title": original_title,
        "defaultDirs": defaultDirs,
        "defaultSaveTitle": defaultSaveTitle,
        "translation_performed": translation_performed,
        "translated_title": translated_title if translation_performed else None,
        "translation_pending": translation_pending,
        "metadata": {
            "title": title,
            "html_content": html_content,
            "add_front_matter": not data.get("no_front_matter", False),
            "translated_title": translated_title if translation_performed else None,
            "source_url": source_url,
            "archive_url": archive_url,
            "translator": None,
            "html_inline": data.get("html_inline", False),
        },
    }
    return result, lang_mode, translation_pending
@app.route("/api/process", methods=["POST"])
def process_url():
    """Process a URL or free-form text post and return the result."""

    data = request.get_json(silent=True) or {}

    try:
        result, lang_mode, translation_pending = _process_web_request(data, translate_sync=False)
        result["success"] = True
        if translation_pending:
            llm_provider = (data.get("llm") or "").strip() or None
            job_id = _enqueue_web_translation_job(copy.deepcopy(result), llm_provider, lang_mode)
            result["translation_pending"] = True
            result["translation_job_id"] = job_id
        return jsonify(result)

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)})
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



@app.route("/api/save-async", methods=["POST"])
def save_file_async():
    """Enqueue a background save job and return immediately."""
    data = request.get_json(silent=True) or {}
    fileType = data.get("fileType")
    if not fileType:
        return jsonify({"success": False, "error": "File type is required"})
    try:
        job_id = _enqueue_web_save_job(data)
        return jsonify({"success": True, "job_id": job_id})
    except Exception as exc:
        logger.error("Failed to enqueue save job: %s", exc)
        return jsonify({"success": False, "error": str(exc)})


@app.route("/api/save-jobs/<job_id>", methods=["GET"])
def save_job_status(job_id):
    """Fetch the current status of an async save job."""
    job = _get_save_job(job_id)
    if not job:
        return jsonify({"success": False, "error": "Save job not found"}), 404

    payload = {
        "success": True,
        "job_id": job_id,
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }
    if job.get("status") == "done":
        payload["savePath"] = job.get("savePath")
        payload["fileType"] = job.get("fileType")
    elif job.get("status") == "error":
        payload["error"] = job.get("error") or "Save failed"
    return jsonify(payload)

@app.route("/api/save", methods=["POST"])
def save_file():
    """Generate and save a file to specified directory."""
    data = request.get_json(silent=True) or {}
    fileType = data.get("fileType")
    saveDir = data.get("saveDir", "").strip()
    customTitle = (data.get("customTitle") or "").strip()
    resultData = data.get("data", {})
    if data.get("combine_all"):
        resultData = build_combined_result_payload(data.get("results", [])) or {}
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
        title = customTitle or resultData.get("title", "Untitled")
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
                archive_url=metadata.get("archive_url"),
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
