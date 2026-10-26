"""Obscura CDP backend for Surf.

The Obscura server is a process-wide, on-demand service rather than a
per-request child process.  Requests using the same endpoint are serialized
while they use the service.  This is intentional: ``obscura serve --proxy``
is a server-wide setting, so serializing requests lets us safely replace a
managed server when the proxy changes without closing another request's
browser underneath it.

Two ways of talking to the server are supported:

* ``fetch_native`` drives the raw Chrome DevTools Protocol (``websockets``
  only) and is what the generic headless fetch uses.  It needs no Playwright.
* ``session``/``fetch`` expose a Playwright ``Browser`` over CDP for callers
  that run real page automation (locators, form fills, downloads).  That path
  needs the optional ``browser`` extra.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ``[Browser] obscura_fetch`` values: use native CDP, native CDP with a
# Playwright fallback, or the legacy Playwright path only.
FETCH_MODES = ("auto", "native", "playwright")

# A flock is process-wide on some platforms, and multiple ObscuraBackend
# instances are created by Surf.  The in-process lock also avoids relying on
# platform-specific flock/thread interactions.
_process_locks: Dict[str, threading.RLock] = {}
_process_locks_guard = threading.Lock()
_started_servers: Set[str] = set()
_started_servers_guard = threading.Lock()


def _lock_for(path: str) -> threading.RLock:
    with _process_locks_guard:
        lock = _process_locks.get(path)
        if lock is None:
            lock = threading.RLock()
            _process_locks[path] = lock
        return lock


@contextmanager
def _file_lock(path: str) -> Iterator[None]:
    """Take an exclusive lock that also coordinates gunicorn workers."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = open(path, "a+")
    try:
        if os.name == "nt":  # pragma: no cover - CI and production are Unix
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":  # pragma: no cover - see above
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def _terminate_pid(pid: int) -> None:
    """Terminate a server started by us, even when it was started by another worker."""
    if not _pid_alive(pid):
        return
    if os.name == "nt":  # pragma: no cover - production is Unix
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return

    try:
        os.kill(pid, 15)
    except OSError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.05)
    if _pid_alive(pid):
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def _proxy_key(proxy: Optional[str]) -> str:
    # Do not put proxy credentials in the shared state file.
    value = "<none>" if not proxy else proxy
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_json(path: str, value: dict) -> None:
    directory = os.path.dirname(path)
    fd, temporary = tempfile.mkstemp(prefix=".obscura-", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _remove_file(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


@atexit.register
def _cleanup_started_servers() -> None:
    """Clean managed servers after workers exit, but never external servers."""
    with _started_servers_guard:
        states = list(_started_servers)
    for state_path in states:
        lock_path = state_path[: -len(".json")] + ".lock"
        try:
            with _lock_for(lock_path):
                with _file_lock(lock_path):
                    state = _read_json(state_path)
                    if not state or not state.get("managed"):
                        continue
                    workers = state.get("workers", {})
                    workers.pop(str(os.getpid()), None)
                    state["workers"] = workers
                    if workers:
                        _write_json(state_path, state)
                    else:
                        _terminate_pid(int(state["server_pid"]))
                        _remove_file(state_path)
        except (OSError, ValueError, TypeError, KeyError):
            # Interpreter shutdown should not turn cleanup into an exception.
            continue


class CDPError(RuntimeError):
    """Raised when the DevTools protocol returns an error or times out."""


class CDPSession:
    """Minimal synchronous Chrome DevTools Protocol client.

    Surf only needs a handful of CDP commands (target/page/runtime), so this
    keeps the wire protocol small instead of depending on Playwright.  Events
    that arrive while a command response is awaited are buffered so callers can
    wait for them afterwards.
    """

    def __init__(self, ws_url: str, *, open_timeout: float = 15.0) -> None:
        try:
            from websockets.sync.client import connect as websocket_connect
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise CDPError(
                "The 'websockets' package is required for Obscura's native CDP "
                "path. Reinstall Surf (``uv sync``) to restore it."
            ) from exc
        self._ws = websocket_connect(ws_url, max_size=None, open_timeout=open_timeout)
        self._next_id = 0
        self._events: List[dict] = []

    # -- transport -----------------------------------------------------
    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass

    def _receive(self, timeout: float) -> dict:
        try:
            raw = self._ws.recv(timeout=max(0.01, timeout))
        except TimeoutError as exc:
            raise CDPError("timed out waiting for a CDP message") from exc
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", "replace")
        try:
            message = json.loads(raw)
        except ValueError as exc:
            raise CDPError("received an unparsable CDP message") from exc
        if not isinstance(message, dict):
            raise CDPError("received an unexpected CDP message")
        return message

    def send(
        self,
        method: str,
        params: Optional[dict] = None,
        session_id: Optional[str] = None,
        timeout: float = 30.0,
    ) -> dict:
        """Send one command and return its result, buffering interleaved events."""
        self._next_id += 1
        message_id = self._next_id
        payload: dict = {"id": message_id, "method": method}
        if params:
            payload["params"] = params
        if session_id:
            payload["sessionId"] = session_id
        self._ws.send(json.dumps(payload))

        deadline = time.monotonic() + max(0.1, timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CDPError("timed out waiting for %s" % method)
            message = self._receive(remaining)
            if message.get("id") == message_id:
                if message.get("error"):
                    raise CDPError("%s failed: %s" % (method, message["error"]))
                return message.get("result") or {}
            self._events.append(message)

    # -- events --------------------------------------------------------
    @staticmethod
    def _matches(event: dict, method: Optional[str], session_id: Optional[str]) -> bool:
        if method is not None and event.get("method") != method:
            return False
        return session_id is None or event.get("sessionId") == session_id

    def take_events(self, method: Optional[str] = None, session_id: Optional[str] = None) -> List[dict]:
        """Remove and return buffered events matching *method*/*session_id*."""
        matched = [event for event in self._events if self._matches(event, method, session_id)]
        self._events = [event for event in self._events if not self._matches(event, method, session_id)]
        return matched

    def wait_for_event(self, method: str, session_id: Optional[str] = None, timeout: float = 30.0) -> dict:
        """Return the next buffered or incoming event named *method*."""
        buffered = self.take_events(method, session_id)
        if buffered:
            return buffered[0]
        deadline = time.monotonic() + max(0.1, timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CDPError("timed out waiting for the %s event" % method)
            message = self._receive(remaining)
            if self._matches(message, method, session_id):
                return message
            self._events.append(message)


class ObscuraBackend:
    """Fetch pages through a lazily started, shared Obscura CDP server."""

    def __init__(
        self,
        executable: str = "obscura",
        endpoint: str = "http://127.0.0.1:9222",
        startup_timeout: float = 15.0,
        stealth: bool = False,
        navigation_timeout: float = 60.0,
        fetch_mode: str = "auto",
    ) -> None:
        self.executable = executable
        self.endpoint = endpoint.rstrip("/")
        self.startup_timeout = startup_timeout
        self.stealth = stealth
        # Upper bound (seconds) for a single navigation.  Kept below the
        # gunicorn worker timeout so a slow page cannot get the worker
        # SIGKILLed; callers can override it from config.
        self.navigation_timeout = max(5.0, float(navigation_timeout))
        mode = str(fetch_mode or "auto").strip().lower()
        self.fetch_mode = mode if mode in FETCH_MODES else "auto"
        self._state_path, self._lock_path = self._coordination_paths(self.endpoint)

    @staticmethod
    def _coordination_paths(endpoint: str) -> Tuple[str, str]:
        parsed = urllib.parse.urlsplit(endpoint)
        # localhost and loopback are common spellings of the same local CDP
        # service.  Include the port so unrelated remote endpoints do not
        # unnecessarily share state.
        host = (parsed.hostname or "").lower()
        if host in {"localhost", "127.0.0.1", "::1"}:
            host = "local"
        identity = "%s:%s:%s" % (parsed.scheme.lower(), host, parsed.port or "")
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        base = os.path.join(tempfile.gettempdir(), "surf-obscura-" + digest)
        return base + ".json", base + ".lock"

    def _endpoint_ready(self) -> bool:
        try:
            with urllib.request.urlopen(self.endpoint + "/json/version", timeout=1.0) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError):
            return False

    def _port(self) -> str:
        try:
            port = urllib.parse.urlsplit(self.endpoint).port
        except ValueError as exc:
            raise ValueError("Invalid Obscura endpoint: %s" % self.endpoint) from exc
        if port is None:
            raise ValueError("Obscura endpoint must include a port: %s" % self.endpoint)
        return str(port)

    def _start_server(self, proxy: Optional[str] = None) -> subprocess.Popen:
        command = [self.executable, "serve", "--port", self._port()]
        if self.stealth:
            command.append("--stealth")
        if proxy:
            command.extend(["--proxy", proxy])

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self._endpoint_ready():
                return process
            if process.poll() is not None:
                raise RuntimeError("Obscura exited during startup with code %s" % process.returncode)
            time.sleep(0.1)
        _terminate_pid(process.pid)
        raise TimeoutError("Obscura CDP endpoint did not start: %s" % self.endpoint)

    @staticmethod
    def _state_matches(state: dict, executable: str, stealth: bool, proxy: Optional[str]) -> bool:
        return (
            state.get("executable") == executable
            and bool(state.get("stealth")) == stealth
            and state.get("proxy_key") == _proxy_key(proxy)
        )

    @staticmethod
    def _state_is_controllable(state: Optional[dict]) -> bool:
        if not state:
            return False
        try:
            owner_pid = int(state["owner_pid"])
            server_pid = int(state["server_pid"])
        except (KeyError, TypeError, ValueError):
            return False
        # If the owner disappeared, treat the endpoint as external.  This is
        # safer than killing a newly started service after a PID reuse.
        return bool(state.get("managed")) and _pid_alive(owner_pid) and _pid_alive(server_pid)

    def _start_managed_server(self, proxy: Optional[str]) -> None:
        process = self._start_server(proxy)
        state = {
            "managed": True,
            "owner_pid": os.getpid(),
            "server_pid": process.pid,
            "executable": self.executable,
            "stealth": self.stealth,
            "proxy_key": _proxy_key(proxy),
            "workers": {str(os.getpid()): True},
            "active": {str(os.getpid()): 0},
        }
        _write_json(self._state_path, state)
        with _started_servers_guard:
            _started_servers.add(self._state_path)

    def _acquire_server_user(self) -> bool:
        """Record this worker as a user so its exit cannot kill another worker's server."""
        state = _read_json(self._state_path)
        if not self._state_is_controllable(state):
            return False
        workers = state.setdefault("workers", {})
        active = state.setdefault("active", {})
        pid = str(os.getpid())
        workers[pid] = True
        active[pid] = int(active.get(pid, 0)) + 1
        _write_json(self._state_path, state)
        with _started_servers_guard:
            _started_servers.add(self._state_path)
        return True

    def _release_server_user(self) -> None:
        state = _read_json(self._state_path)
        if not state or not state.get("managed"):
            return
        active = state.setdefault("active", {})
        pid = str(os.getpid())
        count = int(active.get(pid, 0))
        if count <= 1:
            active.pop(pid, None)
        else:
            active[pid] = count - 1
        state["active"] = active
        _write_json(self._state_path, state)

    def _ensure_server(self, proxy: Optional[str]) -> None:
        state = _read_json(self._state_path)
        if self._endpoint_ready():
            # No state means a user-managed/external endpoint: connect only and
            # never stop it.  A live managed state may be replaced safely here
            # because the inter-process lock is held for the whole fetch.
            if self._state_is_controllable(state) and not self._state_matches(
                state, self.executable, self.stealth, proxy
            ):
                _terminate_pid(int(state["server_pid"]))
                _remove_file(self._state_path)
                self._start_managed_server(proxy)
            return

        # The recorded server may have died between requests.  Remove only a
        # server whose owner is still alive; stale state never gives us license
        # to kill an endpoint that may now be external.
        if self._state_is_controllable(state):
            _terminate_pid(int(state["server_pid"]))
            _remove_file(self._state_path)
        self._start_managed_server(proxy)

    @contextmanager
    def _coordinated(self, proxy: Optional[str]) -> Iterator[None]:
        """Hold the inter-process lock and keep the managed server alive."""
        process_lock = _lock_for(self._lock_path)
        with process_lock:
            with _file_lock(self._lock_path):
                self._ensure_server(proxy)
                managed_user = self._acquire_server_user()
                try:
                    yield
                finally:
                    if managed_user:
                        self._release_server_user()

    @contextmanager
    def session(self, proxy: Optional[str] = None) -> Iterator["object"]:
        """Yield a Playwright ``Browser`` connected to the shared Obscura CDP server.

        The coordination lock is held for the whole ``with`` block, and the
        managed server is reference-counted so it is not stopped while other
        requests are still using it.  Callers may create contexts/pages on the
        yielded browser and run arbitrary page interactions before it closes.

        This path needs the optional ``browser`` extra; the generic headless
        fetch uses :meth:`fetch_native` instead and needs no Playwright.
        """
        with self._coordinated(proxy):
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(self.endpoint)
                try:
                    yield browser
                finally:
                    # connect_over_cdp's browser.close() only disconnects
                    # Playwright; it never stops the shared server.
                    try:
                        browser.close()
                    except Exception:
                        pass

    def _websocket_url(self) -> str:
        """Return the browser-level DevTools websocket URL for the endpoint."""
        with urllib.request.urlopen(self.endpoint + "/json/version", timeout=5.0) as response:
            payload = json.loads(response.read().decode("utf-8", "replace") or "{}")
        url = (payload or {}).get("webSocketDebuggerUrl")
        if not url:
            raise CDPError("Obscura did not report a webSocketDebuggerUrl")
        return url

    def fetch_native(self, url: str, proxy: Optional[str] = None) -> str:
        """Return rendered HTML for *url* using the raw DevTools protocol.

        Playwright-free path: create a tab, navigate it, wait for the page to
        settle and read back ``outerHTML``.  The coordination lock covers
        navigation as well as startup, because Obscura's proxy is a
        server-level option.
        """
        nav_timeout = self.navigation_timeout
        with self._coordinated(proxy):
            cdp = CDPSession(self._websocket_url(), open_timeout=min(nav_timeout, 15.0))
            target_id = None
            try:
                target_id = cdp.send("Target.createTarget", {"url": "about:blank"}, timeout=15.0)["targetId"]
                session_id = cdp.send(
                    "Target.attachToTarget", {"targetId": target_id, "flatten": True}, timeout=15.0
                )["sessionId"]
                cdp.send("Page.enable", session_id=session_id, timeout=15.0)
                # Match Playwright's default context viewport so responsive
                # layouts render the same way as the legacy path.
                for method, params in (
                    (
                        "Emulation.setDeviceMetricsOverride",
                        {"width": 1280, "height": 800, "deviceScaleFactor": 1, "mobile": False},
                    ),
                    ("Page.setLifecycleEventsEnabled", {"enabled": True}),
                ):
                    try:
                        cdp.send(method, params, session_id=session_id, timeout=10.0)
                    except CDPError as exc:
                        logger.debug("obscura: %s unsupported (%s); continuing", method, exc)

                navigation = cdp.send("Page.navigate", {"url": url}, session_id=session_id, timeout=30.0)
                if navigation.get("errorText"):
                    raise CDPError("navigation failed: %s" % navigation["errorText"])

                self._wait_for_page_load(cdp, session_id, nav_timeout)

                evaluated = cdp.send(
                    "Runtime.evaluate",
                    {
                        "expression": "document.documentElement ? document.documentElement.outerHTML : ''",
                        "returnByValue": True,
                    },
                    session_id=session_id,
                    timeout=30.0,
                )
                value = (evaluated.get("result") or {}).get("value")
                return value if isinstance(value, str) else ""
            finally:
                if target_id:
                    try:
                        cdp.send("Target.closeTarget", {"targetId": target_id}, timeout=5.0)
                    except Exception:
                        pass
                cdp.close()

    def _wait_for_page_load(self, cdp: CDPSession, session_id: str, timeout: float) -> None:
        """Wait for network idle (or a completed document), then settle."""
        saw_load = False
        deadline = time.monotonic() + timeout
        # After the ``load`` event, keep waiting for ``networkIdle`` only for a
        # bounded grace period; pages with long-polling requests never go idle.
        idle_deadline = None
        while True:
            now = time.monotonic()
            limit = deadline if idle_deadline is None else min(deadline, idle_deadline)
            if now >= limit:
                break
            try:
                event = cdp.wait_for_event("Page.lifecycleEvent", session_id, timeout=limit - now)
            except CDPError:
                break
            name = str((event.get("params") or {}).get("name") or "").lower()
            if name == "networkidle":
                saw_load = True
                break
            if name == "load" and not saw_load:
                saw_load = True
                idle_deadline = now + min(10.0, timeout)

        if not saw_load:
            # CDP servers without lifecycle events: poll readyState instead.
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                state = cdp.send(
                    "Runtime.evaluate",
                    {"expression": "document.readyState", "returnByValue": True},
                    session_id=session_id,
                    timeout=15.0,
                )
                value = str((state.get("result") or {}).get("value") or "").lower()
                if value in {"interactive", "complete"}:
                    break
                time.sleep(0.3)

        # Same settle window the Playwright path used after navigation.
        time.sleep(2.0)

    def _fetch_with_playwright(self, url: str, proxy: Optional[str] = None) -> str:
        """Return rendered HTML for *url* using Playwright over CDP."""
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        nav_timeout_ms = int(self.navigation_timeout * 1000)
        # domcontentloaded fallback is capped so the call stays well below the
        # gunicorn worker timeout even when networkidle times out.
        fallback_timeout_ms = min(nav_timeout_ms, int(max(5.0, self.navigation_timeout * 0.5) * 1000))
        with self.session(proxy) as browser:
            reuse_context = bool(browser.contexts)
            context = browser.contexts[0] if reuse_context else browser.new_context()
            page = context.new_page()
            try:
                try:
                    page.goto(url, wait_until="networkidle", timeout=nav_timeout_ms)
                except PlaywrightTimeoutError:
                    page.goto(url, wait_until="domcontentloaded", timeout=fallback_timeout_ms)
                page.wait_for_timeout(2000)
                return page.content()
            finally:
                page.close()
                # Close only the context we created ourselves; the default
                # context of a CDP browser belongs to the shared server.
                if not reuse_context:
                    try:
                        context.close()
                    except Exception:
                        pass

    def fetch(self, url: str, proxy: Optional[str] = None) -> str:
        """Return rendered HTML for *url* through the shared Obscura server.

        ``fetch_mode`` picks the transport: ``native`` speaks raw CDP,
        ``playwright`` keeps the legacy Playwright path, and ``auto`` (default)
        tries native CDP first and falls back to Playwright when the raw
        protocol path fails.
        """
        if self.fetch_mode == "playwright":
            return self._fetch_with_playwright(url, proxy)

        try:
            return self.fetch_native(url, proxy)
        except Exception as native_error:
            if self.fetch_mode == "native":
                raise
            logger.warning("Obscura native CDP fetch failed (%s); retrying with Playwright", native_error)
            try:
                return self._fetch_with_playwright(url, proxy)
            except Exception as fallback_error:
                logger.warning("Obscura Playwright fallback failed too: %s", fallback_error)
                raise native_error
