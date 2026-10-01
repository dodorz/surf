import surf
from surf import Fetcher


def test_is_xhslink_short_url_accepts_new_cn_domain():
    assert Fetcher._is_xhslink_short_url("https://xhslink.com/o/abc")
    assert Fetcher._is_xhslink_short_url("http://www.xhslink.cn/o/abc")
    assert not Fetcher._is_xhslink_short_url("https://www.xiaohongshu.com/explore/abc")
    assert not Fetcher._is_xhslink_short_url("https://xhslink.example.com/o/abc")
    assert not Fetcher._is_xhslink_short_url("")


def test_xhslink_cn_is_a_common_short_url():
    assert Fetcher._is_common_short_url("https://xhslink.cn/o/abc")
    assert Fetcher._is_common_short_url("https://xhslink.com/o/abc")


def test_xhslink_short_url_resolution_passthrough_for_non_short_urls():
    url = "https://www.xiaohongshu.com/explore/69765621000000000e00c91e?xsec_token=x="
    resolved, cleaned = Fetcher._resolve_xhslink_short_url(url)
    assert resolved == url
    assert cleaned == url


def test_xiaohongshu_risk_url_detection():
    assert Fetcher._is_xiaohongshu_risk_url("https://www.xiaohongshu.com/punish/abc123")
    assert Fetcher._is_xiaohongshu_risk_url("https://www.xiaohongshu.com/captcha.html")
    assert Fetcher._is_xiaohongshu_risk_url("https://www.xiaohongshu.com/security_verify?x=1")
    assert not Fetcher._is_xiaohongshu_risk_url(
        "https://www.xiaohongshu.com/explore/69765621000000000e00c91e?xsec_token=x="
    )
    assert not Fetcher._is_xiaohongshu_risk_url("")


def test_xiaohongshu_content_url_detection():
    assert Fetcher._is_xiaohongshu_content_url(
        "https://www.xiaohongshu.com/explore/69765621000000000e00c91e?xsec_token=x="
    )
    assert Fetcher._is_xiaohongshu_content_url(
        "https://www.xiaohongshu.com/discovery/item/64abc?xsec_token=x="
    )
    assert Fetcher._is_xiaohongshu_content_url("https://www.xiaohongshu.com/user/profile/5f0c")
    assert not Fetcher._is_xiaohongshu_content_url("https://www.xiaohongshu.com/")
    assert not Fetcher._is_xiaohongshu_content_url("https://www.xiaohongshu.com/explore")
    assert not Fetcher._is_xiaohongshu_content_url("https://example.com/explore/abc")


def test_detect_risk_control_from_url():
    reason = Fetcher._detect_xiaohongshu_risk_control(
        "https://www.xiaohongshu.com/punish/xyz", ""
    )
    assert reason is not None
    assert "risk-control URL" in reason


def test_detect_risk_control_from_body_text():
    reason = Fetcher._detect_xiaohongshu_risk_control(
        "https://www.xiaohongshu.com/explore/abc",
        "当前环境异常，请完成验证后继续访问",
    )
    assert reason is not None
    assert "risk-control message" in reason


def test_detect_risk_control_allows_normal_note_page():
    body = "这是一篇正常的笔记正文，分享我的旅行见闻。"
    assert Fetcher._detect_xiaohongshu_risk_control(
        "https://www.xiaohongshu.com/explore/abc", body
    ) is None
    assert Fetcher._detect_xiaohongshu_risk_control(
        "https://www.xiaohongshu.com/explore/abc", ""
    ) is None


def test_normal_note_mentioning_captcha_terms_is_not_flagged():
    body = (
        "本文介绍网站的 captcha 人机校验方案，"
        "服务端出现 too many requests 时应退避重试，"
        "账号异常时提示用户重新登录。"
    )
    assert (
        Fetcher._detect_xiaohongshu_risk_control(
            "https://www.xiaohongshu.com/explore/abc", body
        )
        is None
    )


def test_extract_url_from_xiaohongshu_share_text():
    share_text = "看看这篇笔记【美食探店】打开App查看 https://xhslink.cn/o/xyz，复制本条信息"
    assert Fetcher._extract_url_from_text(share_text) == "https://xhslink.cn/o/xyz"


def test_extract_url_from_share_text_keeps_query_and_token():
    share_text = (
        "【笔记】https://www.xiaohongshu.com/explore/69765621000000000e00c91e"
        "?xsec_token=CB_OFtX= 打开【小红书】App查看精彩内容！"
    )
    assert (
        Fetcher._extract_url_from_text(share_text)
        == "https://www.xiaohongshu.com/explore/69765621000000000e00c91e"
        "?xsec_token=CB_OFtX="
    )


