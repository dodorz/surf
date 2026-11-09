import re

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


def test_rednote_urls_are_recognized_as_xiaohongshu():
    note = "https://www.rednote.com/discovery/item/6ae7acbb000000001202d751?xsec_token=x="
    assert Fetcher._is_xiaohongshu_note_url(note)
    assert Fetcher._is_xiaohongshu_content_url(note)
    assert Fetcher._xiaohongshu_host(note) == "rednote.com"
    _, site_name, _ = surf._get_handler_for_url(note)
    assert site_name == "xiaohongshu"
    assert surf._get_handler_for_url("https://www.rednote.com/user/profile/5f0c")[1] == "xiaohongshu"


def test_xiaohongshu_login_url_detection_includes_rednote():
    assert Fetcher._is_xiaohongshu_login_url("https://www.rednote.com/login?redirectPath=x")
    assert Fetcher._is_xiaohongshu_login_url("https://www.xiaohongshu.com/login?redirectPath=x")
    assert not Fetcher._is_xiaohongshu_login_url(
        "https://www.xiaohongshu.com/explore/abc?xsec_token=x="
    )
    assert not Fetcher._is_xiaohongshu_login_url("https://example.com/login")


def test_note_fetch_urls_prefer_xiaohongshu_over_rednote():
    rednote = "https://www.rednote.com/discovery/item/6ae7acbb000000001202d751?xsec_token=x="
    urls = Fetcher._xiaohongshu_note_fetch_urls(rednote)
    assert urls[0].startswith("https://www.xiaohongshu.com/discovery/item/6ae7acbb000000001202d751")
    assert urls[-1] == rednote

    xhs = "https://www.xiaohongshu.com/explore/abc?xsec_token=x="
    assert Fetcher._xiaohongshu_note_fetch_urls(xhs) == [xhs]


def test_canonicalize_xiaohongshu_source_url_maps_rednote():
    canonical = Fetcher._canonicalize_xiaohongshu_source_url(
        "https://www.rednote.com/discovery/item/6abc?xsec_token=CB_x%3D&share_id=1&type=normal"
    )
    assert canonical.startswith("https://www.xiaohongshu.com/discovery/item/6abc?")
    assert "xsec_token=CB_x%3D" in canonical
    assert "share_id" not in canonical


def test_unwrap_xiaohongshu_login_redirect_handles_rednote():
    login_url = (
        "https://www.rednote.com/login?redirectPath="
        "http%3A%2F%2Fwww.rednote.com%2Fdiscovery%2Fitem%2F6ae7acbb000000001202d751"
        "%3Fxsec_token%3Dabc%3D"
    )
    unwrapped = Fetcher._unwrap_xiaohongshu_login_redirect(login_url)
    assert unwrapped.startswith(
        "https://www.rednote.com/discovery/item/6ae7acbb000000001202d751?"
    )
    assert Fetcher._is_xiaohongshu_note_url(unwrapped)


def test_ssr_note_title_strips_rednote_suffix():
    html = _SSR_HTML.replace(
        "<title>笔记标题 - 小红书</title>", "<title>笔记标题 - rednote</title>"
    ).replace(
        '<meta property="og:title" content="笔记标题 - 小红书">',
        '<meta property="og:title" content="笔记标题 - rednote">',
    ).replace('<h1 id="detail-title" class="title">笔记标题</h1>', "")
    title, _content, _images = Fetcher._extract_xiaohongshu_ssr_note(html)
    assert title == "笔记标题"


def test_resolve_xhslink_passes_cookies_and_recovers_note_url(monkeypatch):
    note_url = "https://www.xiaohongshu.com/discovery/item/6ae7acbb000000001202d751?xsec_token=x="
    seen = {}

    class _Hop:
        def __init__(self, url, location):
            self.url = url
            self.headers = {"location": location}

    class _Response:
        def __init__(self):
            self.url = "https://www.rednote.com/login?redirectPath=http%3A%2F%2Fwww.rednote.com%2F"
            self.status_code = 200
            self.text = ""
            self.history = [_Hop("https://xhslink.cn/o/abc", note_url)]

    def _fake_get(session, url, **kwargs):
        seen["cookies"] = dict(session.cookies)
        return _Response()

    monkeypatch.setattr(surf, "_session_get_interruptibly", _fake_get)

    resolved, cleaned = Fetcher._resolve_xhslink_short_url(
        "https://xhslink.cn/o/abc", cookies={"a1": "v"}
    )
    assert seen["cookies"].get("a1") == "v"
    assert resolved.startswith("https://www.xiaohongshu.com/discovery/item/6ae7acbb000000001202d751")
    assert cleaned.endswith("xsec_token=x%3D")


