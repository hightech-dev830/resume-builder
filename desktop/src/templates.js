// Resume + cover letter HTML renderer. The same HTML drives the live preview and
// the PDF (Electron printToPDF), so what you see is what gets saved.

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function fmtDate(d) {
  if (!d) return '';
  const m = /^(\d{4})(?:-(\d{1,2}))?/.exec(String(d));
  if (!m) return String(d);
  return m[2] ? `${MONTHS[Number(m[2]) - 1]} ${m[1]}` : m[1];
}

function dateRange(start, end, present = 'Present') {
  const s = fmtDate(start);
  const e = end ? fmtDate(end) : present;
  return s ? `${s} – ${e}` : e;
}

function paragraphs(text) {
  return String(text || '')
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => `<p>${esc(p)}</p>`)
    .join('');
}

function contactItems(b, opts) {
  const items = [];
  const loc = b.location || {};
  const locStr = [loc.city, loc.region].filter(Boolean).join(', ');
  const order = opts.contactOrder || ['email', 'phone', 'location', 'profiles'];
  for (const k of order) {
    if (k === 'email' && b.email) items.push(`<a href="mailto:${esc(b.email)}">${esc(b.email)}</a>`);
    if (k === 'phone' && b.phone) items.push(esc(b.phone));
    if (k === 'location' && locStr) items.push(esc(locStr));
    if (k === 'profiles') {
      for (const p of b.profiles || []) {
        const url = p.url || profileUrl(p);
        const text = opts.contactStacked
          ? p.network || 'Profile'
          : opts.fullUrls ? url : url.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '');
        items.push(`<a href="${esc(url)}">${esc(text)}</a>`);
      }
    }
    if (k === 'url' && b.url) items.push(`<a href="${esc(b.url)}">${esc(b.url.replace(/^https?:\/\//, ''))}</a>`);
  }
  return items;
}

function profileUrl(p) {
  const n = (p.network || '').toLowerCase();
  if (n === 'linkedin') return `https://www.linkedin.com/in/${p.username}/`;
  if (n === 'github') return `https://github.com/${p.username}`;
  return p.username || '';
}

// ---------- section renderers ----------

function renderHeader(r, opts) {
  const b = r.basics || {};
  const sep = `<span class="sep">${opts.contactSep ?? '|'}</span>`;
  const contacts = contactItems(b, opts);
  const contactHtml = opts.contactStacked
    ? contacts.map((c) => `<div>${c}</div>`).join('')
    : contacts.join(sep);
  return `<header class="hd">
    <h1 class="name">${esc(b.name)}</h1>
    ${b.label && opts.showLabel !== false ? `<div class="label">${esc(b.label)}</div>` : ''}
    <div class="contact">${contactHtml}</div>
  </header>`;
}

function renderProfile(r, opts) {
  const b = r.basics || {};
  if (!b.summary && !(b.highlights || []).length) return '';
  const hl = opts.showHighlights !== false && (b.highlights || []).length
    ? `<ul class="hl">${b.highlights.map((h) => `<li><b>${esc(h.name)}:</b> ${esc(h.value)}</li>`).join('')}</ul>`
    : '';
  return section('profile', opts.titles.profile, `<div class="summary">${paragraphs(b.summary)}</div>${hl}`);
}

function renderWork(r, opts) {
  const jobs = r.work || [];
  if (!jobs.length) return '';
  const body = jobs.map((w) => renderJob(w, opts)).join('');
  return section('work', opts.titles.work, body);
}

function renderJob(w, opts) {
  const dates = esc(dateRange(w.startDate, w.endDate));
  const company = esc(w.name) + (w.context && opts.showContext !== false ? ` <span class="ctx">– ${esc(w.context)}</span>` : '');
  const loc = esc(w.location || '');
  let head;
  if (opts.entryStyle === 'company-first') {
    head = `<div class="row r1"><span class="company">${company}</span><span class="loc">${loc}</span></div>
            <div class="row r2"><span class="position">${esc(w.position)}</span><span class="dates">${dates}</span></div>`;
  } else if (opts.entryStyle === 'timeline') {
    head = `<div class="row r1"><span class="position">${esc(w.position)}</span></div>
            <div class="row r2"><span class="company">${company}</span>${loc ? `<span class="sep">|</span><span class="loc">${loc}</span>` : ''}</div>`;
  } else {
    head = `<div class="row r1"><span class="position">${esc(w.position)}</span><span class="dates">${dates}</span></div>
            <div class="row r2"><span class="company">${company}</span>${loc ? `<span class="sep">|</span><span class="loc">${loc}</span>` : ''}</div>`;
  }
  const desc = w.description && opts.showDescription !== false ? `<div class="desc">${esc(w.description)}</div>` : '';
  const bullets = (w.highlights || []).length
    ? `<ul class="bullets">${w.highlights.map((h) => `<li>${esc(h)}</li>`).join('')}</ul>`
    : '';
  if (opts.entryStyle === 'timeline') {
    return `<div class="job tl"><div class="tl-date">${dates}</div><div class="tl-body"><div class="jh">${head}</div>${desc}${bullets}</div></div>`;
  }
  return `<div class="job"><div class="jh">${head}</div>${desc}${bullets}</div>`;
}

