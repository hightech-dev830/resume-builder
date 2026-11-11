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
    const fields = all('input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=search]), textarea, select')
      .filter((el) => visible(el) && !el.closest('nav, header')).length;
    const applyEl = findApply();
    return {
      title, company, summary: '', about: about.slice(0, 20000), skills: [], expanded, fields,
      salary: one(ld.baseSalary?.value?.value || ''), remoteFrom: '',
      applyText: applyEl ? one(applyEl.textContent) : '', applyHref: applyEl?.href || ''
    };
  }

  // "Apply Now" that leads to the company's page first; "Apply for this job" only scrolls on Jobicy.
  function findApply() {
    const here = location.hostname.replace(/^www\./, '');
    const cands = all('a, button, [role="button"], input[type="submit"], input[type="button"]')
      .filter((el) => visible(el) || el.tagName === 'A')
      .filter((el) => { const t = one(el.textContent || el.value); return t.length < 50 && /^apply\b|\bapply (now|for this|on company|here)\b/i.test(t) && !/linkedin|indeed|filter/i.test(t); });
    const ext = (el) => el.href && !/^javascript:|^#/.test(el.getAttribute('href') || '#') && !el.href.includes(here);
    const score = (el) => (/^apply now$/i.test(one(el.textContent)) ? 3 : 0) + (ext(el) ? 2 : 0) + (el.target === '_blank' ? 1 : 0);
    return cands.sort((a, b) => score(b) - score(a))[0] || null;
  }

  function clickApply() {
    const el = findApply();
    if (!el) return { clicked: false };
    el.scrollIntoView({ block: 'center' });
    el.click();
    return { clicked: true, text: one(el.textContent || el.value), href: el.href || '' };
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

  window.__abBoards = { scrapeDetail, clickApply };
})();
