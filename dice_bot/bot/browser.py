"""The bot's own Chrome window (Playwright, persistent profile in data/browser-profile).
You sign in to Dice (and DeepSeek, for the chat engine) once in this window; the sessions are kept.
Only one thread may touch Playwright: the bot worker owns this object."""
from __future__ import annotations

from .config import BROWSER_PROFILE


class Browser:
    def __init__(self, channel: str = "chrome"):
        self.channel = channel
        self._pw = None
        self.ctx = None

    def start(self):
        if self.ctx:
            try:
                _ = self.ctx.pages  # still alive?
                return self
            except Exception:
                self.ctx = None
        from playwright.sync_api import sync_playwright
        self._pw = self._pw or sync_playwright().start()
        BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)
        try:
            self.ctx = self._pw.chromium.launch_persistent_context(
                str(BROWSER_PROFILE), channel=self.channel, headless=False, no_viewport=True,
                args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"], accept_downloads=True,
            )
        except Exception as e:
            if "Executable doesn't exist" in str(e) or "not found" in str(e).lower():
                raise RuntimeError(f"Google Chrome was not found (channel '{self.channel}'). Install Chrome or set the browser to msedge in Settings.")
            if "user data directory is already in use" in str(e).lower() or "ProcessSingleton" in str(e):
                raise RuntimeError("The bot's browser is already open in another window. Close it and try again.")
            raise
        self.ctx.on("close", lambda *_: setattr(self, "ctx", None))
        return self

    @property
    def alive(self) -> bool:
        try:
            return self.ctx is not None and self.ctx.pages is not None
        except Exception:
            return False

    def page(self, url: str | None = None):
        self.start()
        blank = [p for p in self.ctx.pages if p.url in ("about:blank", "chrome://newtab/")]
        p = blank[0] if blank else self.ctx.new_page()
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
        try:
            if self.ctx:
                self.ctx.close()
        finally:
            self.ctx = None
            if self._pw:
                self._pw.stop()
                self._pw = None