function renderSkills(r, opts) {
  const groups = r.skills || [];
  if (!groups.length) return '';
  let body;
  if (opts.skillsStyle === 'tags') {
    body = groups.map((g) => `<div class="sg"><div class="sg-name">${esc(g.name)}</div><div class="tags">${
      (g.keywords || []).map((k) => `<span class="tag">${esc(k)}</span>`).join('')}</div></div>`).join('');
  } else if (opts.skillsStyle === 'stacked') {
    body = groups.map((g) => `<div class="sg"><div class="sg-name">${esc(g.name)}</div><div class="sg-kw">${
      esc((g.keywords || []).join(', '))}</div></div>`).join('');
  } else if (opts.skillsStyle === 'grid') {
    body = `<div class="skill-grid">${groups.map((g) => `<div class="sg-name">${esc(g.name)}</div><div class="sg-kw">${
      esc((g.keywords || []).join(', '))}</div>`).join('')}</div>`;
  } else {
    body = groups.map((g) => `<div class="sline"><b>${esc(g.name)}:</b> ${esc((g.keywords || []).join(', '))}</div>`).join('');
  }
  return section('skills', opts.titles.skills, body);
}

function renderEducation(r, opts) {
  const ed = r.education || [];
  if (!ed.length) return '';
  const body = ed.map((e) => {
    const degree = [e.studyType, e.area].filter(Boolean).join(', ');
    const dates = esc(dateRange(e.startDate, e.endDate, ''));
    if (opts.compactEducation) {
      return `<div class="edu"><div class="degree">${esc(degree)}</div><div class="inst">${esc(e.institution)}</div><div class="dates">${dates}</div></div>`;
    }
    return `<div class="edu"><div class="row r1"><span class="degree">${esc(degree)}</span><span class="dates">${dates}</span></div>
      <div class="inst">${esc(e.institution)}</div></div>`;
  }).join('');
  return section('education', opts.titles.education, body);
}

function renderLanguages(r, opts) {
  const l = r.languages || [];
  if (!l.length) return '';
  return section('languages', opts.titles.languages,
    `<div class="langs">${l.map((x) => `<span><b>${esc(x.language)}</b>${x.fluency ? ` (${esc(x.fluency)})` : ''}</span>`).join('<span class="sep">·</span>')}</div>`);
}

function section(key, title, body) {
  return `<section class="sec sec-${key}"><h2>${esc(title)}</h2><div class="sec-body">${body}</div></section>`;
}

const RENDERERS = {
  profile: renderProfile,
  work: renderWork,
  skills: renderSkills,
  education: renderEducation,
  languages: renderLanguages
};

// ---------- base CSS shared by every template ----------

const BASE_CSS = `
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { background: #fff; }
body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
a { color: inherit; text-decoration: none; }
p { margin: 0 0 6px; }
p:last-child { margin-bottom: 0; }
ul { padding-left: 16px; }
.row { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.row > :last-child:not(:first-child) { white-space: nowrap; }
.sep { margin: 0 7px; opacity: .6; }
.jh, .desc { break-after: avoid; }
li { break-inside: avoid; }
h2 { break-after: avoid; }
.hl { margin-top: 6px; }
.bullets li, .hl li { margin-bottom: 2px; }
.tags { display: flex; flex-wrap: wrap; gap: 4px; }
.skill-grid { display: grid; grid-template-columns: max-content 1fr; column-gap: 14px; row-gap: 3px; }
@media screen { body { padding: 0; } }
`;

// ---------- templates ----------

