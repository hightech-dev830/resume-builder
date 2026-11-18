// Side panel app: login, dashboard, job feed, the 3-step application wizard (job → resume → fill),
// applications, profiles and settings. Plain DOM, no framework; every view is re-rendered from state `S`.
import * as store from './lib/store.js';
import * as jobsLib from './lib/jobs.js';
import { listTemplates, renderResumeHtml, renderCoverLetterHtml } from './lib/templates.js';
import { buildPrompt, fixPrompt, parseJson, mergeTailored, toText, fromText, coverPrompt, cleanLetter, TAILOR_SCHEMA } from './lib/resume.js';
import { atsScore, profileTechSet, quickMatch } from './lib/ats.js';
import { validate, issuesForModel } from './lib/validate.js';
import { askChat, CHAT_SITES } from './lib/chat.js';
import { openaiChat } from './lib/openai.js';
import { htmlToPdf } from './lib/pdf.js';
import { resumeDocx, coverDocx, styleFromHtml, DOCX_TYPE } from './lib/docx.js';
import { emptyProfile, importProfile, PERSONAL_FIELDS, STACK_MODES, syncBasics, totalYears } from './lib/profiles.js';
import { esc, timeAgo, localDay, slug, b64ToBytes, bytesToB64, hostOf } from './lib/util.js';
import { configured as sbConfigured } from './lib/supabase.js';

const $app = document.getElementById('app');
const TEMPLATES = listTemplates();
const STATUSES = { draft: 'Draft', resume_ready: 'Resume ready', filling: 'Filling', applied: 'Applied', interview: 'Interview', offer: 'Offer', rejected: 'Rejected', skipped: 'Skipped' };
const STATUS_CHIP = { applied: 'green', interview: 'blue', offer: 'green', rejected: 'red', skipped: '', filling: 'amber', resume_ready: 'acc', draft: '' };
const ENGINES = { deepseek: 'DeepSeek chat', chatgpt: 'ChatGPT chat', openai: 'OpenAI API' };
const DEFAULT_FILTERS = { source: '', type: '', mode: 'Remote', age: '', currency: '', sort: 'latest', platforms: [], seniority: '', category: '', skills: [], hideApplied: true, hideAppliedCo: true, hideViewed: false, hideFlagged: true };

const I = {
  users: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M19 8v6M22 11h-6"/></svg>',
  case: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>',
  send: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13M22 2l-7 20-4-9-9-4z"/></svg>',
  list: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h4"/></svg>',
  gear: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
  refresh: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/></svg>',
  spark: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.9 4.6L18.5 9l-4.6 1.9L12 15.5l-1.9-4.6L5.5 9l4.6-1.4z"/><path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9z"/></svg>',
  dl: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>',
  flag: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1zM4 22v-7"/></svg>',
  ext: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14 21 3"/></svg>',
  eye: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>',
  filter: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h16M7 12h10M10 18h4"/></svg>',
  chev: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg>',
  down: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>'
};
const LOGO = `<div class="logo">${I.spark.replace(/16/g, '22')}</div>`;
const LOGO_SM = `<div class="logo sm">${I.spark.replace(/16/g, '15')}</div>`;

const S = {
  session: null, settings: null, profiles: [], apps: [], active: { profileId: '', appId: '' },
  view: 'home', menu: false, toast: null,
  jobs: { cache: null, marks: null, page: 1, q: '', field: 'all', showFilters: false, f: { ...DEFAULT_FILTERS }, busy: false, progress: '' },
  wiz: { step: 1, app: null, text: '', ats: null, val: null, busy: '', status: '', statusKind: '', engine: '', abort: null, log: [], coverBusy: false, coverStatus: '', fillBusy: false, files: [] },
  prof: { editId: null, tab: 'personal', draft: null },
  apps_f: { q: '', status: '', profile: '' }
};

// ---------- helpers ----------

const profile = () => S.profiles.find((p) => p.id === S.active.profileId) || S.profiles[0] || null;
const opt = (v, label, cur) => `<option value="${esc(v)}"${String(v) === String(cur ?? '') ? ' selected' : ''}>${esc(label)}</option>`;
const sameDay = (t) => t && localDay(t) === localDay();
const fmtMoney = (n, cur) => (n ? `${cur === 'EUR' ? '€' : cur === 'GBP' ? '£' : '$'}${Math.round(n).toLocaleString()}` : '');
const salaryText = (a) => (a.salaryMin || a.salaryMax ? `${fmtMoney(a.salaryMin, a.currency)}${a.salaryMax && a.salaryMax !== a.salaryMin ? ` - ${fmtMoney(a.salaryMax, a.currency)}` : ''} ${a.currency || ''}`.trim() : '');
const send = (msg) => chrome.runtime.sendMessage(msg).then((r) => {
  if (!r?.ok) throw new Error(r?.error || 'The extension did not answer');
  return r.data;
});

function toast(text, kind = '', ms = 4000) {
  S.toast = { text, kind };
  renderToast();
  clearTimeout(toast.t);
  toast.t = setTimeout(() => {
    S.toast = null;
    renderToast();
  }, ms);
}
function renderToast() {
  let el = document.getElementById('toast');
  if (!S.toast) return el?.remove();
  if (!el) {
    el = document.createElement('div');
    el.id = 'toast';
    document.body.appendChild(el);
  }
  el.className = `toast ${S.toast.kind}`;
  el.textContent = S.toast.text;
}

async function loadData() {
  [S.settings, S.profiles, S.apps, S.active] = await Promise.all([store.getSettings(), store.list('profiles'), store.list('applications'), store.getActive()]);
  if (!S.profiles.some((p) => p.id === S.active.profileId) && S.profiles[0]) S.active = await store.setActive({ profileId: S.profiles[0].id });
}

async function setProfile(id) {
  S.active = await store.setActive({ profileId: id });
  if (S.wiz.app && S.wiz.app.profileId !== id) {
    S.wiz.app.profileId = id;
    S.wiz.app.profileName = profile()?.name || '';
  }
  render();
}

function go(view, extra = {}) {
  S.view = view;
  S.menu = false;
  Object.assign(S, extra);
  render();
  window.scrollTo(0, 0);
}

// ---------- downloads (resume files live on this PC) ----------

