// Auto Bid service worker. Two ways to find jobs, one way to apply:
//  - Jobright mode: walks the Jobright list (remote jobs posted today), one job at a time,
//    and confirms "Yes, I applied!" on Jobright afterwards.
//  - Job links mode: works through links you pasted, several jobs at once (one window each).
// Every application: read the posting, fill the form (rules first, the AI only for what is left),
// write the tailored resume + cover letter when a real form appears, submit. Bids go to Supabase.
import { getSettings, getProfile, seedProfile, profileForAI, secretsFor, rpc } from './lib/config.js';
import { rulePlan, answersToSave, isFilled, postingWithApply } from './lib/rules.js';
import { examplesForAI, learnFrom, mergeLearned, loadKB } from './lib/examples.js';

const JOBRIGHT = /^https:\/\/([a-z]+\.)?jobright\.ai\//;
const LINKEDIN = /^https:\/\/([a-z]+\.)?linkedin\.com\//i;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------- saved answers ----------------

// Answers the AI gave to reusable screening questions, reused on later applications without the AI.
async function savedAnswers() {
  const { answers } = await chrome.storage.local.get('answers');
  return answers || {};
}
let answersChain = Promise.resolve();
function rememberAnswers(add) {
  if (!Object.keys(add).length) return answersChain;
  // workers save at the same time: one write after another, never overwriting each other
  answersChain = answersChain.then(async () => {
    const all = await savedAnswers();
    let changed = false;
    for (const [k, v] of Object.entries(add)) {
      if (all[k]?.answer === v.answer) continue; // already saved: nothing to do
      all[k] = v;
      changed = true;
    }
    if (!changed) return;
    const keys = Object.keys(all);
    if (keys.length > 5000) keys.sort((a, b) => all[a].t - all[b].t).slice(0, keys.length - 5000).forEach((k) => delete all[k]);
    await chrome.storage.local.set({ answers: all });
  }).catch(() => {});
  return answersChain;
}

// New questions the AI answered (free text too), kept as examples for the next forms.
// Options > Jobs & behavior > Learned examples downloads them to merge into data/examples.json.
const hostOf = (u) => { try { return new URL(u).hostname; } catch { return ''; } };
function rememberLearned(add) {
  if (!add.length) return answersChain;
  answersChain = answersChain.then(async () => {
    const { learned } = await chrome.storage.local.get('learned');
    await chrome.storage.local.set({ learned: mergeLearned(learned || [], add) });
  }).catch(() => {});
  return answersChain;
}

// ---------------- state (mirrored to storage for the side panel) ----------------

const blank = () => ({
  running: false, mode: 'jobright', phase: 'idle', startedAt: null, workers: 1,
  jobs: [], // one entry per job being applied to: { w, title, company, bidId, step, url, phase }
  counts: { applied: 0, attention: 0, failed: 0, skipped: 0 },
  log: []
});
let state = blank();
let stopRequested = false;

let saveTimer = null;
function save() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => chrome.storage.local.set({ bot: state }), 150);
}

function setPhase(phase) {
  state.phase = phase;
  save();
  updateBadge();
}

function log(msg, level = 'info', J = null) {
  const line = J && state.workers > 1 ? `[${J.w + 1}] ${msg}` : msg;
  state.log.push({ t: Date.now(), msg: line, level });
  if (state.log.length > 400) state.log.splice(0, state.log.length - 400);
  console[level === 'error' ? 'warn' : 'log']('[auto-bid]', line);
  save();
  if (J?.bidId) rpc('bot:log', J.bidId, msg).catch(() => {});
}

function updateBadge() {
  chrome.action.setBadgeText({ text: state.running ? String(state.counts.applied) : '' });
  const human = state.jobs.some((j) => j.phase === 'waiting_human');
  chrome.action.setBadgeBackgroundColor({ color: human ? '#d97706' : '#16a34a' });
}

// ---------------- one job = one context ----------------

// Manual skip: pressing "Skip" rejects the job's promise, so the job ends at once,
// and every guarded await inside the abandoned work throws too.
class SkipError extends Error {}
function jobContext() {
  let reject;
  const skip = new Promise((_, rej) => { reject = rej; });
  skip.catch(() => {});
  const ctx = {
    skipped: false,
    guard: (p) => Promise.race([p, skip]),
    check: () => { if (ctx.skipped) throw new SkipError('skipped by you'); },
    cancel: () => { ctx.skipped = true; reject(new SkipError('skipped by you')); }
  };
  return ctx;
}

const active = new Map(); // worker number -> job
function syncJobs() {
  state.jobs = [...active.values()].map((J) => ({ ...J.cur, bidId: J.bidId }));
  save();
  updateBadge();
}

function newJob(w, cur) {
  const J = {
    w, ctx: jobContext(), tabs: [], bidId: null, outcome: 'failed', account: false,
    label: `${cur.title || 'job'} @ ${cur.company || '?'}`,
    cur: { w, step: 0, phase: 'opening', ...cur }
  };
  J.log = (msg, level) => log(msg, level, J);
  J.phase = (p) => { J.cur.phase = p; syncJobs(); };
  J.set = (patch) => { Object.assign(J.cur, patch); if (patch.title || patch.company) J.label = `${J.cur.title || 'job'} @ ${J.cur.company || '?'}`; syncJobs(); };
  active.set(w, J);
  syncJobs();
  return J;
}

function skipJob(w) {
  const J = w != null ? active.get(Number(w)) : [...active.values()][0];
  if (!state.running || !J || J.ctx.skipped) throw new Error('No job is being applied to right now.');
  J.log('Skip pressed; dropping this job');
  J.ctx.cancel();
}

// Runs one job: skip / fail handling, cost line, tab cleanup. `work(g)` sets J.outcome.
async function runJob(J, s, work) {
  const g = J.ctx.guard;
  try {
    const p = work(g);
    p.catch(() => {});
    await g(p);
  } catch (err) {
    if (J.outcome === 'applied') {
      // Skip pressed after the application went through: keep it applied
    } else if (err instanceof SkipError || J.ctx.skipped) {
      J.outcome = 'skipped';
      state.counts.skipped++;
      J.log(`⏭ Skipped ${J.label}: you pressed Skip`, 'warn');
      if (J.bidId) await rpc('bot:log', J.bidId, 'Skipped by you', { bot_status: 'skipped' }).catch(() => {});
    } else {
      J.outcome = 'failed';
      state.counts.failed++;
      J.log(`✗ ${J.label}: ${err.message}`, 'error');
      if (J.bidId) await rpc('bot:log', J.bidId, `Failed: ${err.message}`, { bot_status: 'failed' }).catch(() => {});
    }
  } finally {
    if (J.ctx.skipped) for (const t of J.tabs) chrome.notifications.clear(`ab-captcha-${t}`);
    if (J.bidId) {
      const cost = await rpc('usage:forBid', J.bidId).catch(() => null);
      if (cost?.calls) J.log(`  AI for this bid: ${cost.calls} calls, ${cost.tokens.toLocaleString()} tokens, ${cost.unpriced ? 'cost unknown for some models' : `$${cost.cost.toFixed(4)}`}`);
    }
    if (active.get(J.w) === J) active.delete(J.w);
    syncJobs();
    // close the job's tabs after a clean finish. "Need you" jobs keep everything (tabs, bid, files) for you.
    if ((J.outcome === 'applied' || J.outcome === 'skipped') && s.behavior.closeTabs) {
      for (const id of J.tabs) await chrome.tabs.remove(id).catch(() => {});
    }
  }
  return J.outcome;
}

// ---------------- notifications ----------------