const DEFAULT_TITLES = {
  profile: 'Professional Overview',
  work: 'Work Experience',
  skills: 'Skills',
  education: 'Education',
  languages: 'Languages'
};

const TEMPLATES = [
  {
    id: 'kevin',
    name: 'Kevin Original',
    description: 'Matches your current CV: bold centered name, underlined title, navy rules, role-first entries.',
    opts: { entryStyle: 'role-first', skillsStyle: 'lines', order: ['profile', 'work', 'skills', 'education', 'languages'], fullUrls: true },
    css: `
@page { size: Letter; margin: 0.5in 0.6in; }
body { font-family: Calibri, Carlito, 'Segoe UI', sans-serif; font-size: 10.5pt; color: #111; line-height: 1.38; }
.hd { text-align: center; margin-bottom: 14px; }
.name { font-size: 22pt; font-weight: 700; }
.label { font-size: 13pt; font-weight: 700; text-decoration: underline; margin: 2px 0 4px; }
.contact a:first-child { color: #1a56c4; text-decoration: underline; }
.sec { margin-top: 16px; }
h2 { font-size: 11pt; font-weight: 700; text-transform: uppercase; border-bottom: 1.2px solid #1f3864; padding-bottom: 2px; margin-bottom: 8px; }
.job { margin-bottom: 10px; }
.position { font-weight: 700; font-size: 11pt; }
.r1 .dates { font-weight: 700; }
.r2 { justify-content: flex-start; gap: 0; color: #666; font-style: italic; }
.desc { margin: 2px 0 2px; }
.sline { margin-bottom: 3px; }
.degree { font-weight: 700; }
`
  },
  {
    id: 'professional',
    name: 'Professional',
    description: 'Centred blue name, blue section headings on black rules, employer-first entries. ATS-safe.',
    opts: { entryStyle: 'company-first', skillsStyle: 'lines', order: ['profile', 'skills', 'work', 'education', 'languages'],
      titles: { profile: 'Profile', work: 'Experience' }, contactOrder: ['location', 'phone', 'email', 'profiles'], showContext: false },
    css: `
@page { size: Letter; margin: 0.5in 0.65in; }
body { font-family: 'Times New Roman', Times, serif; font-size: 11pt; color: #000; line-height: 1.25; }
.hd { text-align: center; margin-bottom: 8px; }
.name { font-size: 24pt; font-weight: 400; color: #3b6db3; text-transform: uppercase; letter-spacing: 1px; }
.label { font-size: 11.5pt; margin: 2px 0 3px; }
.contact { font-size: 10pt; }
.contact .sep { margin: 0 9px; color: #999; }
.sec { margin-top: 10px; }
h2 { font-size: 12.5pt; font-weight: 700; color: #3b6db3; text-transform: uppercase; letter-spacing: 1px; border-bottom: 1px solid #000; padding-bottom: 1px; margin-bottom: 6px; }
.job { margin-bottom: 8px; }
.company { font-weight: 700; }
.position { font-style: italic; }
.desc { font-style: italic; margin: 1px 0 2px; }
.sline { margin-bottom: 2px; }
.degree { font-weight: 700; }
`
  },
  {
    id: 'classic',
    name: 'Classic',
    description: 'Traditional single-column layout with a serif face. Safest for ATS screening.',
    opts: { entryStyle: 'role-first', skillsStyle: 'lines', order: ['profile', 'work', 'skills', 'education', 'languages'],
      titles: { profile: 'Summary', work: 'Professional Experience', skills: 'Technical Skills' }, contactSep: '•' },
    css: `
@page { size: Letter; margin: 0.6in 0.7in; }
body { font-family: Georgia, 'Times New Roman', serif; font-size: 10.5pt; color: #111; line-height: 1.35; }
.hd { text-align: center; margin-bottom: 10px; border-bottom: 2px solid #111; padding-bottom: 8px; }
.name { font-size: 22pt; font-weight: 700; letter-spacing: .5px; }
.label { font-size: 11pt; font-style: italic; margin: 2px 0 4px; }
.contact { font-size: 9.5pt; }
.sec { margin-top: 12px; }
h2 { font-size: 11pt; font-weight: 700; text-transform: uppercase; letter-spacing: 1.5px; border-bottom: .75px solid #111; padding-bottom: 2px; margin-bottom: 7px; }
.job { margin-bottom: 9px; }
.position { font-weight: 700; }
.r2 { justify-content: flex-start; gap: 0; font-style: italic; }
.desc { margin: 2px 0; }
.sline { margin-bottom: 2px; }
.degree { font-weight: 700; }
`
  },
  {
    id: 'modern',
    name: 'Modern',
    description: 'Two-column layout with an accent-colored sidebar for contact, skills and education.',
    layout: 'sidebar',
    opts: { entryStyle: 'role-first', skillsStyle: 'tags', contactStacked: true, compactEducation: true, showHighlights: false,
      sidebar: ['skills', 'education', 'languages'], main: ['profile', 'work'], titles: { profile: 'About', work: 'Experience' } },
    css: `
@page { size: Letter; margin: 0.5in 0.5in; }
body { font-family: 'Segoe UI', Calibri, Arial, sans-serif; font-size: 9.6pt; color: #1e293b; line-height: 1.4; }
.grid { display: grid; grid-template-columns: 2.05in 1fr; gap: 22px; }
.hd { grid-column: 1 / -1; display: grid; grid-template-columns: 2.05in 1fr; gap: 22px; align-items: end; padding-bottom: 10px; border-bottom: 3px solid #0f766e; margin-bottom: 4px; }
.hd .name { grid-column: 2; font-size: 25pt; font-weight: 700; color: #0f172a; line-height: 1.05; }
.hd .label { grid-column: 2; font-size: 11.5pt; color: #0f766e; font-weight: 600; }
.hd .contact { grid-column: 1; grid-row: 1 / span 2; font-size: 8.8pt; color: #334155; }
.hd .contact div { margin-bottom: 1px; }
.hd .contact a[href^="http"] { color: #0f766e; text-decoration: underline; }
aside { border-right: 1px solid #cbd5e1; padding-right: 14px; }
aside .sec { margin-top: 12px; }
aside h2 { font-size: 9.5pt; color: #0f766e; text-transform: uppercase; letter-spacing: 1.2px; margin-bottom: 6px; }
.sg { margin-bottom: 8px; }
.sg-name { font-weight: 600; font-size: 8.8pt; margin-bottom: 3px; }
.tag { background: #ccfbf1; color: #134e4a; border-radius: 3px; padding: 1px 5px; font-size: 8.2pt; }
.edu { margin-bottom: 6px; }
.edu .degree { font-weight: 600; }
.edu .dates, .edu .inst { font-size: 8.8pt; color: #475569; }
.langs { display: flex; flex-direction: column; } .langs .sep { display: none; }
main .sec { margin-top: 12px; }
main h2 { font-size: 11pt; color: #0f172a; text-transform: uppercase; letter-spacing: 1.2px; border-bottom: 1px solid #cbd5e1; padding-bottom: 2px; margin-bottom: 7px; }
.job { margin-bottom: 10px; }
.position { font-weight: 700; font-size: 10.5pt; }
.dates { color: #0f766e; font-weight: 600; font-size: 9pt; }
.r2 { justify-content: flex-start; gap: 0; color: #475569; font-weight: 600; }
.desc { color: #475569; font-style: italic; margin: 2px 0; }
`
  },
  {
    id: 'minimal',
    name: 'Minimal',
    description: 'Clean sans-serif, generous whitespace, gray headings and no rules.',
    opts: { entryStyle: 'role-first', skillsStyle: 'grid', order: ['profile', 'work', 'skills', 'education', 'languages'],
      contactSep: '/', titles: { profile: 'Profile', work: 'Experience' } },
    css: `
@page { size: Letter; margin: 0.65in 0.75in; }
body { font-family: Arial, Helvetica, sans-serif; font-size: 9.8pt; color: #222; line-height: 1.45; }
.hd { margin-bottom: 14px; }
.name { font-size: 26pt; font-weight: 300; letter-spacing: -.5px; }
.label { font-size: 11pt; color: #777; margin: 0 0 6px; }
.contact { font-size: 9pt; color: #555; }
.sec { margin-top: 16px; }
h2 { font-size: 9pt; font-weight: 700; color: #999; text-transform: uppercase; letter-spacing: 2.5px; margin-bottom: 7px; }
.job { margin-bottom: 11px; }
.position { font-weight: 700; }
.dates { color: #888; font-size: 9pt; }
.r2 { justify-content: flex-start; gap: 0; color: #555; }
.desc { color: #666; margin: 2px 0; }
.sg-name { font-weight: 700; }
.degree { font-weight: 700; }
.inst { color: #555; }
`
  },
  {
    id: 'executive',
    name: 'Executive',
    description: 'Navy header band with white type, Calibri body, strong hierarchy for senior roles.',
    opts: { entryStyle: 'role-first', skillsStyle: 'grid', order: ['profile', 'work', 'skills', 'education', 'languages'],
      titles: { profile: 'Executive Summary', work: 'Career History', skills: 'Core Technologies' } },
    css: `
@page { size: Letter; margin: 0.45in 0.55in; }
body { font-family: Calibri, Carlito, 'Segoe UI', sans-serif; font-size: 10.5pt; color: #1b1b1b; line-height: 1.35; }
.hd { background: #1f2f4f; color: #fff; padding: 16px 20px 13px; margin-bottom: 6px; }
.name { font-size: 24pt; font-weight: 700; letter-spacing: .5px; }
.label { font-size: 12pt; color: #c9d6ee; margin: 1px 0 6px; text-transform: uppercase; letter-spacing: 1.5px; }
.contact { font-size: 9.5pt; color: #e6ecf7; }
.sec { margin-top: 13px; }
h2 { font-size: 12pt; color: #1f2f4f; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; border-bottom: 2px solid #b8913b; padding-bottom: 2px; margin-bottom: 7px; }
.job { margin-bottom: 10px; }
.position { font-weight: 700; color: #1f2f4f; font-size: 11pt; }
.dates { font-weight: 700; color: #1f2f4f; }
.r2 { justify-content: flex-start; gap: 0; font-weight: 600; color: #555; }
.desc { font-style: italic; color: #444; margin: 2px 0; }
.sg-name { font-weight: 700; color: #1f2f4f; }
.degree { font-weight: 700; }
`
  },
  {
    id: 'compact',
    name: 'Compact',
    description: 'Dense layout with small type and tight spacing — fits more on fewer pages.',
    opts: { entryStyle: 'role-first', skillsStyle: 'lines', order: ['profile', 'skills', 'work', 'education', 'languages'],
      showDescription: false, titles: { profile: 'Summary', work: 'Experience' } },
    css: `
@page { size: Letter; margin: 0.4in 0.5in; }
body { font-family: Calibri, Carlito, Arial, sans-serif; font-size: 9.6pt; color: #111; line-height: 1.22; }
.hd { display: flex; justify-content: space-between; align-items: flex-end; border-bottom: 2px solid #333; padding-bottom: 4px; margin-bottom: 4px; flex-wrap: wrap; }
.name { font-size: 18pt; font-weight: 700; }
.label { font-size: 10.5pt; font-weight: 600; color: #444; flex: 1; margin-left: 12px; }
.contact { width: 100%; font-size: 8.8pt; margin-top: 2px; }
.contact .sep { margin: 0 4px; }
.sec { margin-top: 7px; }
h2 { font-size: 10pt; font-weight: 700; text-transform: uppercase; background: #eee; padding: 1px 5px; margin-bottom: 4px; }
.summary p { margin-bottom: 3px; }
.hl { margin-top: 3px; }
.job { margin-bottom: 5px; }
.position { font-weight: 700; }
.dates { font-weight: 600; }
.r2 { justify-content: flex-start; gap: 0; font-style: italic; color: #444; }
.bullets li, .hl li { margin-bottom: 0; }
.sline { margin-bottom: 1px; }
.degree { font-weight: 700; }
`
  },
  {
    id: 'technical',
    name: 'Technical',
    description: 'Developer-style: monospace headings, skills up front, slate and green accents.',
    opts: { entryStyle: 'role-first', skillsStyle: 'grid', order: ['profile', 'skills', 'work', 'education', 'languages'],
      contactSep: '·', titles: { profile: 'about', work: 'experience', skills: 'stack', education: 'education', languages: 'languages' } },
    css: `
@page { size: Letter; margin: 0.5in 0.6in; }
body { font-family: 'Segoe UI', Arial, sans-serif; font-size: 9.8pt; color: #1f2933; line-height: 1.4; }
.hd { margin-bottom: 8px; }
.name { font-family: Consolas, 'Courier New', monospace; font-size: 22pt; font-weight: 700; color: #111827; }
.name::before { content: '> '; color: #16a34a; }
.label { font-family: Consolas, monospace; font-size: 10.5pt; color: #16a34a; margin: 1px 0 4px; }
.contact { font-size: 9pt; color: #4b5563; }
.sec { margin-top: 12px; }
h2 { font-family: Consolas, 'Courier New', monospace; font-size: 11pt; color: #111827; margin-bottom: 6px; border-bottom: 1px dashed #9ca3af; padding-bottom: 2px; }
h2::before { content: '// '; color: #16a34a; }
.skill-grid .sg-name { font-family: Consolas, monospace; font-weight: 700; color: #374151; font-size: 9pt; }
.job { margin-bottom: 9px; }
.position { font-weight: 700; color: #111827; }
.dates { font-family: Consolas, monospace; font-size: 8.8pt; color: #4b5563; }
.r2 { justify-content: flex-start; gap: 0; color: #16a34a; font-weight: 600; }
.desc { color: #4b5563; margin: 2px 0; }
.degree { font-weight: 700; }
`
  },
  {
    id: 'elegant',
    name: 'Elegant',
    description: 'Garamond serif, small caps, gold accents and thin double rules.',
    opts: { entryStyle: 'company-first', skillsStyle: 'lines', order: ['profile', 'work', 'skills', 'education', 'languages'],
      contactSep: '◆', titles: { profile: 'Profile', work: 'Experience', skills: 'Expertise' } },
    css: `
@page { size: Letter; margin: 0.6in 0.75in; }
body { font-family: Garamond, 'EB Garamond', Georgia, serif; font-size: 11pt; color: #222; line-height: 1.32; }
.hd { text-align: center; margin-bottom: 10px; }
.name { font-size: 26pt; font-weight: 400; font-variant: small-caps; letter-spacing: 3px; color: #2b2b2b; }
.label { font-size: 11.5pt; font-style: italic; color: #8a6d2f; margin: 0 0 5px; }
.contact { font-size: 9.8pt; }
.contact .sep { color: #b8913b; font-size: 7pt; }
.sec { margin-top: 13px; }
h2 { font-size: 13pt; font-weight: 400; font-variant: small-caps; letter-spacing: 2px; text-align: center; color: #8a6d2f; border-top: 3px double #b8913b; border-bottom: 1px solid #e0cfa6; padding: 2px 0; margin-bottom: 8px; }
.job { margin-bottom: 9px; }
.company { font-weight: 700; font-variant: small-caps; letter-spacing: .5px; font-size: 11.5pt; }
.position { font-style: italic; }
.loc, .dates { font-style: italic; color: #555; }
.desc { margin: 2px 0; color: #444; }
.sline { margin-bottom: 2px; }
.degree { font-weight: 700; }
`
  },
  {
    id: 'bold',
    name: 'Bold',
    description: 'Large left-aligned name, crimson accent bars on headings — confident and modern.',
    opts: { entryStyle: 'role-first', skillsStyle: 'stacked', order: ['profile', 'work', 'skills', 'education', 'languages'],
      titles: { profile: 'Profile', work: 'Experience' } },
    css: `
@page { size: Letter; margin: 0.55in 0.6in; }
body { font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; font-size: 9.8pt; color: #1a1a1a; line-height: 1.4; }
.hd { margin-bottom: 10px; border-left: 8px solid #b91c1c; padding-left: 14px; }
.name { font-size: 30pt; font-weight: 800; line-height: 1; letter-spacing: -1px; text-transform: uppercase; }
.label { font-size: 12.5pt; color: #b91c1c; font-weight: 700; margin: 4px 0 5px; }
.contact { font-size: 9pt; color: #444; }
.sec { margin-top: 13px; }
h2 { font-size: 12pt; font-weight: 800; text-transform: uppercase; color: #1a1a1a; display: flex; align-items: center; gap: 8px; margin-bottom: 7px; }
h2::after { content: ''; flex: 1; height: 2px; background: #b91c1c; }
.job { margin-bottom: 10px; }
.position { font-weight: 800; font-size: 10.5pt; }
.dates { font-weight: 700; color: #b91c1c; font-size: 9pt; }
.r2 { justify-content: flex-start; gap: 0; color: #555; font-weight: 600; }
.desc { color: #555; margin: 2px 0; }
.sg { display: grid; grid-template-columns: 1.45in 1fr; margin-bottom: 3px; }
.sg-name { font-weight: 700; }
.degree { font-weight: 700; }
`
  },
  {
    id: 'timeline',
    name: 'Timeline',
    description: 'Dates in a left rail beside each role, purple accents, easy to scan career progression.',
    opts: { entryStyle: 'timeline', skillsStyle: 'grid', order: ['profile', 'work', 'skills', 'education', 'languages'],
      titles: { profile: 'Profile', work: 'Experience' } },
    css: `
@page { size: Letter; margin: 0.55in 0.6in; }
body { font-family: Calibri, Carlito, 'Segoe UI', sans-serif; font-size: 10.3pt; color: #1f1f2e; line-height: 1.36; }
.hd { margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px solid #ddd6fe; }
.name { font-size: 24pt; font-weight: 700; color: #4c1d95; }
.label { font-size: 12pt; color: #6d28d9; margin-bottom: 4px; }
.contact { font-size: 9.5pt; color: #444; }
.sec { margin-top: 12px; }
h2 { font-size: 11.5pt; color: #4c1d95; text-transform: uppercase; letter-spacing: 1.3px; margin-bottom: 7px; }
.job.tl { display: grid; grid-template-columns: 1.2in 1fr; gap: 12px; margin-bottom: 10px; }
.tl-date { font-size: 9pt; font-weight: 700; color: #6d28d9; padding-top: 2px; text-align: right; }
.tl-body { border-left: 2px solid #ddd6fe; padding-left: 12px; }
.position { font-weight: 700; font-size: 10.8pt; }
.r2 { justify-content: flex-start; gap: 0; color: #555; font-style: italic; }
.desc { margin: 2px 0; color: #444; }
.sg-name { font-weight: 700; color: #4c1d95; }
.degree { font-weight: 700; }
`
  }
];

