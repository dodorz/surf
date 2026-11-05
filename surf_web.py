#!/usr/bin/env -S uv run
"""
Surf Web Interface - A web interface for the Surf URL to Markdown/PDF converter.

Usage:
    python surf_web.py [--host HOST] [--port PORT] [--root ROOT]

Example:
    python surf_web.py --host 0.0.0.0 --port 8080
    python surf_web.py --host 127.0.0.1 --port 18473 --root /surf
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
    PublishHandler,
    PdfDownload,
    SearchHandler,
    TTSHandler,
    _apply_browser_backend_override,
    _apply_tts_overrides,
    _config_bool,
    _convert_embedded_html_in_markdown,
    _extract_direct_markdown_payload,
    _get_handler_for_url,
    _get_version,
    logger,
    _process_fetched_content,
    _save_failed_url_note,
    _translate_markdown_document,
    build_search_filename_prefix,
    resolve_extractor,
    resolve_user_path,
)

_ROOT_PATH = os.environ.get("SURF_ROOT_PATH", "").rstrip("/")

app = Flask(__name__)

class _PrefixStripper:
    """WSGI middleware that strips a URL prefix so Flask routes work behind a reverse proxy."""

    def __init__(self, wsgi_app, prefix):
        self.app = wsgi_app
        self.prefix = prefix.rstrip("/")

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if self.prefix and path.startswith(self.prefix):
            environ["SCRIPT_NAME"] = self.prefix
            environ["PATH_INFO"] = path[len(self.prefix):]
        return self.app(environ, start_response)

_TRANSLATION_JOBS = {}
_TRANSLATION_JOBS_LOCK = threading.Lock()
_SAVE_JOBS = {}
_SAVE_JOBS_LOCK = threading.Lock()


def _apply_root_path():
    """Apply the _ROOT_PATH prefix to the Flask app (middleware + config)."""
    if _ROOT_PATH:
        app.config["APPLICATION_ROOT"] = _ROOT_PATH
        if not isinstance(app.wsgi_app, _PrefixStripper):
            app.wsgi_app = _PrefixStripper(app.wsgi_app, _ROOT_PATH)


_apply_root_path()


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
    <title>Surf</title>
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%230e7490'/%3E%3Cpath d='M12 38c6-8 10-12 16-12 7 0 10 8 16 8 4 0 7-2 12-8v10c-5 6-8 8-12 8-6 0-9-8-16-8-6 0-10 4-16 12V38z' fill='white' fill-opacity='.92'/%3E%3Cpath d='M12 24c6-8 10-12 16-12 7 0 10 8 16 8 4 0 7-2 12-8v10c-5 6-8 8-12 8-6 0-9-8-16-8-6 0-10 4-16 12V24z' fill='white' fill-opacity='.72'/%3E%3C/svg%3E">
    <style>
        :root {
            --bg: #f5f7f9;
            --surface: #ffffff;
            --surface-2: #f1f4f7;
            --surface-3: #e9edf2;
            --line: #e2e7ed;
            --line-strong: #cbd3dc;
            --ink: #101820;
            --ink-2: #48535f;
            --ink-3: #7b8794;
            --accent: #0e7490;
            --accent-ink: #0b5b72;
            --accent-soft: #e6f4f7;
            --ok: #0f7b4f;
            --ok-soft: #e8f6ef;
            --warn: #8a5a00;
            --warn-soft: #fdf4e3;
            --err: #b3261e;
            --err-soft: #fdecea;
            --radius: 16px;
            --radius-sm: 10px;
            --shadow: 0 1px 2px rgba(16, 24, 40, .05), 0 18px 40px -28px rgba(16, 24, 40, .35);
            --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
        }

        @media (prefers-color-scheme: dark) {
            :root {
                --bg: #0d1117;
                --surface: #141a21;
                --surface-2: #1a222b;
                --surface-3: #222c37;
                --line: #232d38;
                --line-strong: #35424f;
                --ink: #e9eef4;
                --ink-2: #a9b5c2;
                --ink-3: #7d8b99;
                --accent: #4cc2d9;
                --accent-ink: #7fd7e8;
                --accent-soft: rgba(76, 194, 217, .13);
                --ok: #55c98a;
                --ok-soft: rgba(85, 201, 138, .12);
                --warn: #e0aa55;
                --warn-soft: rgba(224, 170, 85, .12);
                --err: #f08b82;
                --err-soft: rgba(240, 139, 130, .12);
                --shadow: 0 1px 2px rgba(0, 0, 0, .4), 0 18px 40px -28px rgba(0, 0, 0, .9);
            }
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        html { -webkit-text-size-adjust: 100%; }

        body {
            background: var(--bg);
            color: var(--ink);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
            font-size: 15px;
            line-height: 1.55;
            -webkit-font-smoothing: antialiased;
        }

        .sr-only {
            position: absolute;
            width: 1px;
            height: 1px;
            padding: 0;
            margin: -1px;
            overflow: hidden;
            clip: rect(0 0 0 0);
            white-space: nowrap;
            border: 0;
        }

        /* ---------- top bar ---------- */

        .topbar {
            position: sticky;
            top: 0;
            z-index: 50;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            padding: 12px 24px;
            background: var(--bg);
            background: color-mix(in srgb, var(--bg) 82%, transparent);
            backdrop-filter: blur(12px);
            border-bottom: 1px solid var(--line);
        }

        .brand { display: flex; align-items: center; gap: 10px; }

        .brand-mark {
            width: 28px;
            height: 28px;
            border-radius: 9px;
            background: var(--accent);
            color: #fff;
            display: grid;
            place-items: center;
            flex: 0 0 auto;
        }

        .brand-mark svg { width: 18px; height: 18px; }

        .brand-name { font-size: 17px; font-weight: 700; letter-spacing: -.01em; }

        .brand-ver {
            font-family: var(--mono);
            font-size: 11px;
            color: var(--ink-3);
            border: 1px solid var(--line);
            border-radius: 999px;
            padding: 1px 8px;
        }

        .topbar-meta { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--ink-3); }

        .live-dot {
            width: 7px; height: 7px; border-radius: 50%;
            background: var(--ok);
            box-shadow: 0 0 0 3px var(--ok-soft);
        }

        .queue-badge {
            display: none;
            align-items: center;
            gap: 6px;
            font-size: 12.5px;
            font-weight: 600;
            color: var(--accent-ink);
            background: var(--accent-soft);
            border-radius: 999px;
            padding: 3px 10px;
        }

        .queue-badge.show { display: inline-flex; }

        /* ---------- layout ---------- */

        .page {
            max-width: 1060px;
            margin: 0 auto;
            padding: 0 24px 88px;
        }

        .intro { padding: 46px 0 26px; }

        .intro h1 {
            font-size: clamp(26px, 3.6vw, 38px);
            line-height: 1.15;
            letter-spacing: -.025em;
            font-weight: 720;
        }

        .intro p {
            margin-top: 10px;
            max-width: 60ch;
            color: var(--ink-2);
            font-size: 15.5px;
        }

        .panel {
            background: var(--surface);
            border: 1px solid var(--line);
            border-radius: var(--radius);
            box-shadow: var(--shadow);
            margin-bottom: 18px;
        }

        .section-label {
            display: flex;
            align-items: baseline;
            gap: 10px;
            margin: 34px 0 14px;
        }

        .section-label h2 {
            font-size: 13px;
            font-weight: 700;
            letter-spacing: .08em;
            text-transform: uppercase;
            color: var(--ink-3);
        }

        .section-label .count { font-size: 12.5px; color: var(--ink-3); }

        /* ---------- composer ---------- */

        .composer { padding: 20px; }

        .mode-switch {
            display: inline-flex;
            gap: 2px;
            padding: 3px;
            background: var(--surface-2);
            border: 1px solid var(--line);
            border-radius: 11px;
            margin-bottom: 14px;
        }

        .mode {
            border: 0;
            background: transparent;
            color: var(--ink-3);
            font: inherit;
            font-size: 13.5px;
            padding: 6px 14px;
            border-radius: 8px;
            cursor: pointer;
            transition: background .15s, color .15s;
        }

        .mode:hover { color: var(--ink); }

        .mode.active {
            background: var(--surface);
            color: var(--accent-ink);
            font-weight: 650;
            box-shadow: 0 1px 2px rgba(16, 24, 40, .08);
        }

        textarea#url {
            display: block;
            width: 100%;
            min-height: 118px;
            resize: vertical;
            padding: 16px 18px;
            border: 1px solid var(--line-strong);
            border-radius: var(--radius-sm);
            background: var(--surface-2);
            color: var(--ink);
            font: inherit;
            font-size: 16px;
            line-height: 1.6;
            transition: border-color .15s, background .15s, box-shadow .15s;
        }

        textarea#url::placeholder { color: var(--ink-3); }

        textarea#url:focus {
            outline: none;
            background: var(--surface);
            border-color: var(--accent);
            box-shadow: 0 0 0 4px var(--accent-soft);
        }

        .composer-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 14px;
            flex-wrap: wrap;
            margin-top: 14px;
        }

        .composer-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }

        .composer-hint-inline { font-size: 13px; color: var(--ink-3); }

        .btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            border: 1px solid transparent;
            border-radius: var(--radius-sm);
            font: inherit;
            font-size: 14.5px;
            font-weight: 600;
            padding: 11px 20px;
            cursor: pointer;
            transition: transform .12s, background .15s, border-color .15s, color .15s, box-shadow .15s;
            white-space: nowrap;
        }

        .btn:active { transform: translateY(1px); }

        .btn:disabled { opacity: .55; cursor: not-allowed; transform: none; }

        /* The primary action is the one people press all day: make it big,
           accent-filled and rightmost so it sits under the thumb / cursor. */
        .btn-primary {
            background: var(--accent);
            color: #fff;
            font-size: 15.5px;
            font-weight: 680;
            padding: 13px 34px;
            min-width: 124px;
            box-shadow: 0 10px 24px -14px rgba(14, 116, 144, .95);
        }

        .btn-primary:hover:not(:disabled) { background: var(--accent-ink); }

        .btn-ghost {
            background: transparent;
            border-color: var(--line-strong);
            color: var(--ink-2);
        }

        .btn-ghost:hover:not(:disabled) { border-color: var(--accent); color: var(--accent-ink); }

        .btn-sm { padding: 8px 14px; font-size: 13.5px; }

        .check {
            display: inline-flex;
            align-items: center;
            gap: 9px;
            font-size: 14px;
            color: var(--ink-2);
            cursor: pointer;
            user-select: none;
        }

        .check input[type="checkbox"] {
            appearance: none;
            width: 18px;
            height: 18px;
            border: 1.5px solid var(--line-strong);
            border-radius: 6px;
            background: var(--surface);
            cursor: pointer;
            position: relative;
            flex: 0 0 auto;
            transition: background .15s, border-color .15s;
        }

        .check input[type="checkbox"]:checked {
            background: var(--accent);
            border-color: var(--accent);
        }

        .check input[type="checkbox"]:checked::after {
            content: "";
            position: absolute;
            inset: 0;
            background: no-repeat center/11px 11px url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='none' stroke='white' stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round' d='M3.5 8.5l3 3 6-7'/%3E%3C/svg%3E");
        }

        .check input[type="checkbox"]:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

        .check input[type="checkbox"]:disabled { opacity: .45; cursor: not-allowed; }

        .hint {
            margin-top: 10px;
            font-size: 13px;
            line-height: 1.6;
            color: var(--ink-3);
        }

        .hint code {
            font-family: var(--mono);
            font-size: 12px;
            background: var(--surface-2);
            border: 1px solid var(--line);
            border-radius: 5px;
            padding: 1px 5px;
        }

        .search-fields {
            display: none;
            grid-template-columns: 1.4fr .7fr 1.2fr;
            gap: 14px;
            margin-top: 16px;
            padding-top: 16px;
            border-top: 1px dashed var(--line);
        }

        /* ---------- fields ---------- */

        .field { display: block; margin-bottom: 18px; }

        .field:last-child { margin-bottom: 0; }

        .field-label {
            display: block;
            font-size: 12.5px;
            font-weight: 650;
            letter-spacing: .01em;
            color: var(--ink-2);
            margin-bottom: 8px;
        }

        .input, select.input {
            width: 100%;
            padding: 9px 12px;
            border: 1px solid var(--line-strong);
            border-radius: 9px;
            background: var(--surface);
            color: var(--ink);
            font: inherit;
            font-size: 14px;
            transition: border-color .15s, box-shadow .15s;
        }

        .input:focus {
            outline: none;
            border-color: var(--accent);
            box-shadow: 0 0 0 3px var(--accent-soft);
        }

        select.input {
            appearance: none;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='none' stroke='%237b8794' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round' d='M4 6.5l4 4 4-4'/%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: right 10px center;
            background-size: 14px;
            padding-right: 32px;
        }

        .seg {
            display: inline-flex;
            flex-wrap: wrap;
            gap: 2px;
            padding: 3px;
            background: var(--surface-2);
            border: 1px solid var(--line);
            border-radius: 10px;
        }

        .radio-option { position: relative; }

        .radio-option input[type="radio"] {
            position: absolute;
            opacity: 0;
            width: 1px;
            height: 1px;
            pointer-events: none;
        }

        .radio-option label {
            display: inline-flex;
            align-items: center;
            padding: 6px 12px;
            border-radius: 7px;
            font-size: 13.5px;
            color: var(--ink-2);
            cursor: pointer;
            white-space: nowrap;
            user-select: none;
            transition: background .15s, color .15s, box-shadow .15s;
        }

        .radio-option label:hover { background: var(--surface); color: var(--ink); }

        .radio-option input[type="radio"]:checked + label {
            background: var(--surface);
            color: var(--accent-ink);
            font-weight: 650;
            box-shadow: 0 1px 2px rgba(16, 24, 40, .1);
        }

        .radio-option input[type="radio"]:focus-visible + label {
            outline: 2px solid var(--accent);
            outline-offset: 1px;
        }

        .radio-option input[type="radio"]:disabled + label { opacity: .45; cursor: not-allowed; }

        /* ---------- options ---------- */

        .options { overflow: hidden; }

        .options-head {
            display: flex;
            align-items: center;
            gap: 14px;
            width: 100%;
            padding: 16px 20px;
            border: 0;
            background: transparent;
            color: var(--ink);
            font: inherit;
            cursor: pointer;
            text-align: left;
        }

        .options-head:hover { background: var(--surface-2); }

        .options-title { font-size: 14.5px; font-weight: 650; }

        .options-summary {
            flex: 1;
            min-width: 0;
            font-size: 13px;
            color: var(--ink-3);
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .chevron { color: var(--ink-3); transition: transform .18s; flex: 0 0 auto; }

        .options[data-open="false"] .chevron { transform: rotate(-90deg); }

        .options-body {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(288px, 1fr));
            gap: 26px 30px;
            padding: 4px 20px 22px;
            border-top: 1px solid var(--line);
        }

        .options[data-open="false"] .options-body { display: none; }

        .options-group { padding-top: 18px; }

        .options-group > h3 {
            font-size: 12px;
            font-weight: 700;
            letter-spacing: .08em;
            text-transform: uppercase;
            color: var(--ink-3);
            margin-bottom: 14px;
        }

        .tts-grid { display: grid; grid-template-columns: 1.6fr .7fr .7fr; gap: 10px; }

        .tts-grid .input { font-size: 13.5px; }

        /* ---------- activity ---------- */

        .activity {
            display: flex;
            align-items: flex-start;
            gap: 12px;
            padding: 14px 18px;
            margin-bottom: 18px;
            border-left: 3px solid var(--accent);
        }

        .activity[hidden] { display: none; }

        .activity-dot {
            width: 9px;
            height: 9px;
            margin-top: 6px;
            border-radius: 50%;
            background: var(--accent);
            flex: 0 0 auto;
        }

        .activity-text { flex: 1; min-width: 0; font-size: 14px; color: var(--ink-2); word-break: break-word; }

        .activity-text b { color: var(--ink); }

        .activity-processing .activity-dot { animation: pulse 1.4s ease-in-out infinite; }

        .activity-success { border-left-color: var(--ok); background: var(--ok-soft); }
        .activity-success .activity-dot { background: var(--ok); animation: none; }
        .activity-success .activity-text { color: var(--ok); }

        .activity-error { border-left-color: var(--err); background: var(--err-soft); }
        .activity-error .activity-dot { background: var(--err); animation: none; }
        .activity-error .activity-text { color: var(--err); }

        .activity-close {
            border: 0;
            background: transparent;
            color: inherit;
            opacity: .6;
            font-size: 18px;
            line-height: 1;
            cursor: pointer;
            padding: 2px 4px;
        }

        .activity-close:hover { opacity: 1; }

        @keyframes pulse {
            0%, 100% { opacity: .35; transform: scale(.85); }
            50% { opacity: 1; transform: scale(1); }
        }

        /* ---------- result cards ---------- */

        .result-card { padding: 20px; }

        .result-head {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 14px;
        }

        .result-title-wrap { min-width: 0; }

        .result-title {
            font-size: 17px;
            font-weight: 680;
            line-height: 1.35;
            letter-spacing: -.01em;
            word-break: break-word;
        }

        .result-meta {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 8px;
            margin-top: 7px;
            font-size: 12.5px;
            color: var(--ink-3);
        }

        .result-source { word-break: break-all; }

        .badge {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            font-size: 11px;
            font-weight: 650;
            letter-spacing: .02em;
            padding: 1px 8px;
            border-radius: 999px;
            background: var(--surface-3);
            color: var(--ink-2);
        }

        .badge-ok { background: var(--ok-soft); color: var(--ok); }
        .badge-accent { background: var(--accent-soft); color: var(--accent-ink); }
        .badge-err { background: var(--err-soft); color: var(--err); }

        .save-bar {
            display: flex;
            align-items: flex-end;
            justify-content: space-between;
            gap: 16px;
            flex-wrap: wrap;
            margin-top: 16px;
            padding: 14px;
            background: var(--surface-2);
            border: 1px solid var(--line);
            border-radius: 12px;
        }

        .save-config {
            display: grid;
            grid-template-columns: minmax(180px, 1fr) minmax(180px, 1fr);
            gap: 12px;
            flex: 1 1 380px;
            min-width: 0;
        }

        .save-field { display: flex; flex-direction: column; gap: 5px; min-width: 0; }

        .save-field > span {
            font-size: 11.5px;
            font-weight: 650;
            letter-spacing: .02em;
            color: var(--ink-3);
        }

        .save-input {
            width: 100%;
            padding: 7px 10px;
            border: 1px solid var(--line-strong);
            border-radius: 8px;
            background: var(--surface);
            color: var(--ink);
            font: inherit;
            font-size: 13px;
        }

        .save-input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }

        .save-links { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }

        .save-btn {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 7px 12px;
            border: 1px solid var(--line-strong);
            border-radius: 8px;
            background: var(--surface);
            color: var(--ink-2);
            font: inherit;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            transition: border-color .15s, color .15s, background .15s;
        }

        .save-btn:hover:not(:disabled) { border-color: var(--accent); color: var(--accent-ink); background: var(--accent-soft); }

        .save-btn:disabled { opacity: .5; cursor: not-allowed; }

        .save-btn-primary { background: var(--accent); border-color: var(--accent); color: #fff; }

        .save-btn-primary:hover:not(:disabled) { background: var(--accent-ink); border-color: var(--accent-ink); color: #fff; }

        .save-btn-divider { width: 1px; align-self: stretch; background: var(--line-strong); margin: 0 2px; }

        .tabs { display: flex; gap: 2px; margin-top: 18px; border-bottom: 1px solid var(--line); }

        .tab {
            border: 0;
            background: transparent;
            padding: 9px 12px;
            margin-bottom: -1px;
            border-bottom: 2px solid transparent;
            color: var(--ink-3);
            font: inherit;
            font-size: 13.5px;
            cursor: pointer;
        }

        .tab:hover { color: var(--ink); }

        .tab.active { color: var(--accent-ink); border-bottom-color: var(--accent); font-weight: 650; }

        .tab-content { display: none; }

        .tab-content.active { display: block; }

        .content-preview {
            max-height: 460px;
            overflow: auto;
            margin-top: 12px;
            padding: 16px;
            background: var(--surface-2);
            border: 1px solid var(--line);
            border-radius: 12px;
            color: var(--ink-2);
            font-family: var(--mono);
            font-size: 13px;
            line-height: 1.65;
            white-space: pre-wrap;
            word-break: break-word;
        }

        .result-card[data-tone="error"] { border-color: var(--err); }

        .results { scroll-margin-top: 76px; }

        .empty-state {
            padding: 56px 24px;
            text-align: center;
            border: 1px dashed var(--line-strong);
            border-radius: var(--radius);
            color: var(--ink-3);
            font-size: 14px;
        }

        .empty-state strong { display: block; margin-bottom: 6px; color: var(--ink-2); font-size: 15px; }

        /* ---------- search picker ---------- */

        .search-preview-card { border-color: var(--accent); }

        .search-toolbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            flex-wrap: wrap;
            margin-top: 14px;
        }

        .search-toolbar-left { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }

        .search-result-count { font-size: 12.5px; color: var(--ink-3); }

        .search-result-list {
            display: flex;
            flex-direction: column;
            gap: 8px;
            max-height: 440px;
            overflow-y: auto;
            margin-top: 14px;
        }

        .search-result-item {
            display: flex;
            gap: 11px;
            align-items: flex-start;
            padding: 10px 12px;
            border: 1px solid var(--line);
            border-radius: 10px;
            background: var(--surface);
            cursor: pointer;
            transition: border-color .15s, background .15s;
        }

        .search-result-item:hover { border-color: var(--accent); background: var(--accent-soft); }

        .search-result-checkbox {
            appearance: none;
            width: 17px;
            height: 17px;
            margin-top: 2px;
            border: 1.5px solid var(--line-strong);
            border-radius: 5px;
            flex: 0 0 auto;
            position: relative;
            cursor: pointer;
        }

        .search-result-checkbox:checked { background: var(--accent); border-color: var(--accent); }

        .search-result-checkbox:checked::after {
            content: "";
            position: absolute;
            inset: 0;
            background: no-repeat center/10px 10px url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath fill='none' stroke='white' stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round' d='M3.5 8.5l3 3 6-7'/%3E%3C/svg%3E");
        }

        .search-result-text { min-width: 0; display: block; }

        .search-result-title { display: block; font-weight: 600; font-size: 14px; word-break: break-word; }

        .search-result-url { display: block; font-size: 12px; color: var(--accent-ink); word-break: break-all; margin-top: 2px; }

        /* ---------- save queue ---------- */

        .queue-panel { display: none; padding: 16px 20px; }

        .queue-panel.show { display: block; }

        .save-queue-title {
            font-size: 12px;
            font-weight: 700;
            letter-spacing: .08em;
            text-transform: uppercase;
            color: var(--ink-3);
            margin-bottom: 10px;
        }

        .save-queue-list { list-style: none; display: flex; flex-direction: column; gap: 4px; }

        .save-queue-item {
            display: flex;
            align-items: baseline;
            gap: 10px;
            padding: 7px 10px;
            border-radius: 8px;
            background: var(--surface-2);
            font-size: 12.5px;
            word-break: break-all;
        }

        .save-queue-type { flex: 0 0 auto; font-family: var(--mono); font-size: 10.5px; font-weight: 700; color: var(--ink-3); }

        .save-queue-url { color: var(--ink-2); }

        .save-queue-status { margin-left: auto; flex: 0 0 auto; font-weight: 650; white-space: nowrap; }

        .save-queue-status-pending { color: var(--warn); }
        .save-queue-status-running { color: var(--accent-ink); }
        .save-queue-status-done { color: var(--ok); }
        .save-queue-status-error { color: var(--err); }

        .save-queue-detail { display: block; width: 100%; color: var(--ink-3); font-family: var(--mono); font-size: 11.5px; }

        /* ---------- toasts ---------- */

        .toast-container {
            position: fixed;
            right: 20px;
            bottom: 20px;
            z-index: 9999;
            display: flex;
            flex-direction: column;
            gap: 10px;
            max-width: min(380px, calc(100vw - 40px));
        }

        .toast {
            padding: 12px 16px;
            border-radius: 11px;
            border: 1px solid var(--line);
            background: var(--surface);
            color: var(--ink);
            font-size: 13.5px;
            box-shadow: var(--shadow);
            cursor: pointer;
            animation: toast-in .22s ease-out;
            transition: opacity .3s, transform .3s;
            word-break: break-word;
        }

        .toast::before {
            content: "";
            display: inline-block;
            width: 7px;
            height: 7px;
            border-radius: 50%;
            margin-right: 9px;
            vertical-align: 1px;
            background: var(--accent);
        }

        .toast-success::before { background: var(--ok); }
        .toast-error::before { background: var(--err); }

        .toast.fade-out { opacity: 0; transform: translateY(6px); }

        @keyframes toast-in {
            from { opacity: 0; transform: translateY(8px); }
            to { opacity: 1; transform: translateY(0); }
        }

        /* ---------- spinner ---------- */

        .spinner {
            display: inline-block;
            width: 13px;
            height: 13px;
            border: 2px solid currentColor;
            border-top-color: transparent;
            border-radius: 50%;
            animation: spin .7s linear infinite;
            vertical-align: -2px;
        }

        @keyframes spin { to { transform: rotate(360deg); } }

        /* ---------- footer ---------- */

        .footer {
            padding: 26px 0 0;
            border-top: 1px solid var(--line);
            margin-top: 30px;
            font-size: 12.5px;
            color: var(--ink-3);
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            align-items: center;
        }

        kbd {
            font-family: var(--mono);
            font-size: 11px;
            border: 1px solid var(--line-strong);
            border-bottom-width: 2px;
            border-radius: 5px;
            padding: 0 5px;
            color: var(--ink-3);
        }

        /* ---------- responsive ---------- */

        @media (max-width: 860px) {
            .search-fields { grid-template-columns: 1fr 1fr; }
            .search-fields > :last-child { grid-column: 1 / -1; }
        }

        @media (max-width: 640px) {
            .page { padding: 0 16px 64px; }
            .topbar { padding: 10px 16px; }
            .intro { padding: 30px 0 20px; }
            .composer { padding: 16px; }
            .composer-actions { width: 100%; }
            .composer-actions .btn { flex: 1 1 auto; }
            .save-config { grid-template-columns: 1fr; }
            .save-bar { align-items: stretch; }
            .save-links { justify-content: flex-start; }
            .search-fields { grid-template-columns: 1fr; }
            .tts-grid { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <header class="topbar">
        <div class="brand">
            <span class="brand-mark">
                <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <path d="M2 14c2.8-3.6 4.7-5.4 7.3-5.4 3.1 0 4.4 3.6 7.2 3.6 1.8 0 3.2-.9 5.5-3.6v4.5c-2.3 2.7-3.7 3.6-5.5 3.6-2.8 0-4.1-3.6-7.2-3.6C6.7 13.1 4.8 14.9 2 18v-4z" fill="currentColor"/>
                    <path d="M2 7.5c2.8-3.6 4.7-5.4 7.3-5.4 3.1 0 4.4 3.6 7.2 3.6 1.8 0 3.2-.9 5.5-3.6v4.5c-2.3 2.7-3.7 3.6-5.5 3.6-2.8 0-4.1-3.6-7.2-3.6C6.7 6.6 4.8 8.4 2 11.5V7.5z" fill="currentColor" fill-opacity=".55"/>
                </svg>
            </span>
            <span class="brand-name">Surf</span>
            <span class="brand-ver">v{{ version }}</span>
        </div>
        <div class="topbar-meta">
            <span class="queue-badge" id="queueBadge"><span class="spinner"></span><span id="queueBadgeText">0</span></span>
            <span class="live-dot" aria-hidden="true"></span>
            <span>本地运行</span>
        </div>
    </header>

    <main class="page">
        <section class="intro">
            <h1>一个链接，一份干净的文档。</h1>
            <p>抓取网页或本地文件，去掉广告与噪音，按需翻译，然后保存为 Markdown、HTML、PDF 或音频。</p>
        </section>

        <form id="surfForm" class="panel composer">
            <div class="mode-switch" role="tablist" aria-label="输入模式">
                <button type="button" class="mode active" data-mode="link" role="tab" aria-selected="true">链接 / 文本</button>
                <button type="button" class="mode" data-mode="search" role="tab" aria-selected="false">关键词搜索</button>
            </div>
            <input type="checkbox" id="searchMode" name="search_mode" class="sr-only" tabindex="-1">

            <label for="url" id="urlLabel" class="sr-only">URL 地址或纯文本</label>
            <textarea id="url" name="url" rows="3"
                      placeholder="粘贴一个或多个链接，或直接输入一段文字"></textarea>

            <div class="composer-row">
                <span class="composer-hint-inline" id="composerModeHint"></span>
                <div class="composer-actions">
                    <button type="button" class="btn btn-ghost" id="previewBtn" title="先抓取并预览，再决定保存格式">预览</button>
                    <button type="button" class="btn btn-ghost" id="fullTextSaveBtn" title="保留整段文字（含其中的链接），直接按帖子保存">全文保存</button>
                    <button type="submit" class="btn btn-primary" id="submitBtn">保存</button>
                </div>
            </div>

            <p class="hint" id="urlHint">输入里包含链接时只抓取第一个 <code>http/https</code> URL；不含链接时，这段文字会作为一篇帖子保存，第一句作为标题。</p>

            <div class="search-fields" id="searchOptionsGroup">
                <label class="field">
                    <span class="field-label">搜索引擎</span>
                    <select id="searchEngine" name="search_engine" class="input">
                        {% for option in search_engine_options %}
                        <option value="{{ option.value }}" {% if option.selected %}selected{% endif %}>{{ option.label }}</option>
                        {% endfor %}
                    </select>
                </label>
                <label class="field">
                    <span class="field-label">结果数量</span>
                    <input type="number" id="searchLimit" name="search_limit" class="input" min="1" max="20" value="{{ default_search_limit }}">
                </label>
                <label class="field">
                    <span class="field-label">文件名前缀</span>
                    <input type="text" id="searchPrefix" name="search_prefix" class="input" placeholder="留空则用关键词">
                </label>
            </div>
        </form>

        <section class="panel options" id="optionsPanel" data-open="true">
            <button type="button" class="options-head" id="optionsToggle" aria-expanded="true" aria-controls="optionsBody">
                <span class="options-title">选项</span>
                <span class="options-summary" id="optionsSummary"></span>
                <svg class="chevron" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                    <path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
            </button>
            <div class="options-body" id="optionsBody">
                <div class="options-group">
                    <h3>抓取</h3>
                    <div class="field">
                        <span class="field-label">代理</span>
                        <div class="seg" id="proxyGroup">
                            <div class="radio-option">
                                <input type="radio" id="proxy-auto" name="proxy" value="auto" {% if default_proxy_mode == 'auto' %}checked{% endif %}>
                                <label for="proxy-auto">自动</label>
                            </div>
                            {% if is_windows %}
                            <div class="radio-option">
                                <input type="radio" id="proxy-win" name="proxy" value="win" {% if default_proxy_mode == 'win' %}checked{% endif %}>
                                <label for="proxy-win">系统代理</label>
                            </div>
                            {% endif %}
                            <div class="radio-option">
                                <input type="radio" id="proxy-env" name="proxy" value="env" {% if default_proxy_mode == 'env' %}checked{% endif %}>
                                <label for="proxy-env">环境变量</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="proxy-custom" name="proxy" value="custom" {% if default_proxy_mode == 'custom' %}checked{% endif %}>
                                <label for="proxy-custom">自定义</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="proxy-no" name="proxy" value="no" {% if default_proxy_mode == 'no' %}checked{% endif %}>
                                <label for="proxy-no">不使用</label>
                            </div>
                        </div>
                    </div>

                    <label class="field" id="customProxyGroup" style="display: none;">
                        <span class="field-label">代理地址</span>
                        <input type="text" id="customProxy" name="custom_proxy" class="input"
                               value="{{ default_custom_proxy }}" placeholder="http://127.0.0.1:7890">
                    </label>

                    <div class="field">
                        <span class="field-label">内容提取</span>
                        <div class="seg">
                            <div class="radio-option">
                                <input type="radio" id="extractor-auto" name="extractor" value="auto" {% if default_extractor == 'auto' %}checked{% endif %}>
                                <label for="extractor-auto">自动</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="extractor-readability" name="extractor" value="readability" {% if default_extractor == 'readability' %}checked{% endif %}>
                                <label for="extractor-readability">Readability</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="extractor-trafilatura" name="extractor" value="trafilatura" {% if default_extractor == 'trafilatura' %}checked{% endif %}>
                                <label for="extractor-trafilatura">Trafilatura</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="extractor-raw" name="extractor" value="raw" {% if default_extractor == 'raw' %}checked{% endif %}>
                                <label for="extractor-raw">原始 HTML</label>
                            </div>
                        </div>
                    </div>

                    <div class="field">
                        <span class="field-label">浏览器渲染</span>
                        <label class="check" style="margin-bottom: 10px;">
                            <input type="checkbox" id="browser" name="browser">
                            <span>强制用无头浏览器打开</span>
                        </label>
                        <div class="seg">
                            <div class="radio-option">
                                <input type="radio" id="browser-backend-auto" name="browser_backend" value="auto" checked>
                                <label for="browser-backend-auto">跟随配置（{{ config_browser_backend }}）</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="browser-backend-obscura" name="browser_backend" value="obscura">
                                <label for="browser-backend-obscura">Obscura</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="browser-backend-playwright" name="browser_backend" value="playwright">
                                <label for="browser-backend-playwright">Playwright</label>
                            </div>
                        </div>
                    </div>

                    <div class="field">
                        <span class="field-label">线程抓取</span>
                        <div class="seg">
                            <div class="radio-option">
                                <input type="radio" id="thread-default" name="thread_mode" value="default" checked>
                                <label for="thread-default">站点默认</label>
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
                    </div>

                    <div class="field">
                        <span class="field-label">线程作者</span>
                        <div class="seg">
                            <div class="radio-option">
                                <input type="radio" id="thread-author-all" name="thread_author" value="all" checked>
                                <label for="thread-author-all">所有作者</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="thread-author-same" name="thread_author" value="same">
                                <label for="thread-author-same">仅原帖作者</label>
                            </div>
                        </div>
                    </div>

                    <label class="check" id="podcastTranscribeGroup" style="display: none;">
                        <input type="checkbox" id="podcastTranscribe" name="podcast_transcribe" disabled>
                        <span>转写 Podcast 音频</span>
                    </label>
                </div>

                <div class="options-group">
                    <h3>处理</h3>
                    <div class="field">
                        <span class="field-label">语言</span>
                        <div class="seg">
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

                    <label class="field" id="llmProviderGroup">
                        <span class="field-label">LLM Provider</span>
                        <select id="llm" name="llm" class="input">
                            {% for provider in llm_providers %}
                            <option value="{{ provider }}" {% if provider == default_llm_provider %}selected{% endif %}>{{ provider }}</option>
                            {% endfor %}
                        </select>
                    </label>

                    <div class="field">
                        <span class="field-label">图片 OCR</span>
                        <div class="seg">
                            <div class="radio-option">
                                <input type="radio" id="ocr-mode-default" name="ocr_mode" value="default" checked>
                                <label for="ocr-mode-default">站点默认</label>
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

                    <div class="field" id="ocrEngineGroup" {% if not default_ocr_enabled %}style="display: none;"{% endif %}>
                        <span class="field-label">OCR 引擎</span>
                        <div class="seg">
                            <div class="radio-option">
                                <input type="radio" id="ocr-engine-auto" name="ocr_engine" value="auto" {% if default_ocr_engine == 'auto' %}checked{% endif %}>
                                <label for="ocr-engine-auto">自动回退</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="ocr-engine-paddleocr" name="ocr_engine" value="paddleocr" {% if default_ocr_engine == 'paddleocr' %}checked{% endif %}>
                                <label for="ocr-engine-paddleocr">PaddleOCR</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="ocr-engine-rapidocr" name="ocr_engine" value="rapidocr" {% if default_ocr_engine == 'rapidocr' %}checked{% endif %}>
                                <label for="ocr-engine-rapidocr">RapidOCR</label>
                            </div>
                            <div class="radio-option">
                                <input type="radio" id="ocr-engine-tesseract" name="ocr_engine" value="tesseract" {% if default_ocr_engine == 'tesseract' %}checked{% endif %}>
                                <label for="ocr-engine-tesseract">Tesseract</label>
                            </div>
                        </div>
                    </div>

                    <div class="field" id="ocrLangGroup" style="display: none;">
                        <span class="field-label">OCR 语言（Tesseract）</span>
                        <div class="seg">
                            {% for option in ocr_lang_options %}
                            <div class="radio-option">
                                <input type="radio" id="ocr-lang-{{ loop.index }}" name="ocr_lang" value="{{ option.value }}" {% if option.checked %}checked{% endif %}>
                                <label for="ocr-lang-{{ loop.index }}">{{ option.label }}</label>
                            </div>
                            {% endfor %}
                        </div>
                    </div>
                </div>

                <div class="options-group">
                    <h3>输出</h3>
                    <label class="field">
                        <span class="field-label">默认保存格式</span>
                        <select id="format" name="format" class="input">
                            <option value="md" selected>Markdown (.md)</option>
                            <option value="html">HTML (.html)</option>
                            <option value="pdf">PDF (.pdf)</option>
                            <option value="audio">音频 (.mp3)</option>
                            <option value="publish">发布到 Pastebin</option>
                        </select>
                    </label>

                    <div class="field" id="ttsOptionsGroup">
                        <span class="field-label">TTS 语音（保存音频时使用）</span>
                        <div class="tts-grid">
                            <select id="ttsVoice" name="tts_voice" class="input">
                                {% for option in tts_voice_options %}
                                <option value="{{ option.value }}" {% if option.selected %}selected{% endif %}>{{ option.label }}</option>
                                {% endfor %}
                            </select>
                            <input type="text" id="ttsRate" name="tts_rate" class="input" value="{{ default_tts_rate }}" placeholder="+0%">
                            <input type="text" id="ttsVolume" name="tts_volume" class="input" value="{{ default_tts_volume }}" placeholder="+0%">
                        </div>
                    </div>

                    <div class="field">
                        <span class="field-label">Markdown / HTML</span>
                        <label class="check" style="margin-bottom: 9px;">
                            <input type="checkbox" id="noFrontMatter" name="no_front_matter" {% if default_no_front_matter %}checked{% endif %}>
                            <span>不写入 YAML Front Matter</span>
                        </label>
                        <label class="check" style="margin-bottom: 9px;">
                            <input type="checkbox" id="htmlInline" name="html_inline" {% if default_html_inline %}checked{% endif %}>
                            <span>HTML 内联 CSS/JS</span>
                        </label>
                        <label class="check">
                            <input type="checkbox" id="archiveSource" name="archive_source" {% if default_archive %}checked{% endif %}>
                            <span>提交原链接到 Internet Archive</span>
                        </label>
                    </div>
                </div>
            </div>
        </section>

        <div class="panel activity" id="activityBar" hidden>
            <span class="activity-dot" aria-hidden="true"></span>
            <div class="activity-text" id="status"></div>
            <button type="button" class="activity-close" id="statusClose" aria-label="关闭">×</button>
        </div>

        <section class="results" id="resultsSection">
            <div class="section-label">
                <h2>结果</h2>
                <span class="count" id="resultCount"></span>
            </div>
            <div id="resultsContainer"></div>
            <div class="empty-state" id="emptyState">
                <strong>还没有结果</strong>
                点「预览」后，每份内容会生成一张卡片，可预览并分别保存为多种格式。
            </div>
        </section>

        <div class="panel queue-panel" id="saveQueuePanel"></div>

        <footer class="footer">
            <span>Surf v{{ version }}</span>
            <span>·</span>
            <span>本地服务，内容不离开你的机器</span>
            <span>·</span>
            <span><kbd>Ctrl</kbd> + <kbd>Enter</kbd> 保存</span>
        </footer>
    </main>

    <div id="toastContainer" class="toast-container"></div>

    <script>
        const API_BASE = '{{ root_path }}';

        // ---------------------------------------------------------------- state
        let currentResults = [];
        const translationJobIds = new Set();
        const saveJobIds = new Set();
        const knownSaveJobStates = new Map();
        let proxyModeTouched = false;
        let proxyModeProgrammaticUpdate = false;
        let langModeTouched = false;
        let langModeProgrammaticUpdate = false;
        let currentSiteDefaults = {
            site_name: null,
            lang_mode: 'trans',
            ocr_enabled: {{ 'true' if default_ocr_enabled else 'false' }},
            podcast_transcribe_supported: false,
        };
        let siteDefaultsRequestId = 0;
        let siteDefaultsTimer = null;
        let statusTimer = null;

        const urlInput = document.getElementById('url');
        const searchModeCheckbox = document.getElementById('searchMode');
        const previewBtn = document.getElementById('previewBtn');
        const fullTextSaveBtn = document.getElementById('fullTextSaveBtn');
        const composerModeHint = document.getElementById('composerModeHint');
        const resultsContainer = document.getElementById('resultsContainer');
        const emptyState = document.getElementById('emptyState');
        const resultCount = document.getElementById('resultCount');

        // -------------------------------------------------------------- helpers
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

        function getSaveFormat() {
            const select = document.getElementById('format');
            return (select && select.value) || 'md';
        }

        function escapeHtml(value) {
            return String(value ?? '')
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

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

        // --------------------------------------------------------------- modes
        document.querySelectorAll('.mode').forEach((button) => {
            button.addEventListener('click', () => {
                const isSearch = button.dataset.mode === 'search';
                if (searchModeCheckbox) {
                    searchModeCheckbox.checked = isSearch;
                }
                document.querySelectorAll('.mode').forEach((other) => {
                    const active = other === button;
                    other.classList.toggle('active', active);
                    other.setAttribute('aria-selected', active ? 'true' : 'false');
                });
                updateSearchModeControls();
                if (!isSearch) {
                    scheduleSiteDefaultsRefresh();
                }
            });
        });

        function updateSearchModeControls() {
            const enabled = !!searchModeCheckbox?.checked;
            const group = document.getElementById('searchOptionsGroup');
            if (group) {
                group.style.display = enabled ? 'grid' : 'none';
            }
            const label = document.getElementById('urlLabel');
            if (label) {
                label.textContent = enabled ? '搜索关键词' : 'URL 地址或纯文本';
            }
            if (urlInput) {
                urlInput.placeholder = enabled
                    ? '输入搜索关键词，例如：python asyncio tutorial'
                    : '粘贴一个或多个链接，或直接输入一段文字';
            }
            // Full-text mode only makes sense for pasted text, so the button
            // steps aside while searching.
            if (fullTextSaveBtn) {
                fullTextSaveBtn.style.display = enabled ? 'none' : 'inline-flex';
            }
            if (previewBtn) {
                previewBtn.textContent = enabled ? '搜索' : '预览';
            }
            if (composerModeHint) {
                composerModeHint.textContent = enabled
                    ? '搜索后勾选条目；「保存」直接保存全部结果'
                    : '';
            }
            const urlHint = document.getElementById('urlHint');
            if (urlHint) {
                urlHint.style.display = enabled ? 'none' : 'block';
            }
        }

        if (searchModeCheckbox) {
            updateSearchModeControls();
        }

        function isSearchMode() {
            return !!searchModeCheckbox?.checked;
        }

        // ------------------------------------------------------- options panel
        const optionsPanel = document.getElementById('optionsPanel');
        const optionsToggle = document.getElementById('optionsToggle');

        if (optionsToggle && optionsPanel) {
            // On narrow screens the options panel would push the results far
            // below the fold, so start collapsed there.
            if (window.matchMedia('(max-width: 720px)').matches) {
                optionsPanel.dataset.open = 'false';
                optionsToggle.setAttribute('aria-expanded', 'false');
            }
            optionsToggle.addEventListener('click', () => {
                const open = optionsPanel.dataset.open !== 'false';
                optionsPanel.dataset.open = open ? 'false' : 'true';
                optionsToggle.setAttribute('aria-expanded', open ? 'false' : 'true');
            });
        }

        const LANG_LABELS = { trans: '翻译', raw: '原文', both: '双语' };
        const PROXY_LABELS = { auto: '自动代理', win: '系统代理', env: '环境变量代理', custom: '自定义代理', no: '不使用代理' };
        const OCR_LABELS = { default: 'OCR 站点默认', on: 'OCR 开启', off: 'OCR 关闭' };

        function updateOptionsSummary() {
            const summary = document.getElementById('optionsSummary');
            if (!summary) {
                return;
            }
            const parts = [
                LANG_LABELS[getCheckedRadioValue('lang')] || '',
                PROXY_LABELS[getCheckedRadioValue('proxy')] || '',
                OCR_LABELS[getCheckedRadioValue('ocr_mode')] || '',
                (getSaveFormat() === 'publish' ? 'Pastebin' : getSaveFormat().toUpperCase()),
            ];
            summary.textContent = parts.filter(Boolean).join(' · ');
        }

        document.querySelectorAll('input[name="lang"], input[name="proxy"], input[name="ocr_mode"]').forEach((input) => {
            input.addEventListener('change', updateOptionsSummary);
        });
        const formatSelect = document.getElementById('format');
        if (formatSelect) {
            formatSelect.addEventListener('change', () => {
                updateOptionsSummary();
                refreshSaveButtonHighlight();
            });
        }
        updateOptionsSummary();

        function refreshSaveButtonHighlight() {
            const preferred = getSaveFormat();
            resultsContainer.querySelectorAll('.save-links').forEach((links) => {
                links.querySelectorAll('.save-btn').forEach((button) => {
                    const isPreferred = button.dataset.saveType === preferred;
                    button.classList.toggle('save-btn-primary', isPreferred);
                });
            });
        }

        // ------------------------------------------------------ default proxy
        function updateCustomProxyVisibility() {
            const customGroup = document.getElementById('customProxyGroup');
            if (customGroup) {
                customGroup.style.display = getCheckedRadioValue('proxy') === 'custom' ? 'block' : 'none';
            }
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
            if (!force && (proxyModeTouched || isSearchMode())) {
                return;
            }
            try {
                const url = (urlInput?.value || '').trim();
                const query = url ? ('?url=' + encodeURIComponent(url)) : '';
                const response = await fetch(API_BASE + '/api/proxy-default' + query);
                const result = await parseJsonResponse(response);
                if (result.success && result.proxy_mode) {
                    proxyModeProgrammaticUpdate = true;
                    setCheckedRadioValue('proxy', result.proxy_mode);
                    updateCustomProxyVisibility();
                    updateOptionsSummary();
                    proxyModeProgrammaticUpdate = false;
                }
            } catch (error) {
                proxyModeProgrammaticUpdate = false;
            }
        }

        // ------------------------------------------------------- site defaults
        async function refreshSiteDefaults(force = false) {
            if (isSearchMode()) {
                return;
            }
            const requestId = ++siteDefaultsRequestId;
            try {
                const url = (urlInput?.value || '').trim();
                const query = url ? ('?url=' + encodeURIComponent(url)) : '';
                const response = await fetch(API_BASE + '/api/site-defaults' + query);
                const result = await parseJsonResponse(response);
                if (requestId !== siteDefaultsRequestId || !result.success) {
                    return;
                }

                currentSiteDefaults = {
                    site_name: result.site_name || null,
                    lang_mode: result.lang_mode || 'trans',
                    ocr_enabled: !!result.ocr_enabled,
                    podcast_transcribe_supported: !!result.podcast_transcribe_supported,
                };

                if ((force || !langModeTouched) && currentSiteDefaults.lang_mode) {
                    langModeProgrammaticUpdate = true;
                    setCheckedRadioValue('lang', currentSiteDefaults.lang_mode);
                    updateLanguageControls();
                    updateOptionsSummary();
                    langModeProgrammaticUpdate = false;
                }
                updateOcrControls();
                updatePodcastTranscribeControls();
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
            urlInput.addEventListener('blur', () => {
                refreshSiteDefaults(false);
                refreshProxyDefault(false);
            });
        }
        refreshProxyDefault(true);
        refreshSiteDefaults(true);

        // --------------------------------------------------- conditional bits
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

        function updatePodcastTranscribeControls() {
            const group = document.getElementById('podcastTranscribeGroup');
            const checkbox = document.getElementById('podcastTranscribe');
            if (!group || !checkbox) {
                return;
            }
            const supported = currentSiteDefaults.podcast_transcribe_supported;
            group.style.display = supported ? 'inline-flex' : 'none';
            checkbox.disabled = !supported;
            if (!supported) {
                checkbox.checked = false;
            }
        }

        function updateLanguageControls() {
            const keepOriginal = getCheckedRadioValue('lang') === 'raw';
            const llmProviderGroup = document.getElementById('llmProviderGroup');
            if (llmProviderGroup) {
                llmProviderGroup.style.display = keepOriginal ? 'none' : 'block';
            }
            const llmSelect = document.getElementById('llm');
            if (llmSelect) {
                llmSelect.disabled = keepOriginal;
            }
        }

        document.querySelectorAll('input[name="lang"]').forEach((input) => {
            input.addEventListener('change', function() {
                updateLanguageControls();
                if (!langModeProgrammaticUpdate) {
                    langModeTouched = true;
                }
            });
        });

        updateLanguageControls();
        updateOcrControls();

        // -------------------------------------------------------- status/toast
        function showStatus(type, message) {
            const bar = document.getElementById('activityBar');
            const body = document.getElementById('status');
            const close = document.getElementById('statusClose');
            if (!bar || !body) {
                return;
            }
            bar.className = 'panel activity activity-' + type;
            body.innerHTML = message;
            bar.hidden = false;
            if (close) {
                close.hidden = type === 'processing';
            }
            window.clearTimeout(statusTimer);
            if (type !== 'processing') {
                statusTimer = window.setTimeout(() => {
                    bar.hidden = true;
                }, type === 'error' ? 14000 : 7000);
            }
        }

        const statusCloseBtn = document.getElementById('statusClose');
        if (statusCloseBtn) {
            statusCloseBtn.addEventListener('click', () => {
                document.getElementById('activityBar').hidden = true;
            });
        }

        function showToast(type, message, duration = 5000) {
            const container = document.getElementById('toastContainer');
            if (!container) {
                return;
            }
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

        // ------------------------------------------------------- result cards
        function updateResultsMeta() {
            const cards = resultsContainer.querySelectorAll('.result-card:not(.aggregate-card)');
            if (resultCount) {
                resultCount.textContent = cards.length
                    ? `${cards.length} 张结果卡${cards.length > 1 ? '（可合并保存）' : ''}`
                    : '';
            }
            if (emptyState) {
                emptyState.style.display = cards.length ? 'none' : 'block';
            }
        }

        function revealResults() {
            const section = document.getElementById('resultsSection');
            if (section) {
                section.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }

        function collapseOptions() {
            if (optionsPanel && optionsPanel.dataset.open !== 'false') {
                optionsPanel.dataset.open = 'false';
                if (optionsToggle) {
                    optionsToggle.setAttribute('aria-expanded', 'false');
                }
            }
        }

        function cardShell() {
            return `
                <div class="result-head">
                    <div class="result-title-wrap">
                        <h2 class="result-title"></h2>
                        <div class="result-meta"><span class="result-source"></span><span class="result-flags"></span></div>
                    </div>
                </div>
                <div class="save-bar">
                    <div class="save-config">
                        <label class="save-field">
                            <span>保存到</span>
                            <input class="save-input save-dir-input" type="text" spellcheck="false">
                        </label>
                        <label class="save-field">
                            <span>文件名</span>
                            <input class="save-input save-title-input" type="text">
                        </label>
                    </div>
                    <div class="save-links"></div>
                </div>
                <div class="tabs">
                    <button type="button" class="tab active" data-tab="markdown">Markdown</button>
                    <button type="button" class="tab" data-tab="html">HTML</button>
                    <button type="button" class="tab" data-tab="raw">原文</button>
                </div>
                <div class="tab-content active" data-tab-content="markdown"><pre class="content-preview"></pre></div>
                <div class="tab-content" data-tab-content="html"><pre class="content-preview"></pre></div>
                <div class="tab-content" data-tab-content="raw"><pre class="content-preview"></pre></div>
            `;
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

        const SAVE_FORMATS = [
            { type: 'md', label: 'MD' },
            { type: 'html', label: 'HTML' },
            { type: 'pdf', label: 'PDF' },
            { type: 'audio', label: '音频' },
            { type: 'publish', label: 'Pastebin' },
        ];

        function addSaveButtons(container, result) {
            const saveLinks = container.querySelector('.save-links');
            if (!saveLinks) {
                return;
            }
            saveLinks.innerHTML = '';
            const preferred = getSaveFormat();

            SAVE_FORMATS.forEach((format) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'save-btn' + (format.type === preferred ? ' save-btn-primary' : '');
                button.dataset.saveType = format.type;
                button.textContent = format.label;
                button.title = `保存为 ${format.type.toUpperCase()}`;
                button.onclick = () => saveFile(container, result, { fileType: format.type });
                saveLinks.appendChild(button);
            });

            const divider = document.createElement('span');
            divider.className = 'save-btn-divider';
            saveLinks.appendChild(divider);

            const playBtn = document.createElement('button');
            playBtn.type = 'button';
            playBtn.className = 'save-btn';
            playBtn.textContent = '朗读';
            playBtn.title = '生成并播放语音';
            playBtn.onclick = () => playAudio(container, result);
            saveLinks.appendChild(playBtn);
        }

        function fillResultCard(card, result, meta) {
            const title = result?.title || meta?.title || 'Untitled';
            card.dataset.defaultDirs = JSON.stringify(result?.defaultDirs || {});
            card.dataset.defaultSaveTitle = result?.defaultSaveTitle || title;
            card.dataset.inputUrl = result?.input_url || '';
            card.dataset.translationJobId = result?.translation_job_id || '';

            const titleEl = card.querySelector('.result-title');
            if (titleEl) {
                titleEl.textContent = title;
            }
            const sourceEl = card.querySelector('.result-source');
            if (sourceEl) {
                sourceEl.textContent = meta?.source || '';
            }
            const flags = [];
            if (result?.source_site && result.source_site !== 'text') {
                flags.push(`<span class="badge badge-accent">${escapeHtml(result.source_site)}</span>`);
            }
            if (result?.source_is_pdf) {
                flags.push('<span class="badge">PDF</span>');
            }
            if (result?.translation_pending) {
                flags.push('<span class="badge badge-accent">翻译中</span>');
            } else if (result?.translation_performed) {
                flags.push('<span class="badge badge-ok">已翻译</span>');
            }
            if (result?.metadata?.search_prefix) {
                flags.push(`<span class="badge">${escapeHtml(result.metadata.search_prefix)}</span>`);
            }
            const flagsEl = card.querySelector('.result-flags');
            if (flagsEl) {
                flagsEl.innerHTML = flags.join('');
            }

            const markdownPre = card.querySelector('[data-tab-content="markdown"] pre');
            const htmlPre = card.querySelector('[data-tab-content="html"] pre');
            const rawPre = card.querySelector('[data-tab-content="raw"] pre');
            if (markdownPre) markdownPre.textContent = result?.markdown || '';
            if (htmlPre) htmlPre.textContent = result?.html || '';
            if (rawPre) rawPre.textContent = result?.raw || '';

            const dirInput = card.querySelector('.save-dir-input');
            const titleInput = card.querySelector('.save-title-input');
            if (dirInput) {
                dirInput.value = (result?.defaultDirs || {}).md || '';
            }
            if (titleInput) {
                titleInput.value = result?.defaultSaveTitle || title;
            }

            addSaveButtons(card, result);
            wireResultTabs(card);
        }

        function populateResultCard(card, result, index, total) {
            card.innerHTML = cardShell();
            const label = total > 1 ? ` (${index + 1}/${total})` : '';
            const source = result?.metadata?.source_url || result?.input_url || '';
            fillResultCard(card, result, { source: `${source}${label}` });
            return card;
        }

        function renderResultCard(result, index, total) {
            const card = document.createElement('div');
            card.className = 'panel result-card';
            populateResultCard(card, result, index, total);
            resultsContainer.appendChild(card);
            updateResultsMeta();
            return card;
        }

        function updateResultCard(card, result, index, total) {
            if (!card) {
                return;
            }
            populateResultCard(card, result, index, total);
        }

        function renderErrorCard(input, message, index, total) {
            const card = document.createElement('div');
            card.className = 'panel result-card';
            card.dataset.tone = 'error';
            const label = total > 1 ? ` (${index + 1}/${total})` : '';
            card.innerHTML = `
                <div class="result-head">
                    <div class="result-title-wrap">
                        <h2 class="result-title"></h2>
                        <div class="result-meta">
                            <span class="result-source"></span>
                            <span class="badge badge-err">失败</span>
                        </div>
                    </div>
                </div>
                <pre class="content-preview"></pre>
            `;
            card.querySelector('.result-title').textContent = `获取失败${label}`;
            card.querySelector('.result-source').textContent = input;
            card.querySelector('pre').textContent = message || 'Unknown error';
            resultsContainer.appendChild(card);
            updateResultsMeta();
            return card;
        }

        // ------------------------------------------------------- merge / save
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

                markdownSections.push(`## ${itemTitle}\\n\\n${markdownBody || '_No content_'}`);
                rawSections.push(`## ${itemTitle}\\n\\n${rawBody || '_No content_'}`);
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
                    add_front_matter: !!results[0]?.metadata?.search_query,
                    translated_title: null,
                    source_url: null,
                    archive_url: null,
                    translator: null,
                    html_inline: false,
                    combined_sources: sourceLines,
                    search_query: results[0]?.metadata?.search_query || null,
                    search_prefix: results[0]?.metadata?.search_prefix || '',
                },
            };
        }

        function renderAggregateSaveCard(results) {
            const container = resultsContainer;
            const existingAggregate = container.querySelector('.aggregate-card');
            if (existingAggregate) {
                existingAggregate.remove();
            }
            if (!results || results.length <= 1) {
                updateResultsMeta();
                return;
            }

            const aggregateResult = mergeResultsForSave(results);
            if (!aggregateResult) {
                return;
            }

            const card = document.createElement('div');
            card.className = 'panel result-card aggregate-card';
            card.innerHTML = cardShell();
            fillResultCard(card, aggregateResult, {
                source: `把 ${results.length} 张结果卡合并成一份文档`,
                title: '合并保存全部结果',
            });
            card.querySelector('.result-title').textContent = '合并保存全部结果';
            container.prepend(card);
            updateResultsMeta();
        }

        function refreshAggregateCard() {
            renderAggregateSaveCard(currentResults);
        }

        async function saveFile(container, resultData, options = {}) {
            if (!resultData) {
                showStatus('error', '没有可保存的内容');
                return;
            }
            const fileType = options.fileType || getSaveFormat();
            const speak = !!options.speak;
            const { saveDir, customTitle } = getSaveFormState(container, fileType);
            const inputUrl = container.dataset.inputUrl || '';
            const formData = getCurrentFormData(inputUrl);

            const body = {
                fileType,
                saveDir,
                customTitle,
                data: resultData,
                formData: formData,
                speak
            };
            if (container.classList.contains('aggregate-card')) {
                body.combine_all = true;
                body.results = currentResults;
            }

            try {
                showStatus('processing', '正在提交后台保存...');
                const response = await fetch(API_BASE + '/api/save-async', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                const result = await parseJsonResponse(response);
                if (result.success && result.job_id) {
                    const urlSuffix = inputUrl
                        ? `：<br><span style="word-break: break-all;">${escapeHtml(inputUrl)}</span>`
                        : '';
                    const queueLabel = fileType === 'publish' ? '发布任务' : `${fileType.toUpperCase()} 保存任务`;
                    showStatus('success', `${queueLabel}已加入后台队列，可继续处理其他 URL${urlSuffix}`);
                    notifySaveJobState(result.job_id, 'pending');
                    pollSaveJob(result.job_id);
                } else {
                    showStatus('error', '保存提交失败: ' + (result.error || 'unknown error'));
                }
            } catch (error) {
                showStatus('error', '请求失败: ' + error.message);
            }
        }

        async function playAudio(container, resultData) {
            if (!resultData) {
                showStatus('error', '没有可朗读的内容');
                return;
            }
            try {
                showStatus('processing', '正在生成语音...');
                const formData = getCurrentFormData(container.dataset.inputUrl || '');
                const response = await fetch(API_BASE + '/api/play-audio', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ data: resultData, formData })
                });
                const result = await parseJsonResponse(response);
                if (result.success) {
                    showStatus('success', '语音播放中...');
                } else {
                    showStatus('error', '语音生成失败: ' + (result.error || 'unknown error'));
                }
            } catch (error) {
                showStatus('error', '请求失败: ' + error.message);
            }
        }

        // ------------------------------------------------------ save queue UI
        const SAVE_QUEUE_STATUS_LABELS = {
            pending: '排队中',
            running: '保存中',
            done: '已完成',
            error: '失败'
        };

        function notifySaveJobState(jobId, status) {
            if (!jobId || !status) {
                return;
            }
            if (knownSaveJobStates.get(jobId) === status) {
                return;
            }
            knownSaveJobStates.set(jobId, status);
            refreshSaveQueuePanel();
        }

        function updateQueueBadge(activeCount) {
            const badge = document.getElementById('queueBadge');
            const text = document.getElementById('queueBadgeText');
            if (!badge || !text) {
                return;
            }
            text.textContent = activeCount ? `${activeCount} 个任务` : '';
            badge.classList.toggle('show', activeCount > 0);
        }

        async function refreshSaveQueuePanel() {
            const panel = document.getElementById('saveQueuePanel');
            if (!panel) {
                return;
            }

            try {
                const response = await fetch(API_BASE + '/api/save-jobs?limit=20');
                const payload = await parseJsonResponse(response);
                if (!payload.success) {
                    return;
                }
                const jobs = Array.isArray(payload.jobs) ? payload.jobs : [];
                const activeJobs = jobs.filter(job => job.status === 'pending' || job.status === 'running');
                updateQueueBadge(activeJobs.length);
                if (!jobs.length) {
                    panel.classList.remove('show');
                    panel.innerHTML = '';
                    return;
                }

                const items = jobs.map(job => {
                    const statusLabel = SAVE_QUEUE_STATUS_LABELS[job.status] || job.status;
                    const fileTypeLabel = (job.fileType || '?').toUpperCase();
                    let detailHtml = '';
                    if (job.status === 'done' && job.savePath) {
                        detailHtml = `<span class="save-queue-detail">${escapeHtml(job.savePath)}</span>`;
                    } else if (job.status === 'error' && job.error) {
                        detailHtml = `<span class="save-queue-detail">${escapeHtml(job.error)}</span>`;
                    }
                    return `<li class="save-queue-item">
                        <span class="save-queue-type">${escapeHtml(fileTypeLabel)}</span>
                        <span class="save-queue-url">${escapeHtml(job.url || '(未知 URL)')}</span>
                        <span class="save-queue-status save-queue-status-${escapeHtml(job.status)}">${escapeHtml(statusLabel)}</span>
                        ${detailHtml}
                    </li>`;
                }).join('');

                panel.innerHTML = `
                    <div class="save-queue-title">保存队列 · ${jobs.length}</div>
                    <ul class="save-queue-list">${items}</ul>
                `;
                panel.classList.add('show');
            } catch (error) {
                // Silently ignore queue panel refresh failures
            }
        }

        // -------------------------------------------------------- form data
        // "Full text" is a mode of the composer, not a checkbox: picking the
        // 全文保存 button sets it, pressing 保存 clears it again.
        let fullTextMode = false;

        function setFullTextMode(enabled) {
            fullTextMode = !!enabled;
            const hint = document.getElementById('urlHint');
            if (hint) {
                hint.textContent = fullTextMode
                    ? '全文保存模式：整段文字原样作为一篇帖子保存，其中的链接不会被抓取；第一句作为标题。'
                    : '输入里包含链接时只抓取第一个 http/https URL；不含链接时，这段文字会作为一篇帖子保存，第一句作为标题。';
                hint.style.display = isSearchMode() ? 'none' : 'block';
            }
        }

        function getCurrentFormData(inputUrl) {
            const form = document.getElementById('surfForm');
            const fd = new FormData(form);
            const data = Object.fromEntries(fd.entries());
            if (!data.proxy) {
                data.proxy = getCheckedRadioValue('proxy') || 'no';
            }
            data.url = inputUrl || (data.url || '').trim();
            data.lang_touched = langModeTouched;
            data.browser = data.browser === 'on';
            data.podcast_transcribe = data.podcast_transcribe === 'on';
            data.html_inline = data.html_inline === 'on';
            data.no_front_matter = data.no_front_matter === 'on';
            data.archive_source = data.archive_source === 'on';
            data.save_full_text = fullTextMode;
            data.search_mode = data.search_mode === 'on';
            return data;
        }

        function collectInputs(rawInput, formData) {
            if (formData.save_full_text) {
                return rawInput ? [rawInput] : [];
            }
            const urls = extractUrlsFromInput(rawInput);
            return urls.length ? urls : (rawInput ? [rawInput] : []);
        }

        // ------------------------------------------------------------ search
        async function requestSearch(keywords, data) {
            const payload = {
                keywords,
                search_engine: data.search_engine || '',
                search_limit: data.search_limit || '',
                search_prefix: data.search_prefix || '',
                proxy: data.proxy || 'no',
                custom_proxy: data.custom_proxy || '',
            };
            const response = await fetch(API_BASE + '/api/search', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const result = await parseJsonResponse(response);
            if (!result.success) {
                throw new Error(result.error || '搜索失败');
            }
            return result;
        }

        function renderSearchPreviewCard(searchData) {
            resultsContainer.querySelectorAll('.search-preview-card').forEach((el) => el.remove());
            const card = document.createElement('div');
            card.className = 'panel result-card search-preview-card';
            card.dataset.keywords = searchData.keywords || '';
            card.dataset.prefix = searchData.prefix || '';
            card.innerHTML = `
                <div class="result-head">
                    <div class="result-title-wrap">
                        <h2 class="result-title"></h2>
                        <div class="result-meta">
                            <span class="badge badge-accent">${escapeHtml(searchData.engine)}</span>
                            <span>${searchData.results.length} 条检索结果</span>
                        </div>
                    </div>
                </div>
                <div class="search-toolbar">
                    <div class="search-toolbar-left">
                        <label class="check">
                            <input type="checkbox" class="search-select-all" checked>
                            <span>全选</span>
                        </label>
                        <span class="search-result-count"></span>
                    </div>
                    <button type="button" class="btn btn-primary btn-sm search-fetch-btn">预览选中</button>
                </div>
                <div class="search-result-list"></div>
            `;
            card.querySelector('.result-title').textContent = `搜索结果：${searchData.keywords}`;

            const list = card.querySelector('.search-result-list');
            searchData.results.forEach((item, index) => {
                const row = document.createElement('label');
                row.className = 'search-result-item';
                row.innerHTML = `
                    <input type="checkbox" class="search-result-checkbox" data-index="${index}" checked>
                    <span class="search-result-text">
                        <span class="search-result-title"></span>
                        <span class="search-result-url"></span>
                    </span>
                `;
                row.querySelector('.search-result-title').textContent = item.title || item.url;
                row.querySelector('.search-result-url').textContent = item.url;
                list.appendChild(row);
            });

            const updateCount = () => {
                const boxes = Array.from(card.querySelectorAll('.search-result-checkbox'));
                const checked = boxes.filter((box) => box.checked).length;
                card.querySelector('.search-result-count').textContent =
                    `已选 ${checked}/${boxes.length} · 文件名前缀 ${searchData.prefix || '(无)'}`;
                const selectAll = card.querySelector('.search-select-all');
                selectAll.checked = checked === boxes.length;
                selectAll.indeterminate = checked > 0 && checked < boxes.length;
                card.querySelector('.search-fetch-btn').disabled = checked === 0;
            };
            card.querySelectorAll('.search-result-checkbox').forEach((box) => {
                box.addEventListener('change', updateCount);
            });
            card.querySelector('.search-select-all').addEventListener('change', function() {
                card.querySelectorAll('.search-result-checkbox').forEach((box) => {
                    box.checked = this.checked;
                });
                updateCount();
            });
            card.querySelector('.search-fetch-btn').addEventListener('click', () => {
                processSearchSelection(card, searchData);
            });
            updateCount();
            resultsContainer.appendChild(card);
            updateResultsMeta();
            revealResults();
            return card;
        }

        function getSearchSelectionUrls(card, searchData) {
            return Array.from(card.querySelectorAll('.search-result-checkbox'))
                .filter((box) => box.checked)
                .map((box) => searchData.results[Number(box.dataset.index)].url);
        }

        async function processSearchSelection(card, searchData) {
            const urls = getSearchSelectionUrls(card, searchData);
            if (!urls.length) {
                return;
            }
            const fetchBtn = card.querySelector('.search-fetch-btn');
            const submitBtn = document.getElementById('submitBtn');
            fetchBtn.disabled = true;
            fetchBtn.innerHTML = '<span class="spinner"></span>预览中...';
            submitBtn.disabled = true;
            currentResults = [];
            resultsContainer.querySelectorAll('.result-card:not(.search-preview-card)').forEach((el) => el.remove());
            updateResultsMeta();
            const data = getCurrentFormData('');
            data.search_mode = false;
            data.search_query = searchData.keywords;
            data.search_prefix = (document.getElementById('searchPrefix')?.value || '').trim();
            data.save_full_text = false;
            try {
                const successCount = await processInputList(urls, data);
                showStatus(
                    successCount ? 'success' : 'error',
                    `预览完成：成功 ${successCount}/${urls.length}`
                );
            } catch (error) {
                showStatus('error', '预览失败: ' + error.message);
            } finally {
                fetchBtn.disabled = false;
                fetchBtn.textContent = '预览选中';
                submitBtn.disabled = false;
                submitBtn.textContent = '保存';
            }
        }

        // -------------------------------------------------------- processing
        async function processInputList(inputs, data) {
            let successCount = 0;
            for (let i = 0; i < inputs.length; i += 1) {
                const input = inputs[i];
                showStatus('processing', `正在处理 ${i + 1}/${inputs.length}：<b>${escapeHtml(input)}</b>`);
                const itemData = { ...data, url: input };
                const response = await fetch(API_BASE + '/api/process', {
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
            revealResults();
            return successCount;
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
                    const response = await fetch(API_BASE + `/api/translation-jobs/${encodeURIComponent(jobId)}`);
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
                        showStatus('success', `翻译完成：${escapeHtml(updated.title || 'Untitled')}`);
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
                    const response = await fetch(API_BASE + `/api/save-jobs/${encodeURIComponent(jobId)}`);
                    const job = await parseJsonResponse(response);
                    notifySaveJobState(jobId, job.status);
                    if (!job.success) {
                        showToast('error', '保存失败: ' + (job.error || 'unknown error'));
                        saveJobIds.delete(jobId);
                        return;
                    }
                    if (job.status === 'done') {
                        const fileTypeLabel = (job.fileType || '').toUpperCase();
                        if (job.fileType === 'publish') {
                            showToast('success', `已发布到: ${job.savePath}`, 8000);
                        } else {
                            showToast('success', `${fileTypeLabel} 已保存到: ${job.savePath}`, 8000);
                        }
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

        function resetBeforeRun() {
            currentResults = [];
            resultsContainer.innerHTML = '';
            updateResultsMeta();
            collapseOptions();
        }

        async function queueDirectSaves(inputs, formData) {
            const fileType = getSaveFormat();
            const queuedJobs = [];
            for (let i = 0; i < inputs.length; i += 1) {
                const input = inputs[i];
                showStatus('processing', `正在抓取并保存 ${i + 1}/${inputs.length}：<b>${escapeHtml(input)}</b>`);
                const itemFormData = { ...formData, url: input };
                const response = await fetch(API_BASE + '/api/save-async', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        fileType,
                        saveDir: '',
                        customTitle: '',
                        data: {},
                        formData: itemFormData,
                        speak: false,
                    }),
                });
                const result = await parseJsonResponse(response);
                if (!result.success || !result.job_id) {
                    throw new Error(result.error || '保存提交失败');
                }
                queuedJobs.push({ jobId: result.job_id, url: input });
                pollSaveJob(result.job_id);
            }
            if (!queuedJobs.length) {
                throw new Error('没有可保存的内容');
            }
            let message;
            if (queuedJobs.length === 1) {
                message = `已加入 ${fileType.toUpperCase()} 后台保存队列：<br><span style="word-break: break-all;">${escapeHtml(queuedJobs[0].url)}</span>`;
            } else {
                const urlList = queuedJobs.map(job => `<li>${escapeHtml(job.url)}</li>`).join('');
                message = `已加入 ${queuedJobs.length} 个 ${fileType.toUpperCase()} 后台保存任务：<ul style="margin: 8px 0 0; padding-left: 20px;">${urlList}</ul>`;
            }
            showStatus('success', message);
            queuedJobs.forEach(job => notifySaveJobState(job.jobId, 'pending'));
            return queuedJobs;
        }

        // The composer has three actions over one pipeline:
        //   previewBtn       fetch and render result cards
        //   fullTextSaveBtn  keep the pasted text verbatim and save it directly
        //   submitBtn        fetch and save with the current default format
        async function runComposer(action) {
            const buttons = [submitBtn, previewBtn, fullTextSaveBtn].filter(Boolean);
            if (action === 'fullText') {
                setFullTextMode(true);
            } else if (action === 'link') {
                setFullTextMode(false);
            }
            buttons.forEach((button) => { button.disabled = true; });
            const busyLabel = action === 'preview' ? '预览中...' : '保存中...';
            const labelOf = (button) => (button === previewBtn
                ? (isSearchMode() ? '搜索' : '预览')
                : button.textContent);
            const labels = new Map(buttons.map((button) => [button, labelOf(button)]));
            const activeBtn = action === 'preview' ? previewBtn : submitBtn;
            if (activeBtn) {
                activeBtn.innerHTML = `<span class="spinner"></span>${busyLabel}`;
            }
            if (action !== 'preview') {
                resetBeforeRun();
            }

            const data = getCurrentFormData('');
            const rawInput = (data.url || '').trim();

            try {
                if (data.search_mode) {
                    if (!rawInput) {
                        showStatus('error', '请输入搜索关键词');
                        return;
                    }
                    showStatus('processing', `正在搜索：<b>${escapeHtml(rawInput)}</b>`);
                    const searchData = await requestSearch(rawInput, data);
                    if (action === 'preview') {
                        renderSearchPreviewCard(searchData);
                        showStatus('success', `找到 ${searchData.results.length} 条结果，勾选后点击“预览选中”。`);
                        return;
                    }
                    const urls = searchData.results.map((item) => item.url);
                    if (!urls.length) {
                        throw new Error('没有可保存的搜索结果');
                    }
                    data.search_mode = false;
                    data.save_full_text = false;
                    data.search_query = searchData.keywords;
                    data.search_prefix = (document.getElementById('searchPrefix')?.value || '').trim();
                    showStatus('processing', '正在抓取并保存全部检索结果...');
                    await queueDirectSaves(urls, data);
                    return;
                }

                const inputs = collectInputs(rawInput, data);
                if (!inputs.length) {
                    showStatus('error', action === 'fullText' ? '请输入要保存的文字' : '请输入 URL 或文本');
                    return;
                }

                if (action === 'preview') {
                    showStatus('processing', '正在抓取网页内容...');
                    const successCount = await processInputList(inputs, data);
                    showStatus(successCount ? 'success' : 'error', `处理完成：成功 ${successCount}/${inputs.length}`);
                    return;
                }

                showStatus('processing', action === 'fullText' ? '正在保存全文...' : '正在抓取并保存...');
                await queueDirectSaves(inputs, data);
            } catch (error) {
                const prefix = action === 'preview' ? '请求失败: ' : '保存失败: ';
                showStatus('error', prefix + error.message);
            } finally {
                buttons.forEach((button) => {
                    button.disabled = false;
                    button.textContent = labels.get(button);
                });
                // A one-shot 全文保存 press should not stick for the next run.
                if (fullTextMode && action !== 'preview') {
                    setFullTextMode(false);
                }
            }
        }

        document.getElementById('surfForm').addEventListener('submit', function(e) {
            e.preventDefault();
            runComposer('save');
        });

        if (previewBtn) {
            previewBtn.addEventListener('click', () => runComposer('preview'));
        }

        if (fullTextSaveBtn) {
            fullTextSaveBtn.addEventListener('click', () => runComposer('fullText'));
        }

        // Ctrl/Cmd + Enter submits the composer.
        document.getElementById('surfForm').addEventListener('keydown', function(event) {
            if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                this.requestSubmit();
            }
        });
    </script>
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


def resolve_web_save_proxy_overrides(formData):
    """Derive proxy overrides from re-submitted form data for save-time network calls."""
    if not formData:
        return None, None
    proxy_mode = normalize_web_proxy_mode(formData.get("proxy", "no"))
    custom_proxy = (formData.get("custom_proxy") or "").strip() or None
    return get_web_proxy_override(proxy_mode), custom_proxy


def save_pdf_result(title, resultData, config, output_path, form_data=None):
    """Save a PDF result, keeping the original file when the source was a PDF.

    URLs that pointed directly at a PDF were converted to text for previews and
    Markdown/HTML output.  For PDF output we re-download the original bytes so
    the user gets the source document rather than a re-rendered copy.
    """
    source_url = ((resultData.get("metadata") or {}).get("source_url") or "").strip()
    if resultData.get("source_is_pdf") and source_url:
        proxy_override, custom_proxy = resolve_web_save_proxy_overrides(form_data)
        candidates = [source_url]
        archive_url = (resultData.get("pdf_archive_url") or "").strip()
        if archive_url and archive_url != source_url:
            candidates.append(archive_url)
        for candidate in candidates:
            try:
                downloaded = Fetcher.fetch(
                    candidate,
                    config=config,
                    proxy_mode_override=proxy_override,
                    custom_proxy_override=custom_proxy,
                    pdf_mode="download",
                )
                if isinstance(downloaded, PdfDownload):
                    return OutputHandler.save_pdf_download(
                        downloaded,
                        config,
                        output_path=output_path,
                    )
                logger.warning("PDF source did not return a PDF: %s", candidate)
            except Exception as exc:
                logger.warning("Could not re-download PDF from %s (%s)", candidate, exc)
        logger.warning("Falling back to a PDF generated from the extracted text")

    return OutputHandler.generate_pdf(
        title,
        resultData.get("markdown", ""),
        config,
        output_path=output_path,
    )


def resolve_web_bool_option(data, key, config, section, option):
    """Boolean option: explicit form/API value wins; otherwise fall back to config."""
    if key in data:
        return bool(data[key])
    return _config_bool(config, section, option)


# Curated common edge-tts voices for the Web UI dropdown.
_TTS_VOICE_CHOICES = [
    ("zh-CN-XiaoxiaoNeural", "晓晓 (zh-CN-XiaoxiaoNeural)"),
    ("zh-CN-YunxiNeural", "云希 (zh-CN-YunxiNeural)"),
    ("zh-CN-YunyangNeural", "云扬 (zh-CN-YunyangNeural)"),
    ("zh-CN-XiaoyiNeural", "晓伊 (zh-CN-XiaoyiNeural)"),
    ("zh-CN-YunjianNeural", "云健 (zh-CN-YunjianNeural)"),
    ("zh-TW-HsiaoChenNeural", "晓臻 (zh-TW-HsiaoChenNeural)"),
    ("zh-HK-HiuMaanNeural", "晓曼 (zh-HK-HiuMaanNeural)"),
    ("en-US-AriaNeural", "Aria (en-US-AriaNeural)"),
    ("en-US-GuyNeural", "Guy (en-US-GuyNeural)"),
    ("en-US-JennyNeural", "Jenny (en-US-JennyNeural)"),
    ("en-US-ChristopherNeural", "Christopher (en-US-ChristopherNeural)"),
    ("en-US-MichelleNeural", "Michelle (en-US-MichelleNeural)"),
    ("en-GB-SoniaNeural", "Sonia (en-GB-SoniaNeural)"),
    ("ja-JP-NanamiNeural", "Nanami (ja-JP-NanamiNeural)"),
    ("ko-KR-SunHiNeural", "SunHi (ko-KR-SunHiNeural)"),
]


def build_tts_voice_options(default_voice):
    """Build Web UI TTS voice options, ensuring the configured default is present."""
    options = [
        {"value": value, "label": label, "selected": value == default_voice}
        for value, label in _TTS_VOICE_CHOICES
    ]
    if default_voice and not any(opt["value"] == default_voice for opt in options):
        options.insert(0, {"value": default_voice, "label": default_voice, "selected": True})
    return options


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
    return Fetcher._extract_url_from_text(value)


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
        ocr=ocr_mode == "on",
        no_ocr=ocr_mode == "off",
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
        config.get("OCR", "engine", fallback="auto").strip().lower() or "auto"
    )
    if default_ocr_engine not in {"rapidocr", "tesseract", "paddleocr", "auto"}:
        default_ocr_engine = "auto"

    default_ocr_lang = (config.get("OCR", "lang", fallback="chi_sim+eng") or "").strip()
    if not default_ocr_lang:
        default_ocr_lang = "chi_sim+eng"

    config_browser_backend = (
        config.get("Browser", "backend", fallback="obscura").strip().lower() or "obscura"
    )
    if config_browser_backend not in {"obscura", "playwright"}:
        config_browser_backend = "obscura"

    default_extractor = resolve_extractor(config)
    default_html_inline = _config_bool(config, "Output", "html_inline")
    default_no_front_matter = _config_bool(config, "Output", "no_front_matter")
    default_archive = _config_bool(config, "Output", "archive")

    default_search_engine = (
        config.get("Search", "engine", fallback=SearchHandler.DEFAULT_ENGINE) or ""
    ).strip().lower()
    if default_search_engine not in SearchHandler.ENGINE_CHOICES:
        default_search_engine = SearchHandler.DEFAULT_ENGINE
    try:
        default_search_limit = int(
            config.get("Search", "limit", fallback=str(SearchHandler.DEFAULT_LIMIT))
        )
    except (TypeError, ValueError):
        default_search_limit = SearchHandler.DEFAULT_LIMIT
    default_search_limit = max(1, min(default_search_limit, SearchHandler.MAX_LIMIT))
    search_engine_labels = {
        "auto": "自动（DuckDuckGo → Bing → DeepSeek/SearXNG/Metaso）",
        "duckduckgo": "DuckDuckGo",
        "bing": "Bing",
        "searxng": "SearXNG（需配置实例）",
        "metaso": "Metaso（需配置 API Key）",
        "deepseek": "DeepSeek（需配置 API Key）",
    }
    search_engine_options = [
        {
            "value": engine,
            "label": search_engine_labels.get(engine, engine),
            "selected": engine == default_search_engine,
        }
        for engine in SearchHandler.ENGINE_CHOICES
    ]

    default_tts_voice = (config.get("TTS", "voice", fallback="zh-CN-XiaoxiaoNeural") or "").strip()
    if not default_tts_voice:
        default_tts_voice = "zh-CN-XiaoxiaoNeural"
    default_tts_rate = (config.get("TTS", "rate", fallback="+0%") or "+0%").strip() or "+0%"
    default_tts_volume = (config.get("TTS", "volume", fallback="+0%") or "+0%").strip() or "+0%"
    tts_voice_options = build_tts_voice_options(default_tts_voice)

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
        "config_browser_backend": config_browser_backend,
        "default_extractor": default_extractor,
        "default_html_inline": default_html_inline,
        "default_no_front_matter": default_no_front_matter,
        "default_archive": default_archive,
        "default_search_engine": default_search_engine,
        "default_search_limit": default_search_limit,
        "search_engine_options": search_engine_options,
        "default_tts_voice": default_tts_voice,
        "default_tts_rate": default_tts_rate,
        "default_tts_volume": default_tts_volume,
        "tts_voice_options": tts_voice_options,
    }


def build_output_path(title, extension, target_dir, source_url=None, html_content=None,
                      filename_prefix=""):
    filename_title = OutputHandler._get_filename_title(
        title, source_url=source_url, html_content=html_content
    )
    if filename_prefix:
        filename_title = f"{filename_prefix}{filename_title}"
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


def _build_translated_web_result(base_result, translated_markdown, translated_title, translation_performed, lang_mode, translated_description=None):
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
    result["metadata"]["translated_description"] = translated_description if translation_performed else None
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
        source_site = (source.get("source_site") or "").strip().lower()
        if not source_site:
            payload = _extract_direct_markdown_payload((source.get("metadata") or {}).get("html_content"))
            if payload:
                source_site = (payload.get("site_name") or "").strip().lower()
        translated_markdown, translated_title = _translate_markdown_document(
            raw_markdown,
            title=original_title,
            target_lang=config.get("Output", "target_language", fallback="zh-cn"),
            config=config,
            llm_provider=job.get("llm_provider"),
            source_site=source_site,
        )
        translation_performed = (
            translated_markdown != raw_markdown
            or (translated_title or "") != original_title
        )
        translated_description = None
        original_description = source.get("original_description")
        if original_description and translation_performed:
            translated_description, _ = ContentProcessor.translate_if_needed(
                original_description,
                title=None,
                target_lang=config.get("Output", "target_language", fallback="zh-cn"),
                config=config,
                llm_provider=job.get("llm_provider"),
            )
        result = _build_translated_web_result(
            source,
            translated_markdown,
            translated_title,
            translation_performed,
            job.get("lang_mode", "trans"),
            translated_description=translated_description,
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

        # Resolve resultData: formData always wins (save-time settings),
        # then explicit translation_job_id, then cached data as last resort
        translation_job_id = data.get("translation_job_id")
        formData = data.get("formData")
        cached_pending = (data.get("data") or {}).get("translation_pending")
        cached_tjid = (data.get("data") or {}).get("translation_job_id")
        logger.warning(
            "Save worker resolving: tid=%s formData=%s dataPending=%s dataTid=%s",
            bool(translation_job_id), bool(formData), bool(cached_pending), bool(cached_tjid),
        )
        cached_metadata = (data.get("data") or {}).get("metadata") or {}
        if formData and cached_metadata.get("search_query") and not formData.get("search_query"):
            formData = {
                **formData,
                "search_query": cached_metadata.get("search_query"),
                "search_prefix": formData.get("search_prefix")
                or cached_metadata.get("search_prefix")
                or "",
            }

        if data.get("combine_all"):
            source_results = data.get("results") or []
            logger.warning("Save worker: combining %s results", len(source_results))
            if formData:
                processed_results = []
                for item in source_results:
                    source_input = (item.get("input_url") or "").strip()
                    if not source_input:
                        continue
                    item_metadata = item.get("metadata") or {}
                    item_form = {
                        **formData,
                        "url": source_input,
                        "search_query": item_metadata.get("search_query") or formData.get("search_query") or "",
                        "search_prefix": formData.get("search_prefix") or item_metadata.get("search_prefix") or "",
                    }
                    processed_item, _lang_mode, _translation_pending = _process_web_request(
                        item_form, translate_sync=True
                    )
                    processed_results.append(processed_item)
                resultData = build_combined_result_payload(processed_results) or {}
            else:
                resultData = build_combined_result_payload(source_results) or {}
        elif formData:
            logger.warning("Save worker: re-processing with formData (lang=%s)", formData.get("lang"))
            result, _lang_mode, _translation_pending = _process_web_request(formData, translate_sync=True)
            resultData = result
        elif translation_job_id:
            logger.warning("Save worker: using explicit translation_job_id=%s", translation_job_id)
            resultData = _wait_for_translation_and_get_result(translation_job_id)
        else:
            resultData = data.get("data", {})
            logger.warning("Save worker: using cached resultData, pending=%s", bool(resultData.get("translation_pending")))
            if resultData.get("translation_pending") and resultData.get("translation_job_id"):
                logger.warning("Save worker: cached resultData has pending translation, waiting for job %s", resultData["translation_job_id"])
                resultData = _wait_for_translation_and_get_result(resultData["translation_job_id"])
            if data.get("combine_all"):
                resultData = build_combined_result_payload(data.get("results", [])) or {}

        defaultDirs = {
            "md": config.get_path("Output", "md_dir", fallback="notes"),
            "html": config.get_path("Output", "html_dir", fallback="web"),
            "pdf": config.get_path("Output", "pdf_dir", fallback="pdf"),
            "audio": config.get_path("Output", "audio_dir", fallback="audio"),
        }

        # Publishing to Pastebin does not write local files.
        if fileType != "publish":
            if saveDir:
                targetDir = resolve_user_path(saveDir)
            else:
                targetDir = defaultDirs.get(fileType, ".")
            os.makedirs(targetDir, exist_ok=True)

        title = customTitle or resultData.get("title", "Untitled")
        metadata = resultData.get("metadata", {})
        html_content = metadata.get("html_content", "")
        source_url = metadata.get("source_url")
        search_query = metadata.get("search_query")
        search_prefix = metadata.get("search_prefix") or ""

        if fileType == "publish":
            md_content = resultData.get("markdown", "")
            proxy_override, custom_proxy_override = resolve_web_save_proxy_overrides(formData)
            paste_url = PublishHandler.publish_to_pastebin(
                title,
                md_content,
                config,
                proxy_mode_override=proxy_override,
                custom_proxy_override=custom_proxy_override,
            )
            if not paste_url:
                raise ValueError("发布到 Pastebin 失败（请检查 [publish.pastebin] api_dev_key 配置）")
            save_path = paste_url
        elif fileType == "md":
            md_content = resultData.get("markdown", "")
            output_path = build_output_path(
                title, "md", targetDir, source_url=source_url, html_content=html_content,
                filename_prefix=search_prefix,
            )
            save_path = OutputHandler.save_markdown(
                title,
                md_content,
                config,
                output_path=output_path,
                html_content=html_content,
                add_front_matter=metadata.get("add_front_matter", True),
                translated_title=metadata.get("translated_title"),
                translated_description=metadata.get("translated_description"),
                source_url=metadata.get("source_url"),
                translator=metadata.get("translator"),
                archive_url=metadata.get("archive_url"),
                search_query=search_query,
            )
        elif fileType == "html":
            cleaned_html = resultData.get("html", "")
            output_path = build_output_path(
                title, "html", targetDir, source_url=source_url, html_content=html_content,
                filename_prefix=search_prefix,
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
                title, "pdf", targetDir, source_url=source_url, html_content=html_content,
                filename_prefix=search_prefix,
            )
            save_path = save_pdf_result(
                title, resultData, config, output_path, form_data=formData
            )
        elif fileType == "audio":
            if resultData.get("source_site") in {"pocketcasts", "xiaoyuzhoufm"}:
                output_path = OutputHandler._get_podcast_audio_path(
                    resultData, targetDir, custom_title=customTitle or None
                )
                save_path = OutputHandler._save_podcast_audio(resultData, config, output_path)
            else:
                md_content = resultData.get("markdown", "")
                output_path = build_output_path(
                    title, "mp3", targetDir, source_url=source_url, html_content=html_content,
                    filename_prefix=search_prefix,
                )
                if formData:
                    _apply_tts_overrides(
                        config,
                        formData.get("tts_voice"),
                        formData.get("tts_rate"),
                        formData.get("tts_volume"),
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

    first_metadata = valid_results[0].get("metadata") or {}
    search_query = first_metadata.get("search_query")
    search_prefix = first_metadata.get("search_prefix") or ""

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
            "add_front_matter": bool(search_query),
            "translated_title": None,
            "source_url": None,
            "archive_url": None,
            "translator": None,
            "html_inline": False,
            "combined_sources": combined_sources,
            "search_query": search_query,
            "search_prefix": search_prefix,
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

    args = SimpleNamespace(ocr=False, no_ocr=False)
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
        "podcast_transcribe_supported": site_name in {"pocketcasts", "xiaoyuzhoufm"},
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
        root_path=_ROOT_PATH,
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


@app.route("/api/search", methods=["POST"])
def search_keywords():
    """Resolve keywords to search results for the Web UI picker."""
    data = request.get_json(silent=True) or {}
    config = get_config()
    keywords = (data.get("keywords") or data.get("url") or "").strip()
    if not keywords:
        return jsonify({"success": False, "error": "请输入搜索关键词"})

    engine = (
        data.get("search_engine")
        or config.get("Search", "engine", fallback=SearchHandler.DEFAULT_ENGINE)
        or SearchHandler.DEFAULT_ENGINE
    ).strip().lower()
    if engine not in SearchHandler.ENGINE_CHOICES:
        engine = SearchHandler.DEFAULT_ENGINE

    try:
        limit = int(
            data.get("search_limit")
            or config.get("Search", "limit", fallback=str(SearchHandler.DEFAULT_LIMIT))
        )
    except (TypeError, ValueError):
        limit = SearchHandler.DEFAULT_LIMIT
    limit = max(1, min(limit, SearchHandler.MAX_LIMIT))

    proxy_mode = normalize_web_proxy_mode(data.get("proxy", "no"))
    proxy_override = get_web_proxy_override(proxy_mode)
    custom_proxy = (data.get("custom_proxy") or "").strip() or None
    if proxy_mode == "custom" and not custom_proxy:
        return jsonify({"success": False, "error": "custom 代理模式需要填写自定义代理地址"})

    try:
        results = SearchHandler.search(
            keywords,
            engine=engine,
            limit=limit,
            config=config,
            proxy_mode_override=proxy_override,
            custom_proxy_override=custom_proxy,
        )
    except Exception as exc:
        logger.error("Web search failed: %s", exc)
        return jsonify({"success": False, "error": str(exc)})

    if not results:
        return jsonify({"success": False, "error": f"没有找到与“{keywords}”相关的搜索结果"})

    prefix = build_search_filename_prefix(data.get("search_prefix"), keywords)
    return jsonify(
        {
            "success": True,
            "keywords": keywords,
            "engine": engine,
            "limit": limit,
            "prefix": prefix,
            "results": results,
        }
    )


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

    search_query = (data.get("search_query") or "").strip() or None
    search_prefix = (
        build_search_filename_prefix(data.get("search_prefix"), search_query)
        if search_query
        else ""
    )

    # Per-request headless browser backend override (Web UI radio).
    _apply_browser_backend_override(config, data.get("browser_backend"))

    original_md = ""
    original_title = "Untitled"
    translated_title = None
    content_base_url = None
    archive_is_url = None
    translation_performed = False
    translation_pending = False
    translated_description = None
    processed = {}
    source_is_pdf = False

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

        if str(data.get("podcast_transcribe", False)).strip().lower() in {"1", "true", "yes", "on"}:
            if site_name not in {"pocketcasts", "xiaoyuzhoufm"}:
                raise ValueError("podcast_transcribe currently supports Pocket Casts and Xiaoyuzhou episode URLs only")
            html_content = Fetcher._transcribe_podcast_content(
                html_content,
                config,
                proxy_mode_override=proxy_override,
                custom_proxy_override=custom_proxy,
            )

        # Paywall detection and archive-domain fallback
        archive_is_url = None
        paywall_result = Fetcher._detect_paywall(html_content, url=url)
        if paywall_result and paywall_result.get("detected"):
            logger.warning(
                f"Paywall detected (confidence: {paywall_result['confidence']:.0%}): "
                f"{paywall_result.get('reason', 'unknown')}"
            )
            logger.info("Attempting to fetch from archive domains...")
            archived_html, snapshot_url = Fetcher._fetch_archiveis_snapshot(
                url,
                config=config,
                proxy_mode_override=proxy_override,
                custom_proxy_override=custom_proxy,
            )
            if archived_html:
                logger.info("Archive snapshot fetched successfully, using it as content source.")
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
            extractor=resolve_extractor(config, data.get("extractor")),
        )
        source_url = processed["source_url"]
        content_base_url = processed["content_base_url"]
        title = processed["title"]
        cleaned_html = processed["cleaned_html"]
        md_content = processed["markdown"]
        original_md = processed["raw_markdown"]
        original_title = processed["original_title"]
        translated_title = processed["translated_title"]
        translated_description = processed["translated_description"]
        translation_performed = processed["translation_performed"]
        source_is_pdf = bool(processed.get("is_pdf"))
        translation_pending = (not translate_sync) and lang_mode in {"trans", "both"}
        if translation_pending:
            title = original_title
            md_content = original_md

    # archive_url: prioritize archive snapshot (from paywall fallback)
    archive_url = archive_is_url
    archive_source = resolve_web_bool_option(data, "archive_source", config, "Output", "archive")
    no_front_matter = resolve_web_bool_option(
        data, "no_front_matter", config, "Output", "no_front_matter"
    )
    html_inline = resolve_web_bool_option(data, "html_inline", config, "Output", "html_inline")
    if not archive_url and archive_source and not no_front_matter:
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
        "source_site": site_name,
        "source_is_pdf": source_is_pdf,
        "pdf_archive_url": processed.get("pdf_archive_url"),
        "defaultDirs": defaultDirs,
        "defaultSaveTitle": defaultSaveTitle,
        "translation_performed": translation_performed,
        "translated_title": translated_title if translation_performed else None,
        "translated_description": translated_description if translation_performed else None,
        "translation_pending": translation_pending,
        "metadata": {
            "title": title,
            "html_content": html_content,
            "original_description": processed.get("original_description"),
            "add_front_matter": not no_front_matter,
            "translated_title": translated_title if translation_performed else None,
            "translated_description": translated_description if translation_performed else None,
            "source_url": source_url,
            "archive_url": archive_url,
            "translator": None,
            "html_inline": html_inline,
            "search_query": search_query,
            "search_prefix": search_prefix,
        },
    }
    return result, lang_mode, translation_pending
def _record_web_failure(data):
    """Persist a URL that failed to fetch/convert so it can be retried later."""
    failed_url = extract_url_from_text(data.get("url"))
    if not failed_url:
        return
    try:
        _save_failed_url_note(failed_url, get_config())
    except Exception as exc:
        logger.warning("Could not save failed URL note for %s: %s", failed_url, exc)


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
        _record_web_failure(data)
        return jsonify({"success": False, "error": str(e)})
    except Exception as e:
        logger.error(f"Processing failed: {e}")
        _record_web_failure(data)
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


@app.route("/api/save-jobs", methods=["GET"])
def save_jobs_list():
    """List recent async save jobs, newest first."""
    try:
        limit = max(1, min(int(request.args.get("limit", 20)), 100))
    except (TypeError, ValueError):
        limit = 20

    with _SAVE_JOBS_LOCK:
        jobs = sorted(
            _SAVE_JOBS.items(),
            key=lambda kv: kv[1].get("created_at") or 0,
            reverse=True,
        )

    entries = []
    for job_id, job in jobs[:limit]:
        job_data = job.get("data") or {}
        form_data = job_data.get("formData") or {}
        result_data = job_data.get("data") or {}
        entry = {
            "job_id": job_id,
            "status": job.get("status"),
            "fileType": job_data.get("fileType"),
            "url": form_data.get("url") or result_data.get("input_url") or "",
            "title": (job_data.get("customTitle") or "").strip(),
            "created_at": job.get("created_at"),
            "updated_at": job.get("updated_at"),
        }
        if job.get("status") == "done":
            entry["savePath"] = job.get("savePath")
        elif job.get("status") == "error":
            entry["error"] = job.get("error") or "Save failed"
        entries.append(entry)

    return jsonify({"success": True, "jobs": entries})


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

@app.route("/api/play-audio", methods=["POST"])
def play_audio():
    """Generate and play TTS audio without saving a file."""
    data = request.get_json(silent=True) or {}
    resultData = data.get("data", {})
    if not resultData:
        return jsonify({"success": False, "error": "No content to play"})
    try:
        config = get_config()
        title = resultData.get("title", "Untitled")
        md_content = resultData.get("markdown", "")
        if not md_content.strip():
            return jsonify({"success": False, "error": "No text content to speak"})
        form_data = data.get("formData") or {}
        _apply_tts_overrides(
            config,
            form_data.get("tts_voice"),
            form_data.get("tts_rate"),
            form_data.get("tts_volume"),
        )
        threading.Thread(
            target=TTSHandler.run_tts,
            args=(title, md_content, config),
            kwargs={"speak": True, "save_path": None},
            daemon=True,
        ).start()
        return jsonify({"success": True})
    except Exception as e:
        logger.error("Play audio failed: %s", e)
        return jsonify({"success": False, "error": str(e)})


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

    # Use user-specified directory or default; publishing writes no local files.
    if fileType != "publish":
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
        search_query = metadata.get("search_query")
        search_prefix = metadata.get("search_prefix") or ""

        if fileType == "publish":
            md_content = resultData.get("markdown", "")
            proxy_override, custom_proxy_override = resolve_web_save_proxy_overrides(
                data.get("formData")
            )
            paste_url = PublishHandler.publish_to_pastebin(
                title,
                md_content,
                config,
                proxy_mode_override=proxy_override,
                custom_proxy_override=custom_proxy_override,
            )
            if not paste_url:
                return jsonify(
                    {
                        "success": False,
                        "error": "发布到 Pastebin 失败（请检查 [publish.pastebin] api_dev_key 配置）",
                    }
                )
            return jsonify({"success": True, "savePath": paste_url})

        if fileType == "md":
            md_content = resultData.get("markdown", "")
            output_path = build_output_path(
                title, "md", targetDir, source_url=source_url, html_content=html_content,
                filename_prefix=search_prefix,
            )
            md_path = OutputHandler.save_markdown(
                title,
                md_content,
                config,
                output_path=output_path,
                html_content=html_content,
                add_front_matter=metadata.get("add_front_matter", True),
                translated_title=metadata.get("translated_title"),
                translated_description=metadata.get("translated_description"),
                source_url=metadata.get("source_url"),
                translator=metadata.get("translator"),
                archive_url=metadata.get("archive_url"),
                search_query=search_query,
            )
            return jsonify({"success": True, "savePath": md_path})

        elif fileType == "html":
            cleaned_html = resultData.get("html", "")
            output_path = build_output_path(
                title, "html", targetDir, source_url=source_url, html_content=html_content,
                filename_prefix=search_prefix,
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
                title, "pdf", targetDir, source_url=source_url, html_content=html_content,
                filename_prefix=search_prefix,
            )
            pdf_path = save_pdf_result(
                title,
                resultData,
                config,
                output_path,
                form_data=data.get("formData"),
            )
            return jsonify({"success": True, "savePath": pdf_path})

        elif fileType == "audio":
            if resultData.get("source_site") in {"pocketcasts", "xiaoyuzhoufm"}:
                output_path = OutputHandler._get_podcast_audio_path(
                    resultData, targetDir, custom_title=customTitle or None
                )
                audio_path = OutputHandler._save_podcast_audio(resultData, config, output_path)
                return jsonify({"success": True, "savePath": audio_path})

            md_content = resultData.get("markdown", "")
            output_path = build_output_path(
                title, "mp3", targetDir, source_url=source_url, html_content=html_content,
                filename_prefix=search_prefix,
            )
            form_data = data.get("formData") or {}
            _apply_tts_overrides(
                config,
                form_data.get("tts_voice"),
                form_data.get("tts_rate"),
                form_data.get("tts_volume"),
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


def run_server(host="127.0.0.1", port=18473, debug=False, root=""):
    """Run the web server."""
    global _ROOT_PATH
    if root:
        _ROOT_PATH = root.rstrip("/")
        _apply_root_path()

    # Open browser
    base = f"http://{host}:{port}"
    url = f"{base}{_ROOT_PATH}" if _ROOT_PATH else base
    threading.Timer(1, lambda: webbrowser.open(url)).start()

    print(f"\nSurf Web Interface v{get_runtime_version()}")
    print("=====================================")
    print(f"Server running at: {url}")
    if _ROOT_PATH:
        print(f"Root path prefix: {_ROOT_PATH}")
    print("Press Ctrl+C to stop\n")

    app.run(host=host, port=port, debug=debug)


def main():
    parser = argparse.ArgumentParser(
        prog="python surf_web.py",
        usage="%(prog)s [options]",
        description=(
            "Serve the Surf web interface: paste a URL or text and press 保存 to fetch\n"
            "and save it as Markdown, HTML, PDF, audio, or a Pastebin post, or press\n"
            "预览 first to read the result and pick a format per document."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python surf_web.py                          http://127.0.0.1:18473
  python surf_web.py --port 8080              pick another port
  python surf_web.py --host 0.0.0.0           listen on every interface
  python surf_web.py --root /surf             serve under a reverse-proxy prefix
  python surf_web.py --debug                  Flask debug mode with reloader

deployment:
  The built-in server is Flask's development server. For anything long-running,
  point a WSGI server at surf_web:app instead:

    uv run waitress-serve --listen=0.0.0.0:18473 --threads=4 surf_web:app

  When a reverse proxy forwards the prefix as-is (proxy_pass without a trailing
  slash), start Surf with the same prefix via --root.
""",
    )

    group = parser.add_argument_group("server")
    group.add_argument(
        "--host",
        default="127.0.0.1",
        metavar="ADDR",
        help="Interface to bind (default: 127.0.0.1, local access only)",
    )
    group.add_argument(
        "--port",
        type=int,
        default=18473,
        metavar="PORT",
        help="Port to bind (default: 18473)",
    )
    group.add_argument(
        "--root",
        default="",
        metavar="PATH",
        help="URL path prefix when running behind a reverse proxy, e.g. /surf",
    )
    group.add_argument(
        "--bind",
        metavar="ADDR",
        help="Deprecated alias for --host",
    )
    group.add_argument(
        "--debug",
        action="store_true",
        help="Run Flask in debug mode (never expose this publicly)",
    )

    args = parser.parse_args()

    host = args.bind if args.bind else args.host
    run_server(host=host, port=args.port, debug=args.debug, root=args.root)


if __name__ == "__main__":
    main()