// Notifications remember their tab, so clicking one brings that tab to the front.
async function notify(title, message, { id, tabId, sticky = false } = {}) {
  const nid = id || `ab-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
  if (tabId != null) {
    const { notifTabs = {} } = await chrome.storage.session.get('notifTabs');
    notifTabs[nid] = tabId;
    await chrome.storage.session.set({ notifTabs });
  }
  chrome.notifications.create(nid, {
    type: 'basic', iconUrl: 'icons/icon128.png', title,
    message: tabId != null ? `${message}\nClick to open the tab.` : message,
    priority: sticky ? 2 : 1, requireInteraction: sticky
  });
}

chrome.notifications.onClicked.addListener(async (nid) => {
  const { notifTabs = {} } = await chrome.storage.session.get('notifTabs');
  const tabId = notifTabs[nid];
  chrome.notifications.clear(nid);
  if (tabId == null) return;
  if (await tabExists(tabId)) {
    await focusTab(tabId);
    await call(tabId, '__abAgent', 'showCaptcha', [], { allFrames: true }).catch(() => {});
  }
});

// Keeps the service worker alive during long AI calls.
let keepAlive = null;
function startKeepAlive() {
  if (!keepAlive) keepAlive = setInterval(() => chrome.runtime.getPlatformInfo(), 20000);
  chrome.alarms.create('ab-keepalive', { periodInMinutes: 0.5 });
}
function stopKeepAlive() {
  clearInterval(keepAlive);
  keepAlive = null;
  chrome.alarms.clear('ab-keepalive');
}
chrome.alarms.onAlarm.addListener(() => {});

// ---------------- tabs ----------------

const createdTabs = []; // { id, opener, t }
chrome.tabs.onCreated.addListener((tab) => {
  createdTabs.push({ id: tab.id, opener: tab.openerTabId, win: tab.windowId, t: Date.now() });
  if (createdTabs.length > 100) createdTabs.shift();
});
const ownTabs = new Set(); // tabs the bot opened itself (never mistaken for a tab a page opened)

// The tab a click in `openerId` opened. Sites that open links with "noopener" leave no opener on the
// new tab, so a tab without one in the same window right after the click counts too.
async function waitNewTab(openerId, since, timeout) {
  const end = Date.now() + timeout;
  const win = (await tabExists(openerId))?.windowId;
  while (Date.now() < end) {
    const hit = createdTabs.find((c) => c.t >= since && !ownTabs.has(c.id) && c.id !== openerId &&
      (c.opener === openerId || (c.opener == null && c.win === win)));
    if (hit) return hit.id;
    await sleep(300);
  }
  return null;
}

function waitComplete(tabId, timeout = 45000) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (ok) => {
      if (done) return;
      done = true;
      chrome.tabs.onUpdated.removeListener(onUpd);
      clearTimeout(timer);
      resolve(ok);
    };
    const onUpd = (id, info) => id === tabId && info.status === 'complete' && finish(true);
    chrome.tabs.onUpdated.addListener(onUpd);
    const timer = setTimeout(() => finish(false), timeout);
    chrome.tabs.get(tabId).then((t) => t.status === 'complete' && finish(true)).catch(() => finish(false));
  });
}

async function tabExists(tabId) {
  try {
    return await chrome.tabs.get(tabId);
  } catch {
    return null;
  }
}

async function inject(tabId, file, allFrames = false) {
  try {
    await chrome.scripting.executeScript({ target: { tabId, allFrames }, files: [file] });
  } catch (err) {
    if (!allFrames) throw err;
  }
}

// Runs window[ns][method](...args) in the page; returns the top-frame result or all frames' results.
async function call(tabId, ns, method, args = [], { allFrames = false, frameId } = {}) {
  const target = frameId != null ? { tabId, frameIds: [frameId] } : { tabId, allFrames };
  const res = await chrome.scripting.executeScript({
    target,
    func: (n, m, a) => (window[n] && window[n][m] ? window[n][m](...a) : null),
    args: [ns, method, args]
  });
  return allFrames ? res : res[0]?.result;
}

async function jobright(tabId, method, ...args) {
  await inject(tabId, 'content/jobright.js');
  return call(tabId, '__abJobright', method, args);
}

async function settle(tabId) {
  await waitComplete(tabId, 45000);
  await sleep(600);
  await inject(tabId, 'content/agent.js', true);
  await call(tabId, '__abAgent', 'waitStable', [700, 8000]).catch(() => {});
}

async function focusTab(tabId, attention = false) {
  const tab = await tabExists(tabId);
  if (!tab) return;
  await chrome.tabs.update(tabId, { active: true });
  const win = await chrome.windows.get(tab.windowId).catch(() => null);
  await chrome.windows.update(tab.windowId, { focused: true, ...(win?.state === 'minimized' ? { state: 'normal' } : {}), ...(attention ? { drawAttention: true } : {}) }).catch(() => {});
}

// Makes a tab the visible one in its window without stealing focus from you (several workers run at once).
async function showTab(tabId) {
  await chrome.tabs.update(tabId, { active: true }).catch(() => {});
}

// ---------------- helpers ----------------

const words = (csv) => String(csv || '').split(',').map((w) => w.trim().toLowerCase()).filter(Boolean);

function filterReason(job, s) {
  const f = s.filters;
  if (f.remoteOnly && !job.remote) return 'not remote';
  if (f.maxAgeHours && job.ageHours != null && job.ageHours >= f.maxAgeHours) return `posted ${job.posted}`;
  if (f.minMatch && job.match && job.match < f.minMatch) return `match ${job.match}%`;
  const t = (job.title || '').toLowerCase();
  const bad = words(f.titleExclude).find((w) => t.includes(w));
  if (bad) return `title has "${bad}"`;
  if (words(f.companyExclude).includes((job.company || '').toLowerCase())) return 'excluded company';
  return '';
}

async function processed() {
  const { done } = await chrome.storage.local.get('done');
  return done || {};
}

async function markProcessed(id, status) {
  const done = await processed();
  done[id] = { status, t: Date.now() };
  await chrome.storage.local.set({ done });
}

// Adds what the server decides: the template (extension's choice, else the web app's default)
// and the folder where bid files are saved.
async function withTemplate(s) {
  const server = await rpc('settings:get');
  return { ...s, filesDir: server.local_output_dir || '', behavior: { ...s.behavior, template: s.behavior.template || server.default_template || 'kevin' } };
}

// ---------------- start / stop ----------------

async function start(mode = 'jobright') {
  if (state.running) return;
  const s = await getSettings();
  if (!s.token) throw new Error('Sign in to the Resume Builder server first (extension settings).');
  if (mode === 'links' && !linkQueue.some((l) => l.status === 'pending')) throw new Error('Add some job links first.');
  state = { ...blank(), mode, running: true, startedAt: Date.now(), log: state.log.slice(-50) };
  stopRequested = false;
  startKeepAlive();
  log(mode === 'links' ? 'Bot started on your job links' : 'Bot started on Jobright');
  setPhase('starting');
  (mode === 'links' ? runLinks() : runLoop()).catch((err) => {
    log('Bot stopped: ' + err.message, 'error');
    notify('Auto Bid stopped', err.message);
  }).finally(finish);
}

function stop() {
  if (!state.running) return;
  stopRequested = true;
  log('Stop requested; finishing the current step…');
  setPhase('stopping');
}

function finish() {
  state.running = false;
  stopKeepAlive();
  active.clear();
  state.jobs = [];
  setPhase('idle');
  const c = state.counts;
  log(`Bot finished: ${c.applied} applied, ${c.attention} need attention, ${c.failed} failed, ${c.skipped} skipped`);
  updateBadge();
}

// ---------------- Jobright mode ----------------

async function listTab(s) {
  const tabs = await chrome.tabs.query({ url: 'https://jobright.ai/jobs/*' });
  const list = tabs.find((t) => /\/jobs\/(recommend|search|list)/.test(t.url));
  if (list) return list.id;
  const tab = await chrome.tabs.create({ url: s.listUrl, active: true });
  await waitComplete(tab.id);
  await sleep(2500);
  return tab.id;
}

async function runLoop() {
  const s = await withTemplate(await getSettings());
  const listId = await listTab(s);
  const tried = new Set();
  let emptyRounds = 0;
  const refreshMs = Math.max(1, Number(s.filters.refreshMin) || 5) * 60000;
  let lastRefresh = Date.now();

  const refreshList = async () => {
    log('Refreshing the Jobright job list');
    await chrome.tabs.reload(listId);
    await waitComplete(listId);
    await sleep(3000);
    lastRefresh = Date.now();
    emptyRounds = 0;
  };

  while (!stopRequested && state.counts.applied < s.filters.maxBids) {
    setPhase('scanning');
    if (!(await tabExists(listId))) throw new Error('The Jobright list tab was closed.');
    if (Date.now() - lastRefresh >= refreshMs) await refreshList();
    let cards = (await jobright(listId, 'scrapeList')) || [];
    if (!cards.length) {
      await sleep(3000);
      cards = (await jobright(listId, 'scrapeList')) || [];
    }
    const done = await processed();
    const fresh = cards.filter((c) => !tried.has(c.id || c.title + c.company) && !(c.id && done[c.id]));
    const seen = fresh.length ? await rpc('bot:seen', fresh.map((c) => c.url).filter(Boolean)).catch(() => []) : [];
    const seenUrls = new Set(seen.map((r) => r.source_url));

    let next = null;
    for (const c of fresh) {
      tried.add(c.id || c.title + c.company);
      if (seenUrls.has(c.url)) continue;
      const why = filterReason(c, s);
      if (why) {
        if (c.id) await markProcessed(c.id, 'skipped');
        continue;
      }
      next = c;
      break;
    }

    if (!next) {
      // nothing usable on screen: load more, give up after a few empty scrolls
      const before = cards.length;
      const after = await jobright(listId, 'loadMore');
      emptyRounds = after > before ? 0 : emptyRounds + 1;
      if (emptyRounds >= 3) {
        // wait for the next refresh instead of stopping; new jobs keep arriving
        const at = new Date(lastRefresh + refreshMs);
        log(`No new matching jobs right now; checking again at ${at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`);
        setPhase('waiting_jobs');
        while (!stopRequested && Date.now() < at.getTime()) await sleep(3000);
        if (stopRequested) break;
        await refreshList();
      }
      continue;
    }

    setPhase('running');
    await processJobright(next, { listId, settings: s });
    if (!stopRequested) await sleep(s.behavior.delaySec * 1000);
  }
  if (state.counts.applied >= s.filters.maxBids) log(`Reached the limit of ${s.filters.maxBids} bids for this run.`);
}

async function processJobright(card, { listId, settings: s, detailTabId }) {
  const J = newJob(0, { title: card.title, company: card.company, url: card.url });
  J.log(`Opening ${J.label}`);
  let detailId = detailTabId;

  await runJob(J, s, async (g) => {
    // 1. job page on Jobright
    if (!detailId) {
      if (card.url) {
        detailId = (await chrome.tabs.create({ url: card.url, active: true })).id;
      } else {
        const t0 = Date.now();
        await jobright(listId, 'openCard', card.index);
        detailId = await waitNewTab(listId, t0, 10000);
        if (!detailId) throw new Error('Could not open the job card.');
      }
      J.tabs.push(detailId);
    }
    await g(waitComplete(detailId));
    await g(sleep(2000));
    const detail = { ...card, ...((await g(jobright(detailId, 'scrapeDetail'))) || {}) };
    detail.company = detail.company || card.company;
    detail.title = detail.title || card.title;
    if (s.filters.remoteOnly && !detail.remote && !card.remote) {
      J.log(`Skipped ${J.label}: not remote`);
      state.counts.skipped++;
      J.outcome = 'skipped';
      return;
    }
    J.set({ url: detail.url });

    // 2. company application page
    J.phase('opening_apply');
    let applyId = null;
    const t0 = Date.now();
    if (await g(jobright(detailId, 'clickApply'))) applyId = await g(waitNewTab(detailId, t0, 15000));
    if (!applyId) {
      const cur = await tabExists(detailId);
      if (cur && !JOBRIGHT.test(cur.url)) applyId = detailId; // same-tab redirect
    }
    if (!applyId && detail.applyUrl) applyId = (await chrome.tabs.create({ url: detail.applyUrl, active: true })).id;
    if (!applyId) throw new Error('Could not open the company application page.');
    if (applyId !== detailId) J.tabs.push(applyId);
    // wait out Jobright's redirect to the real posting
    for (let i = 0; i < 30; i++) {
      const t = await tabExists(applyId);
      if (t && t.url && !JOBRIGHT.test(t.url) && !/^about:|^chrome/.test(t.url)) break;
      await g(sleep(700));
    }
    await focusTab(applyId);

    const status = await applyFlow(J, g, {
      applyId, detail, s, source: 'jobright', sourceUrl: detail.url,
      notes: `Auto-bid from Jobright${detail.match ? ` (match ${detail.match}%)` : ''}. Posted ${detail.posted || '?'}.`
    });

    // 3. tell Jobright
    if (status === 'applied' && (await tabExists(detailId))) {
      if (applyId === detailId) {
        await chrome.tabs.update(detailId, { url: detail.url });
        await waitComplete(detailId);
        await sleep(1500);
      }
      await focusTab(detailId);
      const yes = await jobright(detailId, 'confirmApplied', 12000);
      J.log(yes ? 'Marked as applied on Jobright' : 'Jobright "Did you apply?" dialog did not appear');
    }
  });

  if (card.id) await markProcessed(card.id, J.outcome);
  if (listId && (await tabExists(listId)) && J.outcome !== 'attention') await chrome.tabs.update(listId, { active: true }).catch(() => {});
}

// Runs the full bid on a Jobright job page the user has open.
async function runSingle(tabId) {
  if (state.running) throw new Error('The bot is already running.');
  const tab = await chrome.tabs.get(tabId);
  if (!/jobright\.ai\/jobs\/info\//.test(tab.url)) throw new Error('Open a Jobright job page first.');
  let s = await getSettings();
  if (!s.token) throw new Error('Sign in to the Resume Builder server first (extension settings).');
  s = await withTemplate(s);
  state = { ...blank(), running: true, startedAt: Date.now(), log: state.log.slice(-50) };
  stopRequested = false;
  startKeepAlive();
  const id = (/([0-9a-f]{24})/.exec(tab.url) || [])[1] || '';
  setPhase('running');
  processJobright({ id, url: tab.url.split('?')[0], title: tab.title }, { settings: { ...s, filters: { ...s.filters, remoteOnly: false } }, detailTabId: tabId })
    .finally(finish);
}

// ---------------- Job links mode ----------------

// The queue lives in memory while the worker runs and is mirrored to storage for the side panel.
let linkQueue = []; // { url, status: pending|running|applied|attention|skipped|failed|stopped, title, company, bidId, note, t }
let linksTimer = null;
function saveLinks(now = false) {
  clearTimeout(linksTimer);
  if (now) return chrome.storage.local.set({ links: linkQueue });
  linksTimer = setTimeout(() => chrome.storage.local.set({ links: linkQueue }), 200);
}
// The saved queue, read when the service worker wakes up. "Add links" can be what wakes it, so adding
// waits for this first; otherwise the saved list would replace the links just added.
let linksReady = null;
const restoreLinks = () => (linksReady ||= chrome.storage.local.get('links').then(({ links }) => {
  linkQueue = (links || []).map((l) => (l.status === 'running' ? { ...l, status: 'pending' } : l));
}));

const normUrl = (u) => {
  try {
    const x = new URL(u.trim());
    x.hash = '';
    // the link is opened exactly as you pasted it (some boards, e.g. Himalayas, use ?utm_source=api)
    return x.toString();
  } catch {
    return '';
  }
};

// Every link is added, also one that is already in the list or was applied to before (repeats are allowed).
async function addLinks(text) {
  await restoreLinks();
  const have = new Set(linkQueue.map((l) => l.url));
  // one link per match, also when links are glued together or separated by commas / semicolons
  const found = String(text || '').match(/https?:\/\/.+?(?=https?:\/\/|[\s"'<>,;|]|$)/g) || [];
  let added = 0;
  let dupes = 0;
  for (const raw of found) {
    const url = normUrl(raw.replace(/[),.;]+$/, ''));
    if (!url) continue;
    if (have.has(url)) dupes++;
    have.add(url);
    linkQueue.push({ url, status: 'pending', t: Date.now() });
    added++;
  }
  await saveLinks(true); // straight away, so the list shows them at once
  return { added, dupes, total: linkQueue.length };
}

async function clearLinks(which) {
  await restoreLinks();
  if (which === 'all') linkQueue = linkQueue.filter((l) => l.status === 'running');
  else if (which === 'retry') linkQueue.forEach((l) => { if (['failed', 'stopped'].includes(l.status)) l.status = 'pending'; });
  else linkQueue = linkQueue.filter((l) => l.status === 'pending' || l.status === 'running');
  await saveLinks(true);
  return { total: linkQueue.length };
}

// Same loop as Jobright mode, except the next job comes from your link list instead of the Jobright board.
async function runLinks() {
  await restoreLinks();
  const s = await withTemplate(await getSettings());
  linkQueue.forEach((l) => { if (l.status === 'running') l.status = 'pending'; });
  saveLinks();
  setPhase('running');
  log(`${linkQueue.filter((l) => l.status === 'pending').length} links to apply to`);
  while (!stopRequested) {
    const item = linkQueue.find((l) => l.status === 'pending');
    if (!item) break;
    item.status = 'running';
    saveLinks();
    await processLink(item, s);
    saveLinks();
    if (!stopRequested) await sleep((Number(s.behavior.linkDelaySec) || 0) * 1000);
  }
  if (!stopRequested) {
    const c = state.counts;
    notify('Job links done', `${c.applied} applied, ${c.attention} need you, ${c.skipped} skipped, ${c.failed} failed`);
  }
}

// Same as processJobright: 1. open the job link in a new tab and read the job,
// 2. click Apply -> the company's application (new tab), 3. the application.
async function processLink(item, s) {
  const J = newJob(0, { title: item.title || new URL(item.url).hostname, company: item.company || '', url: item.url });
  J.log(`Opening ${item.url}`);
  await runJob(J, s, async (g) => {
    // a link applied to before is applied to again (repeats are allowed); just say so in the log
    const seen = await g(rpc('bot:seen', [item.url]).catch(() => []));
    const prior = seen.find((r) => r.bot_status === 'applied');
    if (prior) J.log(`  Applying again (bid #${prior.id} for this link is already applied)`);

    // 1. job page
    const detailId = (await chrome.tabs.create({ url: item.url, active: true })).id;
    ownTabs.add(detailId);
    J.tabs.push(detailId);
    await g(settle(detailId));
    await inject(detailId, 'content/boards.js');
    let detail = (await g(call(detailId, '__abBoards', 'scrapeDetail').catch(() => null))) || {};
    // still on the job page? (a link on the page may have taken the tab elsewhere): go back to the link once
    const pathOf = (u) => { try { const x = new URL(u); return x.hostname.replace(/^www\./, '') + x.pathname.replace(/\/+$/, ''); } catch { return ''; } };
    const sameUrl = (a, b) => pathOf(a) === pathOf(b);
    const onPage = await tabExists(detailId);
    if (onPage && !sameUrl(onPage.url, item.url)) {
      J.log(`  Left the job page (${onPage.url.slice(0, 80)}); opening the link again`, 'warn');
      await chrome.tabs.update(detailId, { url: item.url });
      await g(settle(detailId));
      await inject(detailId, 'content/boards.js');
      detail = (await g(call(detailId, '__abBoards', 'scrapeDetail').catch(() => null))) || {};
    }
    const info = (await g(call(detailId, '__abAgent', 'jobInfo').catch(() => null))) || {};
    const host = new URL(item.url).hostname.replace(/^www\./, '');
    detail.title = detail.title || info.title || 'Job';
    detail.company = detail.company || info.company || host;
    detail.location = detail.location || info.location || '';
    detail.salary = detail.salary || info.salary || '';
    detail.remote = detail.remote || !!info.remote;
    detail.url = item.url;
    detail.postingUrl = item.url;
    J.set({ title: detail.title, company: detail.company });
    item.title = detail.title;
    item.company = detail.company;
    J.log(`Job: ${J.label} (description ${(detail.jd || '').length.toLocaleString()} characters)`);

    // 2. company application page: click Apply once (twice only when a newsletter pop-up was in the way).
    // The job page is never opened again and its Apply is never clicked by the application steps.
    J.phase('opening_apply');
    const skip = (reason) => {
      J.log(`Skipped ${J.label}: ${reason}`);
      state.counts.skipped++;
      J.outcome = 'skipped';
      J.note = reason;
    };
    let applyId = null;
    if (LINKEDIN.test(item.url) || detail.formHere) {
      applyId = detailId; // LinkedIn is skipped next; a link that is the form itself is filled right here
    } else {
      await call(detailId, '__abAgent', 'acceptCookies').catch(() => {});
      let noButton = false;
      for (let attempt = 1; attempt <= 2 && !applyId; attempt++) {
        const t0 = Date.now();
        const before = (await tabExists(detailId))?.url || item.url;
        // the board's own sign-up / login (Himalayas when you are logged out): never sign up on a job board,
        // so this is checked before clicking (a company's own account page is fine: the application may need one)
        const boardHost = new URL(item.url).hostname;
        const info = await call(detailId, '__abBoards', 'applyInfo').catch(() => null);
        const toBoardAccount = (h) => { try { const u = new URL(h); return u.hostname === boardHost && /sign-?up|sign-?in|register|log-?in/i.test(u.pathname); } catch { return false; } };
        if (info && JOB_BOARDS.test(boardHost) && toBoardAccount(info.href)) {
          return skip(`${boardHost.replace(/^www\./, '')} wants you signed in before Apply: sign in to it in this browser once, then retry`);
        }
        // Buttons such as Himalayas' "Apply now" open the company page with window.open(). A scripted click
        // has no user gesture, so Chrome blocks that pop-up: record the address the page tried to open instead.
        await chrome.scripting.executeScript({
          target: { tabId: detailId }, world: 'MAIN',
          func: () => {
            if (window.__abOpenHook) return;
            window.__abOpenHook = true;
            const orig = window.open;
            window.open = function (url, ...rest) {
              try { if (url) document.documentElement.dataset.abOpened = new URL(url, location.href).href; } catch {}
              return orig.call(this, url, ...rest);
            };
          }
        }).catch(() => {});
        // the click may navigate the page away mid-call: then the answer never comes and the click still counts
        const clicked = await g(Promise.race([
          call(detailId, '__abBoards', 'clickApply').catch(() => null),
          sleep(4000).then(() => ({ clicked: true, text: info?.text || 'Apply', href: info?.href || '' }))
        ]));
        if (!clicked?.clicked) { noButton = attempt === 1; break; }
        J.log(`  Clicked "${clicked.text}"`);
        // a window.open the page asked for: if no tab shows up in 3 s, Chrome blocked it and the address is opened below
        const r = await g(afterApplyClick(detailId, t0, before, clicked.opened ? 3000 : 10000));
        if (r.tab) {
          applyId = r.tab;
        } else if (r.popup?.newsletter && attempt === 1) {
          // e.g. DevITjobs: "Get a weekly email…": close it (never fill it in) and press Apply again
          J.log(`  Closed a pop-up that is not the application ("${r.popup.text.slice(0, 60)}…")`);
          await call(detailId, '__abBoards', 'closePopup').catch(() => {});
        } else if ((r.popup?.open && !r.popup.newsletter) || r.fields >= 3) {
          applyId = detailId; // the application (form or start dialog) opened on this page
        } else if (clicked.href && /^https?:/.test(clicked.href) && clicked.href.split('#')[0] !== before.split('#')[0]) {
          applyId = (await chrome.tabs.create({ url: clicked.href, active: true })).id; // the link, opened by hand
          ownTabs.add(applyId);
        }
      }
      if (!applyId) return skip(noButton ? 'no Apply button on the job page' : 'the Apply button did not open the application');
    }
    if (applyId !== detailId) J.tabs.push(applyId);
    // wait out redirects to the real application page
    for (let i = 0; i < 20; i++) {
      const t = await tabExists(applyId);
      if (t?.url && !/^about:|^chrome/.test(t.url)) break;
      await g(sleep(500));
    }
    await focusTab(applyId);

    // 3. the application (same as Jobright mode)
    await applyFlow(J, g, { applyId, detail, s, source: 'links', sourceUrl: item.url, notes: `Auto-bid from a job link (${host}).` });
  });
  item.status = J.outcome;
  item.bidId = J.bidId;
  item.note = J.note || '';
  item.t = Date.now();
}

