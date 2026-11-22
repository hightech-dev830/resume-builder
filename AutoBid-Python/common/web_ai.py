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
        # Plain home URL is more reliable than temporary-chat (that mode often leaves the
        # composer covered / not clickable, which caused Locator.click timeouts on resumes).
        "url": "https://chatgpt.com/",
        # 2026 ChatGPT UI: #prompt-textarea was removed. Composer is ProseMirror with
        # data-composer-markdown (Free and Plus use the same DOM). Old ids kept as fallback.
        "input": [
            "div.ProseMirror[contenteditable='true'][role='textbox'][data-composer-markdown]",
            "div[contenteditable='true'][role='textbox'][data-composer-markdown]",
            "form[data-chatgpt-composer] [data-composer-markdown][contenteditable='true']",
            "div.ProseMirror[contenteditable='true'][role='textbox']",
            "[contenteditable='true'][role='textbox'][data-composer-markdown]",
            "[contenteditable='true'][role='textbox'][aria-label*='Ask']",
            "[contenteditable='true'][role='textbox'][aria-label*='ChatGPT']",
            "[contenteditable='true'][role='textbox'][aria-placeholder*='Ask']",
            "form[data-type='unified-composer'] #prompt-textarea",
            "#prompt-textarea.ProseMirror",
            "#prompt-textarea[contenteditable='true']",
            "[data-testid='prompt-textarea']",
            "#prompt-textarea",
            "div[contenteditable='true'][data-lexical-editor='true']",
            "textarea[placeholder*='Ask']",
            "textarea",
        ],
        "new_chat": [
            "a[data-testid='create-new-chat-button']",
            "button[data-testid='create-new-chat-button']",
            "a[href='/'][data-discover='true']",
            "[aria-label='New chat']",
            "nav a[href='/']",
        ],
        "send": ["button[data-testid='send-button']", "#composer-submit-button",
                 "button[aria-label*='Send message']", "button[aria-label*='Send']"],
        "busy": ["button[data-testid='stop-button']", "button[aria-label='Stop streaming']",
                 "button[aria-label*='Stop generating']"],
        "answer": [
            "[data-message-author-role='assistant']",
            "div[data-message-author-role='assistant']",
            "[data-role='assistant']",
            "[data-message-author='assistant']",
            ".agent-turn",
        ],
        "user": [
            "[data-message-author-role='user']",
            "div[data-message-author-role='user']",
            "[data-role='user']",
            ".user-turn",
        ],
    },
    "claude": {
        "url": "https://claude.ai/new",
        "input": ["div[contenteditable='true'].ProseMirror", "div[contenteditable='true']"],
        "send": ["button[aria-label='Send message']", "button[aria-label*='Send']"],
        "busy": ["[data-is-streaming='true']", "button[aria-label='Stop response']"],
        "answer": ["div.font-claude-response", "[data-testid='ai-message']",
                   "[data-is-streaming='false']", "div.font-claude-message"],
        "user": ["[data-testid='human-message']", ".font-user-message"],
    },
    "deepseek": {
        "url": "https://chat.deepseek.com/",
        "input": ["textarea#chat-input", "textarea.ds-input", "textarea"],
        "new_chat": [
            "button:has-text('New chat')",
            "a:has-text('New chat')",
            "[class*='new-chat']",
        ],
        "send": [
            "div[role='button'][aria-disabled='false']:has(svg)",
        ],
        "busy": [
            "div[class*='stopping']",
            "button:has-text('Stop')",
        ],
        "answer": [
            "div.ds-markdown",
            "div[class*='markdown']",
            ".ds-message",
        ],
        "user": [],
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

LOGIN_WAIT_MIN = 30   # web chat: wait this long for you to log in (Google OAuth can be slow)
LOGIN_REMIND_MIN = 5  # remind again every N minutes while still waiting
ANSWER_TIMEOUT_MIN = 6
API_TIMEOUT_MIN = 15      # a full resume with a large model at high effort can take minutes

# Text inside a login *dialog* (not the whole page - logged-in ChatGPT still mentions "log in"
# in marketing copy, which used to make the bot wait forever after a real login).
_LOGIN_DIALOG = re.compile(
    r"log\s*in or sign\s*up|sign\s*in to (continue|chatgpt|claude|deepseek)|"
    r"continue with (google|apple|microsoft)|create (your )?account|"
    r"enter your (email|password)",
    re.I)

# Composer selectors that only appear when you are actually logged in enough to chat.
_LOGGED_IN_COMPOSER = {
    "chatgpt": [
        "div.ProseMirror[contenteditable='true'][role='textbox'][data-composer-markdown]",
        "div[contenteditable='true'][role='textbox'][data-composer-markdown]",
        "div.ProseMirror[contenteditable='true'][role='textbox']",
        "[contenteditable='true'][role='textbox'][aria-label*='Ask']",
        "form[data-type='unified-composer'] #prompt-textarea",
        "#prompt-textarea",
        "[data-testid='prompt-textarea']",
        "#composer-submit-button",
        "button[data-testid='send-button']",
    ],
    "claude": ["div[contenteditable='true'].ProseMirror",
               "button[aria-label='Send message']"],
    "deepseek": ["textarea#chat-input"],
}

# Extra proof of a saved ChatGPT session (sidebar profile / history) when the
# composer is slow to paint or Playwright thinks it is not "visible".
_SESSION_MARKERS = {
    "chatgpt": [
        "nav a[href='/']",
        "[data-testid='accounts-profile-button']",
        "button[data-testid='profile-button']",
        "[data-composer-markdown]",
        "div.ProseMirror[role='textbox']",
        "text=New chat",
        "text=Ask ChatGPT",
        "text=Where should we begin",
    ],
    "claude": ["div[contenteditable='true'].ProseMirror"],
    "deepseek": ["textarea#chat-input"],
}


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
        API platforms: pass schema=... for a guaranteed-valid JSON answer (structured output).
        required_keys: string keys that must exist (essay answers use \"0\",\"1\",...)."""
        keys = [str(k) for k in required_keys]
        text = self.ask(prompt, **api_opts)
        data = coerce_keyed_json(text, keys)
        if data is None or any(k not in data for k in keys):
            need = ", ".join(repr(k) for k in keys) or "(any object)"
            example = "{" + ", ".join(f'"{k}": "answer {k}"' for k in keys) + "}"
            fix = (
                f"Your reply was missing required keys [{need}]. "
                f"Reply with ONLY one JSON object inside a ```json code block, exactly like:\n"
                f"{example}\n"
                "Keys must be those strings. No nesting, no arrays, no question titles as keys."
            )
            if self.provider in API:
                text = self.ask(prompt + "\n\n" + fix, **api_opts)
            elif self.provider in CLI:
                text = self.ask(prompt + "\n\n" + fix)
            else:
                text = self._send_and_wait(fix)
            data = coerce_keyed_json(text, keys)
        if data is None:
            raise RuntimeError(f"{self.provider} did not return valid JSON")
        missing = [k for k in keys if k not in data or not str(data.get(k) or "").strip()]
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
        """First visible/usable element for any of the selectors (waits up to timeout seconds)."""
        end = time.time() + timeout
        while True:
            for sel in selectors:
                loc = self.page.locator(sel)
                try:
                    if self._element_usable(loc):
                        # prefer the last match (main composer, not a hidden clone)
                        n = loc.count()
                        return loc.nth(n - 1) if n else loc.first
                except Exception:
                    pass
            # ChatGPT Free/Plus 2026: role=textbox named "Ask ChatGPT"
            if self.provider == "chatgpt":
                box = self._chatgpt_composer_by_role()
                if box is not None:
                    return box
            if time.time() >= end:
                return None
            time.sleep(0.5)

    def _chatgpt_composer_by_role(self):
        """Find the Ask ChatGPT box when CSS ids are gone (current Free UI)."""
        try:
            loc = self.page.get_by_role("textbox", name=re.compile(r"Ask|ChatGPT|Message|prompt", re.I))
            if self._element_usable(loc):
                return loc.last if loc.count() > 1 else loc.first
        except Exception:
            pass
        try:
            loc = self.page.locator("[contenteditable='true'][role='textbox']")
            if not loc.count():
                return None
            # pick the largest visible textbox (the main composer)
            best, best_area = None, 0
            for i in range(min(loc.count(), 8)):
                el = loc.nth(i)
                try:
                    box = el.bounding_box()
                    if not box:
                        continue
                    area = box.get("width", 0) * box.get("height", 0)
                    if area > best_area:
                        best, best_area = el, area
                except Exception:
                    continue
            return best if best_area > 200 else None
        except Exception:
            return None

    def _dump_composer_debug(self):
        """One-line hint when the composer cannot be found (DOM churn / Free UI)."""
        try:
            info = self.page.evaluate(
                """() => {
                    const nodes = [...document.querySelectorAll(
                        '[contenteditable="true"], textarea, [role="textbox"], [data-composer-markdown], #prompt-textarea')];
                    return nodes.slice(0, 12).map(el => ({
                        tag: el.tagName, id: el.id || '',
                        role: el.getAttribute('role') || '',
                        aria: (el.getAttribute('aria-label') || '').slice(0, 40),
                        ph: (el.getAttribute('aria-placeholder') || el.getAttribute('placeholder') || '').slice(0, 40),
                        cls: (el.className || '').toString().slice(0, 60),
                        w: Math.round(el.getBoundingClientRect().width),
                        h: Math.round(el.getBoundingClientRect().height),
                    }));
                }""")
            print(f"    ! chatgpt composer debug: {info!r}")
        except Exception as e:
            print(f"    ! chatgpt composer debug failed: {e}")

    def _page_url(self):
        try:
            return self.page.url or ""
        except Exception:
            return ""

    def _on_ai_site(self, url=None):
        url = url if url is not None else self._page_url()
        if self.provider == "chatgpt":
            return bool(re.search(r"chatgpt\.com|chat\.openai\.com", url, re.I))
        if self.provider == "claude":
            return bool(re.search(r"claude\.ai", url, re.I))
        if self.provider == "deepseek":
            return bool(re.search(r"chat\.deepseek\.com", url, re.I))
        return False

    def _has_session_markers(self):
        """True when the saved browser profile already has a logged-in AI session."""
        for sel in _SESSION_MARKERS.get(self.provider, []):
            try:
                loc = self.page.locator(sel)
                if loc.count() > 0:
                    return True
            except Exception:
                continue
        return False

    def _element_usable(self, loc):
        """Attached is enough; Playwright 'visible' is flaky on ChatGPT's Lexical composer."""
        try:
            if not loc.count():
                return False
            el = loc.first
            if el.is_visible():
                return True
            # attached in DOM with a box (opacity/animation can fool is_visible)
            box = el.bounding_box()
            return bool(box and box.get("width", 0) > 8 and box.get("height", 0) > 8)
        except Exception:
            return False

    def _login_wall(self):
        """True only when a real login dialog / auth URL is blocking chat.

        Must not scan the whole page for 'Log in' - a logged-in ChatGPT page still
        contains that phrase in side copy, which blocked the bot after a real login.
        A saved session in the bot profile (.browser-profile) counts - your normal
        Chrome does not need a second login.
        """
        url = self._page_url()
        # Any ChatGPT/Claude chat surface with a composer or session chrome = logged in
        if self._on_ai_site(url) and (self._logged_in_composer() or self._has_session_markers()):
            return False
        if re.search(r"chatgpt\.com/(c|g)/|claude\.ai/chat/|chat\.deepseek\.com/a/", url, re.I):
            return False
        if re.search(r"/(login|signin|sign-in|signup|sign-up|auth)(/|$|\?)", url, re.I):
            # still allow through if the composer already rendered after OAuth
            if self._logged_in_composer():
                return False
            return True
        # only visible dialogs / modals, not the whole body
        for sel in ("[role='dialog']", "[data-testid='modal-no-auth']", "form[action*='login']"):
            try:
                loc = self.page.locator(sel)
                for i in range(min(loc.count(), 4)):
                    el = loc.nth(i)
                    if not el.is_visible():
                        continue
                    text = (el.inner_text(timeout=1000) or "")[:800]
                    if _LOGIN_DIALOG.search(text):
                        # Upgrade / marketing dialogs can mention "sign up"; ignore if we can chat
                        if self._logged_in_composer() or self._has_session_markers():
                            return False
                        return True
            except Exception:
                pass
        return False

    def _logged_in_composer(self):
        """Chat composer present (saved session) - visibility is best-effort only."""
        for sel in _LOGGED_IN_COMPOSER.get(self.provider, []):
            try:
                loc = self.page.locator(sel)
                if self._element_usable(loc):
                    return True
            except Exception:
                pass
        return False

    def _chat_ready(self):
        """Input box is usable. Composer / session markers beat a false login-wall."""
        if self._login_wall() and not self._logged_in_composer():
            return None
        # Prefer the real logged-in composer; fall back to the provider's input list
        box = self._first(PROVIDERS[self.provider]["input"], timeout=1)
        if box is not None:
            return box
        # _first requires is_visible(); retry with attached+box check
        for sel in PROVIDERS[self.provider]["input"]:
            try:
                loc = self.page.locator(sel)
                if self._element_usable(loc):
                    return loc.first
            except Exception:
                continue
        return None

    def _adopt_ai_page(self):
        """Use an existing bot-Chrome tab that already shows the AI site (saved session)."""
        if self.context is None:
            return False
        try:
            pages = list(self.context.pages)
        except Exception:
            return False
        for p in pages:
            try:
                if p.is_closed():
                    continue
                url = p.url or ""
                if not self._on_ai_site(url):
                    continue
                self.page = p
                if self._logged_in_composer() or self._has_session_markers() or self._chat_ready():
                    try:
                        self.page.bring_to_front()
                    except Exception:
                        pass
                    return True
            except Exception:
                continue
        return False

    def _wait_until_logged_in(self):
        """Pause until a usable session exists in the bot Chrome.

        The bot profile already remembers ChatGPT login - do NOT keep refreshing a
        logged-in homepage (that was the 'require login and refresh' bug)."""
        from browser import PROFILE_DIR
        cfg = PROVIDERS[self.provider]
        self._adopt_ai_page()
        try:
            self.page.bring_to_front()
        except Exception:
            pass

        # Fast path: session cookie already loaded, composer on screen
        box = self._chat_ready()
        if box is not None and not self._login_wall():
            print(f"    using saved {self.provider} login in bot Chrome - continuing")
            return box
        if self._on_ai_site() and (self._logged_in_composer() or self._has_session_markers()):
            box = self._first(cfg["input"], timeout=5) or self._chat_ready()
            if box is not None:
                print(f"    using saved {self.provider} login in bot Chrome - continuing")
                return box

        print(f"    waiting for you to log in to {self.provider} in the BOT's Chrome "
              f"(not your normal Chrome) - profile: {PROFILE_DIR}  "
              f"(up to {LOGIN_WAIT_MIN} min) ...")
        notify(f"AutoBid: log in to {self.provider} in the BOT Chrome",
               f"A separate Chrome window opened by AutoBid. Log in THERE "
               f"(your usual Chrome login does not count). Waits up to {LOGIN_WAIT_MIN} min.")
        end = time.time() + LOGIN_WAIT_MIN * 60
        next_remind = time.time() + LOGIN_REMIND_MIN * 60
        last_reload = time.time()
        while time.time() < end:
            time.sleep(2)
            self._adopt_ai_page()
            try:
                self.page.bring_to_front()
            except Exception:
                pass
            self._dismiss_overlays()
            url = self._page_url()
            # Home or conversation - either is fine once the composer/session is there
            if self._on_ai_site(url) and (self._logged_in_composer() or self._has_session_markers()):
                box = self._chat_ready() or self._first(cfg["input"], timeout=3)
                if box is not None:
                    print(f"    logged in to {self.provider} - continuing")
                    notify(f"AutoBid: {self.provider} ready", "Login detected - the bot continues.")
                    return box
            box = self._chat_ready()
            if box is not None and not self._login_wall():
                print(f"    logged in to {self.provider} - continuing")
                notify(f"AutoBid: {self.provider} ready", "Login detected - the bot continues.")
                return box
            if time.time() >= next_remind:
                left = max(1, int((end - time.time()) / 60))
                print(f"    still waiting for {self.provider} login in the BOT Chrome "
                      f"({left} min left) - your other Chrome window does not count ...")
                notify(f"AutoBid: still waiting - {self.provider}",
                       f"Log in in the AutoBid Chrome window, not your normal one ({left} min left).")
                next_remind = time.time() + LOGIN_REMIND_MIN * 60
            # Only reload when we are clearly NOT on a usable AI page (auth / blank / other site).
            # Reloading a logged-in chatgpt.com home was wiping the session UI every 30s.
            if time.time() - last_reload > 45:
                last_reload = time.time()
                on_site = self._on_ai_site()
                has_session = self._logged_in_composer() or self._has_session_markers()
                if not on_site or self._login_wall():
                    try:
                        self.page.goto(cfg["url"], wait_until="domcontentloaded", timeout=60000)
                    except Exception:
                        pass
                elif on_site and not has_session:
                    # Soft reload once if the site loaded but session chrome never appeared
                    try:
                        self.page.reload(wait_until="domcontentloaded", timeout=60000)
                    except Exception:
                        pass
        raise RuntimeError(
            f"not logged in to {self.provider} after {LOGIN_WAIT_MIN} min - "
            f"log in in the bot's Chrome (AutoBid-Python/.browser-profile), then run again")

    def _dismiss_overlays(self):
        """Close cookie / upgrade / 'Got it' dialogs that block the ChatGPT composer."""
        for sel in [
            "button:has-text('Accept all')", "button:has-text('Accept')",
            "button:has-text('Got it')", "button:has-text('Okay')",
            "button:has-text('OK')", "button:has-text('Close')",
            "button:has-text('Not now')", "button:has-text('Stay logged out')",
            "[aria-label='Close']", "[data-testid='close-button']",
        ]:
            try:
                loc = self.page.locator(sel)
                if loc.count() and loc.first.is_visible():
                    loc.first.click(timeout=800, force=True)
                    time.sleep(0.3)
            except Exception:
                pass
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass

    def _click_new_chat(self):
        """Start a blank ChatGPT thread so jobs do not mix."""
        for sel in PROVIDERS[self.provider].get("new_chat") or []:
            try:
                loc = self.page.locator(sel)
                if not loc.count():
                    continue
                btn = loc.first
                if btn.is_visible():
                    btn.click(timeout=2000, force=True)
                    time.sleep(0.8)
                    return True
            except Exception:
                continue
        # fallback: go home (ChatGPT opens a fresh composer there)
        try:
            self.page.goto(PROVIDERS[self.provider]["url"], wait_until="domcontentloaded",
                           timeout=60000)
            time.sleep(0.5)
            return True
        except Exception:
            return False

    def _open_new_chat(self):
        cfg = PROVIDERS[self.provider]
        # Prefer an existing bot-Chrome tab that already has the saved ChatGPT session
        # (avoids about:blank + false "please log in" while another tab is already in).
        if not self._adopt_ai_page():
            if self.page is None or self.page.is_closed():
                self.page = self.context.new_page()
            self.page.goto(cfg["url"], wait_until="domcontentloaded", timeout=60000)
        elif not self._chat_ready() and not self._logged_in_composer():
            try:
                self.page.goto(cfg["url"], wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass
        self._dismiss_overlays()
        box = self._chat_ready()
        if box is None and (self._login_wall() or not (self._logged_in_composer() or self._has_session_markers())):
            # not logged in yet: wait for you (will not refresh a saved session homepage)
            box = self._wait_until_logged_in()
            try:
                self.page.goto(cfg["url"], wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass
            self._dismiss_overlays()
            box = self._chat_ready() or self._first(cfg["input"], timeout=20)
        if box is None:
            # Session markers present but composer slow to paint - wait longer (SPA)
            box = self._first(cfg["input"], timeout=25)
        if box is None and self.provider == "chatgpt":
            box = self._chatgpt_composer_by_role()
        if box is None:
            if self.provider == "chatgpt":
                self._dump_composer_debug()
            raise RuntimeError(
                f"logged in to {self.provider}, but the chat box did not appear "
                f"(ChatGPT Free is fine - the page DOM likely changed; see composer debug above)")
        if self.provider == "chatgpt":
            self._click_new_chat()
            self._dismiss_overlays()
            box = (self._chat_ready() or self._first(cfg["input"], timeout=15)
                   or self._chatgpt_composer_by_role() or box)
        elif self.provider == "deepseek":
            # Always start a blank DeepSeek thread so resume JSON is not mixed into essays
            try:
                self.page.goto(cfg["url"], wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass
            self._dismiss_overlays()
            self._click_new_chat()
            self._dismiss_overlays()
            box = self._chat_ready() or self._first(cfg["input"], timeout=15) or box
        return box

    def _locate_any(self, selectors):
        """First matching locator on the page or any iframe (ChatGPT sometimes nests the thread)."""
        if not selectors:
            return None
        roots = [self.page] + list(self.page.frames)
        for root in roots:
            for sel in selectors:
                try:
                    loc = root.locator(sel)
                    if loc.count():
                        return loc
                except Exception:
                    continue
        return None

    def _answers(self):
        return self._locate_any(PROVIDERS[self.provider]["answer"])

    def _users(self):
        return self._locate_any(PROVIDERS[self.provider].get("user") or [])

    def _count(self, loc):
        try:
            return loc.count() if loc is not None else 0
        except Exception:
            return 0

    def _last_answer_text(self):
        loc = self._answers()
        if loc is None or not self._count(loc):
            # last-resort: any fenced JSON already on the page (selector churn)
            return self._page_json_fallback()
        last = loc.last
        try:
            # prefer a code block (the JSON) if the answer has one
            code = last.locator("pre code, pre, code")
            if code.count():
                parts = []
                for i in range(code.count()):
                    try:
                        parts.append(code.nth(i).inner_text(timeout=2000))
                    except Exception:
                        pass
                text = "\n".join(p for p in parts if p and p.strip())
                if text.strip():
                    return text
            # markdown / prose body inside the assistant turn
            for inner_sel in [".markdown", ".prose", "[class*='markdown']"]:
                try:
                    inner = last.locator(inner_sel)
                    if inner.count():
                        t = inner.last.inner_text(timeout=2000)
                        if t and t.strip():
                            return t
                except Exception:
                    pass
            return last.inner_text(timeout=3000) or ""
        except Exception:
            return self._page_json_fallback()

    def _page_json_fallback(self):
        """Grab ```json ... ``` from the whole page when role selectors miss the bubble."""
        try:
            html = self.page.inner_text("body", timeout=3000) or ""
        except Exception:
            return ""
        fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", html, re.I)
        objs = []
        for block in fenced:
            objs.extend(_balanced_objects(block))
        if not objs:
            objs = _balanced_objects(html)
        if objs:
            # prefer the longest object (resume JSON is big; essay JSON is smaller)
            return "```json\n" + max(objs, key=len) + "\n```"
        return ""

    def _busy(self):
        for sel in PROVIDERS[self.provider]["busy"]:
            try:
                loc = self.page.locator(sel)
                if loc.count() and loc.first.is_visible():
                    return True
            except Exception:
                pass
        return False

    def _composer_text(self, box):
        try:
            return (box.evaluate(
                """el => {
                    if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') return el.value || '';
                    return (el.innerText || el.textContent || '');
                }""") or "").strip()
        except Exception:
            try:
                return (box.inner_text(timeout=1000) or "").strip()
            except Exception:
                return ""

    def _focus_composer(self, box):
        """Focus without Playwright's actionability click (overlays used to time out here)."""
        self._dismiss_overlays()
        try:
            box.evaluate("el => { el.scrollIntoView({block:'center'}); el.focus(); }")
            return True
        except Exception:
            pass
        for kwargs in ({"timeout": 1500, "force": True}, {"timeout": 1500}):
            try:
                box.click(**kwargs)
                return True
            except Exception:
                continue
        return False

    def _fill_composer(self, box, prompt):
        """Put the full prompt in the chat box (ChatGPT Lexical / ProseMirror safe).

        Never hard-depends on a normal click - that is what timed out on resume prompts
        (Locator.click 5s/30s) when a dialog covered the composer.
        """
        self._dismiss_overlays()
        # re-resolve in case the page refreshed / New chat swapped the node
        fresh = self._chat_ready() or self._first(PROVIDERS[self.provider]["input"], timeout=8)
        if fresh is not None:
            box = fresh
        self._focus_composer(box)

        # clear leftover draft via JS (no Ctrl+A click path)
        try:
            box.evaluate(
                """el => {
                    el.focus();
                    if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
                        el.value = '';
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                    } else if (document.execCommand) {
                        document.execCommand('selectAll', false, null);
                        document.execCommand('delete', false, null);
                    } else {
                        el.textContent = '';
                        el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'deleteContent' }));
                    }
                }""")
        except Exception:
            try:
                plat = self.page.evaluate("() => navigator.platform || ''") or ""
                modifier = "Meta" if str(plat).lower().startswith("mac") else "Control"
                self.page.keyboard.press(f"{modifier}+A")
                self.page.keyboard.press("Backspace")
            except Exception:
                pass

        # ChatGPT's Lexical editor: keyboard.insert_text keeps the full multiline prompt;
        # Playwright fill() often collapses it or waits 30s on a covered node.
        inserted = False
        try:
            self.page.keyboard.insert_text(prompt)
            inserted = True
        except Exception:
            inserted = False
        if not inserted or len(self._composer_text(box)) < min(80, max(20, len(prompt) // 10)):
            try:
                # CDP-style paste via execCommand after focus
                box.evaluate(
                    """(el, text) => {
                        el.focus();
                        if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
                            el.value = text;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            return;
                        }
                        const ok = document.execCommand && document.execCommand('insertText', false, text);
                        if (!ok) {
                            el.textContent = text;
                            el.dispatchEvent(new InputEvent('input', {
                                bubbles: true, data: text, inputType: 'insertText' }));
                        }
                    }""",
                    prompt,
                )
            except Exception:
                try:
                    box.fill(prompt, timeout=8000, force=True)
                except Exception as e:
                    raise RuntimeError(
                        f"{self.provider} composer could not take the prompt "
                        f"({type(e).__name__}: {str(e).splitlines()[0][:120]})") from e

        time.sleep(0.6)
        got = self._composer_text(box)
        if len(got) < min(40, max(10, len(prompt) // 20)):
            raise RuntimeError(
                f"{self.provider} composer stayed empty after fill "
                f"(got {len(got)} chars, prompt {len(prompt)} chars) - "
                f"dismiss any ChatGPT popup in the bot Chrome and retry")

    def _click_send(self, cfg):
        self._dismiss_overlays()
        send = self._first(cfg["send"], timeout=8)
        if send is not None:
            # ChatGPT enables the send button only after input settles
            for _ in range(40):
                try:
                    if send.is_visible() and send.is_enabled():
                        try:
                            send.click(timeout=2000, force=True)
                            return True
                        except Exception:
                            try:
                                send.evaluate("el => el.click()")
                                return True
                            except Exception:
                                pass
                except Exception:
                    pass
                time.sleep(0.25)
        # Enter on the composer (not a covered Send button)
        try:
            self.page.keyboard.press("Enter")
            return True
        except Exception:
            return False

    def _send_and_wait(self, prompt, box=None):
        cfg = PROVIDERS[self.provider]
        # never type into the page while a login dialog is up
        if self._login_wall() or box is None:
            box = self._wait_until_logged_in()
        self._dismiss_overlays()
        before_ans = self._count(self._answers())
        before_user = self._count(self._users())
        before_json = self._page_json_fallback()
        box = (self._chat_ready() or self._first(cfg["input"], timeout=10) or box)
        if box is None:
            raise RuntimeError(f"{self.provider} chat box not found (are you logged in?)")

        try:
            self._fill_composer(box, prompt)
        except RuntimeError:
            # one recovery: new chat page + try again
            self._open_new_chat()
            box = self._chat_ready() or self._first(cfg["input"], timeout=15)
            if box is None:
                raise
            self._fill_composer(box, prompt)
        if not self._click_send(cfg):
            raise RuntimeError(f"{self.provider} send button not found")

        # confirm the prompt actually left the composer (user bubble or empty box)
        end = time.time() + ANSWER_TIMEOUT_MIN * 60
        sent = False
        probe_end = time.time() + 45
        while time.time() < probe_end:
            if self._count(self._users()) > before_user:
                sent = True
                break
            if self._busy():
                sent = True
                break
            if self._count(self._answers()) > before_ans:
                sent = True
                break
            time.sleep(0.5)
        if not sent:
            # one more Enter attempt
            try:
                self.page.keyboard.press("Enter")
            except Exception:
                pass
            time.sleep(2)
            if (self._count(self._users()) <= before_user
                    and self._count(self._answers()) <= before_ans
                    and not self._busy()):
                raise RuntimeError(
                    f"{self.provider} did not send the prompt "
                    f"(composer/send failed - try a shorter prompt or re-login)")

        # wait for a new answer (role node, or a new ```json block on the page)
        while time.time() < end:
            if self._count(self._answers()) > before_ans:
                break
            now_json = self._page_json_fallback()
            if now_json and now_json != before_json:
                break
            if self._busy():
                # streaming started even if role attr is missing - keep waiting for text
                time.sleep(1)
                if self._last_answer_text().strip():
                    break
            time.sleep(1)
        else:
            raise RuntimeError(f"{self.provider} did not answer")

        last, stable_since = None, time.time()
        saw_busy = False
        while time.time() < end:
            time.sleep(1.2)
            busy = self._busy()
            if busy:
                saw_busy = True
            text = self._last_answer_text()
            if text != last:
                last, stable_since = text, time.time()
            elif text and text.strip() and not busy and time.time() - stable_since > (2 if saw_busy else 4):
                return text
        if last and last.strip():
            return last
        raise RuntimeError(f"{self.provider} answer did not finish in {ANSWER_TIMEOUT_MIN} min")

    def _ask_web(self, prompt):
        box = self._open_new_chat()
        try:
            return self._send_and_wait(prompt, box)
        except Exception as e:
            msg = str(e).splitlines()[0][:180]
            # Playwright actionability errors used to surface as bare Locator.click timeouts
            if "Locator.click" in msg or "Timeout" in msg or "composer" in msg.lower():
                print(f"    ! {self.provider} UI glitch ({msg}) - opening a fresh chat and retrying once ...")
                self._dismiss_overlays()
                box = self._open_new_chat()
                return self._send_and_wait(prompt, box)
            raise


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


def _balanced_objects(text):
    """Top-level {...} slices in text (handles nested braces / strings)."""
    out, start, depth, in_str, esc = [], None, 0, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                out.append(text[start:i + 1])
                start = None
    return out


def extract_json(text, preferred_keys=()):
    """The JSON object/array in an AI answer (with or without a ```json fence).

    If preferred_keys are given, pick the candidate that covers the most of them
    (DeepSeek often emits several JSON blobs; the first is not always the answers)."""
    if not text:
        return None
    raw_bits = []
    for block in re.findall(r"```(?:json)?\s*([\s\S]*?)```", text, re.I):
        raw_bits.extend(_balanced_objects(block) or [block.strip()])
    # also bare arrays
    for block in re.findall(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text, re.I):
        raw_bits.append(block)
    raw_bits.extend(_balanced_objects(text))
    arr = re.search(r"\[[\s\S]*\]", text)
    if arr:
        raw_bits.append(arr.group(0))

    preferred = [str(k) for k in preferred_keys]
    parsed, seen = [], set()
    for c in raw_bits:
        c = (c or "").strip()
        if not c or c in seen:
            continue
        seen.add(c)
        try:
            data = json.loads(c)
        except Exception:
            continue
        if isinstance(data, (dict, list)):
            parsed.append(data)

    if not parsed:
        return None
    if not preferred:
        for data in parsed:
            if isinstance(data, dict):
                return data
        return parsed[0]

    def score(data):
        norm = normalize_keyed_answers(data, preferred)
        if not norm:
            return -1
        return sum(1 for k in preferred if str(norm.get(k) or "").strip())

    best = max(parsed, key=score)
    if score(best) < 0:
        for data in parsed:
            if isinstance(data, dict):
                return data
        return parsed[0]
    return best


def _as_answer_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for k in ("answer", "text", "value", "response", "content"):
            if isinstance(value.get(k), str) and value[k].strip():
                return value[k]
        for v in value.values():
            if isinstance(v, str) and v.strip():
                return v
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "\n".join(_as_answer_text(v) for v in value if _as_answer_text(v))
    return str(value)


def normalize_keyed_answers(data, required_keys):
    """Map common AI shapes into {\"0\": \"...\", \"1\": \"...\"}.

    DeepSeek often returns arrays, 1-based keys, or {\"answers\": [...]} instead of
    the exact essay schema - that used to show as: answer is missing ['0']."""
    keys = [str(k) for k in required_keys]
    if not keys:
        return data if isinstance(data, dict) else None
    n = len(keys)

    if isinstance(data, list):
        if len(data) >= n:
            return {keys[i]: _as_answer_text(data[i]) for i in range(n)}
        return None

    if not isinstance(data, dict):
        return None

    # exact keys already present
    if all(k in data for k in keys):
        return {k: _as_answer_text(data[k]) for k in keys}

    # wrapped: {"answers": [...]} / {"result": {"0": "..."}}
    for wrap in ("answers", "response", "result", "data", "items", "output"):
        if wrap in data:
            nested = normalize_keyed_answers(data[wrap], keys)
            if nested and all(str(nested.get(k) or "").strip() for k in keys):
                return nested

    # alternate key spellings: question_0 / q0 (do NOT map 0→1 here; that collides with 1-based)
    out = {}
    for k in keys:
        found = None
        alts = [k, f"question_{k}", f"question{k}", f"q{k}", f"Q{k}", f"a{k}", f"A{k}"]
        for alt in alts:
            if alt in data and str(_as_answer_text(data[alt])).strip():
                found = _as_answer_text(data[alt])
                break
        if found is not None:
            out[k] = found
    if len(out) == n:
        return out

    # 1-based only: {"1": "...", "2": "..."} when "0" is absent
    if keys and keys[0] == "0" and "0" not in data and all(str(i + 1) in data for i in range(n)):
        return {keys[i]: _as_answer_text(data[str(i + 1)]) for i in range(n)}

    # same number of stringy values, preserve insertion order
    vals = [_as_answer_text(v) for v in data.values()]
    if len(vals) == n and all(v.strip() for v in vals):
        return {keys[i]: vals[i] for i in range(n)}

    return out if len(out) == n else None


def coerce_keyed_json(text, required_keys=()):
    """extract_json + normalize into the required key map (or None)."""
    keys = [str(k) for k in required_keys]
    raw = extract_json(text, preferred_keys=keys)
    if raw is None:
        return None
    if not keys:
        return raw if isinstance(raw, dict) else None
    return normalize_keyed_answers(raw, keys)
