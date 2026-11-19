"""Background thread that owns the browser (Playwright must stay on one thread).
The GUI sends commands through `cmd`; the worker reports through `events` (polled by the GUI)."""
from __future__ import annotations

import queue
import threading
import traceback

from . import config, dice
from .browser import Browser
from .runner import Run
from .store import Supabase, bid_row, ledger_add


class UI:
    def __init__(self, events: queue.Queue):
        self.events = events

    def log(self, text, level="info"):
        self.events.put(("log", str(text), level))

    def status(self, text):
        self.events.put(("status", text))

    def job(self, row):
        self.events.put(("job", dict(row)))

    def count(self, n, limit):
        self.events.put(("count", n, limit))


class Worker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.cmd: queue.Queue = queue.Queue()
        self.events: queue.Queue = queue.Queue()
        self.stop = threading.Event()
        self.ui = UI(self.events)
        self.browser: Browser | None = None
        self.busy = False

    def run(self):
        while True:
            name, *args = self.cmd.get()
            if name == "quit":
                try:
                    if self.browser:
                        self.browser.close()
                except Exception:
                    pass
                return
            self.busy = True
            try:
                getattr(self, f"_{name}")(*args)
            except Exception as e:
                self.ui.log(f"Error: {e}", "err")
                self.ui.log(traceback.format_exc(limit=4), "dim")
                self.events.put(("error", str(e)))
            finally:
                self.busy = False
                self.events.put(("idle",))

    def _browser(self, settings) -> Browser:
        if not self.browser:
            self.browser = Browser(settings.get("chrome_channel") or "chrome")
        return self.browser.start()

    # ---------- commands ----------
    def _open_browser(self, settings):
        b = self._browser(settings)
        p = b.page("https://www.dice.com/recommended-jobs")
        p.bring_to_front()
        self.ui.log("Browser open. Sign in to Dice there (and to chat.deepseek.com if you use DeepSeek chat). The sign-ins are kept for next time.", "ok")

    def _start(self, settings):
        self.stop.clear()
        self.ui.status("Starting…")
        try:
            Run(settings, self._browser(settings), self.ui, self.stop).run()
        except dice.NotLoggedIn as e:
            self.ui.log(str(e), "err")
        finally:
            self.ui.status("Stopped" if self.stop.is_set() else "Idle")

    def _read_job(self, settings, url):
        b = self._browser(settings)
        p = b.page()
        try:
            job = dice.read_job(p, url)
            job["url"] = url
            self.events.put(("job_read", job))
        finally:
            p.close()

    def _save_manual(self, settings, job, extra):
        sb = Supabase(settings["supabase_url"], settings["supabase_key"])
        if not sb.ready:
            raise RuntimeError("Supabase is not set (Settings).")
        row = bid_row(job=job, source="dice-manual", folder=extra.get("folder", ""), resume_path=extra.get("resume", ""),
                      cover_path=extra.get("cover", ""), notes=extra.get("notes", "") or "Manual Dice bid", log=["applied by hand"])
        saved = sb.insert_bid(row)
        ledger_add({"status": "applied", "job_id": url_id(job.get("url", "")), "url": job.get("url", ""), "company": job.get("company", ""),
                    "title": job.get("title", ""), "why": "manual", "folder": extra.get("folder", ""), "source": "dice-manual"})
        self.events.put(("manual_saved", saved.get("id")))
        self.ui.log(f"Manual bid saved to Supabase (#{saved.get('id', '?')}): {job.get('title')} @ {job.get('company')}", "ok")


def url_id(url: str) -> str:
    import re
    m = re.search(r"job-detail/([0-9a-f-]{16,})", url or "", re.I)
    return m.group(1) if m else (url or "")