// Job boards (they list other companies' jobs): no account is ever made on these.
const JOB_BOARDS = /(^|\.)(himalayas\.app|jobicy\.com|devitjobs\.(com|us)|remoteok\.(com|io)|weworkremotely\.com|remotive\.(com|io)|wellfound\.com|builtin\.com|dice\.com|ziprecruiter\.com|simplyhired\.com|monster\.com|careerbuilder\.com|glassdoor\.com|workingnomads\.com|remote\.co|jobspresso\.co|nodesk\.co|4dayweek\.io|otta\.com|welcometothejungle\.com)$/i;

// What an Apply click on the job page did: opened a new tab, moved this tab to another page,
// or showed a pop-up / form on the page. Waits up to 10 s.
async function afterApplyClick(tabId, since, before, wait = 10000) {
  const end = Date.now() + wait;
  let popup = null;
  while (Date.now() < end) {
    const tab = await waitNewTab(tabId, since, 400);
    if (tab) return { tab };
    const now = await tabExists(tabId);
    if (!now) return {};
    const url = now.pendingUrl || now.url || '';
    if (url && url.split('#')[0] !== before.split('#')[0]) return { tab: tabId }; // went on in this tab
    if (Date.now() - since > 2000) {
      popup = await call(tabId, '__abBoards', 'popup').catch(() => null);
      // a pop-up came up instead of a new page
      if (popup?.open) return { popup };
    }
  }
  const fields = await call(tabId, '__abBoards', 'fieldCount').catch(() => 0);
  return { popup, fields };
}

