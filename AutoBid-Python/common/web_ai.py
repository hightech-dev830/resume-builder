"""Ask an AI without an API key: through a local AI CLI, or through its web page.

Providers:
    claude_code   "claude -p" (Claude Code, uses your Claude login)   <- recommended
    cursor        "cursor-agent -p" (Cursor CLI, uses your Cursor login)
    chatgpt / claude / deepseek   the chat WEB page in the bot's Chrome window.
        Not recommended: these sites detect automated browsers (ChatGPT shows a
        Cloudflare "Just a moment..." check) and their terms forbid automated use,
        so your account can be limited. The bot never tries to get around that.

Each question goes to a NEW chat, so jobs never mix. The browser profile is shared with
the bots (common/browser.py), so you log in to the AI sites only once.
"""
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from notify import notify

PROVIDERS = {
    "chatgpt": {
        "url": "https://chatgpt.com/",
        # logged in: #prompt-textarea (contenteditable); logged out: a plain textarea
        "input": ["#prompt-textarea", "div[contenteditable='true']",
                  "textarea[placeholder*='Ask']", "textarea"],
        "send": ["button[data-testid='send-button']", "#composer-submit-button"],
        "busy": ["button[data-testid='stop-button']", "button[aria-label*='Stop']"],
        "answer": ["div[data-message-author-role='assistant']"],
    },
    "claude": {
        "url": "https://claude.ai/new",
        "input": ["div[contenteditable='true'].ProseMirror", "div[contenteditable='true']"],
        "send": ["button[aria-label='Send message']", "button[aria-label*='Send']"],
        "busy": ["[data-is-streaming='true']", "button[aria-label='Stop response']"],
        "answer": ["div.font-claude-response", "[data-is-streaming='false']", "div.font-claude-message"],
    },
    "deepseek": {
        "url": "https://chat.deepseek.com/",
        "input": ["textarea#chat-input", "textarea"],
        "send": ["div[role='button'][aria-disabled='false']:has(svg)"],
        "busy": ["div[role='button'] svg rect"],   # the stop square while answering
        "answer": ["div.ds-markdown"],
    },
}

CLI = {
    # --tools "": text answers only, Claude Code never runs tools (faster, nothing touched)
    "claude_code": ["claude", "-p", "--output-format", "text", "--tools", ""],
    "cursor": ["cursor-agent", "-p", "--output-format", "text"],
}

LOGIN_WAIT_MIN = 5
ANSWER_TIMEOUT_MIN = 6


