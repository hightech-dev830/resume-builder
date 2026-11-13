import { getSettings, saveSettings, rpc, SITES, siteOf, listUrlOf } from './lib/config.js';

const $ = (s) => document.querySelector(s);
const PHASES = {
  idle: 'Idle', starting: 'Starting…', scanning: 'Scanning jobs', opening: 'Opening job', opening_apply: 'Opening application',
  generating: 'Writing resume', waiting_jobs: 'Waiting for new jobs', applying: 'Filling form', thinking: 'Reading page (AI)', waiting_human: 'Waiting for you', stopping: 'Stopping…'
};

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

function render(bot) {
  const b = bot || { running: false, phase: 'idle', counts: {}, log: [] };
  $('#dot').className = 'dot' + (b.running ? (b.phase === 'waiting_human' ? ' human' : ' on') : '');
  $('#phase').textContent = PHASES[b.phase] || b.phase;
  $('#startBtn').classList.toggle('hidden', b.running);
  $('#stopBtn').classList.toggle('hidden', !b.running);
  $('#singleBtn').disabled = b.running;
  $('#site').disabled = b.running; // the running bot keeps the board it started on
  for (const k of ['applied', 'attention', 'failed', 'skipped']) $('#c' + k[0].toUpperCase() + k.slice(1)).textContent = b.counts?.[k] || 0;
  const c = b.current;
  $('#current').classList.toggle('hidden', !c);
  $('#skipBtn').classList.toggle('hidden', !b.running || !c);
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

// "Apply again": carry on with the "need you" job open in the active tab (that one job only),
// after you fixed its page. While it runs, "Stop applying again" ends it.
let retrying = null; // tab id of the running / queued "Apply again"
async function showAgain() {
  const all = (await chrome.storage.session.get('attentionTabs')).attentionTabs || {};
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const here = tab && all[tab.id];
  const label = (e) => (e ? `· ${e.job?.role || 'job'}${e.job?.company ? ` @ ${e.job.company}` : ''}` : '');
  $('#stopAgainBtn').classList.toggle('hidden', retrying == null);
  $('#stopAgainInfo').textContent = retrying != null ? label(all[retrying]) : '';
  $('#againBtn').classList.toggle('hidden', !here || retrying != null);
  $('#againInfo').textContent = label(here);
}
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
    $('#msg').textContent = 'Queued: the bot applies again right after the current job.';
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
