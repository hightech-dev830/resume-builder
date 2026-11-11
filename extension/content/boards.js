// Job boards (Jobicy first) as you use them: read the posting (title, company, summary, the full
// "About this role" after "Show more", core skills, details), then press "Apply Now", which opens
// the company's application in a new tab. Injected into the job board tab only.
(() => {
  if (window.__abBoards) return;
  const clean = (s) => String(s || '').replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
  const one = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const visible = (el) => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const all = (sel, root = document) => [...root.querySelectorAll(sel)];
  const byText = (sel, re) => all(sel).filter((el) => re.test(one(el.textContent)));

  // The card that holds a heading: climb until the box has real content besides the heading.
  function sectionOf(heading, min = 200) {
    let box = heading.parentElement;
    for (let i = 0; i < 6 && box; i++, box = box.parentElement) {
      if (one(box.innerText).length > one(heading.innerText).length + min) return box;
    }
    return heading.parentElement;
  }

  function postingData() {
    const out = {};
    const walk = (x) => {
      if (!x || typeof x !== 'object') return;
      if (Array.isArray(x)) return x.forEach(walk);
      if (/JobPosting/i.test(String(x['@type'])) && !out.title) Object.assign(out, x);
      if (x['@graph']) walk(x['@graph']);
    };
    for (const s of all('script[type="application/ld+json"]')) {
      try { walk(JSON.parse(s.textContent)); } catch { /* bad JSON */ }
    }
    return out;
  }

  // Every "Show more" / "Read more" on the page, so the whole description is there.
  async function expand() {
    let n = 0;
    for (let round = 0; round < 3; round++) {
      const more = byText('button, a, [role="button"]', /^(show|read|see|view) (more|full description|all)\b/i).filter(visible);
      if (!more.length) break;
      for (const b of more) {
        b.click();
        n++;
        await sleep(350);
      }
      await sleep(400);
    }
    return n;
  }

  // Label/value cells such as "Salary / USD 100k-350k / yr": the value is the cell's text minus its label.
  function detail(label) {
    const el = byText('div, span, p, dt, small, td', new RegExp(`^${label}$`, 'i')).find(visible);
    if (!el) return '';
    let cell = el.parentElement;
    for (let i = 0; i < 3 && cell && one(cell.innerText) === one(el.innerText); i++) cell = cell.parentElement;
    return cell ? one(cell.innerText.replace(el.innerText, '')).slice(0, 120) : '';
  }

  async function jobicy() {
    const expanded = await expand();
    const ld = postingData();
    const h1 = all('h1').find(visible);
    const title = one(h1?.textContent || ld.title);

    // company: the posting data, else the name shown above the title, else the "About the company" card
    let company = one(ld.hiringOrganization?.name);
    if (!company && h1) {
      let prev = h1.previousElementSibling;
      while (prev && !one(prev.textContent)) prev = prev.previousElementSibling;
      company = one(prev?.textContent).slice(0, 80);
    }
    if (!company) {
      const about = byText('div, span, p', /^about the company$/i)[0];
      company = one(about?.nextElementSibling?.textContent || '').slice(0, 80);
    }

    const summaryHead = byText('div, span, p, h2, h3', /^ai summary$/i)[0];
    const summary = summaryHead ? one(sectionOf(summaryHead, 80).innerText.replace(summaryHead.innerText, '')) : '';

    const roleHead = byText('h1, h2, h3, h4, div', /^about this role\.?$/i).find(visible);
    let about = roleHead ? clean(sectionOf(roleHead).innerText) : '';
    about = about.replace(/^(opportunity details\s*)?about this role\.?\s*/i, '').replace(/\n?\s*show (more|less)\s*$/i, '');

    const skillsHead = byText('h1, h2, h3, h4, div', /^core skills$/i).find(visible);
    const skills = skillsHead
      ? [...new Set(all('a, span, button, li', sectionOf(skillsHead, 20)).map((e) => one(e.textContent))
        .filter((t) => t && t.length < 40 && !/^core skills$|skills and capabilities/i.test(t)))]
      : [];

    const info = {
      remoteFrom: detail('remote from'), salary: detail('salary'), employment: detail('employment'),
      experience: detail('experience'), department: detail('department'), applyBefore: detail('apply before')
    };
    const applyEl = findApply();
    return {
      title, company, summary, about: about.slice(0, 20000), skills, ...info, expanded,
      applyText: applyEl ? one(applyEl.textContent) : '', applyHref: applyEl?.href || ''
    };
  }

  // Form fields of the page itself (not the site's search box, not a pop-up).
  const fieldCount = () => all('input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=search]):not([type=checkbox]), textarea, select')
    .filter((el) => visible(el) && !el.closest('nav, header, [role="dialog"], [aria-modal="true"], dialog')).length;

  // Skill chips listed under a heading such as "Skills" (Himalayas) or "Technologies" (DevITjobs):
  // the short texts after the heading, up to the next heading.
  function chipsUnder(re) {
    const head = byText('h1, h2, h3, h4, h5, div, span, p, dt', re).find(visible);
    if (!head) return [];
    let el = head;
    for (let i = 0; i < 3 && el && !el.nextElementSibling; i++) el = el.parentElement;
    const out = [];
    for (let sib = el?.nextElementSibling, n = 0; sib && n < 4; sib = sib.nextElementSibling, n++) {
      if (/^H[1-6]$/.test(sib.tagName) || /^(job categories|browse similar|related|similar jobs)/i.test(one(sib.textContent))) break;
      // a chip: short text, not a box holding several chips ("• Python" with its dot is one chip)
      const chips = [sib, ...all('*', sib)].filter((e) => [...e.children].filter((c) => one(c.textContent).replace(/^[•·]$/, '')).length < 2);
      for (const e of chips) {
        const t = one(e.textContent).replace(/^[•·]\s*/, '');
        if (t.length > 1 && t.length < 40 && !re.test(t)) out.push(t);
      }
    }
    return [...new Set(out)].slice(0, 40);
  }

  // A pop-up in front of the page (newsletter, job alert, …), so it can be closed instead of filled in.
  // Pop-ups sit over the middle of the screen; headers, chat bubbles and cookie bars do not count.
  const COOKIES = /\bcookies?\b|consent|privacy (settings|preferences)/i;
  const inMiddle = (e) => {
    const r = e.getBoundingClientRect();
    return r.left < innerWidth / 2 && r.right > innerWidth / 2 && r.top < innerHeight / 2 && r.bottom > innerHeight / 2 && !COOKIES.test(e.innerText);
  };
  function findPopup() {
    let box = all('[role="dialog"], [aria-modal="true"], dialog[open], .modal.show, .modal.in, [class*="modal" i], [class*="popup" i]')
      .filter((e) => visible(e) && one(e.innerText).length > 20 && e.getBoundingClientRect().width > 200)
      // a real pop-up: marked as a dialog, or fixed on top of the page (not just a class name)
      .filter((e) => e.matches('[role="dialog"], [aria-modal="true"], dialog') || [e, e.parentElement, e.parentElement?.parentElement].some((x) => x && getComputedStyle(x).position === 'fixed'))
      .filter(inMiddle)[0];
    if (!box) {
      // something fixed on top of the middle of the screen
      for (let e = document.elementFromPoint(innerWidth / 2, innerHeight / 2); e && e !== document.body; e = e.parentElement) {
        const st = getComputedStyle(e);
        if ((st.position === 'fixed' || st.position === 'absolute') && Number(st.zIndex) >= 10 && one(e.innerText).length > 20 && !COOKIES.test(e.innerText)) { box = e; break; }
      }
    }
    return box || null;
  }
  function popup() {
    const box = findPopup();
    if (!box) return { open: false };
    const text = one(box.innerText).slice(0, 300);
    return {
      open: true, text,
      // a mailing list / alert sign-up, not the job application
      newsletter: /newsletter|subscribe|job alert|weekly email|daily email|notifications?|get (new )?jobs|email me|stay (up to date|updated)/i.test(text) && !box.querySelector('input[type="file"]'),
      form: box.querySelectorAll('input:not([type=hidden]), textarea, select').length
    };
  }
  async function closePopup() {
    const box = findPopup();
    if (!box) return true;
    const btn = all('button, a, [role="button"], span, div', box).find((e) => visible(e) &&
      (/^(close|no,? thanks|not now|maybe later|skip|dismiss|cancel|×|✕|x)$/i.test(one(e.textContent)) || /close|dismiss/i.test(e.getAttribute('aria-label') || '')));
    if (btn) btn.click();
    else document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', keyCode: 27, bubbles: true }));
    await sleep(600);
    return !findPopup();
  }

  const htmlText = (h) => {
    const d = document.createElement('div');
    d.innerHTML = String(h || '').replace(/<\/(p|li|h\d|div)>|<br\s*\/?>/gi, '$&\n');
    return clean(d.textContent);
  };

  // Any other posting page (Himalayas, company career pages, …): title, company and the whole
  // description after "Show more", plus whether the page already holds the application form.
  async function generic() {
    const expanded = await expand();
    const ld = postingData();
    const h1 = all('h1').find(visible);
    const title = one(ld.title || h1?.textContent);
    const company = one(ld.hiringOrganization?.name);
    let about = ld.description ? htmlText(ld.description) : '';
    if (about.length < 300) {
      const main = document.querySelector('main, article, [role="main"]') || document.body;
      about = clean(main.innerText);
    }
    const applyEl = findApply();
    return {
      title, company, summary: '', about: about.slice(0, 20000), skills: chipsUnder(/^(core skills|skills|technologies|tech stack|tags)$/i), expanded, fields: fieldCount(),
      salary: one(ld.baseSalary?.value?.value || ''), remoteFrom: '',
      applyText: applyEl ? one(applyEl.textContent) : '', applyHref: applyEl?.href || ''
    };
  }

  // The button that starts the application. Any wording with that meaning counts: "Apply", "Apply now",
  // "Apply for this job", "Apply on company site", "Create application", "Start application", "I'm interested", …
  const APPLY_WORDS = [
    // clear "go apply" buttons
    [/^(apply|apply now|apply today|apply online|apply here|quick apply|apply externally)\b/i, 6],
    [/^apply (for|to) (this |the )?(job|position|role|opening|vacancy)\b/i, 6],
    [/^apply (on|at|via|through) (the )?(company|employer|hiring company|external|their)?.{0,30}(site|website|page|portal)?$/i, 6],
    [/^(create|start|begin|continue|complete) (an |my |your |the )?(job )?application\b/i, 6],
    [/^(go|continue|proceed) to (the )?(application|apply|company site|job site)\b/i, 6],
    [/^(view|see) (and|&) apply\b|^apply with (resume|cv|profile)\b|^(interested\??|i'?m interested)( - apply| apply)?$/i, 5],
    [/^(easy apply|1-click apply|one-click apply)$/i, 4],
    // any short label that is about applying
    [/\bapply\b|\bapplication\b/i, 2]
  ];
  const NOT_APPLY = /linkedin|indeed|filter|how to apply|applied|applications? (status|tracker)|my applications|sign in|log ?in|save|alert|share|similar|withdraw/i;
  const label = (el) => one(el.textContent || el.value || el.getAttribute('aria-label') || el.title || '');

  function findApply() {
    const here = location.hostname.replace(/^www\./, '');
    const ext = (el) => el.href && !/^javascript:|^#/.test(el.getAttribute('href') || '#') && !el.href.includes(here);
    let best = null;
    let bestScore = 0;
    for (const el of all('a, button, [role="button"], [role="link"], input[type="submit"], input[type="button"]')) {
      const t = label(el);
      if (!t || t.length > 50 || NOT_APPLY.test(t) || el.disabled) continue;
      const word = APPLY_WORDS.find(([re]) => re.test(t));
      if (!word) continue;
      const score = word[1] + (/^apply now$/i.test(t) ? 1 : 0) + (ext(el) ? 2 : 0) + (el.target === '_blank' ? 1 : 0) + (visible(el) ? 1 : -3);
      if (score > bestScore) { best = el; bestScore = score; }
    }
    return best;
  }

  function clickApply() {
    const el = findApply();
    if (!el) return { clicked: false };
    el.scrollIntoView({ block: 'center' });
    el.click();
    return { clicked: true, text: label(el), href: el.href || '' };
  }

  // The job on this page, like Jobright's scrapeDetail: title, company, location, salary and the job description.
  async function scrapeDetail() {
    const isJobicy = /(^|\.)jobicy\.com$/i.test(location.hostname);
    const b = isJobicy ? await jobicy() : await generic();
    const jd = [
      b.title && `${b.title}${b.company ? ` at ${b.company}` : ''}`,
      [b.remoteFrom && `Remote from: ${b.remoteFrom}`, b.employment && `Employment: ${b.employment}`, b.experience && `Experience: ${b.experience}`,
        b.salary && `Salary: ${b.salary}`, b.department && `Department: ${b.department}`].filter(Boolean).join(' | '),
      b.skills.length ? `Core skills: ${b.skills.join(', ')}` : '',
      b.summary ? `Summary: ${b.summary}` : '',
      b.about ? `About this role:\n${b.about}` : ''
    ].filter(Boolean).join('\n\n');
    return {
      title: b.title, company: b.company, jd, salary: b.salary || '',
      location: b.remoteFrom ? `Remote (${b.remoteFrom})` : '', remote: isJobicy,
      formHere: !isJobicy && b.fields >= 3 // the link is the application form itself
    };
  }

  window.__abBoards = { scrapeDetail, clickApply, popup, closePopup, fieldCount };
})();