function appFolder(app, p) {
  const folder = (S.settings.downloads.folder || 'AutoBid').replace(/[\\/:*?"<>|]+/g, '_');
  return `${folder}/${slug(p?.name || app.profileName || 'Profile')}/${localDay(app.createdAt || Date.now())}_${slug(app.company, 30)}_${slug(app.title, 30)}`;
}

async function download(bytes, mime, filename) {
  const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
  try {
    await chrome.downloads.download({ url, filename, conflictAction: 'overwrite', saveAs: false });
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }
  return filename;
}

async function recordFiles(app, names) {
  const files = [...new Set([...(app.files || []), ...names])].slice(-20);
  S.wiz.app = await store.put('applications', { ...app, files });
  await refreshApps();
}

// ---------- render ----------

function render() {
  if (!S.session) return renderLogin();
  const body = { home: viewHome, jobs: viewJobs, apply: viewApply, apps: viewApps, profiles: viewProfiles, profile: viewProfileEdit, settings: viewSettings }[S.view] || viewHome;
  $app.innerHTML = topBar() + `<main>${body()}</main>`;
  afterRender();
}

function topBar() {
  const name = S.session.user || 'me';
  return `<div class="top"><div class="brand" data-act="nav" data-v="home">${LOGO_SM}<span>Auto Bid</span></div><div class="sp"></div>
    <div class="user"><button data-act="menu"><span class="avatar">${esc(name[0]?.toUpperCase() || 'U')}</span><span>${esc(name)}</span>${I.down}</button>
    ${S.menu ? `<div class="menu"><div class="hint">${S.session.mode === 'supabase' ? 'Synced with Supabase' : 'Local mode (this PC only)'}</div>
      <button data-act="nav" data-v="home">Dashboard</button><button data-act="nav" data-v="profiles">Profiles</button><button data-act="nav" data-v="settings">Settings</button>
      ${S.session.mode === 'supabase' ? '<button data-act="sync">Sync now</button>' : ''}<button data-act="logout">Log out</button></div>` : ''}</div></div>`;
}

function profileSelect() {
  if (!S.profiles.length) return '<button class="btn sm ghost" data-act="nav" data-v="profiles">Create profile</button>';
  return `<select class="in" style="width:auto;max-width:170px;padding:6px 8px" data-change="profile">${S.profiles.map((p) => opt(p.id, p.name, profile()?.id)).join('')}</select>`;
}

// ---------- login ----------

function renderLogin() {
  const sb = S.settings?.supabase || {};
  $app.innerHTML = `<div class="login"><form class="login-card" id="loginForm">
    ${LOGO}<h1>Auto Bid</h1><div class="sub">Sign in to your account</div>
    <label>Username</label><input id="lu" autocomplete="username" placeholder="username">
    <label>Password</label><div class="pw"><input id="lp" type="password" autocomplete="current-password" placeholder="••••••••"><button type="button" data-act="pwToggle" title="Show">${I.eye}</button></div>
    <button class="btn" type="submit">Sign in</button><div class="err" id="lerr"></div>
    <details${sb.enabled ? ' open' : ''}><summary>${sbConfigured(sb) ? `Supabase sync: ${esc(hostOf(sb.url))}` : 'Local mode · Supabase sync (optional)'}</summary>
      <label class="check" style="margin-top:10px;font-weight:600"><input type="checkbox" id="sbon"${sb.enabled ? ' checked' : ''}>Sync profiles and applications with Supabase</label>
      <label>Supabase project URL</label><input id="sbu" value="${esc(sb.url)}" placeholder="https://xxxx.supabase.co">
      <label>Publishable key</label><input id="sbk" value="${esc(sb.key)}" placeholder="sb_publishable_…">
      <div style="margin-top:6px">Off: everything stays on this PC. On: sign in with a Supabase Auth user (username <b>jkrjkr</b> = user <b>jkrjkr@autobid.app</b>).</div></details>
  </form></div>`;
  document.getElementById('lu').focus();
  document.getElementById('loginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const err = document.getElementById('lerr');
    err.textContent = '';
    const btn = e.target.querySelector('button[type=submit]');
    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span> Signing in…';
    try {
      S.settings = await store.saveSettings({ supabase: { enabled: document.getElementById('sbon').checked, url: document.getElementById('sbu').value.trim(), key: document.getElementById('sbk').value.trim() } });
      S.session = await store.login(document.getElementById('lu').value.trim(), document.getElementById('lp').value);
      await loadData();
      go('home');
    } catch (ex) {
      err.textContent = ex.message;
      btn.disabled = false;
      btn.textContent = 'Sign in';
    }
  });
}

// ---------- dashboard ----------

function viewHome() {
  const apps = S.apps;
  const today = apps.filter((a) => sameDay(a.createdAt)).length;
  const applied = apps.filter((a) => a.status === 'applied' && sameDay(a.appliedAt)).length;
  const recent = apps.slice(0, 8);
  return `<div class="welcome"><span>Welcome back,</span><b>${esc(S.session.user)}</b><span class="sp grow"></span><button class="icon-btn" data-act="sync" title="Reload">${I.refresh}</button></div>
  ${!S.profiles.length ? `<div class="card" style="border-color:rgba(124,108,255,.5)"><b>Start with a profile.</b><div class="muted" style="margin:4px 0 8px">A profile holds one candidate's contact details, form answers and work history.</div><button class="btn sm" data-act="newProfile">Create profile</button></div>` : ''}
  <div class="sec-title">Today</div>
  <div class="stats">
    <div class="stat c1">${I.users}<div class="n">${S.profiles.length}</div><div class="l">Profiles</div></div>
    <div class="stat c2">${I.list}<div class="n">${today}</div><div class="l">Applications today</div></div>
    <div class="stat c3">${I.send}<div class="n">${applied}</div><div class="l">Submitted today</div></div>
  </div>
  <div class="sec-title">Quick actions</div>
  <button class="qa c1" data-act="newProfile"><span class="ic">${I.users}</span>Create Profile<span class="chev">${I.chev}</span></button>
  <button class="qa c2" data-act="nav" data-v="jobs"><span class="ic">${I.case}</span>Browse Jobs<span class="chev">${I.chev}</span></button>
  <button class="qa c3" data-act="applyCurrent"><span class="ic">${I.send}</span>Apply to Job (current tab)<span class="chev">${I.chev}</span></button>
  <button class="qa c4" data-act="nav" data-v="apps"><span class="ic">${I.list}</span>Applications<span class="chev">${I.chev}</span></button>
  <button class="qa c5" data-act="nav" data-v="settings"><span class="ic">${I.gear}</span>Settings<span class="chev">${I.chev}</span></button>
  <div class="sec-title">Recent activity</div>
  ${recent.length ? `<div class="list">${recent.map(appRow).join('')}</div>` : '<div class="list"><div class="empty">No applications yet.</div></div>'}`;
}

function appRow(a) {
  const color = { applied: 'var(--green)', interview: 'var(--blue)', filling: 'var(--amber)', resume_ready: '#a99fff' }[a.status] || 'var(--dim)';
  return `<div class="li" data-act="openApp" data-id="${esc(a.id)}"><span style="color:${color}">${I.list}</span>
    <div style="min-width:0"><div class="t">${esc(a.title || 'Untitled')}</div><div class="s">${esc(a.company)}${a.profileName ? ` · ${esc(a.profileName)}` : ''}</div></div>
    <div class="when">${timeAgo(a.updatedAt || a.createdAt)}</div></div>`;
}

// ---------- jobs ----------

function jobFilters() {
  const J = S.jobs;
  const f = J.f;
  const marks = J.marks || { viewed: {}, flagged: {} };
  // "applied" is per profile: a job one candidate applied to is still open for the others
  const mine = S.apps.filter((a) => a.status === 'applied' && a.profileId === profile()?.id);
  const appliedUrls = new Set(mine.flatMap((a) => [a.url, a.jobId].filter(Boolean)));
  const appliedCos = new Set(mine.map((a) => String(a.company).toLowerCase()));
  const q = J.q.trim().toLowerCase();
  const ageMs = f.age ? Number(f.age) * 3600000 : 0;
  const ptech = profileTechSet(profile()?.resume);
  let items = (J.cache?.items || []).filter((j) => {
    if (f.source && j.source !== f.source) return false;
    if (f.type && j.type !== f.type) return false;
    if (f.mode && j.workMode !== f.mode) return false;
    if (ageMs && Date.now() - j.postedAt > ageMs) return false;
    if (f.currency && j.currency !== f.currency) return false;
    if (f.platforms.length && !f.platforms.includes(j.platform)) return false;
    if (f.seniority && j.seniority !== f.seniority) return false;
    if (f.category && j.category !== f.category) return false;
    if (f.skills.length && !f.skills.every((s) => (j.tech || []).includes(s))) return false;
    if (f.hideApplied && (appliedUrls.has(j.url) || appliedUrls.has(j.id))) return false;
    if (f.hideAppliedCo && appliedCos.has(String(j.company).toLowerCase())) return false;
    if (f.hideViewed && marks.viewed[j.id]) return false;
    if (f.hideFlagged && marks.flagged[j.id]) return false;
    if (q) {
      const hay = J.field === 'title' ? j.title : J.field === 'company' ? j.company : J.field === 'description' ? j.description : `${j.title} ${j.company} ${j.location} ${(j.tech || []).join(' ')} ${j.description}`;
      if (!hay.toLowerCase().includes(q)) return false;
    }
    return true;
  });
  items = items.map((j) => ({ ...j, match: quickMatch(ptech, j.tech) }));
  if (f.sort === 'match') items.sort((a, b) => b.match - a.match);
  else if (f.sort === 'salary') items.sort((a, b) => (b.salaryMax || b.salaryMin || 0) - (a.salaryMax || a.salaryMin || 0));
  return items;
}

const activeFilterCount = (f) => Object.entries(f).filter(([k, v]) => k !== 'sort' && (Array.isArray(v) ? v.length : v) && !(k === 'hideApplied' || k === 'hideAppliedCo' || k === 'hideFlagged')).length;

function viewJobs() {
  const J = S.jobs;
  if (!J.cache) return `<div class="bar"><button class="back" data-act="nav" data-v="home">←</button><h2>Jobs</h2></div><div class="empty"><span class="spin"></span></div>`;
  const items = jobFilters();
  const per = 20;
  const pages = Math.max(1, Math.ceil(items.length / per));
  J.page = Math.min(J.page, pages);
  const page = items.slice((J.page - 1) * per, J.page * per);
  const bids = new Map();
  for (const a of S.apps) for (const k of [a.url, a.jobId]) if (k) bids.set(k, (bids.get(k) || 0) + 1);
  const n = activeFilterCount(J.f);
  const all = J.cache.items;
  const uniq = (k) => [...new Set(all.map((j) => j[k]).filter(Boolean))].sort();
  const techCounts = new Map();
  for (const j of all) for (const t of j.tech || []) techCounts.set(t, (techCounts.get(t) || 0) + 1);
  const topTech = [...techCounts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 60).map(([t]) => t).sort();
  const f = J.f;
  const ms = (key, label, values) => `<details class="ms"><summary>${f[key].length ? `${esc(label)}: ${f[key].length}` : esc(label)}</summary><div class="opts">${
    values.map((v) => `<label><input type="checkbox" data-ms="${key}" value="${esc(v)}"${f[key].includes(v) ? ' checked' : ''}>${esc(v)}</label>`).join('') || '<div class="dim">None yet</div>'}</div></details>`;
  return `<div class="bar"><button class="back" data-act="nav" data-v="home">←</button><h2>Jobs <span class="dim">(${items.length.toLocaleString()})</span></h2>${profileSelect()}</div>
  <div class="search"><div class="box"><input data-bind="jobs.q" data-live placeholder="Search jobs" value="${esc(J.q)}">
    <select data-change="jobField">${opt('all', 'All fields', J.field)}${opt('title', 'Title', J.field)}${opt('company', 'Company', J.field)}${opt('description', 'Description', J.field)}</select></div>
    <button class="icon-btn" style="width:auto;padding:0 9px;gap:4px" data-act="toggleFilters">${I.filter}${n ? ` ${n}` : ''}</button>
    <button class="icon-btn" data-act="refreshJobs" title="Fetch new jobs"${J.busy ? ' disabled' : ''}>${J.busy ? '<span class="spin"></span>' : I.refresh}</button></div>
  ${J.busy || J.progress ? `<div class="dim" style="margin:-2px 0 8px">${esc(J.progress)}</div>` : `<div class="dim" style="margin:-2px 0 8px">${J.cache.fetchedAt ? `Updated ${timeAgo(J.cache.fetchedAt)} · ${all.length.toLocaleString()} jobs cached` : 'No jobs yet: press ↻ to fetch.'}${
    (J.cache.report || []).some((r) => r.errors.length) ? ` · <a href="#" data-act="jobReport">${J.cache.report.filter((r) => r.errors.length).length} source problem(s)</a>` : ''}</div>`}
  ${J.showFilters ? `<div class="filters"><div class="grid2">
    <select data-f="source">${opt('', 'All sources', f.source)}${Object.entries(jobsLib.SOURCES).filter(([k]) => k !== 'manual').map(([k, v]) => opt(k, v, f.source)).join('')}</select>
    <select data-f="type">${opt('', 'All types', f.type)}${['Full-time', 'Part-time', 'Contract', 'Internship', 'Freelance'].map((v) => opt(v, v, f.type)).join('')}</select>
    <select data-f="mode">${opt('', 'Any work mode', f.mode)}${['Remote', 'Hybrid', 'Onsite'].map((v) => opt(v, v, f.mode)).join('')}</select>
    <select data-f="age">${opt('', 'Any time', f.age)}${[['24', 'Past 24 hours'], ['72', 'Past 3 days'], ['168', 'Past week'], ['336', 'Past 2 weeks']].map(([v, l]) => opt(v, l, f.age)).join('')}</select>
    <select data-f="currency">${opt('', 'Any currency', f.currency)}${['USD', 'EUR', 'GBP', 'CAD'].map((v) => opt(v, v, f.currency)).join('')}</select>
    <select data-f="sort">${opt('latest', 'Sort by latest', f.sort)}${opt('match', 'Sort by match', f.sort)}${opt('salary', 'Sort by salary', f.sort)}</select>
    <select data-f="category">${opt('', 'All categories', f.category)}${[...jobsLib.CATEGORIES.map((c) => c[0]), 'Other'].map((v) => opt(v, v, f.category)).join('')}</select>
    <select data-f="seniority">${opt('', 'Any seniority', f.seniority)}${['Intern', 'Junior', 'Mid', 'Senior', 'Staff', 'Lead', 'Principal'].map((v) => opt(v, v, f.seniority)).join('')}</select>
    ${ms('platforms', 'Job platforms', jobsLib.PLATFORMS.map((p) => p[0]).filter((p) => uniq('platform').includes(p)))}
    ${ms('skills', 'Skills', topTech)}
  </div><div class="checks">
    <label class="check"><input type="checkbox" data-fc="hideApplied"${f.hideApplied ? ' checked' : ''}>Hide applied</label>
    <label class="check"><input type="checkbox" data-fc="hideAppliedCo"${f.hideAppliedCo ? ' checked' : ''}>Hide applied companies</label>
    <label class="check"><input type="checkbox" data-fc="hideViewed"${f.hideViewed ? ' checked' : ''}>Hide viewed</label>
    <label class="check"><input type="checkbox" data-fc="hideFlagged"${f.hideFlagged ? ' checked' : ''}>Hide flagged</label>
  </div><div class="foot"><span>Active filters: ${n}</span><a href="#" data-act="resetFilters">Reset filters</a></div></div>` : ''}
  ${page.length ? page.map((j) => jobCard(j, bids.get(j.url) || bids.get(j.id) || 0)).join('') : `<div class="empty">${all.length ? 'No jobs match these filters.' : 'Press ↻ to fetch jobs.'}</div>`}
  ${pages > 1 ? `<div class="pager"><span>${items.length.toLocaleString()} jobs</span><span class="row"><button class="btn xs ghost" data-act="page" data-d="-1"${J.page <= 1 ? ' disabled' : ''}>Prev</button>
    Page ${J.page} of ${pages}<button class="btn xs ghost" data-act="page" data-d="1"${J.page >= pages ? ' disabled' : ''}>Next</button></span><span>${per} per page</span></div>` : ''}`;
}

function jobCard(j, bidCount) {
  const marks = S.jobs.marks || { viewed: {}, flagged: {} };
  const sen = { Senior: 'amber', Staff: 'amber', Principal: 'amber', Lead: 'amber', Junior: 'green', Mid: 'green' }[j.seniority] || '';
  return `<div class="job${marks.viewed[j.id] ? ' viewed' : ''}"><div class="h"><div class="grow" style="min-width:0">
    <div class="title" data-act="openJob" data-id="${esc(j.id)}">${esc(j.title)}</div>
    <div class="co">${esc(j.company)}${j.location ? ` - ${esc(j.location)}` : ''}</div>
    <div class="chips"><span class="chip">${esc(jobsLib.SOURCES[j.source] || j.source)}</span>${j.seniority ? `<span class="chip ${sen}">${esc(j.seniority)}</span>` : ''}<span class="chip blue">${esc(j.workMode)}</span>
      ${j.platform ? `<span class="chip">${esc(j.platform)}</span>` : ''}<span class="chip acc">Match ${j.match}%</span>${marks.flagged[j.id] ? '<span class="chip red">Flagged</span>' : ''}</div></div>
    <div class="acts"><button class="btn sm" data-act="applyJob" data-id="${esc(j.id)}">Apply</button>
      <button class="icon-btn" style="width:26px;height:26px" data-act="flagJob" data-id="${esc(j.id)}" title="${marks.flagged[j.id] ? 'Unflag' : 'Flag (hide)'}">${I.flag}</button></div></div>
    <div class="meta"><span>${salaryText(j) || 'Salary not posted'} · ${bidCount} bid${bidCount === 1 ? '' : 's'}</span><span>${new Date(j.postedAt).toLocaleDateString()}</span></div></div>`;
}

async function loadJobs() {
  [S.jobs.cache, S.jobs.marks] = await Promise.all([jobsLib.getCache(), jobsLib.getMarks()]);
  const { jobFilters: saved } = await chrome.storage.local.get('jobFilters');
  if (saved) S.jobs.f = { ...DEFAULT_FILTERS, ...saved };
}
const saveFilters = () => chrome.storage.local.set({ jobFilters: S.jobs.f });

// ---------- application wizard ----------

function newDraft(extra = {}) {
  const p = profile();
  return { title: '', company: '', url: '', applyUrl: '', jd: '', type: 'Full-time', workMode: 'Remote', location: 'United States', salaryMin: '', salaryMax: '', currency: 'USD', notes: '', source: 'manual', status: 'draft', profileId: p?.id || '', profileName: p?.name || '', template: p?.prefs?.template || 'professional', ...extra };
}

async function openWizard(app, step = 1) {
  const resume = app.id ? await store.getResume(app.id) : null;
  S.wiz = { ...S.wiz, step, app, text: resume?.text || '', ats: null, val: null, busy: '', status: '', statusKind: '', log: [], coverStatus: '', engine: S.wiz.engine || S.settings.ai.resumeEngine };
  if (app.profileId && app.profileId !== S.active.profileId && S.profiles.some((p) => p.id === app.profileId)) S.active = await store.setActive({ profileId: app.profileId });
  if (app.id) S.active = await store.setActive({ appId: app.id });
  if (S.wiz.text) analyzeText(false);
  go('apply');
}

function viewApply() {
  const W = S.wiz;
  const a = W.app;
  if (!a) return '<div class="empty">No application open.</div>';
  const titles = { 1: 'Job Application', 2: 'Resume Builder', 3: 'Fill Application' };
  const st = (n) => `<div class="st ${W.step === n ? 'on' : W.step > n || (n === 1 && a.id) ? 'done' : ''}" data-act="step" data-n="${n}">${W.step > n ? '✓' : n}</div>`;
  return `<div class="bar"><button class="back" data-act="wizBack">←</button><h2>${titles[W.step]}</h2>${profileSelect()}</div>
  <div class="steps">${st(1)}<div class="ln ${W.step > 1 ? 'done' : ''}"></div>${st(2)}<div class="ln ${W.step > 2 ? 'done' : ''}"></div>${st(3)}</div>
  ${W.step === 1 ? stepJob(a) : W.step === 2 ? stepResume(a) : stepFill(a)}`;
}

function stepJob(a) {
  return `<div class="grid2"><div><label class="f">Job Title <span class="req">*</span></label><input class="in" data-a="title" value="${esc(a.title)}"></div>
    <div><label class="f">Company <span class="req">*</span></label><input class="in" data-a="company" value="${esc(a.company)}"></div></div>
  <label class="f">Job URL <span class="req">*</span></label><div class="row"><input class="in grow" data-a="url" value="${esc(a.url)}" placeholder="https://…">
    <button class="icon-btn" style="width:36px;height:36px" data-act="readTab" title="Read title, company and description from the current tab">${S.wiz.busy === 'read' ? '<span class="spin"></span>' : I.refresh}</button></div>
  <label class="f">Job Description <span class="req">*</span></label><textarea class="in" data-a="jd" rows="12" placeholder="Paste the job description, or press ↻ to read it from the open tab">${esc(a.jd)}</textarea>
  <div class="grid2"><div><label class="f">Contract Type <span class="req">*</span></label><select class="in" data-a="type">${['Full-time', 'Part-time', 'Contract', 'Internship', 'Freelance'].map((v) => opt(v, v, a.type)).join('')}</select></div>
    <div><label class="f">Work Mode <span class="req">*</span></label><select class="in" data-a="workMode">${['Remote', 'Hybrid', 'Onsite'].map((v) => opt(v, v, a.workMode)).join('')}</select></div></div>
  <label class="f">Location <span class="req">*</span></label><input class="in" data-a="location" value="${esc(a.location)}">
  <label class="f">Salary (optional)</label><div class="grid3"><input class="in" type="number" data-a="salaryMin" placeholder="Min" value="${esc(a.salaryMin || '')}">
    <input class="in" type="number" data-a="salaryMax" placeholder="Max" value="${esc(a.salaryMax || '')}"><select class="in" data-a="currency">${['USD', 'EUR', 'GBP', 'CAD'].map((v) => opt(v, v, a.currency)).join('')}</select></div>
  <label class="f">Notes (optional)</label><input class="in" data-a="notes" value="${esc(a.notes || '')}" placeholder="Referral, recruiter, anything to remember">
  <div class="status ${S.wiz.statusKind}">${esc(S.wiz.status)}</div>
  <div class="footer-act"><button class="btn" data-act="toResume">Next: Build Resume ${I.chev}</button></div>`;
}

function scoreBox(label, v, good, mid, invert = false) {
  const cls = invert ? (v <= good ? 'good' : v <= mid ? 'mid' : 'bad') : (v >= good ? 'good' : v >= mid ? 'mid' : 'bad');
  return `<div class="score ${cls}"><div class="v">${v}%</div><div class="k">${label}</div></div>`;
}

function stepResume(a) {
  const W = S.wiz;
  const busy = W.busy === 'gen';
  const target = S.settings.ai.atsTarget;
  const p = profile();
  return `<div class="card"><h3>Resume content<span class="sp"></span><span class="seg">${Object.entries(ENGINES).map(([k, v]) => `<button data-act="engine" data-e="${k}" class="${W.engine === k ? 'on' : ''}">${esc(v.replace(' chat', '').replace(' API', ' API'))}</button>`).join('')}</span></h3>
    ${busy ? `<button class="btn block" data-act="stopGen"><span class="spin"></span> Generating… (stop)</button>` : `<button class="btn block" data-act="generate"${p ? '' : ' disabled'}>${I.spark} Generate with AI</button>`}
    <div class="status ${W.statusKind}">${esc(W.status)}</div>
    <textarea class="in mono" id="resumeText" rows="16" placeholder="The tailored resume appears here. You can edit it before downloading: keep the ### job lines and the == SECTION == headings.">${esc(W.text)}</textarea>
    <div class="dim">Employers, dates, contact details and education always come from the profile; edits to those lines are ignored.</div></div>
  <div class="card"><h3>Resume analysis<span class="sp"></span><button class="btn xs ghost" data-act="analyze"${W.text ? '' : ' disabled'}>Analyze</button>
    <button class="btn xs" data-act="fixAI"${W.text && !busy ? '' : ' disabled'}>Fix with AI</button></h3>
    ${W.ats ? `<div class="scores">${scoreBox('ATS match (target ' + target + '%)', W.ats.score, target, 80)}${scoreBox('Consistency risk (target 0%)', W.val.risk, 0, 15, true)}</div>
      <div class="bars">${Object.entries({ Keywords: W.ats.parts.keywords, 'Job title': W.ats.parts.title, 'JD phrases': W.ats.parts.phrases, Years: W.ats.parts.years }).map(([k, v]) => `<span>${k}</span><i><b style="width:${v}%"></b></i><span>${v}%</span>`).join('')}</div>
      ${W.ats.missing.length ? `<div class="sec-title" style="margin-top:10px">Missing JD keywords</div><div class="chips">${W.ats.missing.map((m) => `<span class="chip ${m.req ? 'red' : 'amber'}">${esc(m.name)}</span>`).join('')}</div>` : ''}
      ${W.ats.skillsOnly.length ? `<div class="sec-title">Only in Skills (not in any bullet)</div><div class="chips">${W.ats.skillsOnly.map((m) => `<span class="chip">${esc(m)}</span>`).join('')}</div>` : ''}
      ${W.val.issues.length ? `<div class="sec-title">Consistency checks (${W.val.issues.length})</div><ul class="issues">${W.val.issues.map((x) => `<li class="${x.severity}"><b>${esc(x.where)}:</b> ${esc(x.msg)}<span class="fx">${esc(x.fix)}</span></li>`).join('')}</ul>` : '<div class="status ok">No consistency problems found.</div>'}
      ${W.val.techSpan.length ? `<div class="sec-title">Experience per technology (from job dates)</div><div class="chips">${W.val.techSpan.slice(0, 24).map((t) => `<span class="chip">${esc(t.name)} ${t.years}y</span>`).join('')}</div>` : ''}`
    : '<div class="dim">Generate or paste a resume, then Analyze: ATS match against this JD, and checks that dates, titles and technologies add up.</div>'}</div>
  <div class="card"><h3>Template &amp; export</h3>
    <div class="row"><select class="in grow" data-change="template">${TEMPLATES.map((t) => opt(t.id, t.name, a.template)).join('')}</select><button class="btn sm ghost" data-act="preview"${W.text ? '' : ' disabled'}>${I.eye} Preview</button></div>
    <div class="grid3" style="margin-top:8px"><button class="btn sm ghost" data-act="dlResume" data-k="pdf"${W.text ? '' : ' disabled'}>${I.dl} PDF</button>
      <button class="btn sm ghost" data-act="dlResume" data-k="docx"${W.text ? '' : ' disabled'}>${I.dl} Word</button>
      <button class="btn sm" data-act="dlResume" data-k="both"${W.text ? '' : ' disabled'}>${I.dl} Both</button></div>
    ${W.busy === 'dl' ? '<div class="status"><span class="spin"></span> Making files…</div>' : ''}
    ${(a.files || []).length ? `<div class="files">Saved to Downloads:${a.files.slice(-4).map((f) => `<div>· ${esc(f)}</div>`).join('')}</div>` : ''}</div>
  <div class="footer-act"><button class="btn ghost" style="flex:0 0 auto" data-act="step" data-n="1">Back</button><button class="btn" data-act="toFill"${W.text ? '' : ' disabled'}>Next: Fill Application ${I.chev}</button></div>`;
}

function stepFill(a) {
  const W = S.wiz;
  return `<div class="card"><h3>Autofill<span class="sp"></span><span class="chip ${STATUS_CHIP[a.status] || ''}">${esc(STATUSES[a.status] || a.status)}</span></h3>
    <div class="muted" style="margin-bottom:8px">Open the application form, then press the button here or the round ${I.spark} button on the right edge of the page. It fills what the page shows (and uploads the resume); you check it and press Next / Submit. Press it again on each new step.</div>
    <div class="row"><button class="btn grow" data-act="fillNow"${W.fillBusy ? ' disabled' : ''}>${W.fillBusy ? '<span class="spin"></span> Filling…' : `${I.spark} Auto fill current tab`}</button>
      ${a.applyUrl || a.url ? `<button class="btn ghost" data-act="openUrl" title="Open the job">${I.ext}</button>` : ''}</div>
    ${W.log.length ? `<div class="log" style="margin-top:8px">${W.log.map((l) => `<div class="${l.level || ''}">${esc(l.text)}</div>`).join('')}</div>` : ''}
    <div class="row" style="margin-top:10px"><button class="btn green grow" data-act="markApplied"${a.status === 'applied' ? ' disabled' : ''}>${a.status === 'applied' ? '✓ Applied' : 'Mark as applied'}</button>
      <select class="in" style="width:auto" data-change="status">${Object.entries(STATUSES).map(([k, v]) => opt(k, v, a.status)).join('')}</select></div>
    <div class="dim" style="margin-top:6px">A thank-you page after you submit marks it applied by itself.</div></div>
  <div class="card"><h3>Cover letter<span class="sp"></span><select class="in" style="width:auto;padding:3px 6px;font-size:11.5px" data-change="coverEngine">${Object.entries(ENGINES).map(([k, v]) => opt(k, v, S.settings.ai.coverEngine)).join('')}</select></h3>
    <div class="muted" style="margin-bottom:8px">Only when the job asks for one. Once generated, the fill types it into cover-letter boxes and uploads it to cover-letter file fields.</div>
    <button class="btn block blue" data-act="genCover"${W.coverBusy ? ' disabled' : ''}>${W.coverBusy ? '<span class="spin"></span> Writing…' : `${I.spark} Generate cover letter`}</button>
    <div class="status">${esc(W.coverStatus)}</div>
    <textarea class="in" id="coverText" rows="10" placeholder="Cover letter text">${esc(a.coverLetter || '')}</textarea>
    <div class="grid3" style="margin-top:8px"><button class="btn sm ghost" data-act="dlCover" data-k="pdf">${I.dl} PDF</button><button class="btn sm ghost" data-act="dlCover" data-k="docx">${I.dl} Word</button><button class="btn sm" data-act="dlCover" data-k="both">${I.dl} Both</button></div></div>
  ${(a.answers || []).length ? `<div class="card"><h3>Answers given (${a.answers.length})</h3>${a.answers.slice(-12).reverse().map((x) => `<div style="margin-bottom:6px"><div class="dim">${esc(x.question)}</div><div>${esc(x.answer)}</div></div>`).join('')}</div>` : ''}
  <div class="footer-act"><button class="btn ghost" style="flex:0 0 auto" data-act="step" data-n="2">Back</button><button class="btn" data-act="nav" data-v="apps">Done</button></div>`;
}

// collects the step-1 inputs into S.wiz.app
function readJobForm() {
  for (const el of $app.querySelectorAll('[data-a]')) {
    const k = el.dataset.a;
    S.wiz.app[k] = el.type === 'number' ? (el.value ? Number(el.value) : '') : el.value;
  }
}

async function saveApp(patch = {}) {
  const p = profile();
  const a = { ...S.wiz.app, ...patch, profileId: p?.id || S.wiz.app.profileId, profileName: p?.name || S.wiz.app.profileName, salary: salaryText(S.wiz.app) };
  S.wiz.app = await store.put('applications', a);
  S.active = await store.setActive({ appId: S.wiz.app.id, profileId: a.profileId });
  await refreshApps();
  return S.wiz.app;
}

async function refreshApps() {
  S.apps = await store.list('applications');
}

function setStatus(text, kind = '') {
  S.wiz.status = text;
  S.wiz.statusKind = kind;
  const el = $app.querySelector('.card .status, .status');
  if (el && S.view === 'apply') {
    el.className = `status ${kind}`;
    el.textContent = text;
  }
}

// the textarea is the source of truth once there is text
function currentResume() {
  const p = profile();
  const ta = document.getElementById('resumeText');
  if (ta) S.wiz.text = ta.value;
  if (!S.wiz.text.trim()) return null;
  return fromText(S.wiz.text, p.resume);
}

function analyzeText(save = true) {
  const p = profile();
  const r = currentResume();
  if (!r || !p) return null;
  const a = S.wiz.app;
  S.wiz.ats = atsScore(r, a.jd, a.title, totalYears(p.resume));
  S.wiz.val = validate(r, p.resume, { stackMode: p.prefs?.stackMode });
  if (save && a.id) {
    store.saveResume(a.id, { json: r, text: S.wiz.text, template: a.template });
    store.saveFile(a.id, 'resume', null); // files are made again from the edited text
  }
  return r;
}

async function generate({ fixOnly = false } = {}) {
  const p = profile();
  const a = S.wiz.app;
  const s = S.settings;
  if (!p) return toast('Pick a profile first.', 'err');
  const engine = S.wiz.engine || s.ai.resumeEngine;
  const ctl = new AbortController();
  S.wiz.abort = ctl;
  S.wiz.busy = 'gen';
  S.wiz.status = '';
  render();
  const [origin] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  const job = { ...a, salary: salaryText(a) };
  const prompt = buildPrompt({ profile: p, job, atsTarget: s.ai.atsTarget });
  let chatTab = S.wiz.chatTab || null;
  let last = null;
  const ask = async (msg, newChat) => {
    if (engine === 'openai') {
      setStatus(`Asking ${s.ai.resumeModel}…`);
      return openaiChat({ apiKey: s.ai.openaiKey, model: s.ai.resumeModel, user: msg, schema: TAILOR_SCHEMA, maxTokens: 24000, effort: 'medium', signal: ctl.signal });
    }
    const r = await askChat(engine, msg, { newChat, tabId: chatTab, onStatus: (t) => setStatus(t), signal: ctl.signal, returnTo: origin?.id });
    chatTab = r.tabId;
    S.wiz.chatTab = r.tabId;
    return r.text;
  };
  const apply = (t) => {
    last = t;
    const r = mergeTailored(p.resume, t);
    S.wiz.text = toText(r);
    const ta = document.getElementById('resumeText');
    if (ta) ta.value = S.wiz.text;
    return analyzeText(false);
  };
  const needsFix = () => S.wiz.val.issues.some((x) => x.severity !== 'low') || S.wiz.ats.score < s.ai.atsTarget;
  try {
    let resume;
    if (fixOnly) {
      resume = analyzeText(false);
      const fixMsg = fixPrompt(issuesForModel(S.wiz.val, S.wiz.ats));
      const prev = `\n\nCurrent resume (JSON):\n\`\`\`json\n${JSON.stringify(resumeToTailored(resume))}\n\`\`\`\n\n`;
      // a chat that still has this conversation open gets only the fix; otherwise the whole context again
      const msg = engine !== 'openai' && chatTab ? fixMsg : `${prompt}${prev}${fixMsg}`;
      resume = apply(parseJson(await ask(msg, !(engine !== 'openai' && chatTab))));
    } else {
      resume = apply(parseJson(await ask(prompt, true)));
      for (let round = 0; round < (s.ai.autoFixRounds || 0) && needsFix(); round++) {
        setStatus(`ATS ${S.wiz.ats.score}%, risk ${S.wiz.val.risk}%: asking for a correction (round ${round + 1})…`);
        const fixMsg = fixPrompt(issuesForModel(S.wiz.val, S.wiz.ats));
        const msg = engine === 'openai' ? `${prompt}\n\nYour previous answer:\n\`\`\`json\n${JSON.stringify(last)}\n\`\`\`\n\n${fixMsg}` : fixMsg;
        resume = apply(parseJson(await ask(msg, false)));
      }
    }
    await store.saveResume(a.id, { json: resume, text: S.wiz.text, template: a.template, industry: last?.company_industry || '' });
    await store.saveFile(a.id, 'resume', null);
    await saveApp({ status: a.status === 'draft' ? 'resume_ready' : a.status, atsScore: S.wiz.ats.score, risk: S.wiz.val.risk, industry: last?.company_industry || a.industry || '', engine });
    S.wiz.status = `Done: ATS ${S.wiz.ats.score}%, consistency risk ${S.wiz.val.risk}%.${S.wiz.val.risk ? ' See the checks below; edit the text or press Fix with AI.' : ''}`;
    S.wiz.statusKind = 'ok';
    if (!s.ai.keepChatTab && chatTab) {
      chrome.tabs.remove(chatTab).catch(() => {});
      S.wiz.chatTab = null;
    }
  } catch (err) {
    S.wiz.status = err.message;
    S.wiz.statusKind = 'err';
  } finally {
    S.wiz.busy = '';
    S.wiz.abort = null;
    render();
  }
}