// ---------------- the application, shared by both modes ----------------

// Reads the posting, fills and submits the application. Sets J.outcome; returns it.
async function applyFlow(J, g, { applyId, detail, s, source, sourceUrl, notes }) {
  await g(settle(applyId));
  const applyTab = await tabExists(applyId);
  // LinkedIn applications (Easy Apply / sign in with LinkedIn) are skipped
  if (LINKEDIN.test(applyTab.url)) {
    J.log(`Skipped ${J.label}: the application is on LinkedIn`);
    state.counts.skipped++;
    J.outcome = 'skipped';
    return J.outcome;
  }
  J.log(`Application page: ${new URL(applyTab.url).host}`);

  // exact job description from the company page
  const page = await g(call(applyId, '__abAgent', 'pageText').catch(() => null));
  const job = {
    company: detail.company, role: detail.title, location: detail.location || 'Remote (US)', salary: detail.salary || '',
    job_link: detail.postingUrl || applyTab.url, // the job board posting when there is one
    jd: [detail.jd, page && page.text.length > 300 ? `\n\n--- From the company posting (${new URL(page.url).host}) ---\n${page.text}` : ''].join('').slice(0, 30000)
  };

  // bid row in Supabase; resume + cover letter only once a form needs them
  const docs = { bid: null, files: null, resume: null, coverLetter: '' };
  const ensureBid = async () => {
    if (docs.bid) return docs.bid;
    J.ctx.check();
    docs.bid = await g(rpc('resumes:create', {
      company: detail.company, role: detail.title, job_link: job.job_link, apply_url: job.apply_url || job.job_link,
      location: detail.location || (detail.remote ? 'Remote (US)' : ''), salary: detail.salary || '', jd: job.jd,
      status: 'draft', template: s.behavior.template, notes, source, source_url: sourceUrl, bot_status: 'applying'
    }));
    J.bidId = docs.bid.id;
    syncJobs();
    J.log(`Bid #${J.bidId} created for ${J.label}`);
    return docs.bid;
  };
  const ensureDocs = async () => {
    if (docs.files) return false;
    let bid = await ensureBid();
    J.phase('generating');
    const jobForAi = { ...job, bid_id: bid.id };
    J.log('Tailoring the resume…');
    const tailored = await g(rpc('ai:tailor', jobForAi));
    J.log('Writing the cover letter…');
    const letter = await g(rpc('ai:coverLetter', { resume: tailored.resume, job: jobForAi }));
    bid = await g(rpc('resumes:update', bid.id, { resume_json: tailored.resume, cover_letter: letter, ...tailored.meta }));
    bid = await g(rpc('resumes:exportPdf', bid.id));
    bid = await g(rpc('resumes:exportCover', bid.id));
    docs.bid = bid;
    const [resumePdf, coverPdf] = await g(Promise.all([rpc('files:get', bid.pdf_path), rpc('files:get', bid.cover_letter_path)]));
    docs.files = {
      resume: { b64: resumePdf.__b64, name: bid.pdf_path.split('/').pop() },
      cover: s.behavior.uploadCover ? { b64: coverPdf.__b64, name: bid.cover_letter_path.split('/').pop() } : null
    };
    docs.resume = tailored.resume;
    docs.coverLetter = letter;
    J.log(`Resume and cover letter ready (${tailored.keywords?.length || 0} JD keywords matched)${s.filesDir ? `, saved in ${s.filesDir}/${bid.resume_dir}` : ''}`);
    J.phase('applying');
    return true;
  };

  J.phase('applying');
  const result = await g(runApplication(J, applyId, { job, docs, ensureDocs, s }));
  J.ctx.check();
  if (result.status === 'skipped') {
    J.log(`Skipped ${J.label}: ${result.reason}`);
    state.counts.skipped++;
    J.outcome = 'skipped';
    J.note = result.reason;
    if (J.bidId) await rpc('bot:log', J.bidId, `Skipped: ${result.reason}`, { bot_status: 'skipped' }).catch(() => {});
    return J.outcome;
  }
  await ensureBid();
  await rpc('resumes:update', J.bidId, { answers: result.answers }).catch(() => {});

  if (result.status === 'applied') {
    J.outcome = 'applied';
    state.counts.applied++;
    await rpc('resumes:update', J.bidId, { status: 'applied', bot_status: 'applied', applied_at: new Date().toISOString() });
    J.log(`✓ Applied: ${J.label}`, 'ok');
    if (state.mode !== 'links') notify('Application submitted', `${detail.title} at ${detail.company} (bid #${J.bidId})`);
  } else if (result.status === 'stopped') {
    J.outcome = 'stopped';
    await rpc('bot:log', J.bidId, 'Stopped by user', { bot_status: 'needs_attention' });
  } else {
    // left exactly as it is (tabs, bid, resume files) for you to finish by hand
    J.outcome = 'attention';
    J.note = result.reason;
    state.counts.attention++;
    await rpc('bot:log', J.bidId, `Needs attention: ${result.reason}`, { bot_status: 'needs_attention' });
    J.log(`⚠ ${J.label}: ${result.reason}. Tab left open for you.`, 'warn');
    await notify('Application needs you', `${detail.title} at ${detail.company}: ${result.reason}`, { tabId: J.tabs[J.tabs.length - 1], sticky: true });
  }
  return J.outcome;
}