_XHS_NOTE_WITH_TAGS = """
<html><head><meta charset="utf-8"><title>标题 - 小红书</title>
<meta name="surf-source-site" content="xiaohongshu"></head>
<body><article>
<h1>标题</h1>
<div class="desc" id="detail-desc"><span class="note-text">
<a class="tag" href="/search_result?keyword=homelab" id="hash-tag">#homelab</a>
<span>正文内容。</span>
<a class="tag" href="/search_result?keyword=%E5%AE%B6%E5%BA%AD" id="hash-tag">#家庭服务器</a>
</span></div>
<div class="bottom-container"><div class="bottom-left">
<span class="date">7天前 美国</span></div></div>
</article></body></html>
"""


def test_xiaohongshu_hashtags_become_meta_and_leave_the_body():
    from bs4 import BeautifulSoup

    cleaned = Fetcher._clean_xiaohongshu_content(_XHS_NOTE_WITH_TAGS)
    soup = BeautifulSoup(cleaned, "html.parser")
    tags = [m.get("content") for m in soup.find_all("meta", attrs={"name": "surf-tag"})]
    assert tags == ["homelab", "家庭服务器"]
    body = soup.find("article").get_text(" ", strip=True)
    assert "#homelab" not in body and "#家庭服务器" not in body
    assert "正文内容。" in body
    assert "search_result" not in cleaned


def test_xiaohongshu_relative_date_becomes_created_and_leaves_the_body():
    from bs4 import BeautifulSoup

    cleaned = Fetcher._clean_xiaohongshu_content(_XHS_NOTE_WITH_TAGS)
    soup = BeautifulSoup(cleaned, "html.parser")
    created = soup.find("meta", attrs={"name": "surf-created"})
    assert created is not None
    # Day precision only, so no misleading midnight timestamp.
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", created.get("content"))
    body = soup.find("article").get_text(" ", strip=True)
    assert "7天前" not in body and "美国" not in body


def test_xiaohongshu_metadata_uses_surf_tag_and_surf_created():
    from bs4 import BeautifulSoup

    cleaned = Fetcher._clean_xiaohongshu_content(_XHS_NOTE_WITH_TAGS)
    metadata = surf.OutputHandler._extract_metadata(
        cleaned, source_url="https://www.xiaohongshu.com/explore/abc?xsec_token=x="
    )
    assert metadata["tags"] == ["homelab", "家庭服务器"]
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", metadata["created"])


def test_xiaohongshu_relative_date_parser_units():
    from datetime import datetime

    now = datetime(2026, 11, 9, 12, 0, 0)
    parse = Fetcher._parse_xiaohongshu_relative_date
    assert parse("前天", now=now) == datetime(2026, 11, 7, 0, 0, 0)
    assert parse("2周前", now=now) == datetime(2026, 10, 26, 0, 0, 0)
    assert parse("1个月前", now=now) == datetime(2026, 10, 10, 0, 0, 0)
    assert parse("5小时前", now=now) == datetime(2026, 11, 9, 7, 0, 0)
    # Day-granularity stamps resolve to midnight of the target day.
    assert parse("昨天", now=now) == datetime(2026, 11, 8, 0, 0, 0)
    assert parse("3天前", now=now) == datetime(2026, 11, 6, 0, 0, 0)
    # Edit stamps are not publication dates.
    assert parse("编辑于 3天前", now=now) is None
    assert parse("", now=now) is None


def test_xiaohongshu_published_at_ignores_prose_that_mentions_days():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(
        "<article><p>这次旅行是3天前决定的。</p><span class='date'>2周前 北京</span></article>",
        "html.parser",
    )
    published = Fetcher._extract_xiaohongshu_published_at(soup)
    assert published is not None


def test_ssr_note_ignores_a_stripped_shell_with_only_the_title():
    # The rednote login wall serves a page whose note container holds only the
    # title div; that is not a note body and must not become a document.
    html = """
    <html><head><title>标题 - rednote</title></head><body>
    <div class="note-content"><div id="detail-title">标题</div></div>
    </body></html>
    """
    assert Fetcher._extract_xiaohongshu_ssr_note(html) is None


def test_ssr_note_drops_duplicate_title_div():
    html = """
    <html><head><title>标题 - 小红书</title></head><body>
    <div class="note-content">
      <div id="detail-title">标题</div>
      <div id="detail-desc"><span class="note-text">正文内容。</span></div>
    </div></body></html>
    """
    title, content, _images = Fetcher._extract_xiaohongshu_ssr_note(html)
    assert title == "标题"
    assert "正文内容。" in content
    assert "标题" not in content