function getTemplate(id) {
  return TEMPLATES.find((t) => t.id === id) || TEMPLATES[0];
}

function listTemplates() {
  return TEMPLATES.map(({ id, name, description }) => ({ id, name, description }));
}

function renderResumeHtml(resume, templateId) {
  const tpl = getTemplate(templateId);
  const opts = { ...tpl.opts, titles: { ...DEFAULT_TITLES, ...(tpl.opts.titles || {}) } };
  const r = resume || {};
  let body;
  if (tpl.layout === 'sidebar') {
    body = `${renderHeader(r, opts)}<div class="grid"><aside>${opts.sidebar.map((k) => RENDERERS[k](r, opts)).join('')}</aside>
      <main>${opts.main.map((k) => RENDERERS[k](r, opts)).join('')}</main></div>`;
  } else {
    body = renderHeader(r, opts) + opts.order.map((k) => RENDERERS[k](r, opts)).join('');
  }
  return `<!doctype html><html><head><meta charset="utf-8"><title>${esc(r.basics?.name || 'Resume')}</title>
<style>${BASE_CSS}${tpl.css}</style></head><body class="tpl-${tpl.id}">${body}</body></html>`;
}

// Cover letter reuses the resume template's header + fonts so the pair matches.
function renderCoverLetterHtml(resume, letter, templateId, job = {}) {
  const tpl = getTemplate(templateId);
  const opts = { ...tpl.opts, contactStacked: false, titles: DEFAULT_TITLES };
  const r = resume || {};
  const today = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
  const recipient = [job.company ? `${esc(job.company)}` : '', job.role ? `Re: ${esc(job.role)}` : ''].filter(Boolean).join('<br>');
  const header = tpl.layout === 'sidebar'
    ? renderHeader(r, { ...opts, contactStacked: false }).replace('class="hd"', 'class="hd cl-hd"')
    : renderHeader(r, opts);
  return `<!doctype html><html><head><meta charset="utf-8"><title>Cover Letter</title>
<style>${BASE_CSS}${tpl.css}
.cl-hd { display: block !important; }
.cl-hd .name, .cl-hd .label, .cl-hd .contact { grid-column: auto !important; grid-row: auto !important; }
.letter { margin-top: 22px; font-size: 1.05em; line-height: 1.5; }
.letter .meta { margin-bottom: 18px; }
.letter p { margin: 0 0 11px; }
</style></head><body class="tpl-${tpl.id}">${header}
<div class="letter"><div class="meta">${today}${recipient ? `<br><br>${recipient}` : ''}</div>${
    String(letter || '').split(/\n\s*\n/).map((p) => `<p>${esc(p.trim()).replace(/\n/g, '<br>')}</p>`).join('')
  }</div></body></html>`;
}

module.exports = { listTemplates, renderResumeHtml, renderCoverLetterHtml, fmtDate };
