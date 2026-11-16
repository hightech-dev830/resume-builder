import { getSettings, saveSettings, rpc, SITES, siteOf, listUrlOf } from './lib/config.js';

const $ = (s) => document.querySelector(s);
const PHASES = {
  idle: 'Idle', starting: 'Starting…', scanning: 'Scanning jobs', opening: 'Opening job', opening_apply: 'Opening application',
  generating: 'Writing resume', waiting_jobs: 'Waiting for new jobs', applying: 'Filling form', thinking: 'Reading page (AI)', waiting_human: 'Waiting for you', stopping: 'Stopping…'
};

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

let lastBot = null;
function render(bot) {
  const b = bot || { running: false, phase: 'idle', counts: {}, log: [] };
  lastBot = b;
  if (infoOpen) refreshInfoSoon(b);
  $('#dot').className = 'dot' + (b.running ? (b.phase === 'waiting_human' ? ' human' : ' on') : '');
  $('#phase').textContent = PHASES[b.phase] || b.phase;
  $('#startBtn').classList.toggle('hidden', b.running);
  $('#stopBtn').classList.toggle('hidden', !b.running);
  $('#singleBtn').disabled = b.running;
  $('#site').disabled = b.running; // the running bot keeps the board it started on
  for (const k of ['applied', 'attention', 'failed', 'skipped']) $('#c' + k[0].toUpperCase() + k.slice(1)).textContent = b.counts?.[k] || 0;
  const c = b.current;
  $('#current').classList.toggle('hidden', !c);
  // every job card gets "Skip job" (Jobright, HiringCafe, Continue to apply…), not only while the main loop runs
  $('#skipBtn').classList.toggle('hidden', !c);
  if (c) {
    $('#curTitle').textContent = `${c.title || 'Job'}${c.company ? ` @ ${c.company}` : ''}`;
    $('#curMeta').textContent = [c.bidId && `Bid #${c.bidId}`, c.step && `step ${c.step}`].filter(Boolean).join(' · ');
  }
  const log = $('#log');
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
  log.innerHTML = (b.log || []).slice(-200).map((l) =>
    `<div class="${l.level === 'error' ? 'err' : l.level === 'warn' ? 'warn' : l.level === 'ok' ? 'ok' : ''}"><time>${new Date(l.t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time>${esc(l.msg)}</div>`).join('');
  if (atBottom) log.scrollTop = log.scrollHeight;
}

async function send(type, extra = {}) {
  $('#msg').textContent = '';
  const res = await chrome.runtime.sendMessage({ type, ...extra });
  if (!res?.ok) {
    $('#msg').textContent = res?.error || 'Something went wrong';
    $('#msg').className = 'err';
  }
}

$('#startBtn').addEventListener('click', () => send('start'));
$('#stopBtn').addEventListener('click', () => send('stop'));
$('#skipBtn').addEventListener('click', () => send('skip'));
$('#singleBtn').addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  send('runSingle', { tabId: tab.id });
});
$('#clearLog').addEventListener('click', () => send('clearLog'));

