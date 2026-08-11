"""Experimental Obscura CDP backend for Surf.

Obscura is deliberately kept behind a small adapter.  Surf still owns the
fetch policy and extraction pipeline; this module only provides a Playwright-
compatible page.content() result through Obscura's CDP endpoint.
"""

from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request
from typing import Optional


class ObscuraBackend:
    """Fetch one page through a short-lived local Obscura CDP server."""

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

    def _endpoint_ready(self) -> bool:
        try:
            with urllib.request.urlopen(self.endpoint + "/json/version", timeout=1.0) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError):
            return False

    def _start_server(self, proxy: Optional[str] = None) -> subprocess.Popen:
        port = self.endpoint.rsplit(":", 1)[-1]
        command = [self.executable, "serve", "--port", port]
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
                raise RuntimeError(
                    f"Obscura exited during startup with code {process.returncode}"
                )
            time.sleep(0.1)
        process.terminate()
        raise TimeoutError(f"Obscura CDP endpoint did not start: {self.endpoint}")

    def fetch(self, url: str, proxy: Optional[str] = None) -> str:
        """Return rendered HTML for *url* using Playwright over CDP."""
        process = None
        if not self._endpoint_ready():
            process = self._start_server(proxy)

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
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