// a full resume back into the model's JSON shape (for "Fix with AI" on edited text)
function resumeToTailored(r) {
  return {
    company_industry: S.wiz.app.industry || '', label: r.basics.label, summary: r.basics.summary, highlights: r.basics.highlights || [],
    skills: r.skills || [], work: (r.work || []).map((w, index) => ({ index, position: w.position, description: w.description || '', highlights: w.highlights || [] })), keywords_matched: []
  };
}

async function resumeFiles(kind) {
  const p = profile();
  const a = S.wiz.app;
  const r = analyzeText(true);
  if (!r) throw new Error('Nothing to export yet.');
  const html = renderResumeHtml(r, a.template);
  const base = `${slug(r.basics?.name || 'Resume')}_${slug(a.company)}_Resume`;
  const dir = appFolder(a, p);
  const out = [];
  if (kind === 'pdf' || kind === 'both') {
    const b64 = await htmlToPdf(html);
    out.push(await download(b64ToBytes(b64), 'application/pdf', `${dir}/${base}.pdf`));
    if (S.settings.fill.fileType !== 'docx') await store.saveFile(a.id, 'resume', { b64, name: `${base}.pdf`, type: 'application/pdf' });
  }
  if (kind === 'docx' || kind === 'both') {
    const bytes = resumeDocx(r, styleFromHtml(html));
    out.push(await download(bytes, DOCX_TYPE, `${dir}/${base}.docx`));
    if (S.settings.fill.fileType === 'docx') await store.saveFile(a.id, 'resume', { b64: bytesToB64(bytes), name: `${base}.docx`, type: DOCX_TYPE });
  }
  if (kind === 'both' || kind === 'pdf') {
    const meta = [`Company: ${a.company}`, `Role: ${a.title}`, `Link: ${a.url}`, `Profile: ${p?.name}`, `ATS: ${S.wiz.ats?.score ?? ''}%  Risk: ${S.wiz.val?.risk ?? ''}%`, '', a.jd].join('\n');
    out.push(await download(new TextEncoder().encode(meta), 'text/plain', `${dir}/job.txt`));
  }
  await recordFiles(a, out.filter((f) => !f.endsWith('job.txt')));
  return out;
}

