import { getSettings, saveSettings, rpc } from './lib/config.js';

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const PHASES = {
  idle: 'Idle', starting: 'Starting…', running: 'Running', scanning: 'Scanning jobs', opening: 'Opening job', opening_apply: 'Opening application',
  generating: 'Writing resume', waiting_jobs: 'Waiting for new jobs', applying: 'Filling form', thinking: 'Reading page (AI)',
  waiting_human: 'Waiting for you', waiting_email: 'Reading Gmail', stopping: 'Stopping…'
};
const LINK_STATUS = { pending: 'queued', running: 'running', applied: 'applied', attention: 'need you', skipped: 'skipped', failed: 'failed', stopped: 'stopped' };

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

let mode = 'jobright';
let bot = null;
let links = [];

function setMode(m) {
  mode = m;
  $$('#modes button').forEach((b) => b.classList.toggle('active', b.dataset.mode === m));
  $('#links').classList.toggle('hidden', m !== 'links');
  $('#openJobright').classList.toggle('hidden', m !== 'jobright');
  checkTab();
  try { localStorage.setItem('ab-mode', m); } catch { /* storage blocked */ }
}

function render() {
  const b = bot || { running: false, phase: 'idle', counts: {}, log: [], jobs: [] };
  if (b.running && b.mode && b.mode !== mode) setMode(b.mode);
  const human = (b.jobs || []).some((j) => j.phase === 'waiting_human');
  $('#dot').className = 'dot' + (b.running ? (human ? ' human' : ' on') : '');
  $('#phase').textContent = human ? PHASES.waiting_human : PHASES[b.phase] || b.phase;
  $('#startBtn').classList.toggle('hidden', b.running);
  $('#stopBtn').classList.toggle('hidden', !b.running);
  $('#singleBtn').disabled = b.running;
  $$('#modes button').forEach((x) => (x.disabled = b.running));
  for (const k of ['applied', 'attention', 'failed', 'skipped']) $('#c' + k[0].toUpperCase() + k.slice(1)).textContent = b.counts?.[k] || 0;

  // one card per job being applied to, each with its own Skip
  $('#jobs').innerHTML = (b.running ? b.jobs || [] : []).map((j) => `
    <div class="card job">
      <div class="info">
        <div class="t" title="${esc(j.url)}">${esc(j.title || 'Job')}${j.company ? ` @ ${esc(j.company)}` : ''}</div>
        <div class="muted">${[(b.workers > 1) && `#${j.w + 1}`, PHASES[j.phase] || j.phase, j.bidId && `Bid #${j.bidId}`, j.step && `step ${j.step}`].filter(Boolean).join(' · ')}</div>
      </div>
      <button data-skip="${j.w}" title="Drop this job now and go to the next one">⏭ Skip</button>
    </div>`).join('');

  const log = $('#log');
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
  log.innerHTML = (b.log || []).slice(-200).map((l) =>
    `<div class="${l.level === 'error' ? 'err' : l.level === 'warn' ? 'warn' : l.level === 'ok' ? 'ok' : ''}"><time>${new Date(l.t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time>${esc(l.msg)}</div>`).join('');
  if (atBottom) log.scrollTop = log.scrollHeight;
}

function renderLinks() {
  const n = (st) => links.filter((l) => l.status === st).length;
  const done = links.length - n('pending') - n('running');
  $('#linkCounts').textContent = links.length
    ? `${n('pending')} queued · ${n('running')} running · ${done} done (${n('applied')} applied, ${n('attention')} need you)`
    : 'No links yet';
  // running first, then queued, then the most recent finished
  const order = { running: 0, pending: 1 };
  const shown = [...links].sort((a, b) => (order[a.status] ?? 2) - (order[b.status] ?? 2) || (b.t || 0) - (a.t || 0)).slice(0, 150);
  $('#linkList').innerHTML = shown.map((l) => `
    <div><span class="st ${l.status}">${LINK_STATUS[l.status] || l.status}</span>
      <a class="u" href="${esc(l.url)}" target="_blank" title="${esc(l.note || l.url)}">${esc(l.company ? `${l.title} @ ${l.company}` : l.url)}</a></div>`).join('');
}

async function send(type, extra = {}) {
  $('#msg').textContent = '';
  const res = await chrome.runtime.sendMessage({ type, ...extra });
  if (!res?.ok) {
    $('#msg').textContent = res?.error || 'Something went wrong';
    $('#msg').className = 'err';
  }
  return res;
}

$('#modes').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-mode]');
  if (b && !b.disabled) setMode(b.dataset.mode);
});
$('#jobs').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-skip]');
  if (b) send('skip', { w: Number(b.dataset.skip) });
});
$('#startBtn').addEventListener('click', () => send('start', { mode }));
$('#stopBtn').addEventListener('click', () => send('stop'));
$('#singleBtn').addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  send('runSingle', { tabId: tab.id });
});
$('#addLinks').addEventListener('click', async () => {
  const res = await send('addLinks', { text: $('#linkInput').value });
  if (res?.ok) {
    $('#linkInput').value = '';
    $('#msg').className = 'muted';
    $('#msg').textContent = `${res.added} links added${res.dupes ? ` (${res.dupes} of them repeats, applied again)` : ''}.`;
  }
});
$('#retryLinks').addEventListener('click', () => send('clearLinks', { which: 'retry' }));
$('#clearDone').addEventListener('click', () => send('clearLinks', { which: 'done' }));
$('#clearAll').addEventListener('click', () => confirm('Remove every link that is not running?') && send('clearLinks', { which: 'all' }));
$('#clearLog').addEventListener('click', () => send('clearLog'));
$('#openOptions').addEventListener('click', () => chrome.runtime.openOptionsPage());
$('#openOptions2').addEventListener('click', () => chrome.runtime.openOptionsPage());
$('#openJobright').addEventListener('click', async () => chrome.tabs.create({ url: (await getSettings()).listUrl }));

chrome.storage.onChanged.addListener((ch) => {
  if (ch.bot) { bot = ch.bot.newValue; render(); }
  if (ch.links) { links = ch.links.newValue || []; renderLinks(); }
});
chrome.storage.local.get(['bot', 'links']).then((r) => {
  bot = r.bot;
  links = r.links || [];
  let saved = 'jobright';
  try { saved = localStorage.getItem('ab-mode') || 'jobright'; } catch { /* storage blocked */ }
  setMode(bot?.running ? bot.mode || saved : saved);
  render();
  renderLinks();
});
getSettings().then((s) => {
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
    $('#singleBtn').classList.toggle('hidden', mode !== 'jobright' || !/jobright\.ai\/jobs\/info\//.test(tab?.url || ''));
  });
}
chrome.tabs.onActivated.addListener(checkTab);
chrome.tabs.onUpdated.addListener((_id, info) => info.url && checkTab());
// refresh the AI cost line every minute
setInterval(() => getSettings().then((s) => s.token && showUsage()), 60000);
