import argparse
from types import SimpleNamespace

import pytest
import requests

import surf


class _FakeConfig:
    def __init__(self, custom_proxy=""):
        self.custom_proxy = custom_proxy

    def get(self, section, key, fallback=None):
        if section == "Network" and key == "custom_proxy":
            return self.custom_proxy
        return fallback


def _make_args(**overrides):
    defaults = {
        "proxy": None,
        "set_proxy": None,
        "c": None,
        "n": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_proxy_custom_uses_config_custom_proxy_when_cli_value_missing():
    parser = argparse.ArgumentParser(prog="surf")
    args = _make_args(proxy="custom")
    config = _FakeConfig(custom_proxy="http://127.0.0.1:7890")

    proxy_mode, custom_proxy = surf._resolve_proxy_args(args, parser, config)

    assert proxy_mode == "custom"
    assert custom_proxy is None
    req_proxies, pw_proxy = surf.Fetcher._get_proxies(config, proxy_mode, custom_proxy)
    assert req_proxies == {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
    }
    assert pw_proxy == {"server": "http://127.0.0.1:7890"}


def test_short_c_without_value_uses_config_custom_proxy():
    parser = argparse.ArgumentParser(prog="surf")
    args = _make_args(c="")
    config = _FakeConfig(custom_proxy="http://127.0.0.1:7890")

    proxy_mode, custom_proxy = surf._resolve_proxy_args(args, parser, config)

    assert proxy_mode == "custom"
    assert custom_proxy == ""
    req_proxies, pw_proxy = surf.Fetcher._get_proxies(config, proxy_mode, custom_proxy)
    assert req_proxies == {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
    }
    assert pw_proxy == {"server": "http://127.0.0.1:7890"}


def test_proxy_custom_without_cli_or_config_value_still_errors():
    parser = argparse.ArgumentParser(prog="surf")
    args = _make_args(proxy="custom")

    with pytest.raises(SystemExit) as exc_info:
        surf._resolve_proxy_args(args, parser, _FakeConfig())

    assert exc_info.value.code == 2


def test_short_c_without_value_still_errors_when_config_missing():
    parser = argparse.ArgumentParser(prog="surf")
    args = _make_args(c="")

    with pytest.raises(SystemExit) as exc_info:
        surf._resolve_proxy_args(args, parser, _FakeConfig())

    assert exc_info.value.code == 2


def test_generic_fetch_retries_direct_connection_after_implicit_proxy_ssl_eof(monkeypatch):
    config = _FakeConfig()
    url = "https://zserge.com/posts/visicalc/"
    proxy_calls = []

    class _FakeResponse:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        text = "<html>" + ("a" * 1200) + "</html>"
        content = text.encode("utf-8")
        apparent_encoding = "utf-8"
        encoding = None

        def raise_for_status(self):
            return None

    def _fake_get(*args, **kwargs):
        proxy_calls.append(kwargs.get("proxies"))
        if kwargs.get("proxies"):
            raise requests.exceptions.SSLError(
                "HTTPSConnectionPool(host='zserge.com', port=443): "
                "Caused by SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING] "
                "EOF occurred in violation of protocol (_ssl.c:1081)')"
            )
        return _FakeResponse()

    monkeypatch.setattr(
        surf.Fetcher,
        "_get_proxies",
        staticmethod(lambda config, proxy_mode_override=None, custom_proxy_override=None: (
            {"http": "http://localhost:7890", "https": "http://localhost:7890"},
            {"server": "http://localhost:7890"},
        )),
    )
    monkeypatch.setattr(surf, "_requests_get_with_system_trust_interruptibly", _fake_get)
    monkeypatch.setattr(
        surf.Fetcher,
        "fetch_with_browser",
        staticmethod(lambda *args, **kwargs: pytest.fail("browser fallback should not run")),
    )

    html = surf.Fetcher.fetch(url, config)

    assert "aaaa" in html
    assert proxy_calls == [
        {"http": "http://localhost:7890", "https": "http://localhost:7890"},
        None,
    ]


def test_generic_fetch_uses_system_trust_requests_session(monkeypatch):
    config = _FakeConfig()
    url = "https://www.antipope.org/charlie/blog-static/fiction/accelerando/accelerando.html"
    calls = []

    class _FakeResponse:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        text = "<html>" + ("a" * 1200) + "</html>"
        content = text.encode("utf-8")
        apparent_encoding = "utf-8"
        encoding = None

        def raise_for_status(self):
            return None

    def _fake_system_get(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakeResponse()

    monkeypatch.setattr(surf, "_requests_get_with_system_trust_interruptibly", _fake_system_get)
    monkeypatch.setattr(
        surf.Fetcher,
        "fetch_with_browser",
        staticmethod(lambda *args, **kwargs: pytest.fail("browser fallback should not run")),
    )

    html = surf.Fetcher.fetch(url, config, use_browser=False, proxy_mode_override="no")

    assert "aaaa" in html
    assert len(calls) == 1
    assert calls[0][0][0] == url
