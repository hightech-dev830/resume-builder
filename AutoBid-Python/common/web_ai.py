"""Ask an AI: through a local AI CLI, your own API key, or a chat web page.

Providers:
    claude_code   "claude -p" (Claude Code, uses your Claude login)   <- recommended
    claude_api    Claude API with your Anthropic key  } same code as Resume Builder / the extension
    openai_api    OpenAI API with your OpenAI key     } (resume_bridge.js ai-complete -> lib/shared/ai.js);
                  keys from Resume Builder settings (Supabase), tokens + cost saved to ai_usage
    cursor        "agent -p" (Cursor Agent desktop CLI, pick a model; your Cursor login)
    cursor_auto   "agent -p --model auto" (same CLI, Auto routing - Cursor picks the model)
    chatgpt / claude / deepseek   the chat WEB page in the bot's Chrome window.
        Not recommended: these sites detect automated browsers (ChatGPT shows a
        Cloudflare "Just a moment..." check) and their terms forbid automated use,
        so your account can be limited. The bot never tries to get around that.

Each question goes to a NEW chat, so jobs never mix. The browser profile is shared with
the bots (common/browser.py), so you log in to the AI sites only once.
"""
import json
import os
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
    # Cursor Agent desktop CLI (https://cursor.com/docs/cli). Ask mode = text only, no file edits.
    # Binary is "agent" (newer) or legacy "cursor-agent". find_cli() resolves either.
    "cursor": ["agent", "-p", "--output-format", "text", "--mode", "ask", "--trust"],
    "cursor_auto": ["agent", "-p", "--output-format", "text", "--mode", "ask", "--trust"],
}

# API platforms -> the provider name in lib/shared/ai.js
API = {"claude_api": "anthropic", "openai_api": "openai"}
BRIDGE = Path(__file__).resolve().parent / "resume_bridge.js"

LOGIN_WAIT_MIN = 5
ANSWER_TIMEOUT_MIN = 6
API_TIMEOUT_MIN = 15      # a full resume with a large model at high effort can take minutes


class WebAI:
    def __init__(self, provider, context=None, model="", task=""):
        """provider: a PROVIDERS key (needs a Playwright browser context), a CLI key or an API key.
        model: model name/alias ("opus", "claude-sonnet-5-5", "gpt-5", ...); "" = default.
        Web chat pages use whatever model is selected on the page.
        task: what the calls are for ("resume" / "relevance"), saved with the API usage."""
        if provider not in PROVIDERS and provider not in CLI and provider not in API:
            raise ValueError(f"unknown AI provider {provider!r}; use one of "
                             f"{', '.join([*CLI, *API, *PROVIDERS])}")
        self.provider = provider
        self.model = (model or "").strip()
        self.context = context
        self.task = task
        self.page = None
        self.last_model = None     # API: the model that actually answered

    @property
    def label(self):
        return f"{self.provider}" + (f" ({self.model})" if self.model else "")

    # ------------------------------------------------------------ public

    def ask(self, prompt, **api_opts):
        """Send one prompt in a fresh chat and return the answer text.
        api_opts (API platforms only): system, schema, max_tokens, effort."""
        if self.provider in API:
            return self._ask_api(prompt, **api_opts)
        if self.provider in CLI:
            return self._ask_cli(prompt)
        return self._ask_web(prompt)

    def ask_json(self, prompt, required_keys=(), **api_opts):
        """ask() and parse a JSON object from the answer (one retry in the same chat).
        API platforms: pass schema=... for a guaranteed-valid JSON answer (structured output)."""
        text = self.ask(prompt, **api_opts)
        data = extract_json(text)
        if data is None or any(k not in data for k in required_keys):
            if self.provider in API:
                text = self.ask(prompt + "\n\nReturn ONLY the JSON object, nothing else.", **api_opts)
            elif self.provider in CLI:
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

    # ------------------------------------------------------------ API (your key)

    def _ask_api(self, prompt, system=None, schema=None, max_tokens=16000, effort="low"):
        """One call with your API key through Resume Builder's own AI code (Node)."""
        req = {"provider": API[self.provider], "model": self.model, "system": system,
               "user": prompt, "schema": schema, "maxTokens": max_tokens, "effort": effort,
               "task": f"autobid-{self.task}" if self.task else "autobid"}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(req, f, ensure_ascii=False)
            path = f.name
        try:
            res = subprocess.run(["node", str(BRIDGE), "ai-complete", path], capture_output=True,
                                 text=True, encoding="utf-8", timeout=API_TIMEOUT_MIN * 60,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except FileNotFoundError:
            raise RuntimeError("Node.js is not installed (needed for the API platforms)")
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{self.provider} did not answer in {API_TIMEOUT_MIN} min")
        finally:
            Path(path).unlink(missing_ok=True)
        if res.returncode != 0:
            first = (res.stderr or "").strip().splitlines()
            raise RuntimeError(first[0][:400] if first else f"{self.provider} call failed")
        out = json.loads(res.stdout)
        self.last_model = out.get("model")
        return out.get("text") or ""

    # ------------------------------------------------------------ CLI

    def _ask_cli(self, prompt):
        cmd = list(CLI[self.provider])
        exe = find_cli(cmd[0])
        if not exe and cmd[0] == "agent":
            # older installs used the name cursor-agent
            exe = find_cli("cursor-agent")
        if not exe:
            hint = ("Install Cursor Agent CLI: in PowerShell run  "
                    "irm 'https://cursor.com/install?win32=true' | iex   "
                    "then  agent login") if cmd[0] == "agent" else ""
            raise RuntimeError(f"'{cmd[0]}' is not installed or not on PATH. {hint}".strip())
        cmd[0] = exe
        if self.provider == "cursor_auto":
            cmd += ["--model", self.model or "auto"]
        elif self.model:
            cmd += ["--model", self.model]
        # run in an empty temp folder so the CLI does not read / edit any project files
        with tempfile.TemporaryDirectory() as tmp:
            res = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                 encoding="utf-8", timeout=ANSWER_TIMEOUT_MIN * 60, cwd=tmp,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if res.returncode != 0:
            err = (res.stderr or res.stdout or "").strip()[:400]
            raise RuntimeError(f"{Path(cmd[0]).name} failed: {err or 'exit ' + str(res.returncode)}")
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
    """A CLI on PATH, or a known install location (Claude Code / Cursor Agent)."""
    found = shutil.which(name)
    if found:
        return found
    if name == "claude":
        bundled = sorted(Path.home().glob(
            ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude*"))
        if bundled:
            return str(bundled[-1])
    if name in ("agent", "cursor-agent"):
        home = Path.home()
        candidates = [
            home / ".local" / "bin" / name,
            home / ".local" / "bin" / f"{name}.exe",
            home / ".cursor" / "bin" / name,
            home / ".cursor" / "bin" / f"{name}.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "cursor-agent" / name,
            Path(os.environ.get("LOCALAPPDATA", "")) / "cursor-agent" / f"{name}.exe",
        ]
        for p in candidates:
            if p.is_file():
                return str(p)
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
