# Regression tests: Zhihu (zhuanlan.zhihu.com) non-content responses.
#
# Zhihu answers plain HTTP content requests with a `zse-ck` challenge, a 404
# error page (`www.zhihu.com/p/...` does not know new article ids), a sign-in
# wall or a redirect to the homepage. Surf used to accept the first response
# longer than 500 bytes, so the 404 page stopped the fallback chain and the
# browser step never ran.
import surf

CHALLENGE_HTML = (
    "<!DOCTYPE html><html lang=\"en\"><head>"
    "<meta id=\"zh-zse-ck\" charset=\"UTF-8\" content=\"2uNO65pZcc2fU4gOXaw+U2XX\">"
    "</head><body><div>知乎，让每一次点击都充满意义</div>"
    "<script data-assets-tracker-config='{\"appName\":\"zse_ck\"}' "
    "src=\"https://static.zhihu.com/zse-ck/v4/deadbeef.js\"></script>"
    "</body></html>"
) + " " * 600

ERROR_PAGE_HTML = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="UTF-8" /><title>404 - 知乎</title></head>
<body><div class="ErrorPage-container"><div class="ErrorPage-text">
<div class="ErrorPage-title">页面无法访问</div>
<div class="ErrorPage-subtitle">您访问的页面不存在</div>
</div></div></body></html>
""" + " " * 600

HOMEPAGE_HTML = """<!DOCTYPE html>
<html lang="zh"><head><title>知乎 - 有问题，就会有答案</title></head>
<body><div id="root"><main class="App-main"><div class="Topstory">
<div class="ContentItem"><div class="RichText ztext">feed filler</div></div>
</div></main></div></body></html>
""" + " " * 600

SIGNIN_HTML = """<!DOCTYPE html>
<html lang="zh"><head><title>知乎 - 有问题，就会有答案</title></head>
<body><main class="App-main"><div class="SignFlowHomepage">
<form class="SignFlow Login-content"><input name="username" placeholder="手机号" /></form>
</div></main></body></html>
""" + " " * 600

ZHUANLAN_ARTICLE_HTML = """<!DOCTYPE html>
<html lang="zh"><head><title>为什么一个 await 能把半个项目染红 - 知乎</title></head>
<body><div class="Post-RichTextContainer">
<h1 class="Post-Title">为什么一个 await 能把半个项目染红</h1>
<div class="RichText Post-RichText"><p>写过 Python asyncio 的人，应该都见过一个很烦的场景。</p>
<p>你只是想把一个老函数改成异步，结果调用链上的每一层都要跟着改，最后整份 diff 都被标红了。</p>
<p>本文章节内容用于通过正文长度校验，确保 DOM 提取逻辑能够选中正文容器。</p></div></div></body></html>
""" + " " * 600

ANSWER_HTML = """<!DOCTYPE html>
<html lang="zh"><head><title>如何优雅地处理异步调用 - 知乎</title></head>
<body><div class="AnswerItem"><h1 class="QuestionHeader-title">如何优雅地处理异步调用</h1>
<div class="RichContent"><div class="RichContent-inner"><p>回答正文内容，需要超过八十字才算有效内容，所以这里补充更多的文字来确保通过长度校验。</p>
</div></div></div></body></html>
""" + " " * 600


def test_challenge_page_is_rejected():
    assert surf.Fetcher._zhihu_page_issue(CHALLENGE_HTML, expect_content=True)
    assert "challenge" in surf.Fetcher._zhihu_page_issue(CHALLENGE_HTML, expect_content=True)


def test_error_page_is_rejected_even_for_generic_lookups():
    assert surf.Fetcher._zhihu_page_issue(ERROR_PAGE_HTML, expect_content=True) == "error page"
    assert surf.Fetcher._zhihu_page_issue(ERROR_PAGE_HTML, expect_content=False) == "error page"


def test_homepage_and_signin_pages_are_rejected_for_articles():
    assert surf.Fetcher._zhihu_page_issue(HOMEPAGE_HTML, expect_content=True)
    assert surf.Fetcher._zhihu_page_issue(SIGNIN_HTML, expect_content=True)
    # Without an article/answer id the homepage is a legitimate page.
    assert surf.Fetcher._zhihu_page_issue(HOMEPAGE_HTML, expect_content=False) is None


def test_real_article_and_answer_pages_pass_validation():
    assert surf.Fetcher._zhihu_page_issue(ZHUANLAN_ARTICLE_HTML, expect_content=True) is None
    assert surf.Fetcher._zhihu_page_issue(ANSWER_HTML, expect_content=True) is None


def test_short_responses_are_rejected():
    assert surf.Fetcher._zhihu_page_issue("", expect_content=True)
    assert surf.Fetcher._zhihu_page_issue("<html></html>", expect_content=True)
    assert surf.Fetcher._zhihu_page_issue(None) is not None


class _FakeResponse:
    def __init__(self, url, status_code, text):
        self.url = url
        self.status_code = status_code
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise surf.requests.exceptions.HTTPError(
                f"{self.status_code} error", response=self
            )


class _FakeSession:
    """Minimal stand-in for requests.Session keyed by candidate URL."""

    def __init__(self, responses, default_status=200):
        self.responses = responses
        self.default_status = default_status
        self.proxies = {}
        self.cookies = {}
        self.requested = []

    def get(self, url, **kwargs):
        self.requested.append(url)
        status, text = self.responses.get(url, (self.default_status, ""))
        return _FakeResponse(url, status, text)

    def close(self):
        pass


def _patch_session(monkeypatch, responses):
    sessions = []

    class _Factory:
        def __call__(self):
            session = _FakeSession(responses)
            sessions.append(session)
            return session

    monkeypatch.setattr(surf.requests, "Session", _Factory())
    return sessions


def test_direct_fetch_rejects_error_page_and_continues(monkeypatch):
    article_id = "2033734830404400470"
    url = f"https://zhuanlan.zhihu.com/p/{article_id}"
    responses = {
        url: (403, CHALLENGE_HTML),
        f"https://www.zhihu.com/p/{article_id}": (200, ERROR_PAGE_HTML),
        f"https://m.zhihu.com/article/{article_id}": (200, HOMEPAGE_HTML),
    }
    sessions = _patch_session(monkeypatch, responses)

    result = surf.Fetcher._fetch_zhihu_direct_content(url, cookie_header="z_c0=token")

    assert result is None
    requested = sessions[0].requested
    assert f"https://www.zhihu.com/p/{article_id}" in requested
    assert f"https://m.zhihu.com/article/{article_id}" in requested


def test_direct_fetch_returns_content_from_first_usable_candidate(monkeypatch):
    article_id = "2033734830404400470"
    url = f"https://zhuanlan.zhihu.com/p/{article_id}"
    responses = {
        url: (403, CHALLENGE_HTML),
        f"https://www.zhihu.com/p/{article_id}": (200, ZHUANLAN_ARTICLE_HTML),
    }
    _patch_session(monkeypatch, responses)

    result = surf.Fetcher._fetch_zhihu_direct_content(url, cookie_header="z_c0=token")

    assert result is not None
    assert "为什么一个 await 能把半个项目染红" in result
    assert "surf-source-site" in result


def test_accept_encoding_skips_brotli_when_no_decoder():
    import importlib.util

    encoding = surf.Fetcher._zhihu_accept_encoding()
    assert encoding.startswith("gzip, deflate")
    has_brotli = any(
        importlib.util.find_spec(name) is not None for name in ("brotli", "brotlicffi")
    )
    assert ("br" in encoding.split(", ")) == has_brotli


def test_build_zhihu_html_strips_zero_width_author_padding():
    html = surf.Fetcher._build_zhihu_html(
        title="Title",
        content_html="<p>Body</p>",
        source_url="https://zhuanlan.zhihu.com/p/1",
        author_name="小盒子\u200b\u200b",
    )

    assert "content='小盒子'" in html
