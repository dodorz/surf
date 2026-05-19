from surf import AuthHandler, Fetcher, _extract_direct_markdown_payload


def test_build_reddit_json_url_normalizes_short_url():
    assert (
        Fetcher._build_reddit_json_url("https://redd.it/abc123")
        == "https://www.reddit.com/comments/abc123.json?raw_json=1"
    )


def test_build_reddit_json_url_normalizes_old_reddit_permalink():
    assert (
        Fetcher._build_reddit_json_url("https://old.reddit.com/r/test/comments/abc123/example/")
        == "https://www.reddit.com/r/test/comments/abc123/example.json?raw_json=1"
    )


def test_cookie_header_for_reddit_uses_only_reddit_domains(monkeypatch):
    monkeypatch.setattr(
        "surf.AuthHandler.load_state",
        lambda site_name, log_load=False: {
            "cookies": [
                {"name": "reddit_session", "value": "abc", "domain": ".reddit.com"},
                {"name": "loid", "value": "def", "domain": "www.reddit.com"},
                {"name": "other", "value": "zzz", "domain": ".example.com"},
            ]
        },
    )

    header = AuthHandler.cookie_header_for_reddit()

    assert header == "reddit_session=abc; loid=def"


def test_fetch_reddit_content_builds_direct_markdown(monkeypatch):
    payload = [
        {
            "data": {
                "children": [
                    {
                        "kind": "t3",
                        "data": {
                            "title": "Example Reddit Post",
                            "author": "alice",
                            "subreddit_name_prefixed": "r/test",
                            "created_utc": 1710000000,
                            "score": 42,
                            "num_comments": 2,
                            "is_self": True,
                            "selftext": "Plain body",
                            "selftext_html": "<div class=\"md\"><p>Plain body</p></div>",
                            "permalink": "/r/test/comments/abc123/example_reddit_post/",
                        },
                    }
                ]
            }
        },
        {
            "data": {
                "children": [
                    {
                        "kind": "t1",
                        "data": {
                            "id": "c1",
                            "author": "alice",
                            "score": 5,
                            "created_utc": 1710000100,
                            "body": "OP comment",
                            "body_html": "<div class=\"md\"><p>OP comment</p></div>",
                            "permalink": "/r/test/comments/abc123/example_reddit_post/c1/",
                            "replies": "",
                        },
                    },
                    {
                        "kind": "t1",
                        "data": {
                            "id": "c2",
                            "author": "bob",
                            "score": 3,
                            "created_utc": 1710000200,
                            "body": "Other comment",
                            "body_html": "<div class=\"md\"><p>Other comment</p></div>",
                            "permalink": "/r/test/comments/abc123/example_reddit_post/c2/",
                            "replies": "",
                        },
                    },
                ]
            }
        },
    ]

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    captured = {}

    def fake_get(url, headers=None, proxies=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["proxies"] = proxies
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr("surf._requests_get_with_system_trust_interruptibly", fake_get)
    monkeypatch.setattr("surf.AuthHandler.cookie_header_for_reddit", lambda: "reddit_session=abc")
    monkeypatch.setattr("surf.Fetcher._get_proxies", lambda config, proxy_mode_override=None, custom_proxy_override=None: (None, None))

    result = Fetcher._fetch_reddit_content(
        "https://www.reddit.com/r/test/comments/abc123/example_reddit_post/",
        config={},
        fetch_thread="after",
        fetch_thread_author="same",
    )

    assert captured["url"] == "https://www.reddit.com/r/test/comments/abc123/example_reddit_post.json?raw_json=1"
    assert captured["headers"]["Cookie"] == "reddit_session=abc"
    assert result is not None
    assert "<title>Example Reddit Post</title>" in result
    assert 'meta name="source-url" content="https://www.reddit.com/r/test/comments/abc123/example_reddit_post/"' in result
    markdown_text = _extract_direct_markdown_payload(result)["markdown"]
    assert "Subreddit: r/test" in markdown_text
    assert "Author: alice" in markdown_text
    assert "Plain body" in markdown_text
    assert "## Comments" in markdown_text
    assert "OP comment" in markdown_text
    assert "Other comment" not in markdown_text
