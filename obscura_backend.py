"""Obscura CDP backend for Surf.

The Obscura server is a process-wide, on-demand service rather than a
per-request child process.  Requests using the same endpoint are serialized
while they use the service.  This is intentional: ``obscura serve --proxy``
is a server-wide setting, so serializing requests lets us safely replace a
managed server when the proxy changes without closing another request's
browser underneath it.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from typing import Dict, Iterator, Optional, Set, Tuple

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


class ObscuraBackend:
    """Fetch pages through a lazily started, shared Obscura CDP server."""

    def __init__(
        self,
        executable: str = "obscura",
        endpoint: str = "http://127.0.0.1:9222",
        startup_timeout: float = 15.0,
        stealth: bool = False,
    ) -> None:
        self.executable = executable
        self.endpoint = endpoint.rstrip("/")
        self.startup_timeout = startup_timeout
        self.stealth = stealth
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

    def fetch(self, url: str, proxy: Optional[str] = None) -> str:
        """Return rendered HTML for *url* using Playwright over CDP.

        The coordination lock deliberately covers navigation as well as server
        startup.  Obscura's proxy is a server-level option; this prevents a
        proxy switch from terminating a browser used by another request.
        """
        process_lock = _lock_for(self._lock_path)
        with process_lock:
            with _file_lock(self._lock_path):
                self._ensure_server(proxy)
                managed_user = self._acquire_server_user()
                try:
                    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
                    from playwright.sync_api import sync_playwright

                    with sync_playwright() as playwright:
                        browser = playwright.chromium.connect_over_cdp(self.endpoint)
                        context = browser.contexts[0] if browser.contexts else browser.new_context()
                        page = context.new_page()
                        try:
                            try:
                                page.goto(url, wait_until="networkidle", timeout=60000)
                            except PlaywrightTimeoutError:
                                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                            page.wait_for_timeout(2000)
                            return page.content()
                        finally:
                            page.close()
                        # connect_over_cdp's browser.close() only disconnects Playwright.
                finally:
                    if managed_user:
                        self._release_server_user()