// ---------------- application agent ----------------

async function snapshotTab(tabId) {
  await inject(tabId, 'content/agent.js', true);
  const frames = await call(tabId, '__abAgent', 'snapshot', [], { allFrames: true });
  // top page plus any real-sized frame with content (forms often live in an iframe, and so do their thank-you pages)
  const parts = frames.filter((f) => f.result && (f.result.top || (f.result.area > 20000 && (f.result.fields.length || f.result.buttons.length || f.result.text.length > 30))));
  const merged = { url: '', title: '', fields: [], buttons: [], errors: [], text: '', captcha: '', success: false, already: false };
  for (const { frameId, result: r } of parts) {
    const pre = `${frameId}:`;
    if (r.top) {
      merged.url = r.url;
      merged.title = r.title;
    }
    merged.fields.push(...r.fields.map((f) => ({ ...f, id: pre + f.id })));
    merged.buttons.push(...r.buttons.map((b) => ({ ...b, id: pre + b.id })));
    merged.errors.push(...r.errors);
    merged.text += (r.top ? '' : `\n[frame ${new URL(r.url).host}]\n`) + r.text;
    merged.captcha = merged.captcha || r.captcha;
    merged.loading = merged.loading || r.loading;
    merged.steps = merged.steps || r.steps;
    merged.success = merged.success || r.success;
    merged.already = merged.already || r.already;
  }
  merged.text = merged.text.slice(0, 12000);
  return merged;
}

