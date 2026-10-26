"""Native (Playwright-free) CDP transport for the Obscura backend.

The default headless fetch now speaks the DevTools protocol directly, so these
tests exercise the wire handling and the ``fetch_mode`` dispatch without a real
Obscura server or a real websocket connection.
"""
import contextlib
import json
import sys
import time
import types

import pytest

import obscura_backend
from obscura_backend import CDPError, CDPSession, ObscuraBackend


class _FakeWebSocket:
    """In-memory stand-in for ``websockets.sync.client.ClientConnection``.

    ``handler(method, params)`` produces the command result.  Returning
    ``{"__error__": {...}}`` makes the command fail with a protocol error.
    """

    def __init__(self, handler, events=None):
        self.handler = handler
        self.inbox = list(events or [])
        self.sent = []
        self.closed = False

    def send(self, raw):
        message = json.loads(raw)
        self.sent.append(message)
        outcome = self.handler(message["method"], message.get("params") or {})
        if isinstance(outcome, dict) and "__error__" in outcome:
            self.inbox.append({"id": message["id"], "error": outcome["__error__"]})
        else:
            self.inbox.append({"id": message["id"], "result": outcome or {}})

    def recv(self, timeout=None):
        if not self.inbox:
            raise TimeoutError("no messages")
        return json.dumps(self.inbox.pop(0))

    def close(self):
        self.closed = True


def _install_fake_websockets(monkeypatch, websocket):
    """Make ``from websockets.sync.client import connect`` return *websocket*."""
    package = types.ModuleType("websockets")
    sync_module = types.ModuleType("websockets.sync")
    client_module = types.ModuleType("websockets.sync.client")
    client_module.connect = lambda url, **kwargs: websocket
    sync_module.client = client_module
    package.sync = sync_module
    monkeypatch.setitem(sys.modules, "websockets", package)
    monkeypatch.setitem(sys.modules, "websockets.sync", sync_module)
    monkeypatch.setitem(sys.modules, "websockets.sync.client", client_module)
    return websocket


def _noop_coordinated(self, proxy):
    @contextlib.contextmanager
    def _manager():
        yield

    return _manager()


def _patch_backend_transport(monkeypatch):
    monkeypatch.setattr(ObscuraBackend, "_coordinated", _noop_coordinated)
    monkeypatch.setattr(ObscuraBackend, "_websocket_url", lambda self: "ws://fake")
    monkeypatch.setattr(time, "sleep", lambda seconds: None)


def test_cdp_session_returns_results(monkeypatch):
    websocket = _install_fake_websockets(
        monkeypatch, _FakeWebSocket(lambda method, params: {"ok": method})
    )
    session = CDPSession("ws://fake")

    assert session.send("Page.enable") == {"ok": "Page.enable"}
    assert [message["method"] for message in websocket.sent] == ["Page.enable"]
    session.close()
    assert websocket.closed is True


def test_cdp_session_sends_session_id_and_buffers_events(monkeypatch):
    event = {"method": "Page.lifecycleEvent", "sessionId": "s1", "params": {"name": "load"}}
    websocket = _install_fake_websockets(monkeypatch, _FakeWebSocket(lambda *a: {}, events=[event]))
    session = CDPSession("ws://fake")

    # The seeded event arrives before the command response and is buffered.
    assert session.send("Page.enable", session_id="s1") == {}
    assert websocket.sent[0]["sessionId"] == "s1"

    assert session.wait_for_event("Page.lifecycleEvent", session_id="s1", timeout=1.0) == event
    assert session.take_events("Page.lifecycleEvent") == []


def test_cdp_session_surfaces_protocol_errors(monkeypatch):
    def handler(method, params):
        return {"__error__": {"code": -32601, "message": "method not found"}}

    _install_fake_websockets(monkeypatch, _FakeWebSocket(handler))
    session = CDPSession("ws://fake")

    with pytest.raises(CDPError, match="method not found"):
        session.send("Emulation.setDeviceMetricsOverride", timeout=1.0)