async function coverFiles(kind) {
  const p = profile();
  const a = S.wiz.app;
  const letter = document.getElementById('coverText')?.value.trim() || a.coverLetter || '';
  if (!letter) throw new Error('Write or generate the cover letter first.');
  if (letter !== a.coverLetter) await saveApp({ coverLetter: letter });
  const resume = (await store.getResume(a.id))?.json || p.resume;
  const html = renderCoverLetterHtml(resume, letter, a.template, { company: a.company, role: a.title });
  const base = `${slug(resume.basics?.name || 'Cover')}_${slug(a.company)}_Cover_Letter`;
  const dir = appFolder(a, p);
  const out = [];
  if (kind === 'pdf' || kind === 'both') {
    const b64 = await htmlToPdf(html);
    out.push(await download(b64ToBytes(b64), 'application/pdf', `${dir}/${base}.pdf`));
    await store.saveFile(a.id, 'cover', { b64, name: `${base}.pdf`, type: 'application/pdf' });
  }
  if (kind === 'docx' || kind === 'both') out.push(await download(coverDocx(resume, letter, { company: a.company, title: a.title }, styleFromHtml(html)), DOCX_TYPE, `${dir}/${base}.docx`));
  await recordFiles(S.wiz.app, out);
  return out;
}

async function genCover() {
  const p = profile();
  const a = S.wiz.app;
  const s = S.settings;
  S.wiz.coverBusy = true;
  S.wiz.coverStatus = '';
  render();
  try {
    const resume = (await store.getResume(a.id))?.json || p.resume;
    const prompt = coverPrompt({ profile: p, resume, job: a });
    let text;
    if (s.ai.coverEngine === 'openai') text = await openaiChat({ apiKey: s.ai.openaiKey, model: s.ai.openaiModel, user: prompt, maxTokens: 4000 });
    else {
      const [origin] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
      const r = await askChat(s.ai.coverEngine, prompt, { newChat: true, onStatus: (t) => { S.wiz.coverStatus = t; const el = $app.querySelectorAll('.status')[1]; if (el) el.textContent = t; }, returnTo: origin?.id });
      text = r.plain || r.text;
    }
    await saveApp({ coverLetter: cleanLetter(text) });
    await store.saveFile(a.id, 'cover', null);
    // a PDF ready for cover-letter upload fields
    const html = renderCoverLetterHtml(resume, S.wiz.app.coverLetter, a.template, { company: a.company, role: a.title });
    await store.saveFile(a.id, 'cover', { b64: await htmlToPdf(html), name: `${slug(resume.basics?.name || 'Cover')}_${slug(a.company)}_Cover_Letter.pdf`, type: 'application/pdf' });
    S.wiz.coverStatus = 'Done. Edit it if you like; downloads use the text in the box.';
  } catch (err) {
    S.wiz.coverStatus = err.message;
  } finally {
    S.wiz.coverBusy = false;
    render();
  }
}

