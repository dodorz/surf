"""Paywall detection must not treat framework markup as a paywall."""

from surf import Fetcher

WISPTERM_URL = "https://wispterm.com/"


def _wispterm_like_page():
    body_text = (
        "WispTerm 把 SSH、分屏与标签页、文件浏览和预览、Agent 会话放在同一个桌面应用里。"
        "选择下载版本，观看视频教程，查看 GitHub。"
    ) * 30
    return f"""<!DOCTYPE html>
<html lang="zh"><head>
<title>WispTerm</title>
<meta name="description" content="WispTerm 是一款为远程开发和 AI 辅助工作流设计的跨平台终端。">
</head><body>
<script class="$tsr" id="$tsr-stream-barrier">
self.$R=self.$R||{{}}; self.$_TSR={{h(){{this.hydrated=!0}},e(){{}},c(){{}}}};
</script>
<article>
  <h2>WIN Windows 推荐</h2>
  <p>大多数 Windows 用户选这个。解压 zip 后直接运行 wispterm.exe。</p>
  <p>下载 ZIP</p>
</article>
<main>
  <h1>开源 · Windows / macOS</h1>
  <p>{body_text}</p>
</main>
</body></html>"""


def test_wispterm_framework_script_is_not_a_paywall():
    result = Fetcher._detect_paywall(_wispterm_like_page(), url=WISPTERM_URL)

    assert result["detected"] is False
    assert result["confidence"] < 0.5
    assert "paywall CSS" not in (result["reason"] or "")
    assert "truncated content" not in (result["reason"] or "")


def test_real_paywall_marker_still_detected():
    html = """<html><head><title>Members only story</title></head><body>
    <article><p>Only a short teaser is available here.</p></article>
    <div class="paywall-overlay">Subscribe to continue reading.</div>
    </body></html>"""

    result = Fetcher._detect_paywall(html, url="https://example.com/news/story")

    assert result["detected"] is True
    assert "paywall CSS elements" in result["reason"]


def test_long_main_body_overrides_tiny_article_card():
    html = f"""<html><head><title>Project page</title></head><body>
    <article><p>Download the Windows build.</p></article>
    <main><p>{'Real documentation content. ' * 80}</p></main>
    <div class="premium-feature">Premium theme included.</div>
    </body></html>"""

    result = Fetcher._detect_paywall(html, url="https://example.com/project")

    assert result["detected"] is False
    assert "truncated content" not in (result["reason"] or "")
