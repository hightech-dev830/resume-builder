// History page: every job the bot handled, with a status filter.
// Two sources, merged: the extension's own log (chrome.storage "history": every result, including
// skipped jobs that never got a bid) and the bot's bids saved in Resume Builder (older runs too).
import { rpc, SITES } from './lib/config.js';

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
const LABEL = { applied: 'Applied', attention: 'Need you', failed: 'Failed', skipped: 'Skipped', stopped: 'Stopped' };
// Resume Builder's bot_status -> this page's status
const FROM_SERVER = { applied: 'applied', needs_attention: 'attention', failed: 'failed', skipped: 'skipped', applying: 'attention' };
const siteLabel = (k) => SITES[k]?.label || k || '';
const safeUrl = (u) => (/^https?:\/\//i.test(u || '') ? u : '');

let all = [];
let note = '';

async function load() {
  const { history = [] } = await chrome.storage.local.get('history');
  const local = history.map((h) => ({ ...h, from: 'bot' }));
  const known = new Set(local.map((h) => h.bidId).filter(Boolean));
  let server = [];
  note = '';
  try {
    const rows = await rpc('bot:history', 2000);
    server = rows.filter((r) => !known.has(r.id) && FROM_SERVER[r.bot_status || ''] && r.source !== 'manual').map((r) => ({
      t: new Date(r.applied_at || r.created_at).getTime(),
      status: FROM_SERVER[r.bot_status],
      reason: r.bot_status === 'applying' ? 'unfinished: the bot stopped during this application' : '',
      site: r.source || '', bidId: r.id, title: r.role, company: r.company, location: r.location, salary: r.salary,
      postingUrl: r.source_url, applyUrl: r.apply_url || r.job_link, from: 'server'
    }));
  } catch (err) {
    note = /Unknown method/.test(err.message)
      ? 'Older bids from Resume Builder are not shown: restart the Resume Builder server (npm run dev) to load them.'
      : `Older bids from Resume Builder could not be loaded (${err.message}).`;
  }
  all = [...local, ...server].sort((a, b) => b.t - a.t);
  const sites = [...new Set(all.map((h) => h.site).filter(Boolean))];
  const cur = $('#site').value;
  $('#site').innerHTML = '<option value="">All sites</option>' + sites.map((k) => `<option value="${esc(k)}">${esc(siteLabel(k))}</option>`).join('');
  $('#site').value = sites.includes(cur) ? cur : '';
  render();
}

function render() {
  const status = $('#status').value;
  const site = $('#site').value;
  const q = $('#search').value.trim().toLowerCase();
  // date range, in your time zone; "To" includes that whole day
  const from = $('#from').value ? new Date(`${$('#from').value}T00:00:00`).getTime() : -Infinity;
  const to = $('#to').value ? new Date(`${$('#to').value}T00:00:00`).getTime() + 86400000 : Infinity;
  const inSite = all.filter((h) => (!site || h.site === site) && h.t >= from && h.t < to);
  const shown = inSite.filter((h) => (!status || h.status === status)
    && (!q || `${h.title} ${h.company} ${h.reason} ${h.bidId || ''}`.toLowerCase().includes(q)));

  const counts = Object.fromEntries(Object.keys(LABEL).map((k) => [k, inSite.filter((h) => h.status === k).length]));
  $('#summary').innerHTML = [['', 'All', inSite.length], ...Object.entries(LABEL).map(([k, l]) => [k, l, counts[k]])]
    .map(([k, l, n]) => `<span class="chip${status === k ? ' on' : ''}" data-status="${k}">${l}<b>${n}</b></span>`).join('');

  $('#rows').innerHTML = shown.slice(0, 2000).map((h) => {
    const posting = safeUrl(h.postingUrl);
    const apply = safeUrl(h.applyUrl);
    const when = new Date(h.t);
    return `<tr>
      <td class="time">${when.toLocaleDateString()} ${when.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</td>
      <td><span class="badge s-${esc(h.status)}">${esc(LABEL[h.status] || h.status)}</span></td>
      <td class="job">${posting ? `<a href="${esc(posting)}" target="_blank" rel="noopener">${esc(h.title || 'Job')}</a>` : esc(h.title || 'Job')}${h.location ? `<div class="src">${esc(h.location)}${h.salary ? ` · ${esc(h.salary)}` : ''}</div>` : ''}</td>
      <td>${esc(h.company)}</td>
      <td>${esc(siteLabel(h.site))}${h.from === 'server' ? '<div class="src">from Resume Builder</div>' : ''}</td>
      <td class="reason">${esc(h.reason)}</td>
      <td class="num">${h.bidId ? `#${esc(h.bidId)}` : ''}</td>
      <td class="links">${apply ? `<a href="${esc(apply)}" target="_blank" rel="noopener">Application</a>` : ''}${posting && h.site !== 'hiringcafe' ? `<a href="${esc(posting)}" target="_blank" rel="noopener">Job page</a>` : ''}</td>
    </tr>`;
  }).join('');
  $('#empty').classList.toggle('hidden', shown.length > 0);
  $('#empty').textContent = $('#from').value || $('#to').value ? 'No jobs with this status in these dates.' : 'No jobs with this status yet.';
  $('#note').textContent = [note, shown.length > 2000 ? `Showing the newest 2,000 of ${shown.length}.` : ''].filter(Boolean).join(' ');
}

function setStatus(st) {
  $('#status').value = st;
  render();
  try {
    const url = new URL(location.href);
    if (st) url.searchParams.set('status', st);
    else url.searchParams.delete('status');
    history.replaceState(null, '', url);
  } catch {}
}

$('#status').addEventListener('change', () => setStatus($('#status').value));
$('#site').addEventListener('change', render);
const ymd = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
$('#from').addEventListener('change', () => {
  if ($('#to').value && $('#to').value < $('#from').value) $('#to').value = $('#from').value;
  render();
});
$('#to').addEventListener('change', () => {
  if ($('#from').value && $('#to').value && $('#from').value > $('#to').value) $('#from').value = $('#to').value;
  render();
});
$('#today').addEventListener('click', () => {
  $('#from').value = $('#to').value = ymd(new Date());
  render();
});
$('#allDates').addEventListener('click', () => {
  $('#from').value = $('#to').value = '';
  render();
});
$('#search').addEventListener('input', render);
$('#refresh').addEventListener('click', load);
$('#summary').addEventListener('click', (e) => {
  const chip = e.target.closest('[data-status]');
  if (chip) setStatus(chip.dataset.status);
});
// the side panel opens this page with ?status=… (or sends it a new one while it is open)
chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === 'historyStatus') setStatus(msg.status || '');
});
// new results while the page is open
let reloadTimer = null;
chrome.storage.onChanged.addListener((ch) => {
  if (!ch.history) return;
  clearTimeout(reloadTimer);
  reloadTimer = setTimeout(load, 1500);
});

$('#status').value = LABEL[new URLSearchParams(location.search).get('status')] ? new URLSearchParams(location.search).get('status') : '';
load();