// ---------- applications ----------

function viewApps() {
  const A = S.apps;
  const f = S.apps_f;
  const q = f.q.toLowerCase();
  const list = A.filter((a) => (!f.status || a.status === f.status) && (!f.profile || a.profileId === f.profile) && (!q || `${a.title} ${a.company}`.toLowerCase().includes(q)));
  return `<div class="bar"><button class="back" data-act="nav" data-v="home">←</button><h2>Applications <span class="dim">(${list.length})</span></h2><button class="btn sm" data-act="newApp">+ New</button></div>
  <div class="search"><div class="box"><input data-bind="apps_f.q" data-live placeholder="Search title or company" value="${esc(f.q)}"></div></div>
  <div class="grid2" style="margin-bottom:10px"><select class="in" data-change="appsStatus">${opt('', 'All statuses', f.status)}${Object.entries(STATUSES).map(([k, v]) => opt(k, v, f.status)).join('')}</select>
    <select class="in" data-change="appsProfile">${opt('', 'All profiles', f.profile)}${S.profiles.map((p) => opt(p.id, p.name, f.profile)).join('')}</select></div>
  ${list.length ? list.slice(0, 300).map((a) => `<div class="job"><div class="h"><div class="grow" style="min-width:0">
      <div class="title" data-act="openApp" data-id="${esc(a.id)}">${esc(a.title || 'Untitled')}</div>
      <div class="co">${esc(a.company)} · ${esc(a.profileName || '')}</div>
      <div class="chips"><span class="chip ${STATUS_CHIP[a.status] || ''}">${esc(STATUSES[a.status] || a.status)}</span>${a.atsScore ? `<span class="chip acc">ATS ${a.atsScore}%</span>` : ''}${a.risk != null && a.atsScore ? `<span class="chip ${a.risk ? 'amber' : 'green'}">Risk ${a.risk}%</span>` : ''}${a.platform ? `<span class="chip">${esc(a.platform)}</span>` : ''}</div></div>
      <div class="acts"><button class="btn xs ghost" data-act="openApp" data-id="${esc(a.id)}">Open</button><button class="btn xs red" data-act="delApp" data-id="${esc(a.id)}">Delete</button></div></div>
      <div class="meta"><span>${a.appliedAt ? `Applied ${new Date(a.appliedAt).toLocaleString()}` : `Created ${timeAgo(a.createdAt)}`}</span><span>${a.url ? esc(hostOf(a.url)) : ''}</span></div></div>`).join('')
    : '<div class="empty">No applications.</div>'}`;
}