def test_cdp_session_times_out(monkeypatch):
    websocket = _install_fake_websockets(monkeypatch, _FakeWebSocket(lambda *a: {}))
    session = CDPSession("ws://fake")
    websocket.send = lambda raw: None  # nothing is ever answered
    with pytest.raises(CDPError):
        session.send("Page.enable", timeout=0.05)


def _navigation_handler(html="<html><body>rendered</body></html>", ready_states=None):
    states = iter(ready_states or ["complete"])

    def handler(method, params):
        if method == "Target.createTarget":
            return {"targetId": "T1"}
        if method == "Target.attachToTarget":
            return {"sessionId": "S1"}
        if method == "Page.navigate":
            return {"frameId": "F1"}
        if method == "Runtime.evaluate":
            if params.get("expression") == "document.readyState":
                return {"result": {"value": next(states, "complete")}}
            return {"result": {"value": html}}
        return {}

    return handler


def test_fetch_native_navigates_and_returns_html(monkeypatch):
    websocket = _install_fake_websockets(
        monkeypatch,
        _FakeWebSocket(
            _navigation_handler(),
            events=[
                {
                    "method": "Page.lifecycleEvent",
                    "sessionId": "S1",
                    "params": {"name": "networkIdle"},
                }
            ],
        ),
    )
    _patch_backend_transport(monkeypatch)

    html = ObscuraBackend().fetch_native("https://example.com/article")

    assert html == "<html><body>rendered</body></html>"
    commands = [(message["method"], message.get("params")) for message in websocket.sent]
    assert commands[0][0] == "Target.createTarget"
    assert ("Page.navigate", {"url": "https://example.com/article"}) in commands
    assert ("Target.closeTarget", {"targetId": "T1"}) in commands
    # The viewport override keeps responsive layouts identical to Playwright.
    assert (
        "Emulation.setDeviceMetricsOverride",
        {"width": 1280, "height": 800, "deviceScaleFactor": 1, "mobile": False},
    ) in commands


def test_fetch_native_reports_navigation_errors(monkeypatch):
    def handler(method, params):
        if method == "Target.createTarget":
            return {"targetId": "T1"}
        if method == "Target.attachToTarget":
            return {"sessionId": "S1"}
        if method == "Page.navigate":
            return {"errorText": "net::ERR_NAME_NOT_RESOLVED"}
        return {}

    websocket = _install_fake_websockets(monkeypatch, _FakeWebSocket(handler))
    _patch_backend_transport(monkeypatch)

    with pytest.raises(CDPError, match="ERR_NAME_NOT_RESOLVED"):
        ObscuraBackend().fetch_native("https://does-not-resolve.invalid/")
    # The tab is still cleaned up on failure.
    assert any(message["method"] == "Target.closeTarget" for message in websocket.sent)


def test_fetch_native_polls_ready_state_without_lifecycle_events(monkeypatch):
    navigation = _navigation_handler(ready_states=["loading", "complete"])

    def handler(method, params):
        if method == "Page.setLifecycleEventsEnabled":
            return {"__error__": {"message": "not supported"}}
        return navigation(method, params)

    websocket = _install_fake_websockets(monkeypatch, _FakeWebSocket(handler))
    _patch_backend_transport(monkeypatch)

    assert ObscuraBackend().fetch_native("https://example.com/") == "<html><body>rendered</body></html>"
    expressions = [
        message["params"]["expression"]
        for message in websocket.sent
        if message["method"] == "Runtime.evaluate"
    ]
    assert expressions.count("document.readyState") == 2


def test_fetch_mode_defaults_and_validation():
    assert ObscuraBackend().fetch_mode == "auto"
    assert ObscuraBackend(fetch_mode="native").fetch_mode == "native"
    assert ObscuraBackend(fetch_mode="playwright").fetch_mode == "playwright"
    assert ObscuraBackend(fetch_mode=" Playwright ").fetch_mode == "playwright"
    assert ObscuraBackend(fetch_mode="bogus").fetch_mode == "auto"
    assert ObscuraBackend(fetch_mode=None).fetch_mode == "auto"
    assert obscura_backend.FETCH_MODES == ("auto", "native", "playwright")


