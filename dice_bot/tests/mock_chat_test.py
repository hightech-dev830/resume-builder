"""The ChatGPT / DeepSeek web-chat engines against mock chat pages (no real accounts):
a ChatGPT-like page (editable prompt box, send button, stop button while streaming, assistant messages)
and a Cloudflare "Just a moment…" page, which must give a clear 'blocked' message.
Run:  .venv\\Scripts\\python tests\\mock_chat_test.py"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot.llm import Chat, LLMError  # noqa: E402

ANSWER = {"label": "Senior Engineer", "summary": "x", "highlights": [], "skills": [], "work": [], "keywords_matched": []}
CHATGPT = """<html><body><main><div id="thread"></div>
<form><div id="prompt-textarea" contenteditable="true" class="ProseMirror" style="min-height:40px;width:600px;border:1px solid #ccc"></div>
<button id="composer-submit-button" data-testid="send-button" type="button">Send</button></form></main>
<script>
const ans = '```json\\n' + %s + '\\n```';
document.getElementById('composer-submit-button').onclick = () => {
  const box = document.getElementById('prompt-textarea'); const q = box.innerText; box.innerHTML = '';
  if (!q.trim()) return;
  const u = document.createElement('div'); u.setAttribute('data-message-author-role', 'user'); u.textContent = q.slice(0, 50); thread.appendChild(u);
  const a = document.createElement('div'); a.setAttribute('data-message-author-role', 'assistant'); thread.appendChild(a);
  const stop = document.createElement('button'); stop.setAttribute('data-testid', 'stop-button'); stop.textContent = 'Stop'; document.body.appendChild(stop);
  let i = 0; const t = setInterval(() => { i += 15; a.innerHTML = '<pre><div>json Copy</div><code>' + ans.slice(0, i).replace(/</g, '&lt;') + '</code></pre>';
    if (i >= ans.length) { clearInterval(t); stop.remove(); } }, 100);
};
</script></body></html>""" % json.dumps(json.dumps(ANSWER))
CLOUDFLARE = "<html><head><title>Just a moment...</title></head><body><h1>chatgpt.com</h1><p>Verify you are human by completing the action below.</p></body></html>"


class B:
    def __init__(self, ctx):
        self.ctx = ctx

    def chat_page(self, url, host):
        p = self.ctx.new_page()
        p.goto(url)
        return p


def main():
    from playwright.sync_api import sync_playwright
    fails = 0
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(tempfile.mkdtemp(), channel="chrome", headless=True)
        page_for = {"html": CHATGPT}
        ctx.route("https://chatgpt.com/**", lambda r: r.fulfill(status=200, content_type="text/html", body=page_for["html"]))
        s = {"engine": "chatgpt_chat"}
        chat = Chat(s, B(ctx), log=print)
        out = chat.send("Write the resume as JSON")
        ok = '"label": "Senior Engineer"' in out.replace("\n", " ") or "Senior Engineer" in out
        print(("  ✔" if ok else "  ✖"), f"ChatGPT chat: typed the prompt, waited for the stop button to go, read {len(out)} chars of JSON")
        fails += not ok
        out2 = chat.send("Fix the problems")  # same conversation, second answer
        ok = out2.count("Senior Engineer") == 1
        print(("  ✔" if ok else "  ✖"), "second message in the same conversation read the newest answer")
        fails += not ok

        page_for["html"] = CLOUDFLARE
        try:
            Chat(s, B(ctx), log=print).send("hello")
            print("  ✖ a Cloudflare page did not raise")
            fails += 1
        except LLMError as e:
            ok = "Cloudflare" in str(e) and "refusing" in str(e)
            print(("  ✔" if ok else "  ✖"), f"Cloudflare page explained: {e}")
            fails += not ok
        ctx.close()
    print("all checks passed" if not fails else f"{fails} check(s) failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
