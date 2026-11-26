// Injected into chatgpt.com / chat.deepseek.com while a resume is being generated.
// Types the prompt into the chat box, sends it, and reads the newest answer back.
// lib/chat.js drives it from the side panel; the sites change their markup now and then, so every
// lookup has a generic fallback (largest text box, last markdown block).
(() => {
  // a page kept open across extension updates may hold an older copy: replace it
  if (window.__abChat && window.__abChat.v === 5) return;

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
    if (site === 'chatgpt') {
      // ChatGPT changes its markup: try the known markers in turn, then any rendered markdown reply
      for (const sel of ['[data-message-author-role="assistant"]', '[data-turn="assistant"]', 'article .markdown', '.markdown.prose, .markdown']) {
        const els = $$(sel).filter((el) => !el.closest('#prompt-textarea, form, [data-message-author-role="user"], [data-turn="user"]'));
        if (els.length) return els.filter((el) => !els.some((o) => o !== el && o.contains(el)));
      }
      return [];
    }
    if (site === 'deepseek') {
      // the visible answer, not the "thinking" block of DeepThink
      return $$('.ds-markdown').filter((el) => !el.closest('[class*="think" i]') && !el.parentElement?.closest('.ds-markdown'));
    }
    return $$('.markdown, [class*="markdown" i]').filter((el) => !el.parentElement?.closest('.markdown, [class*="markdown" i]'));
  }

  function generating() {
    // only the composer's stop button: other "Stop…" buttons (read aloud, voice, writing blocks) stay on
    // the page after the answer is done and used to keep the wait going for minutes
    if (site === 'chatgpt') {
      return !!$$('[data-testid="stop-button"], #composer-submit-button[aria-label*="Stop" i], button[aria-label="Stop streaming" i]').find(visible);
    }
    // DeepSeek has no stable "stop" marker (its class names are hashed); the driver waits until the
    // answer's length has held still for several seconds instead
    return !!$$('[aria-label*="Stop generating" i], [title*="Stop" i]').find(visible);
  }

  function loggedOut() {
    if (site === 'deepseek') return /sign_in|login/.test(location.pathname) || !!$$('input[type="password"]').find(visible);
    if (site === 'chatgpt') return /auth|login/.test(location.pathname) && !composer();
    return false;
  }

  // Answers already on the page before our prompt are marked, so the new answer is the one without the
  // mark. (Counting answers fails in long threads: the sites drop older messages from the page.)
  // ChatGPT also gives each message an id: a node the page re-used for the new reply keeps our mark but
  // gets a new id, so the id decides when there is one
  const idOf = (el) => el.getAttribute('data-message-id') || el.closest('[data-message-id]')?.getAttribute('data-message-id') || '';
  const textLen = (el) => (el.innerText || '').trim().length;
  let oldIds = new Set();
  let oldLast = null; // the last answer's text when we sent: if no node counts as new but it changed, it is the reply
  let sentAt = 0;

  function markOld() {
    const all = answers();
    for (const el of all) el.setAttribute('data-ab-old', '1');
    oldIds = new Set(all.map(idOf).filter(Boolean));
    oldLast = all.length ? all[all.length - 1].innerText || '' : '';
    sentAt = Date.now() / 1000;
    api = { at: 0, busy: null, text: '', done: false };
    return all.length;
  }

  function freshAnswers() {
    const all = answers();
    const fresh = all.filter((el) => (idOf(el) ? !oldIds.has(idOf(el)) : !el.hasAttribute('data-ab-old')));
    if (fresh.length || oldLast === null || !all.length) return fresh;
    const last = all[all.length - 1];
    return textLen(last) && (last.innerText || '') !== oldLast ? [last] : [];
  }

  // a reply can be several assistant blocks (thinking, tool steps, an empty placeholder): the answer is
  // the last one with text
  function pick(fresh) {
    for (let i = fresh.length - 1; i >= 0; i--) if (textLen(fresh[i])) return fresh[i];
    return fresh[fresh.length - 1] || null;
  }

  // ChatGPT's own record of the conversation (what its page loads): the reply's exact text and whether it
  // has finished, whatever the page's markup looks like. Checked at most every 2.5 s.
  let token = '';
  let api = { at: 0, busy: null, text: '', done: false };
  async function apiFetch() {
    const id = (location.pathname.match(/\/c\/([0-9a-f-]{8,})/) || [])[1];
    if (site !== 'chatgpt' || !id || !sentAt) return { text: '', done: false };
    const get = (u, h = {}) => Promise.race([fetch(u, { credentials: 'include', headers: h }).then((r) => (r.ok ? r.json() : null)).catch(() => null),
      new Promise((r) => setTimeout(() => r(null), 8000))]);
    if (!token) token = (await get('/api/auth/session'))?.accessToken || '';
    if (!token) return { text: '', done: false };
    const did = (document.cookie.match(/(?:^|; )oai-did=([^;]+)/) || [])[1];
    const d = await get(`/backend-api/conversation/${id}`, { Authorization: `Bearer ${token}`, ...(did ? { 'oai-device-id': did } : {}) });
    if (!d) token = ''; // maybe expired: fetch a new one next time
    let node = d?.mapping?.[d.current_node];
    while (node) {
      const m = node.message;
      if (m?.author?.role === 'user') return { text: '', done: false }; // nothing after our prompt yet
      const text = m?.author?.role === 'assistant' && m.content?.content_type === 'text' ? (m.content.parts || []).join('\n') : '';
      if (text.trim()) {
        if ((m.create_time || 0) < sentAt - 30) return { text: '', done: false }; // an older reply
        return { text, done: m.status === 'finished_successfully' && m.end_turn !== false };
      }
      node = d.mapping[node.parent];
    }
    return { text: '', done: false };
  }
  function apiState() {
    if (site === 'chatgpt' && sentAt && !api.busy && Date.now() - api.at > 2500) {
      api.busy = apiFetch().then((r) => Object.assign(api, r)).catch(() => {}).finally(() => { api.at = Date.now(); api.busy = null; });
    }
    return api;
  }
  async function apiReply() {
    if (site !== 'chatgpt') return '';
    try {
      Object.assign(api, await apiFetch(), { at: Date.now() });
    } catch { /* keep the page text */ }
    return api.text;
  }

  // the JSON of a code block, if the block is complete and parses
  function jsonOf(el) {
    const blocks = [...$$('code, pre', el).map((c) => c.innerText || ''), el.innerText || ''].filter((t) => t.includes('{'));
    for (const b of blocks.sort((x, y) => y.length - x.length)) {
      const s = b.slice(b.indexOf('{'), b.lastIndexOf('}') + 1);
      try {
        JSON.parse(s);
        return s;
      } catch { /* still streaming or not JSON */ }
    }
    return '';
  }

  // ChatGPT shows the copy / thumbs buttons under a reply only once it has finished
  function finished(el) {
    if (!el || site !== 'chatgpt') return false;
    const turn = el.closest('article, [data-testid^="conversation-turn"]') || el.parentElement?.parentElement;
    return !!turn && !!turn.querySelector('[data-testid="copy-turn-action-button"], [data-testid="good-response-turn-action-button"], [data-testid="bad-response-turn-action-button"]');
  }

  // the answer's text; ChatGPT can put a letter in a "Writing" block (an editor with its own header and
  // buttons): then the block's own text is the answer
  function textOf(el) {
    const blocks = $$('[contenteditable], .ProseMirror', el).filter((b) => !b.parentElement?.closest('[contenteditable], .ProseMirror'));
    const longest = blocks.map((b) => b.innerText || '').sort((x, y) => y.length - x.length)[0] || '';
    const all = el.innerText || '';
    return longest.trim().length > all.trim().length * 0.6 ? longest : all;
  }

  async function lastAnswer() {
    const fresh = freshAnswers();
    const el = pick(fresh) || answers()[answers().length - 1];
    if (!el && !(api.done && api.text)) return { text: '', count: 0 };
    let text = el ? textOf(el) : '';
    let json = el ? jsonOf(el) : '';
    if (api.done && api.text.trim()) {
      // ChatGPT's own finished copy is exact (no "Writing" / "JSON" headers, no half-rendered blocks)
      text = api.text;
      json = '';
      const s = text.slice(text.indexOf('{'), text.lastIndexOf('}') + 1);
      try {
        if (s) JSON.parse(s), (json = s);
      } catch { /* not JSON: a letter */ }
    } else if (!json && text.includes('{')) {
      // a long JSON block the page did not render in full: take the reply from ChatGPT itself
      const full = await apiReply();
      const s = full.slice(full.indexOf('{'), full.lastIndexOf('}') + 1);
      try {
        JSON.parse(s);
        json = s;
        text = full;
      } catch { /* keep the page text */ }
    }
    return { text: json ? '```json\n' + json + '\n```' : text, plain: text, count: answers().length, fresh: fresh.length };
  }

  function state() {
    const c = composer();
    const fresh = freshAnswers();
    const el = pick(fresh);
    const a = apiState();
    return {
      site, ready: !!c && !loggedOut(), loggedOut: loggedOut(), generating: generating(), url: location.href,
      count: answers().length, fresh: fresh.length || (a.text ? 1 : 0),
      length: Math.max(el ? (el.innerText || '').length : 0, a.text.length),
      jsonReady: el ? !!jsonOf(el) : false,
      done: finished(el) || a.done,
      apiDone: a.done
    };
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

  window.__abChat = { v: 5, state, send, lastAnswer, overlay, markOld };
})();