// ---------- profiles ----------

function viewProfiles() {
  return `<div class="bar"><button class="back" data-act="nav" data-v="home">←</button><h2>Profiles</h2><label class="btn sm ghost" style="cursor:pointer">Import<input type="file" accept=".json" hidden data-change="importProfile"></label><button class="btn sm" data-act="newProfile">+ New</button></div>
  ${S.profiles.length ? S.profiles.map((p) => `<div class="pcard${p.id === profile()?.id ? ' on' : ''}"><div class="avatar">${esc((p.name || '?')[0].toUpperCase())}</div>
    <div class="grow" style="min-width:0"><div style="font-weight:700">${esc(p.name)}</div><div class="dim">${esc(p.resume?.basics?.label || '')} · ${(p.resume?.work || []).length} jobs · ${totalYears(p.resume)} yrs</div></div>
    <div class="row">${p.id === profile()?.id ? '<span class="chip acc">Active</span>' : `<button class="btn xs ghost" data-act="useProfile" data-id="${esc(p.id)}">Use</button>`}<button class="btn xs" data-act="editProfile" data-id="${esc(p.id)}">Edit</button></div></div>`).join('')
    : '<div class="empty">No profiles yet. Create one, or import a JSON file (a full profile or a JSON Resume).</div>'}
  <div class="dim" style="margin-top:10px">Import format: see <b>profiles/sample-profile.json</b> in the extension folder. Secrets (password, SSN last 4, date of birth) stay on this PC.</div>`;
}

function viewProfileEdit() {
  const P = S.prof;
  const d = P.draft;
  if (!d) return '';
  const tabs = { personal: 'Personal', experience: 'Experience', json: 'Resume JSON', prefs: 'Preferences' };
  let body = '';
  if (P.tab === 'personal') {
    body = PERSONAL_FIELDS.map(([group, fields]) => `<fieldset><legend>${esc(group)}</legend><div class="grid2">${fields.map(([k, label, type]) =>
      `<div><label class="f">${esc(label)}</label><input class="in" data-pf="${k}" type="${type || 'text'}" value="${esc(d.personal?.[k] || '')}"></div>`).join('')}</div></fieldset>`).join('')
      + `<fieldset><legend>Other answers for the AI</legend><textarea class="in" data-pf="other_info" rows="4" placeholder="Anything forms ask that is not above, e.g. clearance, travel, preferences">${esc(d.personal?.other_info || '')}</textarea></fieldset>`;
  } else if (P.tab === 'experience') {
    const work = d.resume.work || [];
    body = `<div class="dim" style="margin-bottom:8px">Employer, dates and location are printed exactly as entered. <b>Industry</b> and <b>Real stack</b> are not printed: the resume writer and the consistency check use them as evidence of what this job really involved.</div>
      ${work.map((w, i) => `<div class="job-ed"><div class="hd"><span class="grow">${esc(w.name || `Job ${i + 1}`)}</span><button class="btn xs ghost" data-act="jobMove" data-i="${i}" data-d="-1"${i ? '' : ' disabled'}>↑</button><button class="btn xs red" data-act="jobDel" data-i="${i}">Remove</button></div>
        <div class="grid2"><div><label class="f">Company</label><input class="in" data-job="${i}" data-k="name" value="${esc(w.name)}"></div><div><label class="f">Title</label><input class="in" data-job="${i}" data-k="position" value="${esc(w.position)}"></div>
        <div><label class="f">Start (YYYY-MM)</label><input class="in" data-job="${i}" data-k="startDate" value="${esc(w.startDate)}"></div><div><label class="f">End (YYYY-MM, empty = present)</label><input class="in" data-job="${i}" data-k="endDate" value="${esc(w.endDate || '')}"></div>
        <div><label class="f">Location</label><input class="in" data-job="${i}" data-k="location" value="${esc(w.location || '')}"></div><div><label class="f">Industry / product</label><input class="in" data-job="${i}" data-k="industry" value="${esc(w.industry || w.context || '')}"></div></div>
        <label class="f">Real stack (comma-separated)</label><input class="in" data-job="${i}" data-k="stack" value="${esc([].concat(w.stack || []).join(', '))}">
        <label class="f">Description</label><input class="in" data-job="${i}" data-k="description" value="${esc(w.description || '')}">
        <label class="f">Example bullets (one per line)</label><textarea class="in" rows="4" data-job="${i}" data-k="highlights">${esc((w.highlights || []).join('\n'))}</textarea></div>`).join('')}
      <button class="btn sm ghost block" data-act="jobAdd">+ Add job</button>`;
  } else if (P.tab === 'json') {
    body = `<div class="dim" style="margin-bottom:6px">The whole resume base: basics (headline, summary, links), work, skills, education, languages.</div><textarea class="in mono" id="resumeJson" rows="28">${esc(JSON.stringify(d.resume, null, 2))}</textarea>`;
  } else {
    body = `<label class="f">Profile name</label><input class="in" data-pp="name" value="${esc(d.name)}">
      <label class="f">Default template</label><select class="in" data-pp="template">${TEMPLATES.map((t) => opt(t.id, t.name, d.prefs?.template)).join('')}</select>
      <label class="f">Stack flexibility</label>${Object.entries(STACK_MODES).map(([k, v]) => `<label class="check" style="margin:6px 0"><input type="radio" name="sm" data-pp="stackMode" value="${k}"${(d.prefs?.stackMode || 'adjacent') === k ? ' checked' : ''}>${esc(v)}</label>`).join('')}
      <label class="f">Extra instructions for the resume writer</label><textarea class="in" data-pp="instructions" rows="5" placeholder="e.g. Always lead with backend work; keep the summary to 3 sentences.">${esc(d.prefs?.instructions || '')}</textarea>`;
  }
  return `<div class="bar"><button class="back" data-act="nav" data-v="profiles">←</button><h2>${esc(d.name || 'Profile')}</h2><button class="btn sm ghost" data-act="exportProfile">Export</button>${P.editId ? '<button class="btn sm red" data-act="delProfile">Delete</button>' : ''}</div>
  <div class="tabs">${Object.entries(tabs).map(([k, v]) => `<button data-act="ptab" data-t="${k}" class="${P.tab === k ? 'on' : ''}">${v}</button>`).join('')}</div>
  ${body}<div class="footer-act"><button class="btn" data-act="saveProfile">Save profile</button></div>`;
}

function collectProfile() {
  const d = S.prof.draft;
  for (const el of $app.querySelectorAll('[data-pf]')) d.personal[el.dataset.pf] = el.value;
  for (const el of $app.querySelectorAll('[data-job]')) {
    const w = d.resume.work[Number(el.dataset.job)];
    const k = el.dataset.k;
    if (k === 'stack') w.stack = el.value.split(',').map((s) => s.trim()).filter(Boolean);
    else if (k === 'highlights') w.highlights = el.value.split('\n').map((s) => s.replace(/^[-•*]\s*/, '').trim()).filter(Boolean);
    else w[k] = el.value.trim();
  }
  const json = document.getElementById('resumeJson');
  if (json) d.resume = JSON.parse(json.value);
  for (const el of $app.querySelectorAll('[data-pp]')) {
    if (el.type === 'radio' && !el.checked) continue;
    if (el.dataset.pp === 'name') d.name = el.value.trim() || d.name;
    else d.prefs[el.dataset.pp] = el.value;
  }
  return d;
}

// ---------- settings ----------

function viewSettings() {
  const s = S.settings;
  const inp = (path, label, type = 'text', ph = '') => `<label class="f">${label}</label><input class="in" data-s="${path}" type="${type}" value="${esc(path.split('.').reduce((o, k) => o?.[k], s) ?? '')}" placeholder="${esc(ph)}">`;
  const sel = (path, label, options) => `<label class="f">${label}</label><select class="in" data-s="${path}">${options.map(([v, l]) => opt(v, l, path.split('.').reduce((o, k) => o?.[k], s))).join('')}</select>`;
  const chk = (path, label) => `<label class="check" style="margin:7px 0"><input type="checkbox" data-s="${path}"${path.split('.').reduce((o, k) => o?.[k], s) ? ' checked' : ''}>${label}</label>`;
  return `<div class="bar"><button class="back" data-act="nav" data-v="home">←</button><h2>Settings</h2></div>
  <div class="card"><h3>AI</h3>
    ${sel('ai.resumeEngine', 'Resume writer', Object.entries(ENGINES))}
    <div class="dim" style="margin-top:4px">DeepSeek / ChatGPT chat use your signed-in account in a browser tab (no API cost). OpenAI API needs the key below.</div>
    ${inp('ai.openaiKey', 'OpenAI API key (form answers, cover letters, API resumes)', 'password', 'sk-…')}
    <div class="grid2"><div>${inp('ai.openaiModel', 'Model for form answers', 'text', 'gpt-5-mini')}</div><div>${inp('ai.resumeModel', 'Model for API resumes', 'text', 'gpt-5')}</div></div>
    ${sel('ai.coverEngine', 'Cover letter writer', Object.entries(ENGINES))}
    <div class="grid2"><div>${inp('ai.atsTarget', 'ATS target %', 'number')}</div><div>${inp('ai.autoFixRounds', 'Automatic fix rounds', 'number')}</div></div>
    ${chk('ai.keepChatTab', 'Keep the ChatGPT / DeepSeek tab open after generating')}</div>
  <div class="card"><h3>Autofill</h3>${chk('fill.showButton', 'Show the autofill button on the right edge of pages')}${chk('fill.uploadCover', 'Upload the cover letter where a form asks for one (after you generate it)')}
    ${sel('fill.fileType', 'Resume file to upload', [['pdf', 'PDF'], ['docx', 'Word (.docx)']])}
    ${inp('downloads.folder', 'Folder inside Downloads for resumes and cover letters')}</div>
  <div class="card"><h3>Jobs</h3>${inp('jobs.query', 'Search words (Adzuna, JSearch)')}
    <div class="grid2"><div>${inp('jobs.maxAgeDays', 'Keep jobs posted within (days)', 'number')}</div><div>${inp('jobs.refreshHours', 'Refresh every (hours)', 'number')}</div></div>
    ${chk('jobs.techOnly', 'Only engineering / tech titles')}
    <div class="sec-title">Sources</div><div class="grid2">${Object.keys(s.jobs.sources).map((k) => chk(`jobs.sources.${k}`, k === 'boards' ? 'Company boards (below)' : jobsLib.SOURCES[k] || k)).join('')}</div>
    <div class="grid3"><div>${inp('jobs.adzunaId', 'Adzuna app id')}</div><div>${inp('jobs.adzunaKey', 'Adzuna key', 'password')}</div><div>${inp('jobs.adzunaCountry', 'Country', 'text', 'us')}</div></div>
    ${inp('jobs.jsearchKey', 'JSearch (RapidAPI) key', 'password')}
    <label class="f">Company job boards: <code>greenhouse:slug</code>, <code>lever:slug</code>, <code>ashby:slug</code> (one per line; the slug is the name in the company's jobs URL)</label>
    <textarea class="in mono" data-s="jobs.boards" rows="8">${esc(s.jobs.boards)}</textarea></div>
  <div class="card"><h3>Supabase sync</h3><div class="dim">Turn it on or off on the sign-in screen (log out first). Now: ${S.session?.mode === 'supabase' ? `synced with ${esc(hostOf(s.supabase.url))}` : 'local mode, this PC only'}.</div></div>
  <div class="footer-act"><button class="btn" data-act="saveSettings">Save settings</button></div>`;
}