class WebAI:
    def __init__(self, provider, context=None, model=""):
        """provider: a PROVIDERS key (needs a Playwright browser context) or a CLI key.
        model: CLI model name/alias ("opus", "haiku", "claude-sonnet-5-5", ...); "" = default.
        Web chat pages use whatever model is selected on the page."""
        if provider not in PROVIDERS and provider not in CLI:
            raise ValueError(f"unknown AI provider {provider!r}; use one of "
                             f"{', '.join([*PROVIDERS, *CLI])}")
        self.provider = provider
        self.model = (model or "").strip()
        self.context = context
        self.page = None

    @property
    def label(self):
        return f"{self.provider}" + (f" ({self.model})" if self.model else "")

    # ------------------------------------------------------------ public

    def ask(self, prompt):
        """Send one prompt in a fresh chat and return the answer text."""
        if self.provider in CLI:
            return self._ask_cli(prompt)
        return self._ask_web(prompt)

    def ask_json(self, prompt, required_keys=()):
        """ask() and parse a JSON object from the answer (one retry in the same chat)."""
        text = self.ask(prompt)
        data = extract_json(text)
        if data is None or any(k not in data for k in required_keys):
            if self.provider in CLI:
                text = self.ask(prompt + "\n\nReturn ONLY the JSON object, nothing else.")
            else:
                text = self._send_and_wait(
                    "Your reply was not one valid JSON object with all required keys. "
                    "Send the complete JSON object again inside one ```json code block, nothing else.")
            data = extract_json(text)
        if data is None:
            raise RuntimeError(f"{self.provider} did not return valid JSON")
        missing = [k for k in required_keys if k not in data]
        if missing:
            raise RuntimeError(f"{self.provider} answer is missing {missing}")
        return data

    # ------------------------------------------------------------ CLI

    def _ask_cli(self, prompt):
        cmd = list(CLI[self.provider])
        exe = find_cli(cmd[0])
        if not exe:
            raise RuntimeError(f"'{cmd[0]}' is not installed or not on PATH")
        cmd[0] = exe
        if self.model:
            cmd += ["--model", self.model]
        # run in an empty temp folder so the CLI does not read any project files
        with tempfile.TemporaryDirectory() as tmp:
            res = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                 encoding="utf-8", timeout=ANSWER_TIMEOUT_MIN * 60, cwd=tmp,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if res.returncode != 0:
            raise RuntimeError(f"{cmd[0]} failed: {res.stderr.strip()[:300]}")
        return res.stdout

    # ------------------------------------------------------------ web

    def _first(self, selectors, timeout=0):
        """First visible element for any of the selectors (waits up to timeout seconds)."""
        end = time.time() + timeout
        while True:
            for sel in selectors:
                loc = self.page.locator(sel)
                try:
                    if loc.count() and loc.last.is_visible():
                        return loc.last
                except Exception:
                    pass
            if time.time() >= end:
                return None
            time.sleep(0.5)

    def _open_new_chat(self):
        cfg = PROVIDERS[self.provider]
        if self.page is None or self.page.is_closed():
            self.page = self.context.new_page()
        self.page.goto(cfg["url"], wait_until="domcontentloaded", timeout=60000)
        box = self._first(cfg["input"], timeout=20)
        if box is None:
            # not logged in (or a bot check): hand it to the user once
            self.page.bring_to_front()
            notify(f"AutoBid: log in to {self.provider}",
                   f"Log in to {cfg['url']} in the bot's Chrome window. "
                   f"The bot continues by itself (waits {LOGIN_WAIT_MIN} min).")
            end = time.time() + LOGIN_WAIT_MIN * 60
            last_reload = time.time()
            while box is None and time.time() < end:
                time.sleep(3)
                box = self._first(cfg["input"], timeout=1)
                # after logging in, some sites stay on another page: go back to the chat
                # every 30 s, but never while you are on a login page
                if box is None and time.time() - last_reload > 30 and not re.search(
                        r"login|auth|signin|sign_in|sign-in", self.page.url, re.I):
                    last_reload = time.time()
                    try:
                        self.page.goto(cfg["url"], wait_until="domcontentloaded")
                    except Exception:
                        pass
            if box is None:
                raise RuntimeError(f"not logged in to {self.provider}")
        return box

    def _answers(self):
        for sel in PROVIDERS[self.provider]["answer"]:
            loc = self.page.locator(sel)
            if loc.count():
                return loc
        return None

    def _last_answer_text(self):
        loc = self._answers()
        if loc is None or not loc.count():
            return ""
        last = loc.last
        # prefer a code block (the JSON) if the answer has one
        code = last.locator("pre code, pre")
        try:
            if code.count():
                return "\n".join(code.nth(i).inner_text() for i in range(code.count()))
            return last.inner_text()
        except Exception:
            return ""

    def _busy(self):
        for sel in PROVIDERS[self.provider]["busy"]:
            try:
                if self.page.locator(sel).count():
                    return True
            except Exception:
                pass
        return False

    def _send_and_wait(self, prompt, box=None):
        cfg = PROVIDERS[self.provider]
        before = self._answers().count() if self._answers() is not None else 0
        box = box or self._first(cfg["input"], timeout=10)
        box.click()
        # insert_text puts the whole prompt in at once (typing 40k characters would take minutes)
        self.page.keyboard.insert_text(prompt)
        time.sleep(1)
        send = self._first(cfg["send"], timeout=5)
        if send is not None and send.is_enabled():
            send.click()
        else:
            self.page.keyboard.press("Enter")

        # wait for a new answer, then until it stops changing and the site is not busy
        end = time.time() + ANSWER_TIMEOUT_MIN * 60
        while time.time() < end:
            loc = self._answers()
            if loc is not None and loc.count() > before:
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"{self.provider} did not answer")
        last, stable_since = None, time.time()
        while time.time() < end:
            time.sleep(1.5)
            text = self._last_answer_text()
            if text != last:
                last, stable_since = text, time.time()
            elif text and not self._busy() and time.time() - stable_since > 4:
                return text
        raise RuntimeError(f"{self.provider} answer did not finish in {ANSWER_TIMEOUT_MIN} min")

    def _ask_web(self, prompt):
        box = self._open_new_chat()
        return self._send_and_wait(prompt, box)


def find_cli(name):
    """A CLI on PATH, or Claude Code's copy bundled with the VS Code extension."""
    found = shutil.which(name)
    if found:
        return found
    if name == "claude":
        bundled = sorted(Path.home().glob(
            ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude*"))
        if bundled:
            return str(bundled[-1])
    return None


def extract_json(text):
    """The JSON object in an AI answer (with or without a ```json fence)."""
    if not text:
        return None
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = fenced + [text[text.find("{"): text.rfind("}") + 1]] if "{" in text else fenced
    for c in candidates:
        try:
            data = json.loads(c)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return None
