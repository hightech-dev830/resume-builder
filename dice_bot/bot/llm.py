"""The resume writers:
  openai        OpenAI API (strict JSON schema)
  deepseek_api  DeepSeek API (OpenAI-compatible, JSON mode)
  deepseek_chat chat.deepseek.com in a browser tab, signed in with your account (no API cost)
  chatgpt_chat  chatgpt.com in a browser tab, signed in with your account (no API cost)
The chat engines type the prompt and read the answer with extension-v2's content/chat.js.
A Chat object keeps one conversation, so correction rounds go to the same chat."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .config import REPO

CHAT_JS = (REPO / "extension-v2" / "content" / "chat.js").read_text(encoding="utf-8")
# chat engine -> (start page, host, name)
CHAT_SITES = {
    "deepseek_chat": ("https://chat.deepseek.com/", "chat.deepseek.com", "DeepSeek"),
    "chatgpt_chat": ("https://chatgpt.com/", "chatgpt.com", "ChatGPT"),
}
# what a site shows when it refuses the browser or the network instead of loading the chat
BLOCKED = (("verify you are human", "a human check (Cloudflare)"), ("just a moment", "a Cloudflare check"),
           ("access denied", "'Access denied'"), ("unable to load site", "'Unable to load site'"),
           ("not available in your country", "'not available in your country'"), ("unusual activity", "'unusual activity'"),
           ("too many requests", "'too many requests'"), ("error 1020", "Cloudflare error 1020 (blocked network)"))


class LLMError(RuntimeError):
    pass


def _post(url: str, key: str, body: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")
        try:
            detail = json.loads(detail).get("error", {}).get("message", detail)
        except Exception:
            pass
        raise LLMError(f"{url.split('/')[2]} answered {e.code}: {str(detail)[:300]}")
    except urllib.error.URLError as e:
        raise LLMError(f"Could not reach {url.split('/')[2]}: {e.reason}")


class Chat:
    """One conversation. send(text) -> the answer text."""

    def __init__(self, settings: dict, browser=None, log=print, schema: dict | None = None, stop=lambda: False):
        self.s = settings
        self.engine = settings["engine"]
        self.browser = browser          # bot.browser.Browser, for deepseek_chat
        self.log = log
        self.schema = schema
        self.stop = stop
        self.messages: list[dict] = []
        self.page = None

    # ---------- APIs ----------
    def _api(self, text: str, json_mode: bool) -> str:
        self.messages.append({"role": "user", "content": text})
        if self.engine == "openai":
            if not self.s.get("openai_key"):
                raise LLMError("Add your OpenAI API key in Settings.")
            model = self.s.get("openai_model") or "gpt-5"
            body = {"model": model, "messages": self.messages, "max_completion_tokens": 24000}
            if json_mode and self.schema:
                body["response_format"] = {"type": "json_schema", "json_schema": {"name": "resume", "strict": True, "schema": self.schema}}
            if model.startswith(("gpt-5", "o")):
                body["reasoning_effort"] = "medium"
            data = _post("https://api.openai.com/v1/chat/completions", self.s["openai_key"], body)
        else:
            if not self.s.get("deepseek_key"):
                raise LLMError("Add your DeepSeek API key in Settings.")
            body = {"model": self.s.get("deepseek_model") or "deepseek-chat", "messages": self.messages, "max_tokens": 8000}
            if json_mode and body["model"] == "deepseek-chat":
                body["response_format"] = {"type": "json_object"}
            data = _post("https://api.deepseek.com/chat/completions", self.s["deepseek_key"], body)
        text_out = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        if not text_out:
            raise LLMError("The model returned an empty answer.")
        self.messages.append({"role": "assistant", "content": text_out})
        return text_out

    # ---------- DeepSeek web chat ----------
    def _state(self):
        try:
            self.page.evaluate(CHAT_JS)
            return self.page.evaluate("window.__abChat.state()")
        except Exception:
            return None

    def _blocked(self) -> str:
        try:
            t = self.page.evaluate("document.title + ' ' + (document.body ? document.body.innerText.slice(0, 3000) : '')").lower()
        except Exception:
            return ""
        return next((why for key, why in BLOCKED if key in t), "")

    def _chat(self, text: str, new: bool, plain: bool) -> str:
        home, host, site = CHAT_SITES[self.engine]
        if not self.browser:
            raise LLMError(f"{site} chat needs the browser.")
        if self.page is None or self.page.is_closed():
            self.page = self.browser.chat_page(home, host)
            new = True
        elif new:
            self.page.goto(home, wait_until="domcontentloaded")
        self.page.bring_to_front()
        st = None
        for i in range(60):
            if self.stop():
                raise LLMError("Stopped")
            st = self._state()
            if st and st.get("ready"):
                break
            if st and st.get("loggedOut") and i > 3:
                raise LLMError(f"Sign in to {site} in the bot's browser (button: Open browser), then start again.")
            if i in (8, 30) and (why := self._blocked()):
                raise LLMError(f"{site} did not load its chat: it shows {why}. This is {site} refusing this network or browser, "
                               "not the bot (common on cloud servers). Solve the check in the tab if it offers one, or use another engine.")
            time.sleep(0.7)
        if not st or not st.get("ready"):
            raise LLMError(f"{site} did not show its chat box. Check the {site} tab in the bot's browser.")
        # mark the answers already in the thread: the new answer is the one without the mark
        # (counting fails in long threads, where the sites drop older messages from the page)
        self.page.evaluate("window.__abChat.markOld()")
        self.page.evaluate(f"window.__abChat.overlay(true, 'Dice Auto Bid is writing with {site}…')")
        try:
            sent = self.page.evaluate("t => window.__abChat.send(t)", text)
            if not sent or not sent.get("ok"):
                raise LLMError(f"Could not send the prompt to {site} ({(sent or {}).get('note', 'no response')}).")
            end = time.time() + 8 * 60
            last, still, started = -1, 0, False
            while time.time() < end:
                if self.stop():
                    raise LLMError("Stopped")
                time.sleep(0.7)
                st = self._state()
                if not st:
                    continue
                if st.get("fresh", 0) > 0 and st["length"] > 0:
                    started = True
                if not started:
                    continue
                still = still + 1 if (st["length"] == last and not st["generating"]) else 0
                last = st["length"]
                if not plain and st.get("jsonReady") and still >= 1:
                    break  # the JSON block closed and parses
                if still >= (3 if plain else 6) and st["length"] > 0:
                    break
            if not started:
                raise LLMError(f"{site} did not answer in time.")
            ans = self.page.evaluate("window.__abChat.lastAnswer()")
            return (ans.get("plain") if plain else ans.get("text")) or ""
        finally:
            try:
                self.page.evaluate("window.__abChat.overlay(false)")
            except Exception:
                pass

    def send(self, text: str, json_mode: bool = True) -> str:
        if self.engine in CHAT_SITES:
            out = self._chat(text, new=not self.messages, plain=not json_mode)
            self.messages.append({"role": "user", "content": text})
            return out
        return self._api(text, json_mode)

    def close(self, keep_tab: bool = True):
        if self.page and not keep_tab:
            try:
                self.page.close()
            except Exception:
                pass
