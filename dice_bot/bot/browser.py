"""The browser the bot works in. Two ways (Settings → Browser):

  attach            Your own Chrome window, started with a debug port (start_chrome.bat, or the bot starts it).
                    It is a normal Chrome you keep open and use yourself: sign in once to Dice, DeepSeek, Gmail…
                    and the bot works in it alongside you. It looks like a person's browser to sites, because it is
                    not launched by the automation library. Its profile lives in data/chrome-profile: Chrome (since
                    version 136) refuses a debug port on its main profile, so your everyday profile cannot be used.
  chrome / msedge   A window Playwright launches with its own profile (data/browser-profile).
  chromium          The same with Playwright's Chromium (downloaded automatically the first time).

Only one thread may touch Playwright: the bot worker owns this object."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .config import BROWSER_PROFILE, DATA

ATTACH_PROFILE = DATA / "chrome-profile"
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
]


def chrome_exe() -> str | None:
    return next((p for p in CHROME_PATHS if Path(p).exists()), None)


def debug_port_open(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def start_debug_chrome(port: int, url: str = "https://www.dice.com/recommended-jobs") -> None:
    """Starts a normal Chrome window with a debug port (only reachable from this PC) and the bot's own profile."""
    exe = chrome_exe()
    if not exe:
        raise RuntimeError("Google Chrome is not installed. Install it from google.com/chrome, or choose another browser in Settings.")
    ATTACH_PROFILE.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([exe, f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1",
                      f"--user-data-dir={ATTACH_PROFILE}", "--no-first-run", "--no-default-browser-check", "--start-maximized", url],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)


class Browser:
    def __init__(self, channel: str = "chrome", port: int = 9222):
        self.channel = channel
        self.port = int(port or 9222)
        self._pw = None
        self._cdp = None   # the attached Chrome (attach mode)
        self.ctx = None

    @property
    def attached(self) -> bool:
        return self.channel == "attach"

    def start(self):
        if self.ctx:
            try:
                if self.attached:
                    if self._cdp and self._cdp.is_connected():
                        return self
                    raise RuntimeError("disconnected")
                if not self.ctx.pages and self.ctx.browser is None:
                    raise RuntimeError("closed")  # persistent context with every window closed
                return self
            except Exception:
                self._reset()
        from playwright.sync_api import sync_playwright
        self._pw = self._pw or sync_playwright().start()
        if self.attached:
            return self._attach()
        BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)
        channel = None if self.channel in ("chromium", "", None) else self.channel
        for attempt in (1, 2):
            try:
                self.ctx = self._pw.chromium.launch_persistent_context(
                    str(BROWSER_PROFILE), channel=channel, headless=False, no_viewport=True,
                    args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
                    ignore_default_args=["--enable-automation"], accept_downloads=True,
                )
                break
            except Exception as e:
                msg = str(e)
                if "Executable doesn't exist" in msg and channel is None and attempt == 1:
                    # Playwright's Chromium is not downloaded yet: get it once, then try again
                    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                                   capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    continue
                if "Executable doesn't exist" in msg or "not found" in msg.lower():
                    raise RuntimeError(f"The browser '{self.channel}' was not found. Choose 'My Chrome window (attach)' or "
                                       "'Google Chrome' in Settings → Browser, or install Google Chrome.")
                if "display" in msg.lower() or "xserver" in msg.lower():
                    raise RuntimeError("No screen to open the browser on. On a Linux server, run the bot inside a desktop session (see README).")
                if "user data directory is already in use" in msg.lower() or "ProcessSingleton" in msg:
                    raise RuntimeError("The bot's browser is already open in another window. Close it and try again.")
                raise
        self.ctx.on("close", lambda *_: setattr(self, "ctx", None))
        return self

    def _attach(self):
        if not debug_port_open(self.port):
            start_debug_chrome(self.port)
            for _ in range(40):
                time.sleep(0.5)
                if debug_port_open(self.port):
                    break
            else:
                raise RuntimeError(f"Chrome did not open its debug port {self.port}. If a Chrome window with the bot's profile is already open "
                                   "without the port, close it (or run start_chrome.bat) and try again.")
        self._cdp = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}")
        self.ctx = self._cdp.contexts[0] if self._cdp.contexts else self._cdp.new_context()
        return self

    @property
    def alive(self) -> bool:
        try:
            return self.ctx is not None and self.ctx.pages is not None
        except Exception:
            return False

    def _reset(self):
        """The window was closed by hand (or Chrome restarted): forget it so start() connects again."""
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self.ctx = None
        self._cdp = None
        self._pw = None

    def page(self, url: str | None = None):
        self.start()
        try:
            blank = [p for p in self.ctx.pages if p.url in ("about:blank", "chrome://newtab/", "chrome://new-tab-page/")]
            p = blank[0] if blank else self.ctx.new_page()
        except Exception as e:
            if "closed" not in str(e).lower():
                raise
            self._reset()
            self.start()
            p = self.ctx.new_page()
        if url:
            p.goto(url, wait_until="domcontentloaded", timeout=60000)
        return p

    def chat_page(self, url: str):
        self.start()
        for p in self.ctx.pages:
            if "chat.deepseek.com" in p.url:
                p.goto(url, wait_until="domcontentloaded")
                return p
        return self.page(url)

    def close(self):
        """Ends the bot's control. In attach mode your Chrome window stays open."""
        try:
            if self.ctx and not self.attached:
                self.ctx.close()
        except Exception:
            pass
        finally:
            self.ctx = None
            self._cdp = None
            if self._pw:
                try:
                    self._pw.stop()
                except Exception:
                    pass
                self._pw = None
