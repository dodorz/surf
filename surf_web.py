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
import sys
import threading
import webbrowser
from pathlib import Path

__version__ = "1.1.1.21"

# Flask web framework
try:
    from flask import (
        Flask,
        render_template_string,
        request,
        jsonify,
        send_file,
        Response,
    )
except ImportError:
    print("Flask not installed. Installing...")
    os.system("pip install flask")
    from flask import (
        Flask,
        render_template_string,
        request,
        jsonify,
        send_file,
        Response,
    )

# Import surf modules
from surf import (
    Config,
    Fetcher,
    ContentProcessor,
    OutputHandler,
    TTSHandler,
    __version__,
    logger,
)

app = Flask(__name__)

# HTML Template
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Surf - URL转Markdown/PDF/HTML/Audio</title>
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
        
        input[type="text"], input[type="url"], select {
            width: 100%;
            padding: 12px 15px;
            border: 2px solid #e1e5e9;
            border-radius: 8px;
            font-size: 16px;
            transition: border-color 0.3s;
        }
        
        input[type="text"]:focus, input[type="url"]:focus, select:focus {
            outline: none;
            border-color: #667eea;
        }
        
        .url-input {
            font-size: 18px;
        }
        
        .options-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }
        
        .checkbox-group {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .checkbox-group input[type="checkbox"] {
            width: 18px;
            height: 18px;
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
        
        .version-info {
            text-align: center;
            color: rgba(255,255,255,0.7);
            font-size: 12px;
            margin-top: 20px;
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
                    <label for="url">URL 地址</label>
                    <input type="url" id="url" name="url" class="url-input" 
                           placeholder="https://example.com" required>
                </div>
                
                <div class="section-title">输出格式</div>
                <div class="options-grid">
                    <div class="form-group">
                        <label for="format">格式</label>
                        <select id="format" name="format">
                            <option value="md">Markdown (.md)</option>
                            <option value="html">HTML (.html)</option>
                            <option value="pdf">PDF (.pdf)</option>
                            <option value="audio">Audio (.mp3)</option>
                        </select>
                    </div>
                    
                    <div class="form-group">
                        <label for="lang">语言模式</label>
                        <select id="lang" name="lang">
                            <option value="trans">翻译为中文</option>
                            <option value="raw">保留原文</option>
                            <option value="both">双语对照</option>
                        </select>
                    </div>
                    
                    <div class="form-group">
                        <label for="proxy">代理模式</label>
                        <select id="proxy" name="proxy">
                            <option value="auto">自动 (环境变量)</option>
                            <option value="no">不使用代理</option>
                            <option value="win">Windows 系统代理</option>
                            <option value="custom">自定义代理</option>
                        </select>
                    </div>
                    
                    <div class="form-group" id="customProxyGroup" style="display: none;">
                        <label for="customProxy">自定义代理地址</label>
                        <input type="text" id="customProxy" name="custom_proxy" 
                               placeholder="http://127.0.0.1:7890">
                    </div>
                </div>
                
                <div class="section-title">高级选项</div>
                <div class="options-grid">
                    <div class="form-group checkbox-group">
                        <input type="checkbox" id="browser" name="browser">
                        <label for="browser">使用浏览器渲染 (JavaScript)</label>
                    </div>
                    
                    <div class="form-group checkbox-group">
                        <input type="checkbox" id="speak" name="speak">
                        <label for="speak">播放语音 (TTS)</label>
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
                        开始转换
                    </button>
                </div>
            </form>
        </div>
        
        <div class="card result-card" id="resultCard">
            <div class="result-header">
                <h2 class="result-title" id="resultTitle">转换结果</h2>
                <div class="save-links" id="saveLinks">
                    <!-- Save buttons will be added here -->
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
        // Show/hide custom proxy input
        document.getElementById('proxy').addEventListener('change', function() {
            const customGroup = document.getElementById('customProxyGroup');
            customGroup.style.display = this.value === 'custom' ? 'block' : 'none';
        });

        // Show/hide format-specific options
        document.getElementById('format').addEventListener('change', function() {
            const format = this.value;

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
        });

        // Initialize format-specific options visibility on page load
        document.getElementById('format').dispatchEvent(new Event('change'));

        // Tab switching
        document.querySelectorAll('.tab').forEach(tab => {
            tab.addEventListener('click', function() {
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                
                this.classList.add('active');
                document.getElementById('tab-' + this.dataset.tab).classList.add('active');
            });
        });
        
        // Form submission
        document.getElementById('surfForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            
            const submitBtn = document.getElementById('submitBtn');
            const statusDiv = document.getElementById('status') || createStatusDiv();
            
            submitBtn.disabled = true;
            submitBtn.innerHTML = '<span class="spinner"></span>处理中...';
            
            showStatus('processing', '正在获取网页内容...');
            
            const formData = new FormData(this);
            const data = Object.fromEntries(formData.entries());
            
            // Convert checkboxes to booleans
            data.browser = data.browser === 'on';
            data.speak = data.speak === 'on';
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
                
                const result = await response.json();
                
                if (result.success) {
                    showStatus('success', '转换完成！');

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
                    
                    document.getElementById('resultCard').classList.add('show');
                } else {
                    showStatus('error', '错误: ' + result.error);
                }
            } catch (error) {
                showStatus('error', '请求失败: ' + error.message);
            } finally {
                submitBtn.disabled = false;
                submitBtn.textContent = '开始转换';
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

        async function saveFile(fileType) {
            if (!currentResult) {
                showStatus('error', '没有可保存的内容');
                return;
            }

            // Get default directory from config
            const defaultDir = window.defaultDirs[fileType] || '';

            const saveDir = prompt(
                `保存 ${fileType.toUpperCase()} 文件\n\n输入保存目录（留空使用默认目录 ${defaultDir}）：`,
                ''
            );

            // If user cancelled
            if (saveDir === null) {
                return;
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
                        data: currentResult
                    })
                });

                const result = await response.json();

                if (result.success) {
                    showStatus('success', `${fileType.toUpperCase()} 文件已保存到: ${result.savePath}`);
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
    config_path = "config.ini"
    if os.path.exists(config_path):
        return Config(config_path)
    return Config()


@app.route("/")
def index():
    """Serve the main page."""
    return render_template_string(HTML_TEMPLATE, version=__version__)


@app.route("/api/process", methods=["POST"])
def process_url():
    """Process a URL and return the result."""
    import traceback

    data = request.json
    url = data.get("url")

    if not url:
        return jsonify({"success": False, "error": "URL is required"})

    config = get_config()

    try:
        # Fetch content
        proxy_mode = data.get("proxy", "auto")
        custom_proxy = data.get("custom_proxy")

        html_content = Fetcher.fetch(
            url,
            config=config,
            use_browser=data.get("browser", False),
            proxy_mode_override=proxy_mode if proxy_mode != "auto" else None,
            custom_proxy_override=custom_proxy,
        )

        # Extract content
        title, cleaned_html = ContentProcessor.extract_content(html_content)
        if not title:
            title = "Untitled"

        # Convert to markdown
        md_content = ContentProcessor.to_markdown(cleaned_html)

        # Handle language mode
        lang_mode = data.get("lang", "trans")
        target_lang = config.get("Output", "target_language", fallback="zh-cn")

        original_md = md_content
        original_title = title
        translated_title = None

        if lang_mode != "raw":
            md_content, translated_title = ContentProcessor.translate_if_needed(
                md_content, title=title, target_lang=target_lang, config=config
            )

            if lang_mode == "both" and translated_title != original_title:
                title = f"{translated_title} ({original_title})"
                md_content = f"{md_content}\n\n---\n\n### Original Content / 原文内容\n\n{original_md}"
            else:
                title = translated_title

        # Convert relative URLs to absolute
        md_content = OutputHandler._convert_markdown_urls_to_absolute(md_content, url)
        cleaned_html = OutputHandler._convert_urls_to_absolute(cleaned_html, url)

        # Determine if translation was performed for YAML front matter
        translation_performed = lang_mode != "raw"
        translator = None
        if translation_performed:
            try:
                llm_config = config.get_llm_config()
                translator = llm_config["model"]
            except Exception:
                pass

        # Get default directories for frontend
        defaultDirs = {
            "md": config.get("Output", "md_dir", fallback="notes"),
            "html": config.get("Output", "html_dir", fallback="web"),
            "pdf": config.get("Output", "pdf_dir", fallback="pdf"),
            "audio": config.get("Output", "audio_dir", fallback="audio"),
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
                    "translated_title": translated_title if translation_performed else None,
                    "source_url": url,
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
    data = request.json
    fileType = data.get("fileType")
    saveDir = data.get("saveDir", "").strip()
    resultData = data.get("data", {})

    if not fileType:
        return jsonify({"success": False, "error": "File type is required"})

    config = get_config()

    # Determine default directories based on config
    defaultDirs = {
        "md": config.get("Output", "md_dir", fallback="notes"),
        "html": config.get("Output", "html_dir", fallback="web"),
        "pdf": config.get("Output", "pdf_dir", fallback="pdf"),
        "audio": config.get("Output", "audio_dir", fallback="audio"),
    }

    # Use user-specified directory or default
    if saveDir:
        targetDir = saveDir
    else:
        targetDir = defaultDirs.get(fileType, ".")

    # Create target directory if it doesn't exist
    os.makedirs(targetDir, exist_ok=True)

    try:
        title = resultData.get("title", "Untitled")
        html_content = resultData.get("html_content", "")
        metadata = resultData.get("metadata", {})

        # Sanitize title for filename
        safe_title = "".join(
            c for c in title if c.isalnum() or c in (" ", ".", "_", "-")
        ).strip()
        safe_title = safe_title[:100]

        if fileType == "md":
            md_content = resultData.get("markdown", "")
            output_path = os.path.join(targetDir, f"{safe_title}.md")
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
            output_path = os.path.join(targetDir, f"{safe_title}.html")
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
            output_path = os.path.join(targetDir, f"{safe_title}.pdf")
            pdf_path = OutputHandler.generate_pdf(
                title, md_content, config, output_path=output_path
            )
            return jsonify({"success": True, "savePath": pdf_path})

        elif fileType == "audio":
            md_content = resultData.get("markdown", "")
            output_path = os.path.join(targetDir, f"{safe_title[:20].replace(' ', '_')}.mp3")
            TTSHandler.run_tts(title, md_content, config, speak=False, save_path=output_path)

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

    print(f"\n🌊 Surf Web Interface v{__version__}")
    print(f"=====================================")
    print(f"Server running at: {url}")
    print(f"Press Ctrl+C to stop\n")

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
