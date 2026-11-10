// Injected into your open Gmail tab when an application asks for an email verification code or link.
// Reads only the newest messages in the inbox list, opens one, and returns its text and links.
(() => {
  if (window.__abGmail) return;
  const clean = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const visible = (el) => !!el && el.getClientRects().length > 0;

  function listRows() {
    return [...document.querySelectorAll('tr.zA')].filter(visible);
  }

  function refresh() {
    if (!/#inbox/.test(location.hash) && !/#search|#label/.test(location.hash)) location.hash = '#inbox';
    const btn = document.querySelector('[data-tooltip="Refresh"], [aria-label="Refresh"], div[act="20"]');
    btn?.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    btn?.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    btn?.click();
    return !!btn;
  }

  // Newest messages first: sender, subject, snippet, time.
  function rows(n = 3) {
    return listRows().slice(0, n).map((tr, i) => {
      const from = tr.querySelector('.yW [email], .yW span[name], .yW');
      return {
        i,
        unread: tr.classList.contains('zE'),
        from: clean(from?.getAttribute?.('email') || '') + ' ' + clean(from?.getAttribute?.('name') || from?.textContent || ''),
        subject: clean(tr.querySelector('.bog, .bqe')?.textContent),
        snippet: clean(tr.querySelector('.y2')?.textContent),
        time: tr.querySelector('.xW span[title], td.xW span')?.getAttribute('title') || clean(tr.querySelector('.xW')?.textContent)
      };
    });
  }

  // Gmail wraps links as https://www.google.com/url?q=<real link>
  function realHref(a) {
    const h = a.getAttribute('data-saferedirecturl') || a.href || '';
    try {
      const u = new URL(h);
      if (/google\.[a-z.]+$/.test(u.hostname) && u.pathname === '/url') return u.searchParams.get('q') || a.href;
    } catch { /* not a URL */ }
    return a.href;
  }

  async function open(i) {
    const tr = listRows()[i];
    if (!tr) return null;
    tr.click();
    for (let k = 0; k < 30; k++) {
      await sleep(250);
      const bodies = [...document.querySelectorAll('.a3s')].filter(visible);
      if (bodies.length) {
        const body = bodies[bodies.length - 1];
        await sleep(300);
        return {
          subject: clean(document.querySelector('h2.hP')?.textContent),
          text: clean(body.innerText).slice(0, 6000),
          links: [...body.querySelectorAll('a[href]')].map((a) => ({ text: clean(a.textContent).slice(0, 80), href: realHref(a) })).slice(0, 60)
        };
      }
    }
    return null;
  }

  function back() {
    location.hash = '#inbox';
    return true;
  }

  window.__abGmail = { refresh, rows, open, back };
})();
