"""Tests for the unified browser backend selection and archive fail-fast.

These cover the 2026-09 changes:
  * the configured ``[Browser] backend`` is used by every headless handler
    (Obscura is no longer rejected for Twitter/X or Zhihu);
  * the archive snapshot fallback skips the visible-browser CAPTCHA attempt on
    hosts without a graphical session.
"""
import contextlib
import sys
import types

import pytest

import surf
from surf import Fetcher


class _FakeConfig:
    def __init__(self, backend="obscura"):
        self._backend = backend

    def has_section(self, name):
        return name == "Browser"

    def get(self, section, key, fallback=None):
        if section == "Browser" and key == "backend":
            return self._backend
        return fallback


class _FakeObscura:
    endpoint = "http://127.0.0.1:9222"

    def __init__(self):
        self.fetched = []

    def fetch(self, url, proxy=None):
        self.fetched.append((url, proxy))
        return "OBSCURA-OK"

    @contextlib.contextmanager
    def session(self, proxy=None):
        self.session_proxy = proxy
        yield "BROWSER-SENTINEL"


@pytest.fixture(autouse=True)
def _no_real_proxy(monkeypatch):
    monkeypatch.setattr(
        Fetcher, "_get_proxies", staticmethod(lambda *a, **k: ({}, None))
    )


def test_obscura_used_for_twitter_and_zhihu(monkeypatch):
    fake = _FakeObscura()
    monkeypatch.setattr(Fetcher, "_build_obscura_backend", staticmethod(lambda config: fake))

    assert Fetcher._fetch_with_obscura_sync("https://x.com/u/status/1", _FakeConfig()) == "OBSCURA-OK"
    assert (
        Fetcher._fetch_with_obscura_sync(
            "https://www.zhihu.com/question/1/answer/2", _FakeConfig()
        )
        == "OBSCURA-OK"
    )
    assert len(fake.fetched) == 2


def test_browser_session_routes_to_obscura(monkeypatch):
    fake = _FakeObscura()
    monkeypatch.setattr(Fetcher, "_build_obscura_backend", staticmethod(lambda config: fake))

    with Fetcher._browser_session(_FakeConfig(), "https://example.com") as browser:
        assert browser == "BROWSER-SENTINEL"
    assert fake.session_proxy is None


def test_browser_session_headed_uses_playwright(monkeypatch):
    # A headed session must never use the headless-only Obscura backend.
    monkeypatch.setattr(
        Fetcher,
        "_build_obscura_backend",
        staticmethod(lambda config: (_ for _ in ()).throw(AssertionError("obscura used for headed"))),
    )
    fake_sync_api = types.ModuleType("playwright.sync_api")
    launched = {}

    class _Browser:
        def close(self):
            launched["closed"] = True

    class _Chromium:
        def launch(self, **kwargs):
            launched["headless"] = kwargs.get("headless")
            return _Browser()

    class _PW:
        chromium = _Chromium()

    class _CM:
        def __enter__(self):
            return _PW()

        def __exit__(self, *a):
            return False

    fake_sync_api.sync_playwright = lambda: _CM()
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)

    with Fetcher._browser_session(_FakeConfig(), "https://example.com", headless=False):
        pass
    assert launched["headless"] is False
    assert launched["closed"] is True


def test_archive_skips_visible_fallback_without_display(monkeypatch):
    monkeypatch.setattr(surf.AuthHandler, "can_launch_headed_browser", staticmethod(lambda: False))
    calls = []

    @contextlib.contextmanager
    def _fake_session(config, url=None, *args, **kwargs):
        calls.append(kwargs.get("headless"))
        raise RuntimeError("forced failure")
        yield  # pragma: no cover

    monkeypatch.setattr(Fetcher, "_browser_session", staticmethod(_fake_session))

    html, snapshot = Fetcher._fetch_archiveis_snapshot(
        "https://example.com/paywalled", _FakeConfig(), archive_domains=("archive.is", "archive.ph")
    )
    assert (html, snapshot) == (None, None)
    # One headless attempt per domain, and no visible (headless=False) attempt.
    assert calls == [True, True]


def test_archive_tries_visible_when_display_available(monkeypatch):
    monkeypatch.setattr(surf.AuthHandler, "can_launch_headed_browser", staticmethod(lambda: True))
    calls = []

    @contextlib.contextmanager
    def _fake_session(config, url=None, *args, **kwargs):
        calls.append(kwargs.get("headless"))
        raise RuntimeError("forced failure")
        yield  # pragma: no cover

    monkeypatch.setattr(Fetcher, "_browser_session", staticmethod(_fake_session))

    html, snapshot = Fetcher._fetch_archiveis_snapshot(
        "https://example.com/paywalled", _FakeConfig(), archive_domains=("archive.is",)
    )
    assert (html, snapshot) == (None, None)
    # headless attempt, then a visible retry because a display is available.
    assert calls == [True, False]