function collectSettings() {
  const patch = {};
  for (const el of $app.querySelectorAll('[data-s]')) {
    const path = el.dataset.s.split('.');
    let v = el.type === 'checkbox' ? el.checked : el.type === 'number' ? Number(el.value) : el.value.trim();
    if (el.tagName === 'TEXTAREA') v = el.value;
    let o = patch;
    for (const k of path.slice(0, -1)) o = o[k] ||= {};
    o[path[path.length - 1]] = v;
  }
  return patch;
}

// ---------- events ----------

function afterRender() {
  const ta = document.getElementById('resumeText');
  if (ta) {
    // pasting or typing a resume turns on the buttons that need text, without a re-render
    ta.addEventListener('input', () => {
      S.wiz.text = ta.value;
      const has = !!ta.value.trim();
      for (const b of $app.querySelectorAll('[data-act=analyze],[data-act=fixAI],[data-act=preview],[data-act=dlResume],[data-act=toFill]')) {
        b.disabled = !has || (b.dataset.act === 'fixAI' && S.wiz.busy === 'gen');
      }
    });
  }
}

const actions = {
  nav: (el) => {
    if (S.view === 'apply' && S.wiz.step === 1 && S.wiz.app) readJobForm();
    go(el.dataset.v);
    if (el.dataset.v === 'jobs' && !S.jobs.cache) loadJobs().then(render);
  },
  menu: () => {
    S.menu = !S.menu;
    render();
  },
  pwToggle: () => {
    const p = document.getElementById('lp');
    p.type = p.type === 'password' ? 'text' : 'password';
  },
  logout: async () => {
    await store.logout();
    S.session = null;
    render();
  },
  sync: async () => {
    try {
      await store.pull();
      await loadData();
      toast('Up to date.', 'ok');
    } catch (err) {
      toast(err.message, 'err');
    }
    render();
  },

  // jobs
  toggleFilters: () => {
    S.jobs.showFilters = !S.jobs.showFilters;
    render();
  },
  resetFilters: () => {
    S.jobs.f = { ...DEFAULT_FILTERS };
    saveFilters();
    render();
  },
  page: (el) => {
    S.jobs.page += Number(el.dataset.d);
    render();
    window.scrollTo(0, 0);
  },
  refreshJobs: async () => {
    S.jobs.busy = true;
    S.jobs.progress = 'Fetching…';
    render();
    try {
      const r = await send({ type: 'jobs:refresh' });
      await loadJobs();
      const bad = r.report.filter((x) => x.errors.length);
      toast(`${r.count.toLocaleString()} jobs${bad.length ? ` · ${bad.length} source(s) had problems` : ''}`, bad.length ? '' : 'ok');
    } catch (err) {
      toast(err.message, 'err');
    }
    S.jobs.busy = false;
    S.jobs.progress = '';
    render();
  },
  jobReport: () => toast((S.jobs.cache.report || []).map((r) => `${r.source}: ${r.count}${r.errors.length ? ` (${r.errors.slice(0, 3).join('; ')})` : ''}`).join('\n'), '', 12000),
  openJob: async (el) => {
    const j = S.jobs.cache.items.find((x) => x.id === el.dataset.id);
    if (!j) return;
    S.jobs.marks = await jobsLib.mark('viewed', j.id);
    chrome.tabs.create({ url: j.url, active: true });
    render();
  },
  flagJob: async (el) => {
    const id = el.dataset.id;
    S.jobs.marks = await jobsLib.mark('flagged', id, !S.jobs.marks?.flagged[id]);
    render();
  },
  applyJob: async (el) => {
    const j = S.jobs.cache.items.find((x) => x.id === el.dataset.id);
    if (!j) return;
    S.jobs.marks = await jobsLib.mark('viewed', j.id);
    chrome.tabs.create({ url: j.url, active: true });
    openWizard(newDraft({
      jobId: j.id, title: j.title, company: j.company, url: j.url, applyUrl: j.applyUrl !== j.url ? j.applyUrl : '', jd: j.description, type: j.type, workMode: j.workMode,
      location: j.location || 'United States', salaryMin: j.salaryMin || '', salaryMax: j.salaryMax || '', currency: j.currency || 'USD', source: j.source, platform: j.platform
    }));
  },

  // wizard
  applyCurrent: async () => {
    await openWizard(newDraft());
    actions.readTab();
  },
  newApp: () => openWizard(newDraft()),
  openApp: async (el) => {
    const a = S.apps.find((x) => x.id === el.dataset.id);
    if (a) openWizard(a, a.status === 'draft' ? 1 : 2);
  },
  delApp: async (el) => {
    const a = S.apps.find((x) => x.id === el.dataset.id);
    if (!a) return;
    if (el.dataset.sure !== '1') {
      el.dataset.sure = '1';
      el.textContent = 'Sure?';
      return;
    }
    await store.remove('applications', a.id);
    await store.dropLocal(a.id);
    await refreshApps();
    render();
  },
  wizBack: () => {
    if (S.wiz.step > 1) return actions.step({ dataset: { n: String(S.wiz.step - 1) } });
    go(S.wiz.app?.jobId ? 'jobs' : 'home');
  },
  step: async (el) => {
    const n = Number(el.dataset.n);
    if (S.wiz.step === 1) readJobForm();
    if (n > 1 && !S.wiz.app.id) return actions.toResume();
    if (S.wiz.step === 2) currentResume();
    S.wiz.step = n;
    render();
  },
  readTab: async () => {
    if (S.wiz.step === 1) readJobForm();
    S.wiz.busy = 'read';
    render();
    try {
      const j = await send({ type: 'job:read' });
      const a = S.wiz.app;
      Object.assign(a, {
        title: j.title || a.title, company: j.company || a.company, url: j.url || a.url, location: j.location || a.location,
        workMode: j.remote ? 'Remote' : a.workMode, jd: j.text && j.text.length > (a.jd || '').length ? j.text : a.jd, platform: jobsLib.platformOf(j.url) || a.platform
      });
      const m = /([\d,.]+)\s*k?\D{1,6}([\d,.]+)\s*k?/i.exec(j.salary || '');
      if (m) Object.assign(a, { salaryMin: Number(m[1].replace(/,/g, '')) || '', salaryMax: Number(m[2].replace(/,/g, '')) || '' });
      setStatus('');
    } catch (err) {
      S.wiz.status = 'Could not read the tab: ' + err.message;
      S.wiz.statusKind = 'err';
    }
    S.wiz.busy = '';
    render();
  },
  toResume: async () => {
    readJobForm();
    const a = S.wiz.app;
    const missing = [['title', 'Job title'], ['company', 'Company'], ['url', 'Job URL'], ['jd', 'Job description']].filter(([k]) => !String(a[k] || '').trim()).map(([, l]) => l);
    if (missing.length) {
      S.wiz.status = `Fill in: ${missing.join(', ')}`;
      S.wiz.statusKind = 'err';
      return render();
    }
    if (!profile()) return toast('Create a profile first.', 'err');
    S.wiz.status = '';
    await saveApp({ platform: a.platform || jobsLib.platformOf(a.url) });
    S.wiz.step = 2;
    render();
  },
  engine: (el) => {
    S.wiz.engine = el.dataset.e;
    render();
  },
  generate: () => generate(),
  fixAI: () => generate({ fixOnly: true }),
  stopGen: () => S.wiz.abort?.abort(),
  analyze: () => {
    analyzeText(true);
    render();
  },
  preview: () => {
    const r = currentResume();
    if (!r) return;
    const m = document.createElement('div');
    m.className = 'modal';
    m.innerHTML = `<div class="mh"><span class="grow">Preview · ${esc(TEMPLATES.find((t) => t.id === S.wiz.app.template)?.name || '')}</span><button class="btn sm ghost" id="mclose">Close</button></div><iframe></iframe>`;
    document.body.appendChild(m);
    m.querySelector('iframe').srcdoc = renderResumeHtml(r, S.wiz.app.template);
    m.querySelector('#mclose').onclick = () => m.remove();
  },
  dlResume: async (el) => {
    S.wiz.busy = 'dl';
    render();
    try {
      const out = await resumeFiles(el.dataset.k);
      toast(`Saved: ${out.map((f) => f.split('/').pop()).join(', ')}`, 'ok');
    } catch (err) {
      toast(err.message, 'err', 7000);
    }
    S.wiz.busy = '';
    render();
  },
  toFill: async () => {
    analyzeText(true);
    await saveApp({ atsScore: S.wiz.ats?.score, risk: S.wiz.val?.risk });
    S.wiz.step = 3;
    render();
  },
  fillNow: async () => {
    S.wiz.fillBusy = true;
    S.wiz.log = [];
    render();
    try {
      await send({ type: 'fill:tab' });
    } catch (err) {
      S.wiz.log.push({ text: err.message, level: 'err' });
    }
    S.wiz.fillBusy = false;
    if (S.wiz.app?.id) S.wiz.app = (await store.getDoc('applications', S.wiz.app.id)) || S.wiz.app;
    render();
  },
  openUrl: () => chrome.tabs.create({ url: S.wiz.app.applyUrl || S.wiz.app.url, active: true }),
  markApplied: async () => {
    await saveApp({ status: 'applied', appliedAt: Date.now() });
    toast('Saved as applied.', 'ok');
    render();
  },
  genCover: () => genCover(),
  dlCover: async (el) => {
    try {
      const out = await coverFiles(el.dataset.k);
      toast(`Saved: ${out.map((f) => f.split('/').pop()).join(', ')}`, 'ok');
    } catch (err) {
      toast(err.message, 'err', 7000);
    }
    render();
  },

  // profiles
  newProfile: () => {
    S.prof = { editId: null, tab: 'personal', draft: emptyProfile() };
    go('profile');
  },
  editProfile: (el) => {
    const p = S.profiles.find((x) => x.id === el.dataset.id);
    S.prof = { editId: p.id, tab: 'personal', draft: JSON.parse(JSON.stringify(p)) };
    go('profile');
  },
  useProfile: (el) => setProfile(el.dataset.id),
  ptab: (el) => {
    try {
      collectProfile();
    } catch (err) {
      return toast('Resume JSON: ' + err.message, 'err');
    }
    S.prof.tab = el.dataset.t;
    render();
  },
  jobAdd: () => {
    collectProfile();
    S.prof.draft.resume.work.push({ name: '', position: '', startDate: '', endDate: '', location: '', industry: '', stack: [], description: '', highlights: [] });
    render();
  },
  jobDel: (el) => {
    collectProfile();
    S.prof.draft.resume.work.splice(Number(el.dataset.i), 1);
    render();
  },
  jobMove: (el) => {
    collectProfile();
    const w = S.prof.draft.resume.work;
    const i = Number(el.dataset.i);
    [w[i - 1], w[i]] = [w[i], w[i - 1]];
    render();
  },
  saveProfile: async () => {
    let d;
    try {
      d = syncBasics(collectProfile());
    } catch (err) {
      return toast('Resume JSON: ' + err.message, 'err');
    }
    if (!d.personal.first_name && !d.resume.basics?.name) return toast('Add at least a name.', 'err');
    if (d.name === 'New profile' && d.resume.basics?.name) d.name = d.resume.basics.name;
    const saved = await store.put('profiles', { ...d, id: S.prof.editId || undefined });
    S.prof.editId = saved.id;
    S.prof.draft = JSON.parse(JSON.stringify(saved));
    S.profiles = await store.list('profiles');
    if (!S.active.profileId) S.active = await store.setActive({ profileId: saved.id });
    toast(saved._syncError ? `Saved on this PC; ${saved._syncError}` : 'Profile saved.', saved._syncError ? 'err' : 'ok');
    render();
  },
  delProfile: async (el) => {
    if (el.dataset.sure !== '1') {
      el.dataset.sure = '1';
      el.textContent = 'Sure?';
      return;
    }
    await store.remove('profiles', S.prof.editId);
    S.profiles = await store.list('profiles');
    if (S.active.profileId === S.prof.editId) S.active = await store.setActive({ profileId: S.profiles[0]?.id || '' });
    go('profiles');
  },
  exportProfile: async () => {
    const d = JSON.parse(JSON.stringify(collectProfile()));
    for (const k of store.SECRET_KEYS) delete d.personal[k];
    delete d.id;
    await download(new TextEncoder().encode(JSON.stringify(d, null, 2)), 'application/json', `${S.settings.downloads.folder || 'AutoBid'}/profiles/${slug(d.name)}.json`);
    toast('Exported to Downloads (without secrets).', 'ok');
  },

  saveSettings: async () => {
    S.settings = await store.saveSettings(collectSettings());
    chrome.alarms.create('jobs', { periodInMinutes: Math.max(60, (S.settings.jobs.refreshHours || 6) * 60) });
    toast('Settings saved.', 'ok');
    render();
  }
};