// "Continue to apply": carry on with the "need you" job open in the active tab (that one job only),
// after you fixed its page. While it runs, "Stop continuing to apply" ends it.
let retrying = null; // tab id of the running / queued "Continue to apply"
async function showAgain() {
  const all = (await chrome.storage.session.get('attentionTabs')).attentionTabs || {};
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const here = tab && all[tab.id];
  const label = (e) => (e ? `· ${e.job?.role || 'job'}${e.job?.company ? ` @ ${e.job.company}` : ''}` : '');
  $('#stopAgainBtn').classList.toggle('hidden', retrying == null);
  $('#stopAgainInfo').textContent = retrying != null ? label(all[retrying]) : '';
  $('#againBtn').classList.toggle('hidden', !here || retrying != null);
  $('#againInfo').textContent = label(here);
  // "Generate resume & cover letter only": any web page except the job board lists
  const { generatingTab } = await chrome.storage.session.get('generatingTab');
  const url = tab?.url || '';
  const isList = Object.values(SITES).some((x) => x.isList?.(url));
  $('#genBtn').classList.toggle('hidden', !/^https?:/.test(url) || isList);
  $('#genBtn').disabled = generatingTab != null;
  $('#genBtn').textContent = generatingTab != null ? '✎ Writing resume & cover letter…' : '✎ Generate resume & cover letter only';
}
$('#genBtn').addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  $('#msg').textContent = '';
  $('#genBtn').disabled = true;
  $('#genBtn').textContent = '✎ Writing resume & cover letter…';
  const res = await chrome.runtime.sendMessage({ type: 'generateOnly', tabId: tab?.id }).catch((err) => ({ ok: false, error: err.message }));
  $('#msg').textContent = res?.ok ? `Resume & cover letter ready (bid #${res.bidId})${res.dir ? `: ${res.dir}` : ''}` : res?.error || 'Something went wrong';
  $('#msg').className = res?.ok ? 'ok' : 'err';
  showAgain();
});
chrome.tabs.onUpdated.addListener((_id, info) => info.url && showAgain());
$('#stopAgainBtn').addEventListener('click', () => send('stopAgain'));
$('#againBtn').addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  $('#msg').textContent = '';
  const res = await chrome.runtime.sendMessage({ type: 'applyAgain', tabId: tab?.id });
  if (!res?.ok) {
    $('#msg').textContent = res?.error || 'Something went wrong';
    $('#msg').className = 'err';
  } else if (res.queued) {
    retrying = tab.id;
    showAgain();
    $('#msg').textContent = 'Queued: the bot continues to apply right after the current job.';
    $('#msg').className = 'muted';
  }
});
chrome.storage.onChanged.addListener((_ch, area) => area === 'session' && showAgain());
chrome.tabs.onActivated.addListener(showAgain);
chrome.tabs.onRemoved.addListener(() => setTimeout(showAgain, 200));
showAgain();

// History page (one tab, reused): the counters open it filtered to their status
async function openHistory(status = '') {
  const base = chrome.runtime.getURL('history.html');
  const tab = (await chrome.tabs.query({})).find((t) => (t.url || '').startsWith(base));
  if (tab) {
    await chrome.tabs.update(tab.id, { active: true });
    await chrome.windows.update(tab.windowId, { focused: true }).catch(() => {});
    chrome.runtime.sendMessage({ type: 'historyStatus', status }).catch(() => {});
  } else {
    chrome.tabs.create({ url: base + (status ? `?status=${status}` : '') });
  }
}
document.querySelectorAll('.counts [data-status]').forEach((el) => el.addEventListener('click', () => openHistory(el.dataset.status)));
$('#openHistory').addEventListener('click', () => openHistory());
$('#openOptions').addEventListener('click', () => chrome.runtime.openOptionsPage());
$('#openOptions2').addEventListener('click', () => chrome.runtime.openOptionsPage());
$('#openBoard').addEventListener('click', async () => chrome.tabs.create({ url: listUrlOf(await getSettings()) }));

// job site picker
$('#site').innerHTML = Object.entries(SITES).map(([k, v]) => `<option value="${k}">${esc(v.label)}</option>`).join('');
function showSite(s) {
  $('#site').value = s.site;
  $('#openBoard').textContent = `Open ${siteOf(s).label}`;
}
$('#site').addEventListener('change', async () => showSite(await saveSettings({ site: $('#site').value })));
chrome.storage.onChanged.addListener((ch) => ch.settings && getSettings().then(showSite));

function trackRetry(bot) {
  const r = bot?.running && bot.retryTabId != null ? bot.retryTabId : null;
  if (r !== retrying) { retrying = r; showAgain(); }
}
chrome.storage.onChanged.addListener((ch) => ch.bot && (render(ch.bot.newValue), trackRetry(ch.bot.newValue)));
chrome.storage.local.get('bot').then(({ bot }) => (render(bot), trackRetry(bot)));
getSettings().then((s) => {
  showSite(s);
  $('#signin').classList.toggle('hidden', !!s.token);
  if (s.token) showUsage();
});

const money = (t) => (t.unpriced && !t.cost ? 'cost unknown' : '$' + t.cost.toFixed(t.cost < 1 ? 4 : 2) + (t.unpriced ? '+' : ''));