function splitId(id) {
  const i = String(id).indexOf(':');
  return { frameId: Number(String(id).slice(0, i)), local: String(id).slice(i + 1) };
}

async function acceptCookies(J, tabId) {
  const res = await call(tabId, '__abAgent', 'acceptCookies', [], { allFrames: true }).catch(() => []);
  const hit = res.find((r) => r.result);
  if (hit) {
    J.log(`Accepted cookie banner ("${hit.result}")`);
    await sleep(1200);
  }
  return !!hit;
}

// Captchas and bot checks are for you to solve: the bot brings the tab forward, notifies you,
// and carries on by itself once it is solved (other workers keep going meanwhile).
async function waitForCaptcha(J, tabId, s, what) {
  J.phase('waiting_human');
  await focusTab(tabId, true);
  await call(tabId, '__abAgent', 'showCaptcha', [], { allFrames: true }).catch(() => {});
  await notify('Captcha: your turn', `${J.label}: solve the ${what}. The bot continues by itself when it is done.`, { id: `ab-captcha-${tabId}`, tabId, sticky: true });
  J.log(`Waiting up to ${s.behavior.captchaWaitMin} min for you to solve the ${what}…`, 'warn');
  const end = Date.now() + s.behavior.captchaWaitMin * 60000;
  while (Date.now() < end && !stopRequested && !J.ctx.skipped) {
    await sleep(3000);
    const res = await call(tabId, '__abAgent', 'captchaSolved', [], { allFrames: true }).catch(() => []);
    if (res.length && res.every((r) => r.result !== false)) {
      J.log('Captcha solved, continuing');
      chrome.notifications.clear(`ab-captcha-${tabId}`);
      J.phase('applying');
      return true;
    }
  }
  J.phase('applying');
  return false;
}

// ---------------- email verification (Gmail) ----------------