def test_extract_url_from_plain_and_invalid_text():
    assert (
        Fetcher._extract_url_from_text("https://www.xiaohongshu.com/explore/abc")
        == "https://www.xiaohongshu.com/explore/abc"
    )
    assert Fetcher._extract_url_from_text("no url here") is None
    assert Fetcher._extract_url_from_text("") is None
    assert Fetcher._extract_url_from_text(None) is None


def test_special_handler_matches_xhslink_cn():
    handler, site_name, _ = surf._get_handler_for_url("https://xhslink.cn/o/xyz")
    assert site_name == "xiaohongshu"
    assert handler == Fetcher._fetch_xiaohongshu

    handler, site_name, _ = surf._get_handler_for_url("http://xhslink.com/o/xyz")
    assert site_name == "xiaohongshu"


def test_special_handler_matches_note_and_profile_urls():
    for url in (
        "https://www.xiaohongshu.com/explore/69765621000000000e00c91e?xsec_token=x=",
        "https://www.xiaohongshu.com/discovery/item/64abc",
        "https://www.xiaohongshu.com/user/profile/5f0c",
    ):
        _, site_name, _ = surf._get_handler_for_url(url)
        assert site_name == "xiaohongshu", url


def test_special_handler_disables_generic_fallback():
    _, _, config = surf._get_handler_for_url(
        "https://www.xiaohongshu.com/explore/69765621000000000e00c91e?xsec_token=x="
    )
    assert config.get("no_generic_fallback") is True
    assert config.get("default_ocr") is True


def test_unwrap_xiaohongshu_login_redirect_recovers_note_url():
    login_url = (
        "https://www.xiaohongshu.com/login?redirectPath="
        "http%3A%2F%2Fwww.xiaohongshu.com%2Fdiscovery%2Fitem%2F6ab0ed550000000036028a50"
        "%3Fapp_platform%3Dandroid%26xsec_token%3DCBT3G-rSOp22PYDdOiwN6IhLzBinT6_FQaq2s_uraxJtg%3D"
        "%26share_id%3Dabc"
    )
    unwrapped = Fetcher._unwrap_xiaohongshu_login_redirect(login_url)
    assert unwrapped.startswith(
        "https://www.xiaohongshu.com/discovery/item/6ab0ed550000000036028a50?"
    )
    assert "xsec_token=CBT3G-rSOp22PYDdOiwN6IhLzBinT6_FQaq2s_uraxJtg=" in unwrapped
    assert Fetcher._is_xiaohongshu_content_url(unwrapped)


def test_unwrap_xiaohongshu_login_redirect_keeps_other_urls():
    note = "https://www.xiaohongshu.com/explore/abc?xsec_token=x="
    assert Fetcher._unwrap_xiaohongshu_login_redirect(note) == note
    assert Fetcher._unwrap_xiaohongshu_login_redirect("") == ""
    assert Fetcher._unwrap_xiaohongshu_login_redirect(None) is None
    # login page without redirectPath stays untouched
    assert (
        Fetcher._unwrap_xiaohongshu_login_redirect("https://www.xiaohongshu.com/login")
        == "https://www.xiaohongshu.com/login"
    )
    # redirect target outside content paths is not unwrapped
    login_home = (
        "https://www.xiaohongshu.com/login?redirectPath="
        "http%3A%2F%2Fwww.xiaohongshu.com%2F"
    )
    assert Fetcher._unwrap_xiaohongshu_login_redirect(login_home) == login_home


def test_resolve_common_short_url_unwraps_xhs_login_redirect(monkeypatch):
    login_url = (
        "https://www.xiaohongshu.com/login?redirectPath="
        "http%3A%2F%2Fwww.xiaohongshu.com%2Fdiscovery%2Fitem%2F6ab0ed550000000036028a50"
        "%3Fxsec_token%3Dabc%3D"
    )

    monkeypatch.setattr(Fetcher, "_get_proxies", staticmethod(lambda *args, **kwargs: ({}, None)))
    monkeypatch.setattr(
        Fetcher,
        "_resolve_url_with_redirects",
        staticmethod(lambda *args, **kwargs: login_url),
    )

    resolved = Fetcher._resolve_common_short_url("https://xhslink.cn/o/8pOFsq84eIY", None)
    assert resolved.startswith("https://www.xiaohongshu.com/discovery/item/6ab0ed550000000036028a50?")
    assert surf._get_handler_for_url(resolved)[1] == "xiaohongshu"


