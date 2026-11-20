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


def test_ambiguous_paywall_container_only_counts_for_short_teasers():
    long_body = "Real article paragraph. " * 400
    short_body = "Only the first paragraph is served here."

    long_result = Fetcher._detect_paywall(
        f'<html><body><article><div class="paywall-content"><p>{long_body}</p></div>'
        "</article></body></html>",
        url="https://example.com/essay",
    )
    short_result = Fetcher._detect_paywall(
        f'<html><body><article><div class="paywall-content"><p>{short_body}</p></div>'
        '<p class="paywall-text">Subscribe to read the full story. '
        "You've reached your free article limit.</p></article></body></html>",
        url="https://medium.com/@user/story",
    )

    assert "ambiguous paywall CSS" not in (long_result["reason"] or "")
    assert long_result["detected"] is False
    assert short_result["detected"] is True


# --- Chinese-language editions ---------------------------------------------
# WSJ/FT/NYT Chinese editions word their CTA differently ("订阅《华尔街日报》，
# 畅读全文"), so the English patterns never fire and a teaser looked complete.

_WSJ_CN_TEASER = """
<html><head><title>七张图表解析全球年轻人为何对经济现状心怀不满 - WSJ</title>
<meta name="description" content="全球年轻人抗议的背后是他们的共同担忧。">
</head><body><article>
<h1>七张图表解析全球年轻人为何对经济现状心怀不满</h1>
<p>{}</p>
</article>
<footer>订阅《华尔街日报》， 畅读全文 订阅 我们注意到您已经是订阅用户， 请登录</footer>
</body></html>
"""


def _wsj_cn_body():
    return "“迈入成年生活的成本很高，而且这一过程非常、非常缺乏保障，”牛津大学教授蒂姆·弗兰达斯说。" * 12


def test_chinese_subscriber_cta_marks_a_teaser_as_paywalled():
    html = _WSJ_CN_TEASER.format(_wsj_cn_body())
    result = Fetcher._detect_paywall(html, url="https://cn.wsj.com/articles/x")

    assert result["detected"] is True
    assert "paywall text pattern" in (result["reason"] or "")


def test_full_chinese_article_is_not_a_paywall():
    # The complete article, with the same footer. The body is long, so the CTA
    # is weak evidence and must not flip the decision.
    long_body = ("这是一个完整的段落，用来模拟真实文章的长度。 " * 8).join([""] * 1)
    paragraphs = "".join(
        f"<p>{'图表显示，全球年轻人在教育与就业上的处境正在恶化。' * 12}</p>" for _ in range(12)
    )
    html = (
        "<html><head><title>七张图表解析全球年轻人为何对经济现状心怀不满 - WSJ</title></head>"
        f"<body><article><h1>七张图表</h1>{paragraphs}</article>"
        "<footer>订阅《华尔街日报》， 畅读全文 请登录</footer></body></html>"
    )
    result = Fetcher._detect_paywall(html, url="https://cn.wsj.com/articles/x")

    assert result["detected"] is False


def test_generic_chinese_footer_words_are_not_markers():
    # "订阅" and "请登录" appear in every WSJ footer, including readable pages.
    html = (
        "<html><head><title>免费文章 - WSJ</title></head><body>"
        "<article><p>" + ("这是一篇可以自由阅读的文章正文内容。 " * 50) + "</p></article>"
        "<footer>订阅 请登录 回到顶部</footer></body></html>"
    )
    result = Fetcher._detect_paywall(html, url="https://cn.wsj.com/articles/free")

    assert result["detected"] is False