const changes = {
  profile: (el) => setProfile(el.value),
  jobField: (el) => {
    S.jobs.field = el.value;
    S.jobs.page = 1;
    render();
  },
  template: async (el) => {
    S.wiz.app.template = el.value;
    if (S.wiz.app.id) {
      await saveApp({ template: el.value });
      await store.saveFile(S.wiz.app.id, 'resume', null);
    }
    render();
  },
  status: async (el) => {
    await saveApp({ status: el.value, ...(el.value === 'applied' && !S.wiz.app.appliedAt ? { appliedAt: Date.now() } : {}) });
    render();
  },
  coverEngine: async (el) => {
    S.settings = await store.saveSettings({ ai: { coverEngine: el.value } });
  },
  appsStatus: (el) => {
    S.apps_f.status = el.value;
    render();
  },
  appsProfile: (el) => {
    S.apps_f.profile = el.value;
    render();
  },
  importProfile: async (el) => {
    const file = el.files[0];
    if (!file) return;
    try {
      const p = importProfile(await file.text());
      S.prof = { editId: null, tab: 'experience', draft: p };
      toast('Imported: check it and press Save profile.', 'ok');
      go('profile');
    } catch (err) {
      toast('Import failed: ' + err.message, 'err', 7000);
    }
  }
};

$app.addEventListener('click', (e) => {
  const el = e.target.closest('[data-act]');
  if (S.menu && !e.target.closest('.user')) {
    S.menu = false;
    if (!el) render();
  }
  if (!el) return;
  if (el.tagName === 'A') e.preventDefault();
  const fn = actions[el.dataset.act];
  if (fn) Promise.resolve(fn(el, e)).catch((err) => toast(err.message, 'err', 7000));
});

$app.addEventListener('change', (e) => {
  const el = e.target;
  if (el.dataset.change && changes[el.dataset.change]) return Promise.resolve(changes[el.dataset.change](el)).catch((err) => toast(err.message, 'err'));
  if (el.dataset.f) {
    S.jobs.f[el.dataset.f] = el.value;
    S.jobs.page = 1;
    saveFilters();
    render();
  } else if (el.dataset.fc) {
    S.jobs.f[el.dataset.fc] = el.checked;
    S.jobs.page = 1;
    saveFilters();
    render();
  } else if (el.dataset.ms) {
    const k = el.dataset.ms;
    const set = new Set(S.jobs.f[k]);
    if (el.checked) set.add(el.value);
    else set.delete(el.value);
    S.jobs.f[k] = [...set];
    S.jobs.page = 1;
    saveFilters();
    // keep the dropdown open while ticking several options
    const openKey = k;
    render();
    $app.querySelector(`[data-ms="${openKey}"]`)?.closest('details')?.setAttribute('open', '');
  }
});

// live search boxes: update state, re-render after a short pause, keep focus and caret
$app.addEventListener('input', (e) => {
  const el = e.target;
  if (!el.dataset.bind) return;
  const [obj, key] = el.dataset.bind.split('.');
  S[obj][key] = el.value;
  if (obj === 'jobs') S.jobs.page = 1;
  clearTimeout(window.__liveT);
  window.__liveT = setTimeout(() => {
    const pos = el.selectionStart;
    render();
    const again = $app.querySelector(`[data-bind="${el.dataset.bind}"]`);
    if (again) {
      again.focus();
      again.setSelectionRange(pos, pos);
    }
  }, 250);
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'fill:log' && S.view === 'apply') {
    S.wiz.log.push({ text: msg.text, level: msg.level });
    const log = $app.querySelector('.log');
    if (log) {
      const div = document.createElement('div');
      div.className = msg.level || '';
      div.textContent = msg.text;
      log.appendChild(div);
      log.scrollTop = log.scrollHeight;
    } else if (S.wiz.step === 3) render();
  } else if (msg.type === 'fill:done') {
    const d = msg.data;
    if (S.view === 'apply') {
      const lines = d.status === 'success' ? [{ text: 'The page says the application was submitted.', level: 'ok' }]
        : d.status === 'none' ? [{ text: d.note, level: 'warn' }]
          : [{ text: `Filled ${d.filled} field(s)${d.uploaded ? ', resume uploaded' : ''}.`, level: 'ok' },
            ...(d.left ? [{ text: `${d.left} required field(s) still empty.`, level: 'warn' }] : []),
            ...(d.failed || []).map((f) => ({ text: f, level: 'warn' })),
            ...(d.note ? [{ text: d.note, level: 'warn' }] : []),
            { text: 'Check the page, then press Next / Submit yourself.' }];
      S.wiz.log.push(...lines);
      if (!S.wiz.fillBusy) render();
    }
  } else if (msg.type === 'app:changed') {
    refreshApps().then(async () => {
      if (S.wiz.app?.id === msg.id) S.wiz.app = (await store.getDoc('applications', msg.id)) || S.wiz.app;
      if (S.view !== 'profile' && S.view !== 'settings') render();
    });
  } else if (msg.type === 'jobs:progress') {
    S.jobs.progress = msg.text;
    const el = $app.querySelector('.search + .dim');
    if (el && S.view === 'jobs') el.textContent = msg.text;
  } else if (msg.type === 'jobs:changed' && !S.jobs.busy) {
    loadJobs().then(() => S.view === 'jobs' && render());
  }
});

// ---------- start ----------

(async () => {
  S.settings = await store.getSettings();
  S.session = await store.getSession();
  if (S.session) {
    await loadData();
    if (S.session.mode === 'supabase') store.pull().then(loadData).then(render).catch(() => {});
    loadJobs();
  }
  render();
})();
