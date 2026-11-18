// Injected into jobright.ai tabs. Reads job cards from the list page, job details from a job
// page, clicks "Apply with autofill", and answers the "Did you apply?" dialog afterwards.
// Jobright's class names are generated, so everything here works from text and structure.
(() => {
  if (window.__abJobright) return;

  const clean = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const textOf = (el) => clean(el ? el.innerText || el.textContent : '');
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const JOB_ID = /\b([0-9a-f]{24})\b/;
  const POSTED = /(just now|today|\b(\d+|an?|one)\s+(minute|min|hour|hr|day|week|month)s?\s+ago)/i;
  const SALARY = /\$\s?[\d.,]+\s?[kK]?(?:\/(?:yr|hr|year|hour))?\s*[-–]\s*\$\s?[\d.,]+\s?[kK]?(?:\/(?:yr|hr|year|hour))?|\$\s?[\d.,]+\s?[kK]?\/(?:yr|hr)/;

  function visible(el) {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden';
  }

  // "10 hours ago" -> hours since posting (null when unknown)
  function ageHours(posted) {
    const m = /(just now|today)|(\d+|an?|one)\s+(minute|min|hour|hr|day|week|month)s?/i.exec(posted || '');
    if (!m) return null;
    if (m[1]) return 0;
    const n = /^\d+$/.test(m[2]) ? Number(m[2]) : 1;
    const unit = m[3].toLowerCase();
    return unit.startsWith('min') ? n / 60 : unit.startsWith('h') ? n : unit === 'day' ? n * 24 : unit === 'week' ? n * 168 : n * 720;
  }

  function findJobId(el) {
    const attrs = (node) => [node.id, node.getAttribute?.('href'), ...Object.values(node.dataset || {})].filter(Boolean).join(' ');
    let m = JOB_ID.exec(attrs(el));
    if (m) return m[1];
    for (const n of el.querySelectorAll('*')) {
      m = JOB_ID.exec(attrs(n));
      if (m) return m[1];
    }
    return null;
  }

  // Card = smallest block around one "Apply" button / job link that also shows the posting age.
  function cardFor(el) {
    let node = el;
    for (let i = 0; i < 10 && node; i++, node = node.parentElement) {
      const t = textOf(node);
      if (POSTED.test(t) && node.getBoundingClientRect().height > 120 && t.length < 2500) return node;
    }
    return null;
  }

  function parseCard(card) {
    const text = textOf(card);
    const lines = (card.innerText || '').split('\n').map(clean).filter(Boolean);
    const heading = card.querySelector('h1, h2, h3, h4, [class*="title" i]');
    const title = clean(heading ? textOf(heading) : lines.find((l) => l.length > 6 && !POSTED.test(l) && !/applicant|match|unicorn|public company|early/i.test(l)));
    const titleIdx = lines.indexOf(title);
    const companyLine = titleIdx >= 0 ? lines[titleIdx + 1] || '' : '';
    const company = clean(companyLine.split(' / ')[0]);
    const id = findJobId(card);
    return {
      id,
      url: id ? `https://jobright.ai/jobs/info/${id}` : '',
      title,
      company,
      posted: (POSTED.exec(text) || [''])[0],
      ageHours: ageHours((POSTED.exec(text) || [''])[0]),
      remote: /\bremote\b/i.test(text),
      hybrid: /\bhybrid\b/i.test(text),
      onsite: /\bon-?site\b/i.test(text),
      salary: (SALARY.exec(text) || [''])[0],
      match: Number((/(\d{2,3})\s*%/.exec(text) || [])[1]) || null,
      text: text.slice(0, 600)
    };
  }

  function cardElements() {
    const anchors = [...document.querySelectorAll('a[href*="/jobs/info/"]')];
    const buttons = [...document.querySelectorAll('button, a, [role="button"]')].filter((b) => /apply with autofill|^apply now$/i.test(textOf(b)));
    const cards = new Set();
    for (const el of [...anchors, ...buttons]) {
      const c = cardFor(el);
      if (c && visible(c)) cards.add(c);
    }
    // drop wrappers that contain other cards
    return [...cards].filter((c) => ![...cards].some((o) => o !== c && c.contains(o)));
  }

  function scrapeList() {
    return cardElements().map((c, index) => {
      c.dataset.abcard = String(index);
      return { index, ...parseCard(c) };
    });
  }

  // Scrolls the list to load more cards; resolves to the new card count.
  async function loadMore() {
    const before = cardElements().length;
    const scrollers = [document.scrollingElement, ...document.querySelectorAll('div')].filter((d) => d && d.scrollHeight > d.clientHeight + 200 && /(auto|scroll)/.test(getComputedStyle(d).overflowY) || d === document.scrollingElement);
    for (const s of scrollers) s.scrollTop = s.scrollHeight;
    window.scrollTo(0, document.body.scrollHeight);
    for (let i = 0; i < 12; i++) {
      await sleep(500);
      if (cardElements().length > before) break;
    }
    return cardElements().length;
  }

  // Opens a card whose job id could not be read (clicks its title).
  function openCard(index) {
    const card = document.querySelector(`[data-abcard="${index}"]`);
    if (!card) return false;
    const target = card.querySelector('h1, h2, h3, h4, [class*="title" i]') || card;
    target.click();
    return true;
  }

  // ---------- job page ----------

  // Jobright pages are Next.js; when the job JSON is embedded, prefer it over the DOM.
  function nextData() {
    try {
      const raw = document.getElementById('__NEXT_DATA__')?.textContent;
      if (!raw) return null;
      let found = null;
      const walk = (o, depth) => {
        if (found || !o || typeof o !== 'object' || depth > 12) return;
        if (!Array.isArray(o) && ('jobTitle' in o || 'jobSummary' in o) && ('companyName' in o || 'jobId' in o || 'jobSummary' in o)) {
          found = o;
          return;
        }
        for (const v of Object.values(o)) walk(v, depth + 1);
      };
      walk(JSON.parse(raw), 0);
      return found;
    } catch {
      return null;
    }
  }

  const flat = (v) => (Array.isArray(v) ? v.map(flat).filter(Boolean).join('\n') : v && typeof v === 'object' ? Object.values(v).map(flat).filter(Boolean).join('\n') : v == null ? '' : String(v));

  function scrapeDetail() {
    const nd = nextData();
    const h = document.querySelector('h1, h2');
    const body = textOf(document.querySelector('main') || document.body);
    // overview text up to the insider-connection block
    const overview = body.split(/Insider Connection|Find Any Email|Beyond Your Network/)[0];
    const companyLine = [...document.querySelectorAll('div, span, p')].map(textOf).find((t) => /·\s*(Reposted|\d+|an?)\b.*ago/i.test(t) && t.length < 140) || '';
    const original = [...document.querySelectorAll('a[href]')].find((a) => /original job post/i.test(textOf(a)))?.href || '';
    const out = {
      id: (JOB_ID.exec(location.pathname) || [])[1] || '',
      url: location.href.split('?')[0],
      title: clean(nd?.jobTitle) || textOf(h),
      company: clean(nd?.companyName || nd?.company?.companyName || companyLine.split('·')[0]),
      location: clean(nd?.jobLocation || ''),
      salary: clean(nd?.salaryDesc || (SALARY.exec(overview) || [''])[0]),
      remote: nd ? !!(nd.isRemote || /remote/i.test(flat(nd.workModel) + flat(nd.jobLocation))) : /\bremote\b/i.test(overview),
      posted: (POSTED.exec(companyLine || overview) || [''])[0],
      match: Number((/(\d{2,3})\s*%\s*(STRONG|GOOD|FAIR|EXCELLENT)?/i.exec(overview) || [])[1]) || null,
      applyUrl: nd?.applyLink || nd?.originalUrl || nd?.jobUrl || original,
      jd: ''
    };
    if (nd) {
      out.jd = [
        nd.jobSummary && `Summary:\n${flat(nd.jobSummary)}`,
        nd.coreResponsibilities && `Responsibilities:\n${flat(nd.coreResponsibilities)}`,
        nd.qualifications && `Qualifications:\n${flat(nd.qualifications)}`,
        nd.skillSummaries && `Skills: ${flat(nd.skillSummaries)}`,
        nd.jobDescription && flat(nd.jobDescription)
      ].filter(Boolean).join('\n\n');
    }
    if (!out.jd || out.jd.length < 300) out.jd = overview.slice(0, 15000);
    if (!out.location) out.location = out.remote ? 'Remote (US)' : '';
    return out;
  }

  function findButton(re) {
    return [...document.querySelectorAll('button, a, [role="button"]')].find((b) => visible(b) && re.test(textOf(b)));
  }

  function clickApply() {
    const b = findButton(/apply with autofill/i) || findButton(/^apply( now)?\s*↗?$/i);
    if (!b) return false;
    b.click();
    return true;
  }

  // Waits up to `ms` for the "Did you apply?" dialog and answers Yes.
  async function confirmApplied(ms = 12000) {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      const yes = findButton(/^yes,?\s*i applied!?$/i);
      if (yes) {
        yes.click();
        await sleep(800);
        return true;
      }
      // the dialog shows when the page becomes visible again
      document.dispatchEvent(new Event('visibilitychange'));
      window.dispatchEvent(new Event('focus'));
      await sleep(600);
    }
    return false;
  }

  // ---------- list settings: sort by "Most Recent", work model "Remote" only ----------

  // a real mouse press (the site's dropdowns open on mousedown, not on click)
  function press(el) {
    const r = el.getBoundingClientRect();
    const o = { bubbles: true, cancelable: true, view: window, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 };
    el.dispatchEvent(new PointerEvent('pointerdown', o));
    el.dispatchEvent(new MouseEvent('mousedown', o));
    el.dispatchEvent(new PointerEvent('pointerup', o));
    el.dispatchEvent(new MouseEvent('mouseup', o));
    el.dispatchEvent(new MouseEvent('click', o));
  }
  let cardsNow = [];
  const inCard = (el) => !!el.closest('[data-abcard]') || cardsNow.some((c) => c.contains(el));
  // the "Recommended / Liked / Applied / External" tabs at the top are not the sort box
  const inTabs = (el) => {
    for (let p = el.parentElement, i = 0; p && i < 4; p = p.parentElement, i++) if (/\bliked\b/i.test(textOf(p)) && /\bapplied\b/i.test(textOf(p))) return true;
    return !!el.closest('a, [role="tab"], [role="tablist"], nav');
  };
  // the smallest visible element showing exactly this text, outside the job boxes
  const exact = (re, scope = document) => (cardsNow = cardElements(), [...scope.querySelectorAll('div, span, button, li, label, p, [role="option"], [role="menuitem"], [role="combobox"]')]
    .filter((el) => visible(el) && re.test(textOf(el)) && !inCard(el))
    .filter((el, _, all) => !all.some((o) => o !== el && el.contains(o))));

  const SORT_NAMES = /^(recommended|most recent|newest|top match(ed|es)?|best match(es)?|most relevant|relevance)$/i;
  function sortBox() {
    const hits = exact(SORT_NAMES).filter((el) => !inTabs(el) && el.getBoundingClientRect().top < 450);
    const el = hits[0];
    if (!el) return null;
    // press the select itself, not just its label
    return el.closest('[role="combobox"], [class*="select" i], [class*="dropdown" i], button') || el;
  }

  async function ensureSort() {
    let box = sortBox();
    if (!box) return { ok: false, now: '', note: 'sort box not found' };
    const before = textOf(box);
    if (/most recent|newest/i.test(before)) return { ok: true, now: before, changed: false };
    for (let attempt = 0; attempt < 2; attempt++) {
      press(box);
      let opt = null;
      for (let i = 0; i < 10 && !opt; i++) {
        await sleep(250);
        opt = exact(/^(most recent|newest)$/i).find((o) => !box.contains(o));
      }
      if (!opt) continue;
      press(opt.closest('[role="option"], [role="menuitem"], li, [class*="item" i]') || opt);
      await sleep(1500);
      box = sortBox() || box;
      if (/most recent|newest/i.test(textOf(box))) return { ok: true, now: textOf(box), was: before, changed: true };
    }
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    return { ok: false, now: textOf(sortBox() || box), was: before, note: 'could not pick "Most Recent"' };
  }

  // the work-model filter button in the filter bar: "Remote", "Remote (+1)", "Onsite", "Hybrid (+2)", "Work Model"
  const WORK = /^(remote|onsite|on-site|hybrid|work model|work type|workplace)(\s*\(\+\d+\))?\s*▾?$/i;
  function workBox() {
    const el = exact(WORK).find((e) => !inTabs(e) && e.getBoundingClientRect().top < 450);
    return el ? el.closest('button, [role="button"], [role="combobox"], [class*="select" i], [class*="filter" i]') || el : null;
  }
  const isOn = (el) => {
    const input = el.querySelector('input[type="checkbox"], input[type="radio"]') || el.closest('label')?.querySelector('input');
    if (input) return input.checked;
    for (const n of [el, el.parentElement, el.firstElementChild].filter(Boolean)) {
      if (['aria-checked', 'aria-selected', 'data-checked', 'data-selected'].some((a) => n.getAttribute(a) === 'true')) return true;
      if (/(^|[\s_-])(checked|selected|active)(?![a-z])/i.test(typeof n.className === 'string' ? n.className : '')) return true;
    }
    return false;
  };

  async function ensureRemote() {
    let box = workBox();
    if (!box) return { ok: false, now: '', note: 'work-model filter not found' };
    const before = textOf(box);
    if (/^remote\s*▾?$/i.test(before)) return { ok: true, now: before, changed: false };
    press(box);
    await sleep(700);
    const opts = exact(/^(remote|onsite|on-site|hybrid)$/i).filter((o) => !box.contains(o));
    if (!opts.length) {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      return { ok: false, now: before, note: 'work-model choices not found' };
    }
    for (const o of opts) {
      const want = /^remote$/i.test(textOf(o));
      const row = o.closest('label, li, [role="option"], [role="menuitemcheckbox"], [class*="item" i], [class*="option" i]') || o;
      if (isOn(row) !== want) {
        press(row);
        await sleep(300);
      }
    }
    // a confirm button in the menu, else close it
    const ok = exact(/^(apply|confirm|done|save|ok|show (\d+ )?(results|jobs)|update)$/i)[0];
    if (ok) press(ok);
    else {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      press(document.body);
    }
    await sleep(1500);
    box = workBox() || box;
    return { ok: /^remote\s*▾?$/i.test(textOf(box)), now: textOf(box), was: before, changed: true };
  }

  async function prepareList({ remote = true } = {}) {
    const sort = await ensureSort();
    const work = remote ? await ensureRemote() : null;
    return { sort, work };
  }

  window.__abJobright = { scrapeList, loadMore, openCard, scrapeDetail, clickApply, confirmApplied, prepareList };
})();
