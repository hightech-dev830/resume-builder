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

  window.__abJobright = { scrapeList, loadMore, openCard, scrapeDetail, clickApply, confirmApplied };
})();