_SSR_HTML = """
<html><head><title>笔记标题 - 小红书</title>
<meta property="og:title" content="笔记标题 - 小红书">
<meta property="og:image" content="//picasso-static.xiaohongshu.com/fe-platform/logo.png">
<meta property="og:image" content="http://sns-webpic-qc.xhscdn.com/2026/a/img1.jpg">
<meta property="og:image" content="http://sns-webpic-qc.xhscdn.com/2026/b/img2.jpg">
</head><body><div class="note-content">
<h1 id="detail-title" class="title">笔记标题</h1>
<div id="detail-desc">这是正文内容，足够长的一段话。</div>
<div>编辑于 1天前 上海</div>
</div></body></html>
"""


def test_is_xiaohongshu_note_url():
    assert Fetcher._is_xiaohongshu_note_url(
        "https://www.xiaohongshu.com/explore/69765621000000000e00c91e?xsec_token=x="
    )
    assert Fetcher._is_xiaohongshu_note_url(
        "https://www.xiaohongshu.com/discovery/item/6ab0ed550000000036028a50"
    )
    assert not Fetcher._is_xiaohongshu_note_url(
        "https://www.xiaohongshu.com/user/profile/5f0c"
    )
    assert not Fetcher._is_xiaohongshu_note_url("https://www.xiaohongshu.com/")
    assert not Fetcher._is_xiaohongshu_note_url("https://example.com/explore/abc")


def test_extract_ssr_note_title_content_images():
    title, content, images = Fetcher._extract_xiaohongshu_ssr_note(_SSR_HTML)
    assert title == "笔记标题"
    assert "这是正文内容" in content
    assert "<h1" not in content  # duplicate in-content h1 removed
    assert images == [
        "http://sns-webpic-qc.xhscdn.com/2026/a/img1.jpg",
        "http://sns-webpic-qc.xhscdn.com/2026/b/img2.jpg",
    ]


def test_extract_ssr_note_returns_none_without_note_body():
    assert Fetcher._extract_xiaohongshu_ssr_note("<html><body>shell</body></html>") is None


class _FakeResponse:
    def __init__(self, url, status_code=200, text=""):
        self.url = url
        self.status_code = status_code
        self.text = text


def test_ssr_fast_path_extracts_note(monkeypatch):
    note_url = "https://www.xiaohongshu.com/explore/abc?xsec_token=x="
    monkeypatch.setattr(
        surf,
        "_requests_get_interruptibly",
        lambda *args, **kwargs: _FakeResponse(note_url, 200, _SSR_HTML),
    )

    html, fatal = Fetcher._fetch_xiaohongshu_ssr(note_url, {"a1": "v"})
    assert fatal is None
    assert html is not None
    assert 'name="surf-source-site"' in html and 'content="xiaohongshu"' in html
    assert '<h1>笔记标题</h1>' in html
    assert "xsec_token=x" in html  # source-url meta kept
    assert "img1.jpg" in html


def test_ssr_fast_path_fails_fast_on_login_redirect(monkeypatch):
    note_url = "https://www.xiaohongshu.com/explore/abc?xsec_token=x="
    monkeypatch.setattr(
        surf,
        "_requests_get_interruptibly",
        lambda *args, **kwargs: _FakeResponse(
            "https://www.xiaohongshu.com/login?redirectPath=x", 200, "<html></html>"
        ),
    )

    html, fatal = Fetcher._fetch_xiaohongshu_ssr(note_url, {})
    assert html is None
    assert fatal and "expired" in fatal


def test_ssr_fast_path_fails_fast_on_risk_control(monkeypatch):
    note_url = "https://www.xiaohongshu.com/explore/abc?xsec_token=x="
    monkeypatch.setattr(
        surf,
        "_requests_get_interruptibly",
        lambda *args, **kwargs: _FakeResponse(note_url, 412, "denied"),
    )

    html, fatal = Fetcher._fetch_xiaohongshu_ssr(note_url, {})
    assert html is None
    assert fatal and "risk control" in fatal


def test_ssr_fast_path_falls_back_when_page_has_no_note(monkeypatch):
    note_url = "https://www.xiaohongshu.com/explore/abc?xsec_token=x="
    monkeypatch.setattr(
        surf,
        "_requests_get_interruptibly",
        lambda *args, **kwargs: _FakeResponse(note_url, 200, "<html><body></body></html>"),
    )

    html, fatal = Fetcher._fetch_xiaohongshu_ssr(note_url, {})
    assert html is None
    assert fatal is None


def test_web_extract_url_from_text_uses_shared_helper():
    import surf_web

    share_text = "打开App查看 https://xhslink.com/o/abc，复制本条信息"
    assert surf_web.extract_url_from_text(share_text) == "https://xhslink.com/o/abc"
