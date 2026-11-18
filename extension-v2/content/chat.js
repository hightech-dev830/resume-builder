// Injected into chatgpt.com / chat.deepseek.com while a resume is being generated.
// Types the prompt into the chat box, sends it, and reads the newest answer back.
// lib/chat.js drives it from the side panel; the sites change their markup now and then, so every
// lookup has a generic fallback (largest text box, last markdown block).
(() => {
  if (window.__abChat) return;

  const host = location.host;
  const site = /chatgpt\.com|chat\.openai\.com/.test(host) ? 'chatgpt' : /deepseek\.com/.test(host) ? 'deepseek' : 'other';
  const visible = (el) => {
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2 && getComputedStyle(el).visibility !== 'hidden';
  };
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  function composer() {
    const known = site === 'chatgpt'
      ? ['#prompt-textarea', 'div[contenteditable="true"].ProseMirror', 'textarea[name="prompt-textarea"]']
      : site === 'deepseek' ? ['textarea#chat-input', 'textarea[placeholder]'] : [];
    for (const sel of known) {
      const el = $$(sel).find(visible);
      if (el) return el;
    }
    // fallback: the biggest visible text box
    return $$('textarea, [contenteditable="true"]').filter(visible)
      .sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width)[0] || null;
  }

  function answers() {
    if (site === 'chatgpt') return $$('[data-message-author-role="assistant"]');
    if (site === 'deepseek') {
      // the visible answer, not the "thinking" block of DeepThink
      return $$('.ds-markdown').filter((el) => !el.closest('[class*="think" i]') && !el.parentElement?.closest('.ds-markdown'));
    }
    return $$('.markdown, [class*="markdown" i]').filter((el) => !el.parentElement?.closest('.markdown, [class*="markdown" i]'));
  }

  function generating() {
    if (site === 'chatgpt') return !!$$('[data-testid="stop-button"], button[aria-label*="Stop" i]').find(visible);
    // DeepSeek has no stable "stop" marker (its class names are hashed); the driver waits until the
    // answer's length has held still for several seconds instead
    return !!$$('[aria-label*="Stop generating" i], [title*="Stop" i]').find(visible);
  }

  function loggedOut() {
    if (site === 'deepseek') return /sign_in|login/.test(location.pathname) || !!$$('input[type="password"]').find(visible);
    if (site === 'chatgpt') return /auth|login/.test(location.pathname) && !composer();
    return false;
  }

  function lastAnswer() {
    const all = answers();
    const el = all[all.length - 1];
    if (!el) return { text: '', count: 0 };
    // code blocks give the raw JSON (their headers such as "json  Copy" are outside <code>)
    const codes = $$('code', el).map((c) => c.innerText).filter((t) => /[{[]/.test(t)).sort((a, b) => b.length - a.length);
    const text = el.innerText || '';
    return { text: codes[0] && codes[0].length > 200 ? '```json\n' + codes[0] + '\n```' : text, plain: text, count: all.length };
  }

  function state() {
    const c = composer();
    const a = lastAnswer();
    return { site, ready: !!c && !loggedOut(), loggedOut: loggedOut(), generating: generating(), count: a.count, length: a.text.length, url: location.href };
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function setText(el, text) {
    el.focus();
    if (el.tagName === 'TEXTAREA') {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
      setter.call(el, text);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      return el.value.length > 0;
    }
    // contenteditable (ChatGPT's ProseMirror): select all, then insert as if typed
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    sel.removeAllRanges();
    sel.addRange(range);
    document.execCommand('insertText', false, text);
    if (!el.innerText.trim()) {
      // older editors: paste event
      const dt = new DataTransfer();
      dt.setData('text/plain', text);
      el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
    }
    return el.innerText.trim().length > 0;
  }

  function sendButton(c) {
    if (site === 'chatgpt') {
      const b = $$('[data-testid="send-button"], #composer-submit-button, button[aria-label*="Send" i]').find(visible);
      if (b) return b;
    }
    let box = c;
    for (let i = 0; i < 5 && box; i++, box = box.parentElement) {
      const cands = $$('button, [role="button"]', box).filter((b) => visible(b) && !b.contains(c));
      const named = cands.find((b) => /send/i.test(`${b.getAttribute('aria-label') || ''} ${b.title || ''} ${b.dataset.testid || ''}`));
      if (named) return named;
    }
    return null;
  }

  async function send(text) {
    const c = composer();
    if (!c) return { ok: false, note: 'chat box not found' };
    if (!setText(c, text)) return { ok: false, note: 'could not type into the chat box' };
    await sleep(600);
    for (let i = 0; i < 20; i++) {
      const b = sendButton(c);
      if (b && !b.disabled && b.getAttribute('aria-disabled') !== 'true') {
        b.click();
        return { ok: true, how: 'button' };
      }
      await sleep(250);
    }
    // Enter sends on both sites (Shift+Enter is a new line)
    c.focus();
    for (const type of ['keydown', 'keypress', 'keyup']) {
      c.dispatchEvent(new KeyboardEvent(type, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true }));
    }
    return { ok: true, how: 'enter' };
  }

  // a dimmed note on top of the chat while it works (clicks still go through)
  function overlay(on, msg = '') {
    let el = document.getElementById('__ab_overlay');
    if (!on) return el?.remove();
    if (!el) {
      el = document.createElement('div');
      el.id = '__ab_overlay';
      el.style.cssText = 'position:fixed;inset:0;z-index:2147483646;pointer-events:none;display:flex;align-items:center;justify-content:center;background:rgba(15,17,26,.35);font:14px/1.5 system-ui,sans-serif';
      el.innerHTML = '<div style="background:rgba(20,22,34,.92);color:#fff;padding:18px 26px;border-radius:14px;text-align:center;box-shadow:0 10px 40px rgba(0,0,0,.4);max-width:420px"><div style="font-weight:700;font-size:16px;margin-bottom:4px">Auto Bid</div><div class="m"></div><div style="opacity:.7;font-size:12px;margin-top:6px">Keep this tab open until it finishes.</div></div>';
      document.documentElement.appendChild(el);
    }
    el.querySelector('.m').textContent = msg || 'Auto Bid is generating your application content…';
  }

  window.__abChat = { state, send, lastAnswer, overlay };
})();
