import surf


def test_cookie_header_for_douban_uses_only_douban_domains(monkeypatch):
    monkeypatch.setattr(
        "surf.AuthHandler.load_state",
        lambda site_name, log_load=False: {
            "cookies": [
                {"name": "dbcl2", "value": "abc", "domain": ".douban.com"},
                {"name": "ck", "value": "def", "domain": "www.douban.com"},
                {"name": "other", "value": "zzz", "domain": ".example.com"},
            ]
        },
    )

    header = surf.AuthHandler.cookie_header_for_douban()

    assert header == "dbcl2=abc; ck=def"


def test_generic_fetch_uses_saved_douban_cookie_header(monkeypatch):
    captured = {}

    class _FakeResponse:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        text = "<html>" + ("a" * 1200) + "</html>"
        content = text.encode("utf-8")
        apparent_encoding = "utf-8"
        encoding = None

        def raise_for_status(self):
            return None

    def _fake_get(url, headers=None, proxies=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["proxies"] = proxies
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(surf, "_requests_get_with_system_trust_interruptibly", _fake_get)
    monkeypatch.setattr("surf.AuthHandler.cookie_header_for_douban", lambda: "dbcl2=abc; ck=def")
    monkeypatch.setattr(
        surf.Fetcher,
        "_get_proxies",
        staticmethod(lambda config, proxy_mode_override=None, custom_proxy_override=None: (None, None)),
    )
    monkeypatch.setattr(
        surf.Fetcher,
        "fetch_with_browser",
        staticmethod(lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("browser fallback should not run"))),
    )

    html = surf.Fetcher.fetch("https://www.douban.com/note/123456789/", config={})

    assert "aaaa" in html
    assert captured["url"] == "https://www.douban.com/note/123456789/"
    assert captured["headers"]["Cookie"] == "dbcl2=abc; ck=def"