def test_fetch_auto_prefers_native(monkeypatch):
    calls = []

    def _native(self, url, proxy=None):
        calls.append(("native", url))
        return "NATIVE"

    def _playwright(self, url, proxy=None):  # pragma: no cover - must not run
        calls.append(("playwright", url))
        return "PLAYWRIGHT"

    monkeypatch.setattr(ObscuraBackend, "fetch_native", _native)
    monkeypatch.setattr(ObscuraBackend, "_fetch_with_playwright", _playwright)

    assert ObscuraBackend(fetch_mode="auto").fetch("https://example.com/") == "NATIVE"
    assert calls == [("native", "https://example.com/")]


def test_fetch_auto_falls_back_to_playwright(monkeypatch):
    def _native(self, url, proxy=None):
        raise CDPError("protocol exploded")

    monkeypatch.setattr(ObscuraBackend, "fetch_native", _native)
    monkeypatch.setattr(ObscuraBackend, "_fetch_with_playwright", lambda self, url, proxy=None: "PW")

    assert ObscuraBackend(fetch_mode="auto").fetch("https://example.com/") == "PW"


def test_fetch_native_mode_does_not_fall_back(monkeypatch):
    def _native(self, url, proxy=None):
        raise CDPError("protocol exploded")

    def _playwright(self, url, proxy=None):  # pragma: no cover - must not run
        raise AssertionError("playwright must not be used when fetch_mode=native")

    monkeypatch.setattr(ObscuraBackend, "fetch_native", _native)
    monkeypatch.setattr(ObscuraBackend, "_fetch_with_playwright", _playwright)

    with pytest.raises(CDPError):
        ObscuraBackend(fetch_mode="native").fetch("https://example.com/")


def test_fetch_playwright_mode_skips_native(monkeypatch):
    def _native(self, url, proxy=None):  # pragma: no cover - must not run
        raise AssertionError("native CDP must not be used when fetch_mode=playwright")

    monkeypatch.setattr(ObscuraBackend, "fetch_native", _native)
    monkeypatch.setattr(ObscuraBackend, "_fetch_with_playwright", lambda self, url, proxy=None: "PW")

    assert ObscuraBackend(fetch_mode="playwright").fetch("https://example.com/") == "PW"


def test_auto_mode_keeps_the_native_error_visible(monkeypatch):
    def _native(self, url, proxy=None):
        raise CDPError("native broke")

    def _playwright(self, url, proxy=None):
        raise RuntimeError("no python playwright")

    monkeypatch.setattr(ObscuraBackend, "fetch_native", _native)
    monkeypatch.setattr(ObscuraBackend, "_fetch_with_playwright", _playwright)

    with pytest.raises(CDPError, match="native broke"):
        ObscuraBackend(fetch_mode="auto").fetch("https://example.com/")


def test_cdp_session_without_websockets_reports_the_missing_dependency(monkeypatch):
    monkeypatch.setitem(sys.modules, "websockets", None)
    monkeypatch.setitem(sys.modules, "websockets.sync", None)
    monkeypatch.setitem(sys.modules, "websockets.sync.client", None)
    with pytest.raises(CDPError, match="websockets"):
        CDPSession("ws://fake")


def test_obscura_fetch_mode_is_read_from_config():
    import surf

    class _Config:
        def __init__(self, mode):
            self.mode = mode

        def has_section(self, name):
            return name == "Browser"

        def get(self, section, key, fallback=None):
            if key == "obscura_fetch":
                return self.mode
            return fallback

    assert surf.Fetcher._build_obscura_backend(_Config("native")).fetch_mode == "native"
    assert surf.Fetcher._build_obscura_backend(_Config(None)).fetch_mode == "auto"
