// Injected into HiringCafe tabs (hiringcafe.com / hiring.cafe). Same interface as content/jobright.js,
// but HiringCafe has no job page of its own to work from: the bot reads each job box on the list,
// moves the mouse onto it (the box shows "Save / Mark Applied / Apply Directly" on hover) and clicks
// "Apply Directly", which opens the company's real application. After a successful bid it clicks
// "Save" on the box; saved jobs are never bid on again.
// The background moves a real mouse cursor (chrome.debugger) to the points `target()` returns;
// hover() / clickApply() here are the scripted fallback.
// Class names are generated, so everything here works from text and structure.
(() => {
  if (window.__abHiringCafe) return;

  const clean = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const textOf = (el) => clean(el ? el.innerText || el.textContent : '');
  const norm = (s) => clean(s).toLowerCase();
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // posting age on the card: "2h", "45m", "3d", "1w", "2mo", "just now"
  const AGE = /^(just now|today|\d+\s*(?:m|min|h|hr|d|w|mo)s?)$/i;
  const SALARY = /\$\s?[\d.,]+\s?[kK]?(?:\s*\/\s*(?:yr|hr|mo|year|hour|month))?\s*[-–]\s*\$?\s?[\d.,]+\s?[kK]?(?:\s*\/\s*(?:yr|hr|mo|year|hour|month))?|\$\s?[\d.,]+\s?[kK]?\s*\/\s*(?:yr|hr|mo)/;
  const OVERLAY = /^(save|saved|mark applied|applied|apply directly|job posting|view all|see additional activity)$/i;
  const FOOTER = /^(view all|job posting)$/i;
  const SAVE = /^save$/i;

  // A job you (or the bot) saved: the box says "Saved" / "Unsave", or its Save button is pressed.
  function isSaved(card) {
    if (byText(card, /^(saved|unsave|remove from saved)$/i).length) return true;
    return byText(card, SAVE).some((b) => {
      const btn = b.closest('button, [role="button"]') || b;
      return btn.getAttribute('aria-pressed') === 'true' || btn.getAttribute('aria-checked') === 'true' || btn.getAttribute('data-state') === 'on';
    });
  }

  function visible(el) {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden';
  }

  // "2h" -> hours since posting (null when unknown)
  function ageHours(posted) {
    const s = norm(posted);
    if (!s) return null;
    if (/just now|today/.test(s)) return 0;
    const m = /^(\d+)\s*(mo|min|m|hr|h|d|w)/.exec(s);
    if (!m) return null;
    const n = Number(m[1]);
    return { m: n / 60, min: n / 60, h: n, hr: n, d: n * 24, w: n * 168, mo: n * 720 }[m[2]] ?? null;
  }

  // Elements whose own text is exactly `re` (the innermost such element).
  function byText(root, re) {
    return [...root.querySelectorAll('a, button, span, div, p, [role="button"]')]
      .filter((e) => re.test(textOf(e)) && ![...e.children].some((c) => re.test(textOf(c))));
  }

  // Job box = smallest block around the "View all" / "Job Posting" footer that is card-sized.
  function cardFor(el) {
    let node = el.parentElement;
    for (let i = 0; i < 14 && node && node !== document.body; i++, node = node.parentElement) {
      const r = node.getBoundingClientRect();
      const t = textOf(node);
      if (r.height > 180 && r.width > 180 && t.length > 60 && t.length < 4000) return node;
    }
    return null;
  }

  function cardElements() {
    const cards = new Set();
    for (const el of byText(document, FOOTER)) {
      const c = cardFor(el);
      if (c && visible(c)) cards.add(c);
    }
    // drop wrappers that contain other cards
    const list = [...cards].filter((c) => ![...cards].some((o) => o !== c && c.contains(o)));
    // page order: top to bottom, left to right
    return list.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      return Math.abs(ra.top - rb.top) > 20 ? ra.top - rb.top : ra.left - rb.left;
    });
  }

  const linkWithText = (card, re) => [...card.querySelectorAll('a[href]')].find((a) => re.test(textOf(a)));

  function parseCard(card) {
    const text = textOf(card);
    const lines = (card.innerText || card.textContent || '').split('\n').map(clean).filter(Boolean).filter((l) => !OVERLAY.test(l));
    const posted = lines.find((l) => AGE.test(l)) || '';
    const heading = card.querySelector('h1, h2, h3, h4, h5, [class*="title" i]');
    const title = clean(heading && textOf(heading).length > 3 && textOf(heading).length < 160 ? textOf(heading) : lines.find((l) => l.length > 3 && !AGE.test(l)));
    const ti = lines.indexOf(title);
    const location = ti >= 0 ? lines.slice(ti + 1).find((l) => !AGE.test(l)) || '' : '';
    // "GridUnity: Accelerating Interconnection …" -> GridUnity
    const companyLine = lines.slice(Math.max(ti, 0) + 1).find((l) => /^[^:]{2,70}:\s/.test(l) && !SALARY.test(l)) || '';
    const company = clean(companyLine.split(':')[0]);
    // a stable link for the job: HiringCafe's own job link if there is one, else "Job Posting"
    const own = [...card.querySelectorAll('a[href]')].find((a) => /hiring\.?cafe/.test(a.hostname) && /\/(viewjob|jobs?)\//.test(a.pathname));
    const posting = linkWithText(card, /^job posting/i);
    const url = own?.href || posting?.href || '';
    // tags ("Remote", "Full Time") sit side by side, so their text can run together: check the elements
    const tag = (re) => byText(card, re).length > 0 || lines.some((l) => re.test(l));
    return {
      id: url ? `hc:${url.replace(/[?#].*$/, '')}` : `hc:${norm(title)}|${norm(company)}`,
      url: url || `https://hiringcafe.com/?abjob=${encodeURIComponent(`${title}|${company}`)}`,
      title,
      company,
      location,
      posted,
      ageHours: ageHours(posted),
      remote: tag(/^remote$/i) || /\bremote\b/i.test(text),
      hybrid: tag(/^hybrid$/i),
      onsite: tag(/^on-?site$/i),
      salary: (SALARY.exec(text) || [''])[0],
      match: null,
      saved: isSaved(card),
      text: text.slice(0, 600)
    };
  }

  function scrapeList() {
    return cardElements().map((c, index) => {
      c.dataset.abcard = String(index);
      return { index, ...parseCard(c) };
    });
  }

  // What the page looks like when no job box is found (goes to the log).
  function diagnose() {
    const count = (re) => byText(document, re).length;
    return {
      url: location.href.slice(0, 120),
      title: document.title,
      frames: document.querySelectorAll('iframe').length,
      viewAll: count(/^view all$/i),
      jobPosting: count(/^job posting$/i),
      applyDirectly: count(/^apply directly$/i),
      ageBadges: count(AGE),
      textStart: textOf(document.body).slice(0, 200)
    };
  }

  // Scrolls the list to load more cards; resolves to the new card count.
  async function loadMore() {
    const before = cardElements().length;
    const scrollers = [...document.querySelectorAll('div')].filter((d) => d.scrollHeight > d.clientHeight + 200 && /(auto|scroll)/.test(getComputedStyle(d).overflowY));
    for (const s of scrollers) s.scrollTop = s.scrollHeight;
    window.scrollTo(0, document.body.scrollHeight);
    for (let i = 0; i < 12; i++) {
      await sleep(500);
      if (cardElements().length > before) break;
    }
    return cardElements().length;
  }

  // The card for a job: by the index from scrapeList, checked against the title (the list
  // may re-render between scrapes), else searched by title and company.
  function findCard(job) {
    const want = norm(job?.title);
    const byIndex = job?.index != null ? document.querySelector(`[data-abcard="${job.index}"]`) : null;
    if (byIndex && byIndex.isConnected && (!want || norm(textOf(byIndex)).includes(want))) return byIndex;
    return cardElements().find((c) => {
      const t = norm(textOf(c));
      return want && t.includes(want) && (!job.company || t.includes(norm(job.company)));
    }) || null;
  }

  const clickable = (el) => el.closest('a[href], button, [role="button"]') || el;
  function cardButton(card, re) {
    const hit = byText(card, re).find(visible) || byText(card, re)[0];
    return hit ? clickable(hit) : null;
  }
  const center = (el) => {
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
  };

  // Where the mouse should go. what = 'card': a point on the job box (to hover it, after
  // scrolling it into view); 'apply' / 'save': the "Apply Directly" / "Save" button.
  // `saved` tells the background the job was saved before (its hover buttons are showing now).
  async function target(job, what = 'card') {
    const card = findCard(job);
    if (!card) return { error: 'job box not found on the list' };
    if (what === 'card') {
      card.scrollIntoView({ block: 'center' });
      await sleep(500);
      const r = card.getBoundingClientRect();
      // the upper middle of the box (the title area), away from the hover buttons
      return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + Math.min(60, r.height / 4)) };
    }
    const saved = isSaved(card);
    if (what === 'save' && saved) return { saved: true, already: true };
    const b = cardButton(card, what === 'apply' ? /^apply directly$/i : SAVE);
    if (!b) return { error: `"${what === 'apply' ? 'Apply Directly' : 'Save'}" not on the job box`, saved };
    if (!visible(b)) return { error: 'button hidden', href: b.closest('a[href]')?.href || '', saved };
    return { ...center(b), href: b.closest('a[href]')?.href || '', saved };
  }

  // ---------- scripted fallback (no real cursor) ----------

  async function hover(card) {
    card.scrollIntoView({ block: 'center' });
    await sleep(400);
    const r = card.getBoundingClientRect();
    const at = { bubbles: true, cancelable: true, view: window, clientX: r.left + r.width / 2, clientY: r.top + r.height / 3 };
    const point = document.elementFromPoint(at.clientX, at.clientY) || card;
    for (const el of new Set([card, point])) {
      el.dispatchEvent(new PointerEvent('pointerover', at));
      el.dispatchEvent(new PointerEvent('pointerenter', { ...at, bubbles: false }));
      el.dispatchEvent(new MouseEvent('mouseover', at));
      el.dispatchEvent(new MouseEvent('mouseenter', { ...at, bubbles: false }));
      el.dispatchEvent(new PointerEvent('pointermove', at));
      el.dispatchEvent(new MouseEvent('mousemove', at));
    }
  }

  async function hoverButton(card, re, ms) {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      await hover(card);
      const b = cardButton(card, re);
      if (b) return b;
      await sleep(300);
    }
    return null;
  }

  // Not used for HiringCafe (no job page), kept for the shared interface.
  function openCard() {
    return false;
  }

  // There is no job page: the details come from the card (the background adds the
  // company posting's text once the application page is open).
  function scrapeDetail(job) {
    const card = findCard(job);
    if (!card) return null;
    return { ...parseCard(card), jd: textOf(card).slice(0, 4000), applyUrl: '' };
  }

  // Scripted "Apply Directly" click. Returns { clicked, href } (href lets the background open
  // the link itself when the browser blocks the scripted click's new tab).
  async function clickApply(job) {
    const card = findCard(job);
    if (!card) return { clicked: false, error: 'job box not found on the list' };
    const b = await hoverButton(card, /^apply directly$/i, 4000);
    if (!b) return { clicked: false, error: '"Apply Directly" did not appear on the job box' };
    b.click();
    return { clicked: true, href: b.closest('a[href]')?.href || '' };
  }

  // Scripted "Save" click after a successful bid.
  async function confirmApplied(ms = 12000, job = null) {
    const card = findCard(job);
    if (!card) return false;
    if (isSaved(card)) return true;
    const b = await hoverButton(card, SAVE, Math.min(ms, 6000));
    if (!b) return false;
    b.click();
    await sleep(800);
    return true;
  }

  window.__abHiringCafe = { scrapeList, diagnose, loadMore, openCard, scrapeDetail, target, clickApply, confirmApplied };
})();
