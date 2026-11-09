import { getSettings } from './lib/config.js';

const $ = (s) => document.querySelector(s);
const PHASES = {
  idle: 'Idle', starting: 'Starting…', scanning: 'Scanning jobs', opening: 'Opening job', opening_apply: 'Opening application',
  generating: 'Writing resume', applying: 'Filling form', thinking: 'Reading page (AI)', waiting_human: 'Waiting for you', stopping: 'Stopping…'
};

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

function render(bot) {
  const b = bot || { running: false, phase: 'idle', counts: {}, log: [] };
  $('#dot').className = 'dot' + (b.running ? (b.phase === 'waiting_human' ? ' human' : ' on') : '');
  $('#phase').textContent = PHASES[b.phase] || b.phase;
  $('#startBtn').classList.toggle('hidden', b.running);
  $('#stopBtn').classList.toggle('hidden', !b.running);
  $('#singleBtn').disabled = b.running;
  for (const k of ['applied', 'attention', 'failed', 'skipped']) $('#c' + k[0].toUpperCase() + k.slice(1)).textContent = b.counts?.[k] || 0;
  const c = b.current;
  $('#current').classList.toggle('hidden', !c);
  if (c) {
    $('#curTitle').textContent = `${c.title || 'Job'}${c.company ? ` @ ${c.company}` : ''}`;
    $('#curMeta').textContent = [c.bidId && `Bid #${c.bidId}`, c.step && `step ${c.step}`].filter(Boolean).join(' · ');
  }
  const log = $('#log');
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
  log.innerHTML = (b.log || []).slice(-80).map((l) =>
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
$('#singleBtn').addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  send('runSingle', { tabId: tab.id });
});
$('#clearLog').addEventListener('click', () => send('clearLog'));
$('#openOptions').addEventListener('click', () => chrome.runtime.openOptionsPage());
$('#openOptions2').addEventListener('click', () => chrome.runtime.openOptionsPage());
$('#openJobright').addEventListener('click', async () => chrome.tabs.create({ url: (await getSettings()).listUrl }));

chrome.storage.onChanged.addListener((ch) => ch.bot && render(ch.bot.newValue));
chrome.storage.local.get('bot').then(({ bot }) => render(bot));
getSettings().then((s) => $('#signin').classList.toggle('hidden', !!s.token));
chrome.tabs.query({ active: true, currentWindow: true }).then(([tab]) => {
  $('#singleBtn').classList.toggle('hidden', !/jobright\.ai\/jobs\/info\//.test(tab?.url || ''));
});