const CODE_FIELD = /verification code|security code|one[- ]?time|passcode|\botp\b|confirmation code|access code|enter (the )?(\d[- ]digit )?code|^code\b|6[- ]digit/i;
const CHECK_EMAIL = /(verify|confirm|activate) your (email|account|e-mail)|check your (email|inbox|e-mail)|we('ve| have)? (just )?(sent|emailed) (you )?(an? )?(email|link|code|verification)|verification (email|link) (has been|was) sent|click the link (in|we sent)/i;
const MAIL_HINT = /verif|code|confirm|activat|one[- ]time|passcode|security|sign.?in|log.?in|welcome|account|password|application/i;

let gmailLock = Promise.resolve();
const usedMails = new Set();

// Finds the verification email in your open Gmail tab (newest one or two messages) and returns its code or link.
async function findVerification(J, s, { company, host }) {
  const run = gmailLock.then(() => readVerification(J, s, { company, host }));
  gmailLock = run.catch(() => {});
  return run;
}

async function readVerification(J, s, { company, host }) {
  const [gmail] = await chrome.tabs.query({ url: 'https://mail.google.com/*' });
  if (!gmail) {
    J.log('  This site sent a verification email. Open Gmail in a tab so the bot can read it.', 'warn');
    return null;
  }
  const words = `${company} ${host.split('.').slice(-2, -1)[0] || ''}`.toLowerCase().split(/[^a-z0-9]+/).filter((x) => x.length > 2);
  const end = Date.now() + (Number(s.behavior.emailWaitSec) || 120) * 1000;
  J.phase('waiting_email');
  J.log('  Looking for the verification email in Gmail…');
  let baseline = null; // messages already in the inbox when we started looking
  while (Date.now() < end && !stopRequested && !J.ctx.skipped) {
    await inject(gmail.id, 'content/gmail.js');
    await call(gmail.id, '__abGmail', 'refresh').catch(() => {});
    await sleep(2500);
    const rows = (await call(gmail.id, '__abGmail', 'rows', [2]).catch(() => [])) || [];
    const keyOf = (r) => `${r.subject}|${r.snippet}|${r.from}`;
    if (!baseline) baseline = new Set(rows.map(keyOf));
    for (const r of rows) {
      const key = keyOf(r);
      if (usedMails.has(key)) continue;
      const hay = `${r.from} ${r.subject} ${r.snippet}`.toLowerCase();
      // the email must name this company / site, or be a verification email that arrived while we waited
      const forThisJob = words.some((w) => hay.includes(w));
      if (!forThisJob && !(MAIL_HINT.test(hay) && !baseline.has(key))) continue;
      const mail = await call(gmail.id, '__abGmail', 'open', [r.i]).catch(() => null);
      await call(gmail.id, '__abGmail', 'back').catch(() => {});
      if (!mail) continue;
      usedMails.add(key);
      const text = `${mail.subject} ${mail.text}`;
      const code = (/(?:code|passcode|pin|otp|verification)[^A-Za-z0-9]{0,40}(?:is|:)?\s*([0-9]{4,8}|[A-Z0-9]{6,8})\b/i.exec(text) || /\b(\d{6})\b/.exec(text) || [])[1] || '';
      const link = mail.links.find((l) => /verif|confirm|activat|validate|magic|token|sign.?in|log.?in|continue|set.?password/i.test(`${l.text} ${l.href}`)
        && !/unsubscribe|privacy|terms|help|support|preferences|mailto:/i.test(`${l.text} ${l.href}`))?.href || '';
      if (code || link) {
        J.log(`  Found it: "${mail.subject.slice(0, 80)}" → ${code ? `code ${code}` : 'verification link'}`);
        J.phase('applying');
        return { code, link };
      }
    }
    await sleep(5000);
  }
  J.phase('applying');
  return null;
}

// ---------------- the page-by-page agent ----------------

// Reasons that really need you; everything else the bot decides itself.
const HARD_STOP = /captcha|robot|human verification|verif\w* (code|link|email)|one[- ]time|otp|sms code|email code|wrong password|incorrect password|invalid password|locked|password.*(incorrect|invalid)/i;

// Snapshot once the page has finished rendering: slow single-page apps (Workday) show a
// spinner or an empty shell first. Scrolls and re-reads up to ~15 s.
async function readyPage(tabId) {
  let snap = await snapshotTab(tabId);
  for (let i = 0; i < 6 && !snap.success && !snap.already && (snap.loading || (!snap.fields.length && snap.buttons.length <= 2)); i++) {
    await sleep(2500);
    await chrome.scripting.executeScript({ target: { tabId }, func: () => window.scrollBy(0, 200) }).catch(() => {});
    await call(tabId, '__abAgent', 'waitStable', [1000, 6000]).catch(() => {});
    snap = await snapshotTab(tabId);
  }
  return snap;
}

// Pages where the resume (and cover letter) are needed: generated then, not before.
const NEEDS_DOCS = new Set(['application_form', 'review']);

// An account wall the bot tried but could not get past is skipped, not left for you.
async function runApplication(J, startTabId, opts) {
  const r = await runSteps(J, startTabId, opts);
  if (r.status === 'attention' && J.account && !r.keep) {
    return { ...r, status: 'skipped', reason: `could not get past the account step (${r.reason})` };
  }
  return r;
}

async function runSteps(J, startTabId, { job, docs, ensureDocs, s }) {
  const g = J.ctx.guard;
  const profile = await getProfile();
  const baseResume = await rpc('base:get').catch(() => null);
  const aiProfile = profileForAI(profile);
  const secrets = secretsFor(profile);
  const skipAccounts = s.behavior.accounts === 'skip';
  const history = [];
  const answers = [];
  let tabId = startTabId;
  let lastKey = '';
  let repeats = 0;
  let captchaWaits = 0;
  let emailTries = 0;
  let overrides = 0; // times the bot decided by itself instead of asking you
  let idle = 0; // pages that looked empty / still loading
  let lastRulesKey = ''; // page handled by rules alone last step; if it is still there, ask the AI

  // Fills the code from the verification email, or opens its link. true = carry on with the next step.
  const verifyByEmail = async (snap) => {
    if (emailTries++ >= 2) return false;
    const v = await g(findVerification(J, s, { company: job.company, host: new URL(snap.url).hostname }));
    if (!v) return false;
    const codeField = snap.fields.find((f) => !isFilled(f) && CODE_FIELD.test(`${f.label} ${f.question || ''} ${f.placeholder} ${f.name}`));
    if (v.code && codeField) {
      const { frameId, local } = splitId(codeField.id);
      const r = await call(tabId, '__abAgent', 'act', [[{ id: local, op: 'fill', value: v.code }], null, null], { frameId }).catch((err) => [{ ok: false, note: err.message }]);
      if (!r?.[0]?.ok) J.log(`  Could not type the code: ${r?.[0]?.note || 'unknown'}`, 'warn');
      else J.log('  Entered the verification code');
      history.push(`entered the verification code from the email into ${codeField.label || 'the code field'}`);
      return true;
    }
    if (v.code) J.log(`  Got code ${v.code} but no code box on the page (${snap.fields.map((f) => f.label).join(', ') || 'no fields'})`, 'warn');
    if (v.link) {
      await chrome.tabs.update(tabId, { url: v.link });
      await sleep(2000);
      history.push('opened the verification link from the email');
      return true;
    }
    return false;
  };

  for (let step = 1; step <= s.behavior.stepLimit; step++) {
    if (stopRequested) return { status: 'stopped', answers };
    J.ctx.check();
    J.set({ step });
    const tab = await tabExists(tabId);
    if (!tab) return { status: 'attention', reason: 'the application tab was closed', answers, keep: true };
    if (LINKEDIN.test(tab.url)) return { status: 'skipped', reason: 'it sends the application to LinkedIn', answers };

    await g(settle(tabId));
    if (await g(acceptCookies(J, tabId))) await g(settle(tabId));

    let snap = await g(readyPage(tabId));
    const posting = postingWithApply(snap);
    if (posting && step === 1) J.log(`  Job details are readable without an account; using "${posting.text}"`);
    if (!posting && snap.fields.some((f) => f.kind === 'password')) {
      J.account = true;
      if (skipAccounts) return { status: 'skipped', reason: 'it requires an account (sign up / sign in)', answers };
    }
    if (snap.success && !snap.fields.some((f) => f.required && !f.value)) {
      J.log(`Confirmation page: "${(snap.text.match(/[^.]*(thank|submitted|received|congratulations)[^.]*/i) || [''])[0].slice(0, 120)}"`);
      return { status: 'applied', answers };
    }
    if (snap.already) return { status: 'applied', answers, note: 'already applied' };
    if (snap.captcha && /checkbox|challenge|Turnstile|hCaptcha|verification/i.test(snap.captcha)) {
      if (captchaWaits++ < 2 && (await g(waitForCaptcha(J, tabId, s, snap.captcha)))) continue;
      return { status: 'attention', reason: `${snap.captcha} not solved`, answers, keep: true };
    }

    // the site sent a code or a link by email: get it from Gmail
    const wantsCode = snap.fields.some((f) => !isFilled(f) && CODE_FIELD.test(`${f.label} ${f.question || ''} ${f.placeholder} ${f.name}`));
    // (a filled code box is left for the rules to press Verify; "click the link we sent" pages have no boxes)
    if (wantsCode || (CHECK_EMAIL.test(snap.text) && !snap.fields.some((f) => f.kind !== 'file' && f.kind !== 'checkbox'))) {
      if (await verifyByEmail(snap)) {
        step--;
        continue;
      }
      if (!wantsCode) return { status: 'attention', reason: 'the verification email did not arrive', answers };
    }

    // read the real choices of custom dropdowns so the AI only picks values the form offers
    if (snap.fields.some((f) => f.kind === 'combobox' && !f.options)) {
      const probed = await g(call(tabId, '__abAgent', 'probeOptions', [], { allFrames: true }).catch(() => []));
      if (probed.some((r) => r.result)) snap = await g(snapshotTab(tabId));
    }

    // 1. rules first: the profile answers the usual questions and the obvious button needs no AI.
    //    If the last rules-only step did not move the page on, let the AI look at the whole form.
    const pageKey = `${snap.url}|${snap.fields.map((f) => f.id).join(',')}|${snap.buttons.map((b) => b.text).join(',')}`;
    const forceAI = pageKey === lastRulesKey;
    await loadKB();
    const saved = await savedAnswers();
    const ruleArgs = () => ({ profile, saved, uploadCover: s.behavior.uploadCover, coverLetter: docs.coverLetter, forceAI, jobSalary: job.salary });
    let rp = rulePlan(snap, ruleArgs());
    // a real application form: write the tailored resume + cover letter before filling it
    if (!docs.files && !snap.fields.some((f) => f.kind === 'password') && rp.page_type === 'application_form' && (rp.needsDocs || snap.fields.length >= 3)) {
      J.log('  Application form found; preparing the resume and cover letter');
      job.apply_url = snap.url;
      await g(ensureDocs());
      rp = rulePlan(snap, ruleArgs());
    }

    let plan;
    if (!rp.askAI) {
      plan = { page_type: rp.page_type, summary: rp.summary, actions: rp.actions, next_click: rp.next_click, done: false, needs_human: '' };
      lastRulesKey = pageKey;
      J.log(`Step ${step} [${plan.page_type}] no AI: ${plan.summary}`);
    } else {
      // 2. the AI only sees the questions the rules could not answer
      J.phase('thinking');
      const aiSnap = { ...snap, fields: rp.aiFields };
      // the example knowledge base (data/examples.json + what earlier forms taught it) for these questions
      const { learned } = await chrome.storage.local.get('learned');
      const examples = examplesForAI(rp.aiFields, { url: snap.url, profile: aiProfile, learned: learned || [] });
      const ai = await g(rpc('bot:plan', {
        snapshot: aiSnap, prefilled: snap.fields.length - rp.aiFields.length, job, profile: aiProfile, resume: docs.resume || baseResume,
        coverLetter: rp.aiFields.some((f) => /cover letter/i.test(`${f.label} ${f.question || ''}`)) ? docs.coverLetter : '',
        history: history.slice(-5), files: { resume: true, cover: !docs.files || !!docs.files.cover }, bidId: docs.bid?.id, examples
      }));
      J.phase('applying');
      lastRulesKey = '';
      const aiIds = new Set(ai.actions.map((a) => a.id));
      plan = { ...ai, actions: [...rp.actions.filter((a) => !aiIds.has(a.id)), ...ai.actions], next_click: ai.next_click || rp.next_click };
      rememberAnswers(answersToSave(snap, ai.actions));
      rememberLearned(learnFrom(snap, ai.actions, { host: hostOf(snap.url), company: job.company }));
      J.log(`Step ${step} [${plan.page_type}]: ${plan.summary}${rp.actions.length ? ` (+${rp.actions.length} filled by rules)` : ''}`);
    }

    if (plan.page_type === 'requires_linkedin') return { status: 'skipped', reason: 'it can only be applied to through LinkedIn', answers };
    if ((plan.page_type === 'register' || plan.page_type === 'login') && !posting) {
      J.account = true;
      if (skipAccounts) return { status: 'skipped', reason: 'it requires an account (sign up / sign in)', answers };
    }
    // never follow a LinkedIn button
    const nextBtn = snap.buttons.find((b) => b.id === plan.next_click);
    if (nextBtn && /linkedin/i.test(`${nextBtn.text} ${nextBtn.href}`)) return { status: 'skipped', reason: 'it wants a LinkedIn sign-in', answers };

    // first real form: write the tailored resume + cover letter now, then plan this page again with them
    // (rules-only steps already decided this above)
    if (!docs.files && rp.askAI && (NEEDS_DOCS.has(plan.page_type) || plan.actions.some((a) => a.op.startsWith('upload')))) {
      J.log('  Application form found; preparing the resume and cover letter');
      await g(ensureDocs());
      step--;
      continue;
    }

    if (plan.done || plan.page_type === 'success' || plan.page_type === 'already_applied') return { status: 'applied', answers };
    if (plan.page_type === 'job_closed') return { status: 'skipped', reason: 'the posting is closed', answers };
    if (plan.needs_human) {
      if (plan.page_type === 'captcha' && captchaWaits++ < 2 && (await g(waitForCaptcha(J, tabId, s, 'captcha')))) continue;
      // an email code / link: read it from Gmail instead of asking you
      if (/verif|code|email|link|one[- ]time|otp/i.test(plan.needs_human) && !/captcha|robot/i.test(plan.needs_human) && (await verifyByEmail(snap))) {
        step--;
        continue;
      }
      // only real blockers stop the bot; anything else it works out itself
      if (HARD_STOP.test(`${plan.needs_human} ${plan.page_type}`) || overrides >= 3) {
        return { status: 'attention', reason: plan.needs_human, answers, keep: /captcha|robot/i.test(plan.needs_human) };
      }
      overrides++;
      J.log(`  Deciding without you (AI wanted help: ${plan.needs_human.slice(0, 120)})`);
      history.push(`You asked for a human ("${plan.needs_human.slice(0, 160)}") but that is not allowed here. Decide yourself: choose the most likely button to move forward (Apply, Autofill with Resume, Create Account, Sign In, Continue, Next) or fill what you can.`);
      if (!plan.actions.length && !plan.next_click) {
        await sleep(2500);
        continue;
      }
    }

    // empty or still-loading page: wait and look again
    if (!plan.actions.length && !plan.next_click) {
      if (++idle > 4) return { status: 'attention', reason: 'the page never showed anything to fill or click', answers };
      J.log('  Nothing to do yet; waiting for the page');
      await call(tabId, '__abAgent', 'waitStable', [1500, 10000]).catch(() => {});
      await sleep(2000);
      history.push(`${plan.page_type} at ${snap.url.slice(0, 100)}: page looked empty or still loading, waited`);
      step--; // waiting does not use up a page
      continue;
    }
    idle = 0;

    // loop guard: same page, same plan, three times in a row
    const key = `${snap.url}|${plan.page_type}|${plan.actions.map((a) => a.id + a.op).join(',')}|${plan.next_click}`;
    repeats = key === lastKey ? repeats + 1 : 0;
    lastKey = key;
    if (repeats >= 2) return { status: 'attention', reason: `stuck on the same ${plan.page_type.replace('_', ' ')} step`, answers };

    // carry out the actions, frame by frame, in order
    const results = [];
    for (const a of plan.actions) {
      const { frameId, local } = splitId(a.id);
      J.ctx.check();
      const r = await call(tabId, '__abAgent', 'act', [[{ ...a, id: local }], docs.files, secrets], { frameId }).catch((err) => [{ ok: false, note: err.message }]);
      results.push({ ...a, ...(r?.[0] || {}) });
      if (a.question && a.value) answers.push({ question: a.question, answer: a.value });
    }
    const failed = results.filter((r) => !r.ok);
    if (failed.length) J.log(`  ${failed.length} of ${results.length} actions failed: ${failed.slice(0, 3).map((f) => `${f.op} ${f.id} (${f.note})`).join('; ')}`, 'warn');
    // sites parse an uploaded resume (Workday "Autofill with Resume") before Continue works
    if (results.some((r) => r.ok && r.op.startsWith('upload'))) {
      J.log('  Resume uploaded; waiting for the site to read it');
      await sleep(3000);
      await call(tabId, '__abAgent', 'waitStable', [1500, 15000]).catch(() => {});
    }

    let clicked = '';
    if (plan.next_click) {
      const btn = snap.buttons.find((b) => b.id === plan.next_click);
      clicked = btn?.text || plan.next_click;
      const isSubmit = plan.page_type === 'review' || /submit|send application|finish/i.test(clicked);
      if (isSubmit && !s.behavior.autoSubmit) return { status: 'attention', reason: 'ready to submit (auto-submit is off): review and press Submit', answers, keep: true };
      await g(sleep(400 + Math.random() * 500));
      J.ctx.check();
      const t0 = Date.now();
      const { frameId, local } = splitId(plan.next_click);
      await call(tabId, '__abAgent', 'click', [local], { frameId }).catch(() => {});
      // some sites continue in a new tab
      const newTab = await waitNewTab(tabId, t0, 2500);
      if (newTab) {
        J.tabs.push(newTab);
        tabId = newTab;
        const nt = await tabExists(newTab);
        if (nt && LINKEDIN.test(nt.pendingUrl || nt.url || '')) return { status: 'skipped', reason: 'it opened LinkedIn', answers };
        await showTab(tabId);
        J.log('  Continued in a new tab');
      }
      await sleep(1200);
    }
    history.push(`${plan.page_type} at ${snap.url.slice(0, 100)}: ${results.length} actions${failed.length ? ` (${failed.length} failed: ${failed.map((f) => f.note).join(', ').slice(0, 160)})` : ''}${clicked ? `, clicked "${clicked}"` : ''}${snap.errors.length ? `; page errors: ${snap.errors.join(' | ').slice(0, 200)}` : ''}`);
  }
  return { status: 'attention', reason: `no confirmation after ${s.behavior.stepLimit} steps`, answers };
}

// ---------------- messages from the side panel / options ----------------

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  (async () => {
    switch (msg.type) {
      case 'start': await start(msg.mode || 'jobright'); return { ok: true };
      case 'stop': stop(); return { ok: true };
      case 'skip': skipJob(msg.w); return { ok: true };
      case 'runSingle': await runSingle(msg.tabId); return { ok: true };
      case 'addLinks': return { ok: true, ...(await addLinks(msg.text)) };
      case 'clearLinks': return { ok: true, ...(await clearLinks(msg.which)) };
      case 'resetDone': await chrome.storage.local.remove('done'); return { ok: true };
      case 'clearLog': state.log = []; save(); return { ok: true };
      default: return { ok: false, error: 'unknown message' };
    }
  })().then(reply, (err) => reply({ ok: false, error: err.message }));
  return true;
});

// The toolbar icon opens the side panel, which stays open while you browse (a popup closes on every click).
chrome.sidePanel?.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

chrome.runtime.onInstalled.addListener(async () => {
  if (await seedProfile()) console.log('[auto-bid] profile loaded');
});

// A restarted service worker cannot resume a half-done bid; reset to idle.
restoreLinks();
chrome.storage.local.get('bot').then(({ bot }) => {
  state = { ...blank(), ...(bot || {}) };
  if (state.running) {
    state.running = false;
    log('Service worker restarted; bot stopped. Press Start to continue.', 'warn');
  }
  state.phase = 'idle';
  state.jobs = [];
  save();
  restoreLinks().then(() => saveLinks());
  updateBadge();
});