// AI tokens and US$ (today and the last 30 days)
async function showUsage() {
  try {
    const u = await rpc('usage:summary');
    $('#usage').textContent = u.missingTable
      ? 'AI cost tracking: run supabase/migrations/003_ai_usage.sql'
      : `AI today: ${money(u.today)} · ${u.today.tokens.toLocaleString()} tokens   |   30 days: ${money(u.month)}`;
  } catch {
    $('#usage').textContent = '';
  }
}
// the side panel stays open across tabs: show "Apply to this job" only on a Jobright job page
function checkTab() {
  chrome.tabs.query({ active: true, currentWindow: true }).then(([tab]) => {
    $('#singleBtn').classList.toggle('hidden', !/jobright\.ai\/jobs\/info\//.test(tab?.url || ''));
  });
}
checkTab();
chrome.tabs.onActivated.addListener(checkTab);
chrome.tabs.onUpdated.addListener((_id, info) => info.url && checkTab());
// refresh the AI cost line every minute
setInterval(() => getSettings().then((s) => s.token && showUsage()), 60000);

// version label: "1.3.01_R" (manifest version_name)
{ const m = chrome.runtime.getManifest(); const v = document.querySelector("#ver"); if (v) v.textContent = m.version_name || m.version; }

// ---------- job details (the "i" button in the header) ----------
// The job the bot is on: the running job, else the "need you" job in this tab, else the last job handled.
let infoOpen = false;
let infoKey = '';
let serverDir = null; // folder where bid files are saved on this PC ('' = Supabase Storage)

async function currentJob() {
  const b = lastBot || {};
  if (b.running && b.current) return { from: 'Working on now', title: b.current.title, company: b.current.company, url: b.current.url, bidId: b.current.bidId, step: b.current.step };
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const att = tab && ((await chrome.storage.session.get('attentionTabs')).attentionTabs || {})[tab.id];
  if (att) return { from: 'Needs you (this tab)', title: att.job?.role, company: att.job?.company, url: att.postingUrl, applyUrl: att.job?.job_link, bidId: att.bidId, step: att.progress?.step };
  const { history = [] } = await chrome.storage.local.get('history');
  const h = history[history.length - 1];
  if (h) return { from: `Last job (${h.status})`, title: h.title, company: h.company, url: h.postingUrl, applyUrl: h.applyUrl, bidId: h.bidId, reason: h.reason };
  return null;
}

const link = (u) => (u ? `<a href="${esc(u)}" target="_blank" rel="noreferrer">${esc(u.length > 70 ? u.slice(0, 70) + '…' : u)}</a>` : '');
const copyBtn = (v) => (v ? `<button class="copy" data-copy="${esc(v)}" title="Copy">copy</button>` : '');
const when = (t) => (t ? new Date(t).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '');

async function showInfo() {
  const box = $('#jobInfo');
  const job = await currentJob();
  if (!job) {
    box.innerHTML = '<div class="hd"><span class="t">Job details</span><button id="infoClose">✕</button></div><div class="muted">No job yet. Start the bot, or open the tab of a "need you" job.</div>';
    return;
  }
  infoKey = `${job.bidId || ''}|${job.title || ''}|${job.step || ''}`;
  let bid = null;
  let err = '';
  if (job.bidId) {
    try {
      bid = await rpc('resumes:get', job.bidId);
      if (serverDir == null) serverDir = (await rpc('settings:get').catch(() => ({}))).local_output_dir || '';
    } catch (e) {
      err = e.message;
    }
  }
  const full = (rel) => (rel ? (serverDir ? `${serverDir.replace(/[\/]+$/, '')}/${rel}` : `${rel} (Supabase Storage)`) : '');
  const rows = [
    ['Company', esc(bid?.company || job.company)],
    ['Role', esc(bid?.role || job.title)],
    ['Status', esc([bid?.status, bid?.bot_status && `bot: ${bid.bot_status}`].filter(Boolean).join(' · ') || job.from)],
    job.step ? ['Step', esc(job.step)] : null,
    job.reason ? ['Reason', esc(job.reason)] : null,
    ['Bid', job.bidId ? `#${esc(job.bidId)}${bid?.created_at ? ` · ${esc(when(bid.created_at))}` : ''}` : '<span class="muted">not created yet (made when the form needs the resume)</span>'],
    ['Location', esc(bid?.location || '')],
    ['Salary', esc(bid?.salary || '')],
    ['Resume', bid?.pdf_path ? `${esc(full(bid.pdf_path))}${copyBtn(full(bid.pdf_path))}` : ''],
    ['Cover letter', bid?.cover_letter_path ? `${esc(full(bid.cover_letter_path))}${copyBtn(full(bid.cover_letter_path))}` : ''],
    ['Folder', bid?.resume_dir ? `${esc(full(bid.resume_dir))}${copyBtn(full(bid.resume_dir))}` : ''],
    ['Job link', link(bid?.job_link || job.url)],
    ['Apply page', link(bid?.apply_url && bid.apply_url !== bid.job_link ? bid.apply_url : job.applyUrl && job.applyUrl !== (bid?.job_link || job.url) ? job.applyUrl : '')],
    ['Source URL', link(bid?.source_url || '')],
    ['Source', esc(bid?.source || '')],
    ['Template', esc(bid?.template || '')],
    ['AI model', esc([bid?.ai_provider, bid?.ai_model].filter(Boolean).join(' / '))],
    ['Applied at', esc(when(bid?.applied_at))],
    ['Notes', esc(bid?.notes || '')]
  ].filter((r) => r && r[1]);
  const log = Array.isArray(bid?.bot_log) ? bid.bot_log.slice(-12).map((l) => `${new Date(l.t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}  ${l.msg}`).join('\n') : '';
  const answers = Array.isArray(bid?.answers) ? bid.answers.map((a) => `${a.question}\n  → ${a.answer}`).join('\n') : '';
  box.innerHTML = `
    <div class="hd"><span class="t">${esc(job.title || 'Job')}${job.company ? ` @ ${esc(job.company)}` : ''}</span><button id="infoRefresh" title="Reload">↻</button><button id="infoClose" title="Close">✕</button></div>
    <div class="muted">${esc(job.from)}${err ? ` · <span class="err">${esc(err)}</span>` : ''}</div>
    <dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>
    ${bid?.jd ? `<details><summary>Job description (${bid.jd.length.toLocaleString()} characters)</summary><pre>${esc(bid.jd)}</pre></details>` : ''}
    ${answers ? `<details><summary>Answers typed (${bid.answers.length})</summary><pre>${esc(answers)}</pre></details>` : ''}
    ${bid?.cover_letter ? `<details><summary>Cover letter</summary><pre>${esc(bid.cover_letter)}</pre></details>` : ''}
    ${log ? `<details><summary>Bid log</summary><pre>${esc(log)}</pre></details>` : ''}`;
}

let infoTimer = null;
function refreshInfoSoon(b) {
  // the bot moved to another job, made the bid, or went to another step: show the new details
  const c = b?.running && b.current;
  const key = c ? `${c.bidId || ''}|${c.title || ''}|${c.step || ''}` : infoKey;
  if (key === infoKey) return;
  clearTimeout(infoTimer);
  infoTimer = setTimeout(showInfo, 600);
}

$('#infoBtn').addEventListener('click', async () => {
  infoOpen = !infoOpen;
  $('#infoBtn').classList.toggle('on', infoOpen);
  $('#jobInfo').classList.toggle('hidden', !infoOpen);
  if (infoOpen) {
    $('#jobInfo').innerHTML = '<div class="muted">Loading…</div>';
    await showInfo();
  }
});
$('#jobInfo').addEventListener('click', async (e) => {
  if (e.target.closest('#infoClose')) {
    infoOpen = false;
    $('#infoBtn').classList.remove('on');
    $('#jobInfo').classList.add('hidden');
  } else if (e.target.closest('#infoRefresh')) {
    await showInfo();
  } else if (e.target.dataset.copy) {
    await navigator.clipboard.writeText(e.target.dataset.copy).catch(() => {});
    e.target.textContent = 'copied';
    setTimeout(() => (e.target.textContent = 'copy'), 1200);
  }
});
chrome.tabs.onActivated.addListener(() => infoOpen && !lastBot?.running && showInfo());
