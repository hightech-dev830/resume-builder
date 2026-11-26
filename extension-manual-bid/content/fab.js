// The round Auto Bid button on the right edge of every page. Click: fill this page (one pass, no
// navigation). Drag it up or down to move it; the position is remembered. Hidden on the chat sites and
// when "Show the autofill button" is off in Settings.
(() => {
  if (window.top !== window || window.__abFab) return;
  window.__abFab = true;
  if (/chatgpt\.com|chat\.openai\.com|chat\.deepseek\.com/.test(location.host)) return;

  const ICON = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.9 4.6L18.5 9l-4.6 1.9L12 15.5l-1.9-4.6L5.5 9l4.6-1.4z"/><path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9z"/></svg>';
  let host;
  let shadow;
  let btn;
  let toast;
  let busy = false;

  function build(top) {
    host = document.createElement('div');
    host.style.cssText = 'all:initial;position:fixed;right:0;z-index:2147483645;';
    host.style.top = `${top}%`;
    shadow = host.attachShadow({ mode: 'closed' });
    shadow.innerHTML = `<style>
      .b{width:44px;height:44px;border-radius:12px 0 0 12px;border:0;background:linear-gradient(135deg,#6d5efc,#8b5cf6);color:#fff;display:flex;align-items:center;justify-content:center;cursor:pointer;box-shadow:0 6px 20px rgba(80,60,200,.45);transition:width .15s}
      .b:hover{width:52px}
      .b.busy svg{animation:s 1s linear infinite}@keyframes s{to{transform:rotate(360deg)}}
      .b.ok{background:linear-gradient(135deg,#10b981,#059669)}.b.err{background:linear-gradient(135deg,#ef4444,#b91c1c)}
      .t{position:absolute;right:56px;top:50%;transform:translateY(-50%);width:270px;background:#151826;color:#e7e9f3;font:12.5px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;padding:10px 12px;border-radius:10px;box-shadow:0 10px 30px rgba(0,0,0,.35);border:1px solid #2b3050;display:none;white-space:pre-line}
      .t.on{display:block}.t b{color:#fff}
    </style><div class="t"></div><button class="b" title="Auto Bid: fill this page">${ICON}</button>`;
    btn = shadow.querySelector('.b');
    toast = shadow.querySelector('.t');
    document.documentElement.appendChild(host);

    // drag vertically; a click without movement fills
    let startY = 0;
    let moved = false;
    btn.addEventListener('pointerdown', (e) => {
      startY = e.clientY;
      moved = false;
      btn.setPointerCapture(e.pointerId);
    });
    btn.addEventListener('pointermove', (e) => {
      if (!btn.hasPointerCapture(e.pointerId) || Math.abs(e.clientY - startY) < 5) return;
      moved = true;
      const pct = Math.min(90, Math.max(5, (e.clientY / innerHeight) * 100));
      host.style.top = `${pct}%`;
    });
    btn.addEventListener('pointerup', (e) => {
      btn.releasePointerCapture(e.pointerId);
      if (moved) chrome.storage.local.set({ fabTop: parseFloat(host.style.top) });
      else fill();
    });
  }

  function show(text, ms = 6000) {
    toast.innerHTML = '';
    const lines = String(text).split('\n');
    const b = document.createElement('b');
    b.textContent = lines[0];
    toast.append(b, document.createTextNode(lines.length > 1 ? '\n' + lines.slice(1).join('\n') : ''));
    toast.classList.add('on');
    clearTimeout(show.t);
    if (ms) show.t = setTimeout(() => toast.classList.remove('on'), ms);
  }

  async function fill() {
    if (busy) return;
    busy = true;
    btn.className = 'b busy';
    show('Filling this page…', 0);
    try {
      const r = await chrome.runtime.sendMessage({ type: 'fill:sender' });
      if (!r?.ok) throw new Error(r?.error || 'No answer from the extension (reload it in chrome://extensions)');
      const d = r.data;
      btn.className = d.status === 'filled' || d.status === 'success' ? 'b ok' : 'b';
      if (d.status === 'success') show(`Application submitted\n${d.app ? 'Saved as applied.' : ''}`);
      else if (d.status === 'none') show(`Nothing to fill\n${d.note}`);
      else {
        const lines = [`Filled ${d.filled} field${d.filled === 1 ? '' : 's'} as ${d.profile?.name || ''}`];
        if (d.uploaded) lines.push('Resume uploaded.');
        if (d.left) lines.push(`${d.left} required field${d.left > 1 ? 's' : ''} still empty: check them.`);
        if (d.failed?.length) lines.push(`Could not do: ${d.failed.slice(0, 3).join('; ')}`);
        if (d.note) lines.push(d.note);
        lines.push('Check the page, then press Next / Submit yourself.');
        show(lines.join('\n'), 9000);
      }
    } catch (err) {
      btn.className = 'b err';
      show(`Autofill failed\n${err.message}`, 9000);
    } finally {
      busy = false;
      setTimeout(() => {
        if (!busy) btn.className = 'b';
      }, 4000);
    }
  }

  chrome.storage.local.get(['settings', 'session', 'fabTop'], ({ settings, session, fabTop }) => {
    if (!session || settings?.fill?.showButton === false) return;
    build(fabTop || 50);
  });
  chrome.storage.onChanged.addListener((ch) => {
    if (ch.settings && host) host.style.display = ch.settings.newValue?.fill?.showButton === false ? 'none' : '';
    if (ch.session && !ch.session.newValue && host) host.remove();
  });
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg?.type === 'fab:toast' && toast) show(msg.text);
  });
})();
