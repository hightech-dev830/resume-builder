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

  // ---- pagination ("‹ 1 2 3 4 5 6 ›" under the list) ----
  // The page buttons: a row of small number buttons outside the job boxes (the boxes have digit-only
  // view/save counts too), lowest on the page. Returns { nums: [{ el, n }], row } or null.
  function pager() {
    const cards = cardElements();
    const inCard = (el) => cards.some((c) => c.contains(el));
    const nums = [...document.querySelectorAll('button, a, li, [role="button"], span, div')]
      .filter((e) => /^\d{1,3}$/.test(textOf(e)) && ![...e.children].some((c) => /^\d{1,3}$/.test(textOf(c))))
      .map((e) => e.closest('button, a, [role="button"], li') || e)
      .filter((e, i, all) => all.indexOf(e) === i && visible(e) && !inCard(e));
    // group by row (same parent chain within 3 levels), keep rows of 2+ consecutive numbers
    const rows = new Map();
    for (const el of nums) {
      let row = el.parentElement;
      for (let i = 0; i < 3 && row && nums.filter((x) => row.contains(x)).length < 2; i++) row = row.parentElement;
      if (!row) continue;
      if (!rows.has(row)) rows.set(row, []);
      rows.get(row).push({ el, n: Number(textOf(el)) });
    }
    const ok = [...rows.entries()]
      .map(([row, list]) => ({ row, nums: list.filter((x, i, a) => a.findIndex((y) => y.n === x.n) === i).sort((a, b) => a.n - b.n) }))
      .filter(({ nums: l }) => l.length >= 2 && l.every((x, i) => i === 0 || x.n === l[i - 1].n + 1));
    if (!ok.length) return null;
    return ok.sort((a, b) => b.row.getBoundingClientRect().top - a.row.getBoundingClientRect().top)[0];
  }

  // The highlighted page: aria-current, else the one button whose background differs from the rest.
  function activePage(nums) {
    const cur = nums.find(({ el }) => /page|true/.test(el.getAttribute('aria-current') || '') || el.getAttribute('aria-selected') === 'true');
    if (cur) return cur.n;
    const bg = (el) => getComputedStyle(el).backgroundColor;
    const counts = {};
    for (const { el } of nums) counts[bg(el)] = (counts[bg(el)] || 0) + 1;
    const odd = nums.filter(({ el }) => counts[bg(el)] === 1);
    return odd.length === 1 ? odd[0].n : null;
  }

  // "Page [ 1 ] of 6  Go  ‹ ›": the page box (the page number is in an input). Returns
  // { input, box, cur, total, next } (next = the "›" button) or null.
  function inputPager() {
    for (const input of document.querySelectorAll('input')) {
      if (!visible(input) || !/^\d{1,4}$/.test(String(input.value).trim())) continue;
      let box = input.parentElement;
      for (let i = 0; i < 5 && box && box !== document.body; i++, box = box.parentElement) {
        const t = textOf(box);
        const m = /\bof\s*(\d{1,4})\b/i.exec(t);
        if (!m || !/\bpage\b/i.test(t) || t.length > 80) continue;
        // "›" = the last icon button (no text) after the input; "‹" comes before it
        const icons = [...box.querySelectorAll('button, a, [role="button"]')]
          .filter((b) => visible(b) && !textOf(b) && (input.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING));
        return { input, box, cur: Number(input.value), total: Number(m[1]), next: icons[icons.length - 1] || null };
      }
    }
    return null;
  }

  // Types a page number into the page box and presses "Go" (React needs the native value setter).
  function goToPage(p, n) {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(p.input, String(n));
    p.input.dispatchEvent(new Event('input', { bubbles: true }));
    p.input.dispatchEvent(new Event('change', { bubbles: true }));
    const go = [...p.box.querySelectorAll('button, [role="button"]')].find((b) => /^go$/i.test(textOf(b)));
    if (go) go.click();
    else p.input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }));
  }

  const firstCardText = () => { const c = cardElements()[0]; return c ? textOf(c).slice(0, 120) : ''; };
  async function waitNewCards(before) {
    for (let i = 0; i < 24; i++) {
      await sleep(500);
      const now = firstCardText();
      if (now && now !== before) return true;
    }
    return false;
  }
  async function backToTop() {
    window.scrollTo(0, 0);
    for (const s of document.querySelectorAll('div')) if (s.scrollTop > 0) s.scrollTop = 0;
    await sleep(500);
  }

  // Goes to the next result page. `known` = the page the bot thinks it is on (used when the
  // current page cannot be told). Returns { moved, page, total } (page = the page now shown).
  async function nextPage(known = 1) {
    window.scrollTo(0, document.body.scrollHeight);
    await sleep(600);

    // "Page [1] of 6  Go  ‹ ›"
    const box = inputPager();
    if (box) {
      const cur = box.cur || known;
      if (cur >= box.total) return { moved: false, reason: 'last page', page: cur, total: box.total };
      const before = firstCardText();
      const usable = (b) => b && !b.disabled && b.getAttribute('aria-disabled') !== 'true';
      if (usable(box.next)) box.next.click();
      else goToPage(box, cur + 1);
      let changed = await waitNewCards(before);
      if (!changed && usable(box.next)) {
        // the arrow did nothing: type the page number and press Go instead
        const again = inputPager();
        if (again) { goToPage(again, cur + 1); changed = await waitNewCards(before); }
      }
      if (!changed) return { moved: false, reason: 'next page did not load', page: cur, total: box.total };
      await backToTop();
      return { moved: true, page: inputPager()?.cur || cur + 1, total: box.total };
    }

    // "‹ 1 2 3 4 5 6 ›"
    const p = pager();
    if (!p) return { moved: false, reason: 'no page buttons' };
    const cur = activePage(p.nums) ?? known;
    const want = cur + 1;
    let btn = p.nums.find((x) => x.n === want)?.el;
    if (!btn) {
      // page numbers shown only up to the current one: the "›" arrow after the last number
      const last = p.nums[p.nums.length - 1].el;
      if (cur < p.nums[p.nums.length - 1].n) return { moved: false, reason: 'next page not found' };
      const after = [...p.row.querySelectorAll('button, a, [role="button"]')].filter((b) => visible(b) && !/^\d+$/.test(textOf(b)) && (last.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING));
      btn = after.find((b) => !b.disabled && b.getAttribute('aria-disabled') !== 'true');
      if (!btn) return { moved: false, reason: 'last page', page: cur };
    }
    const before = firstCardText();
    btn.click();
    // the click showed the same jobs (a "›" that looks enabled on the last page): treat as the end
    if (!(await waitNewCards(before))) return { moved: false, reason: 'last page', page: cur };
    await backToTop();
    return { moved: true, page: activePage(pager()?.nums || []) ?? want };
  }

  window.__abHiringCafe = { scrapeList, diagnose, loadMore, nextPage, openCard, scrapeDetail, target, clickApply, confirmApplied };
})();