def test_strip_leading_markdown_heading_handles_title_only_body():
    assert surf._strip_leading_markdown_heading("# 标题\n\n正文", "标题") == "正文"
    assert surf._strip_leading_markdown_heading("# 标题", "标题") == ""
    assert surf._strip_leading_markdown_heading("# 别的\n\n正文", "标题") == "# 别的\n\n正文"


_STATE_PAGE = """
<html><head><title>标题 - rednote</title>
<meta name="surf-source-site" content="xiaohongshu"></head>
<body><div class="note-content">
<div class="title" id="detail-title">标题</div>
<div class="desc" id="detail-desc"><span class="note-text">
<a class="tag" href="/search_result?keyword=homelab" id="hash-tag">#homelab</a>
<span> </span>
<a class="tag" href="/search_result?keyword=x" id="hash-tag">#家庭服务器</a>
</span></div></div>
<script>window.__INITIAL_STATE__ = {"note":{"noteDetailMap":{
  "6ae7acbb000000001202d751":{"note":{
    "noteId":"6ae7acbb000000001202d751",
    "title":"标题",
    "desc":"#homelab[话题]# #家庭服务器[话题]#\\n\\n第一段正文。\\n\\n第二段正文。",
    "tagList":[{"name":"homelab"},{"name":"家庭服务器"}],
    "time":1793567931000,
    "imageList":[
      {"urlDefault":"http://sns-webpic-qc.xhscdn.com/a/one.jpg","width":1440,"height":2400},
      {"urlDefault":"http://sns-web-i10.rednotecdn.com/b/two.jpg","width":1440,"height":2400}
    ]
  }}}}};</script>
</body></html>
"""


def test_state_payload_is_used_when_the_dom_only_has_hashtags():
    note = Fetcher._extract_xiaohongshu_ssr_note(_STATE_PAGE)
    assert note is not None
    title, content, images = note
    assert title == "标题"
    assert "第一段正文。" in content and "第二段正文。" in content
    assert "#homelab" not in content
    assert len(images) == 2
    assert images[0].endswith("one.jpg")
    # rednotecdn is a valid note CDN, not a UI asset.
    assert images[1].endswith("two.jpg")


def test_state_payload_parsing_helpers():
    html = "<<<" + '"noteDetailMap":' + '{"id":{"note":{"title":"t","desc":"d"}}}'
    state_note = Fetcher._extract_xiaohongshu_state_note(html)
    assert state_note == {"title": "t", "desc": "d"}
    assert Fetcher._extract_xiaohongshu_state_note("<html></html>") is None
    assert Fetcher._extract_xiaohongshu_state_note('"noteDetailMap":{broken') is None


def test_hashtag_text_is_stripped_from_descriptions():
    assert Fetcher._strip_xiaohongshu_hashtag_text("#homelab[话题]# #家庭服务器[话题]#") == ""
    assert (
        Fetcher._strip_xiaohongshu_hashtag_text("正文。#tag[话题]# 结尾。") == "正文。 结尾。"
    )
    html = Fetcher._xiaohongshu_desc_to_html("#a[话题]#\n\n第一段。\n\n第二段。#b[话题]# 尾。")
    assert html == "<p>第一段。</p><p>第二段。 尾。</p>"
    assert Fetcher._xiaohongshu_desc_to_html("#only[话题]#") == ""


def test_note_without_body_or_images_is_rejected():
    html = """
    <html><head><title>标题 - rednote</title></head><body>
    <div class="note-content"><div id="detail-title">标题</div></div>
    </body></html>
    """
    assert Fetcher._extract_xiaohongshu_ssr_note(html) is None


def test_state_payload_created_time_is_used_for_front_matter():
    note = Fetcher._extract_xiaohongshu_state_note(_STATE_PAGE)
    created = Fetcher._xiaohongshu_state_created(note)
    assert created is not None
    assert created.startswith("2026-11-")
    assert Fetcher._xiaohongshu_state_created({}) is None
    assert Fetcher._xiaohongshu_state_created({"time": "not-a-number"}) is None


def test_english_relative_stamps_are_understood():
    from datetime import datetime

    now = datetime(2026, 11, 9, 12, 0, 0)
    parse = Fetcher._parse_xiaohongshu_relative_date
    assert parse("8d ago", now=now) == datetime(2026, 11, 1, 0, 0, 0)
    assert parse("2w ago", now=now) == datetime(2026, 10, 26, 0, 0, 0)
    assert parse("3h ago", now=now) == datetime(2026, 11, 9, 9, 0, 0)
    assert parse("yesterday", now=now) == datetime(2026, 11, 8, 0, 0, 0)
