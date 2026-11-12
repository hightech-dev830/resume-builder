// Auto Bid service worker: walks the job list of the chosen board (Jobright or HiringCafe), and
// for each new remote job posted today reads the job, generates a tailored resume + cover letter
// through the Resume Builder server, opens the company application, fills it page by page with
// the AI planner, and finally marks the job applied on the board ("Yes, I applied!" on Jobright,
// "Save" on HiringCafe). Every bid is saved to Supabase by the server.
import { getSettings, getProfile, seedProfile, profileForAI, secretsFor, rpc, SITES, siteOf, listUrlOf } from './lib/config.js';
import { rulePlan, answersToSave, isAutofillUpload } from './lib/rules.js';

// Answers the AI gave to reusable screening questions, reused on later applications without the AI.
async function savedAnswers() {
  const { answers } = await chrome.storage.local.get('answers');
  return answers || {};
}
async function rememberAnswers(add) {
  if (!Object.keys(add).length) return;
  const all = { ...(await savedAnswers()), ...add };
  const keys = Object.keys(all);
  if (keys.length > 3000) keys.sort((a, b) => all[a].t - all[b].t).slice(0, keys.length - 3000).forEach((k) => delete all[k]);
  await chrome.storage.local.set({ answers: all });
}

const LINKEDIN = /^https:\/\/([a-z]+\.)?linkedin\.com\//i;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------- state (mirrored to storage for the popup) ----------------

const blank = () => ({
  running: false, phase: 'idle', startedAt: null,
  current: null, // { title, company, bidId, step, url }
  counts: { applied: 0, attention: 0, failed: 0, skipped: 0 },
  log: []
});
let state = blank();
let stopRequested = false;

// Manual skip: every job gets a context; pressing "Skip" rejects its promise, so the job
// ends at once, and every guarded await inside the abandoned work throws too.
class SkipError extends Error {}
let currentJob = null;
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
function skipCurrent() {
  if (!state.running || !currentJob || currentJob.skipped) throw new Error('No job is being applied to right now.');
  log('Skip pressed; dropping the current job');
  currentJob.cancel();
}

async function loadState() {
  const { bot } = await chrome.storage.local.get('bot');
  state = { ...blank(), ...(bot || {}) };
}

let saveTimer = null;
function save() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => chrome.storage.local.set({ bot: state }), 150);
}

function setPhase(phase, current) {
  state.phase = phase;
  if (current !== undefined) state.current = current;
  save();
  updateBadge();
}

function log(msg, level = 'info') {
  state.log.push({ t: Date.now(), msg, level });
  if (state.log.length > 300) state.log.splice(0, state.log.length - 300);
  console[level === 'error' ? 'warn' : 'log']('[auto-bid]', msg);
  save();
  if (state.current?.bidId) rpc('bot:log', state.current.bidId, msg).catch(() => {});
}

function updateBadge() {
  chrome.action.setBadgeText({ text: state.running ? String(state.counts.applied) : '' });
  chrome.action.setBadgeBackgroundColor({ color: state.phase === 'waiting_human' ? '#d97706' : '#16a34a' });
}

// Notifications remember their tab, so clicking one brings that tab to the front.
async function notify(title, message, { id, tabId, sticky = false } = {}) {
  const nid = id || `ab-${Date.now()}`;
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
  createdTabs.push({ id: tab.id, opener: tab.openerTabId, t: Date.now() });
  if (createdTabs.length > 50) createdTabs.shift();
});

async function waitNewTab(openerId, since, timeout) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    // openerId null: any tab opened since `since`
    const hit = createdTabs.find((c) => (openerId == null || c.opener === openerId) && c.t >= since);
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

// Real mouse input (chrome.debugger), for pages whose hover buttons and new tabs only react to a
// real cursor; a scripted click has no user gesture, so the browser blocks the tab it opens.
// Chrome shows "Auto Bid started debugging this browser" while attached.
async function withDebugger(tabId, fn) {
  const target = { tabId };
  let attached = false;
  try {
    await chrome.debugger.attach(target, '1.3');
    attached = true;
  } catch (err) {
    if (!/already attached/i.test(err.message)) throw err;
  }
  try {
    return await fn((method, params) => chrome.debugger.sendCommand(target, method, params));
  } finally {
    if (attached) await chrome.debugger.detach(target).catch(() => {});
  }
}

// Moves the cursor to (x, y) in small steps; clicks there when `click` is set.
async function realMouse(tabId, { x, y }, { click = false } = {}) {
  await withDebugger(tabId, async (send) => {
    const from = realMouse.last?.[tabId] || { x: Math.max(0, x - 120), y: Math.max(0, y - 80) };
    for (let i = 1; i <= 6; i++) {
      const p = { x: Math.round(from.x + ((x - from.x) * i) / 6), y: Math.round(from.y + ((y - from.y) * i) / 6) };
      await send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...p });
      await sleep(40);
    }
    realMouse.last = { ...(realMouse.last || {}), [tabId]: { x, y } };
    if (click) {
      await sleep(120);
      await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', buttons: 1, clickCount: 1 });
      await sleep(60);
      await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', buttons: 0, clickCount: 1 });
    }
  });
}

// On a pointer board (HiringCafe): cursor onto the job box, wait for its hover buttons,
// then a real click on the button. what = 'apply' | 'save'. Returns { clicked, href, error, saved }.
// A job already saved on the board is not applied to again (saved = true, clicked = false).
async function pointerClick(site, tabId, card, what) {
  const spot = await board(site, tabId, 'target', card, 'card');
  if (!spot || spot.error) return { clicked: false, error: spot?.error || 'no reply from the page' };
  await realMouse(tabId, spot);
  let btn = null;
  for (let i = 0; i < 12; i++) {
    await sleep(350);
    btn = await board(site, tabId, 'target', card, what);
    if (btn && (btn.saved || !btn.error)) break;
    if (i % 3 === 2) await realMouse(tabId, { x: spot.x + (i % 2 ? 6 : -6), y: spot.y + 4 }); // nudge to re-trigger hover
  }
  if (what === 'apply' && btn?.saved) return { clicked: false, saved: true, error: 'already saved on the board' };
  if (what === 'save' && btn?.already) return { clicked: true, saved: true };
  if (!btn || btn.error) return { clicked: false, error: btn?.error || 'no reply from the page', href: btn?.href || '' };
  await realMouse(tabId, btn, { click: true });
  return { clicked: true, href: btn.href || '' };
}

// Calls the job board's content script (content/jobright.js, content/hiringcafe.js …).
async function board(site, tabId, method, ...args) {
  await inject(tabId, site.script);
  return call(tabId, site.ns, method, args);
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

// ---------------- helpers ----------------

// The form's own address for applicant systems that show the posting and the form on separate pages.
//   Ashby: jobs.ashbyhq.com/<company>/<uuid>  -> …/<uuid>/application  (the "Application" tab)
//   Lever: jobs.lever.co/<company>/<uuid>     -> …/<uuid>/apply
function directApplyUrl(url) {
  let u;
  try {
    u = new URL(url);
  } catch {
    return '';
  }
  const uuid = /^\/[^/]+\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/?$/i;
  if (/(^|\.)ashbyhq\.com$/.test(u.hostname) && uuid.test(u.pathname)) u.pathname = u.pathname.replace(/\/?$/, '/application');
  else if (/(^|\.)lever\.co$/.test(u.hostname) && uuid.test(u.pathname)) u.pathname = u.pathname.replace(/\/?$/, '/apply');
  else return '';
  return u.toString();
}

function slug(s, max = 40) {
  return String(s || '').normalize('NFKD').replace(/[^\w\s-]/g, '').trim().replace(/[\s-]+/g, '_').slice(0, max) || 'Untitled';
}

// the "skip job sites" entry the URL contains ("https://www.icims.com/" is matched as "icims.com"), or ''
function skippedSite(url, s) {
  const u = String(url || '').toLowerCase();
  if (!u) return '';
  return String(s.behavior.skipUrls || '').split(/[\n,]+/)
    .map((w) => w.trim().toLowerCase().replace(/^[a-z]+:\/\//, '').replace(/^www\./, '').replace(/\/+$/, ''))
    .find((w) => w && u.includes(w)) || '';
}

const words = (csv) => String(csv || '').split(',').map((w) => w.trim().toLowerCase()).filter(Boolean);

function filterReason(job, s) {
  const f = s.filters;
  if (job.saved) return 'already saved on the job board';
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

// Every job's result (applied, attention, failed, skipped, stopped), newest last; the History page reads it.
async function addHistory(entry, { replaceBid = false } = {}) {
  const { history = [] } = await chrome.storage.local.get('history');
  const at = replaceBid && entry.bidId ? history.findLastIndex((h) => h.bidId === entry.bidId) : -1;
  if (at >= 0) history.splice(at, 1);
  history.push({ t: Date.now(), ...entry });
  if (history.length > 5000) history.splice(0, history.length - 5000);
  await chrome.storage.local.set({ history });
}

async function markProcessed(id, status) {
  const done = await processed();
  done[id] = { status, t: Date.now() };
  await chrome.storage.local.set({ done });
}

// ---------------- main loop ----------------

async function start() {
  if (state.running) return;
  const s = await getSettings();
  if (!s.token) throw new Error('Sign in to the Resume Builder server first (extension settings).');
  state = { ...blank(), running: true, startedAt: Date.now(), counts: { applied: 0, attention: 0, failed: 0, skipped: 0 }, log: state.log.slice(-50) };
  stopRequested = false;
  startKeepAlive();
  log('Bot started');
  setPhase('starting');
  runLoop().catch((err) => {
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
  setPhase('idle', null);
  const c = state.counts;
  log(`Bot finished: ${c.applied} applied, ${c.attention} need attention, ${c.failed} failed, ${c.skipped} skipped`);
  updateBadge();
}

// Adds what the server decides: the template (extension's choice, else the web app's default)
// and the folder where bid files are saved.
async function withTemplate(s) {
  const server = await rpc('settings:get');
  return { ...s, filesDir: server.local_output_dir || '', behavior: { ...s.behavior, template: s.behavior.template || server.default_template || 'kevin' } };
}

async function listTab(s, site) {
  const tabs = await chrome.tabs.query({});
  const list = tabs.find((t) => site.host.test(t.url || '') && site.isList(t.url));
  if (list) return list.id;
  const tab = await chrome.tabs.create({ url: listUrlOf(s), active: true });
  await waitComplete(tab.id);
  await sleep(2500);
  return tab.id;
}

async function runLoop() {
  const s = await withTemplate(await getSettings());
  const site = siteOf(s);
  log(`Job board: ${site.label}`);
  const listId = await listTab(s, site);
  const tried = new Set();
  let emptyRounds = 0;
  const refreshMs = Math.max(1, Number(s.filters.refreshMin) || 5) * 60000;
  let lastRefresh = Date.now();
  let diagnosed = false;
  let lastCount = 0;

  const refreshList = async () => {
    log(`Refreshing the ${site.label} job list`);
    await chrome.tabs.reload(listId);
    await waitComplete(listId);
    await sleep(3000);
    lastRefresh = Date.now();
    emptyRounds = 0;
  };

  while (!stopRequested && state.counts.applied < s.filters.maxBids) {
    while (retryQueue.length && !stopRequested) await retryAttention(retryQueue.shift(), s);
    if (stopRequested) break;
    setPhase('scanning', null);
    if (!(await tabExists(listId))) throw new Error(`The ${site.label} list tab was closed.`);
    if (Date.now() - lastRefresh >= refreshMs) await refreshList();
    let cards = (await board(site, listId, 'scrapeList')) || [];
    if (!cards.length) {
      await sleep(3000);
      cards = (await board(site, listId, 'scrapeList')) || [];
      if (!cards.length && !diagnosed) {
        diagnosed = true;
        const d = site.ns === '__abHiringCafe' ? await board(site, listId, 'diagnose').catch(() => null) : null;
        log(`No job boxes found on the ${site.label} list${d ? `. Page: ${JSON.stringify(d)}` : ''}`, 'warn');
      }
    }
    if (cards.length && cards.length !== lastCount) log(`Found ${cards.length} jobs on the list`);
    lastCount = cards.length;
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
        log(`Skipped ${c.title || 'job'} @ ${c.company || '?'}: ${why}`);
        if (c.id) await markProcessed(c.id, 'skipped');
        continue;
      }
      next = c;
      break;
    }

    if (!next) {
      // nothing usable on screen: load more, give up after a few empty scrolls
      const before = cards.length;
      const after = await board(site, listId, 'loadMore');
      emptyRounds = after > before ? 0 : emptyRounds + 1;
      if (emptyRounds >= 3) {
        // wait for the next refresh instead of stopping; new jobs keep arriving
        const at = new Date(lastRefresh + refreshMs);
        log(`No new matching jobs right now; checking again at ${at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`);
        setPhase('waiting_jobs', null);
        while (!stopRequested && !retryQueue.length && Date.now() < at.getTime()) await sleep(3000);
        if (retryQueue.length) continue;
        if (stopRequested) break;
        await refreshList();
      }
      continue;
    }

    await processJob(next, { listId, settings: s, site });
    if (!stopRequested) await sleep(s.behavior.delaySec * 1000);
  }
  if (state.counts.applied >= s.filters.maxBids) log(`Reached the limit of ${s.filters.maxBids} bids for this run.`);
}

// ---------------- one job ----------------

// Tailored resume + cover letter for a bid: written by the AI, exported to PDF, read back for upload.
async function generateDocs(bid, job, s, g = (p) => p) {
  const jobForAi = { ...job, bid_id: bid.id };
  log('Tailoring the resume…');
  const tailored = await g(rpc('ai:tailor', jobForAi));
  log('Writing the cover letter…');
  const letter = await g(rpc('ai:coverLetter', { resume: tailored.resume, job: jobForAi }));
  bid = await g(rpc('resumes:update', bid.id, { resume_json: tailored.resume, cover_letter: letter, ...tailored.meta }));
  bid = await g(rpc('resumes:exportPdf', bid.id));
  bid = await g(rpc('resumes:exportCover', bid.id));
  const docs = { bid, resume: tailored.resume, coverLetter: letter, ...(await g(readDocFiles(bid, s))) };
  log(`Resume and cover letter ready (${tailored.keywords?.length || 0} JD keywords matched)${s.filesDir ? `, saved in ${s.filesDir}/${bid.resume_dir}` : ''}`);
  return docs;
}

// The bid's PDFs as uploadable files.
async function readDocFiles(bid, s) {
  const [resumePdf, coverPdf] = await Promise.all([rpc('files:get', bid.pdf_path), bid.cover_letter_path ? rpc('files:get', bid.cover_letter_path) : null]);
  return {
    files: {
      resume: { b64: resumePdf.__b64, name: bid.pdf_path.split('/').pop() },
      cover: s.behavior.uploadCover && coverPdf ? { b64: coverPdf.__b64, name: bid.cover_letter_path.split('/').pop() } : null
    }
  };
}

// ---------------- "Apply again" for jobs that need you ----------------
// A "need you" job keeps its tab open; the bot remembers what it needs to carry on there
// (session storage survives service-worker restarts). After you fix the page (bot check,
// a missing answer…), "Apply again" continues the same bid on that tab.

async function attentionJobs() {
  const { attentionTabs = {} } = await chrome.storage.session.get('attentionTabs');
  return attentionTabs;
}
async function rememberAttention(tabId, entry) {
  const all = await attentionJobs();
  all[tabId] = { ...entry, t: Date.now() };
  await chrome.storage.session.set({ attentionTabs: all });
}
async function forgetAttention(tabId) {
  const all = await attentionJobs();
  delete all[tabId];
  await chrome.storage.session.set({ attentionTabs: all });
}
chrome.tabs.onRemoved.addListener((tabId) => { forgetAttention(tabId).catch(() => {}); });

const retryQueue = []; // tab ids waiting for the running bot to finish its current job

// Which "need you" job to apply to again: the one in `tabId`, else the newest whose tab is still open.
async function pickAttention(tabId) {
  const all = await attentionJobs();
  if (all[tabId] && (await tabExists(tabId))) return Number(tabId);
  for (const [id, e] of Object.entries(all).sort((a, b) => b[1].t - a[1].t)) {
    if (await tabExists(Number(id))) return Number(id);
    delete all[id];
  }
  await chrome.storage.session.set({ attentionTabs: all });
  return null;
}

async function applyAgain(activeTabId) {
  const tabId = await pickAttention(activeTabId);
  if (tabId == null) throw new Error('No "need you" job is open. Its tab must still be open (the bot leaves it open for you).');
  if (retryQueue.includes(tabId)) return { queued: true };
  if (state.running) {
    retryQueue.push(tabId);
    log('"Apply again" queued; it runs after the current job');
    return { queued: true };
  }
  let s = await getSettings();
  if (!s.token) throw new Error('Sign in to the Resume Builder server first (extension settings).');
  s = await withTemplate(s);
  state = { ...blank(), running: true, startedAt: Date.now(), log: state.log.slice(-50) };
  stopRequested = false;
  startKeepAlive();
  retryAttention(tabId, s).finally(finish);
  return { queued: false };
}

// Carries on with a "need you" job on its tab, then finishes it like a normal bid.
async function retryAttention(tabId, s) {
  const entry = (await attentionJobs())[tabId];
  if (!entry || !(await tabExists(tabId))) return;
  const site = SITES[entry.site] || siteOf(s);
  const { job, card } = entry;
  const label = `${job.role || 'job'} @ ${job.company || '?'}`;
  const tabs = [tabId];
  const ctx = jobContext();
  currentJob = ctx;
  const g = ctx.guard;
  let bid = null;
  let outcome = 'attention';
  let reason = '';
  setPhase('applying', { title: job.role, company: job.company, url: entry.postingUrl, bidId: entry.bidId, step: 0 });
  log(`↻ Applying again: ${label}${entry.bidId ? ` (bid #${entry.bidId})` : ''}`);
  try {
    await focusTab(tabId);
    // the bid and its resume / cover letter, when they were made already
    bid = entry.bidId ? await rpc('resumes:get', entry.bidId).catch(() => null) : null;
    const docs = { bid, files: null, resume: bid?.resume_json || null, coverLetter: bid?.cover_letter || '' };
    if (bid?.pdf_path) Object.assign(docs, await readDocFiles(bid, s).catch(() => ({})));
    const ensureDocs = async () => {
      if (docs.files) return false;
      if (!bid) {
        bid = await rpc('resumes:create', {
          company: job.company, role: job.role, job_link: job.job_link, apply_url: job.job_link, location: job.location, salary: job.salary, jd: job.jd,
          status: 'draft', template: s.behavior.template, notes: `Auto-bid from ${site.label} (applied again).`,
          source: entry.site, source_url: entry.postingUrl, bot_status: 'applying'
        });
        state.current.bidId = bid.id;
        log(`Bid #${bid.id} created for ${label}`);
      }
      setPhase('generating');
      Object.assign(docs, await generateDocs(bid, job, s, g));
      bid = docs.bid;
      setPhase('applying');
      return true;
    };
    if (bid) await rpc('bot:log', bid.id, 'Applying again after your fix', { bot_status: 'applying' }).catch(() => {});

    const progress = { step: 0, history: [], answers: [], ...(entry.progress || {}) };
    if (progress.step) log(`  Continuing at step ${progress.step + 1} (stopped at step ${progress.step}: ${progress.fixed || 'needed you'})`);
    const result = await g(runApplication(tabId, { job, docs, ensureDocs, settings: s, site, tabs, ctx, progress }));
    bid = docs.bid || bid;
    reason = result.reason || result.note || '';
    if (bid) await rpc('resumes:update', bid.id, { answers: result.answers }).catch(() => {});
    if (result.status === 'applied') {
      outcome = 'applied';
      state.counts.applied++;
      if (bid) await rpc('resumes:update', bid.id, { status: 'applied', bot_status: 'applied', applied_at: new Date().toISOString() });
      log(`✓ Applied (again): ${label}`, 'ok');
      notify('Application submitted', `${job.role} at ${job.company}${bid ? ` (bid #${bid.id})` : ''}`);
      await forgetAttention(tabId);
      // tell the job board, like a normal bid
      if (site.pointer && entry.listId && (await tabExists(entry.listId))) {
        await focusTab(entry.listId);
        const yes = (await pointerClick(site, entry.listId, card, 'save').catch(() => ({}))).clicked
          || (await board(site, entry.listId, 'confirmApplied', 12000, card).catch(() => false));
        log(yes ? `Saved the job on ${site.label}` : `Could not click "Save" on ${site.label} (is the job still on the list?)`);
      } else if (site.detailPage && entry.detailId && (await tabExists(entry.detailId))) {
        await focusTab(entry.detailId);
        const yes = await board(site, entry.detailId, 'confirmApplied', 12000, card).catch(() => false);
        log(yes ? `Marked as applied on ${site.label}` : `${site.label} "Did you apply?" dialog did not appear`);
      }
      if (s.behavior.closeTabs) for (const id of tabs) await chrome.tabs.remove(id).catch(() => {});
    } else if (result.status === 'skipped') {
      outcome = 'skipped';
      state.counts.skipped++;
      log(`Skipped ${label}: ${reason}`);
      if (bid) await rpc('bot:log', bid.id, `Skipped: ${reason}`, { bot_status: 'skipped' }).catch(() => {});
      await forgetAttention(tabId);
    } else if (result.status === 'stopped') {
      outcome = 'stopped';
      reason = 'stopped by you';
    } else {
      // still needs you: keep it for another "Apply again", on the tab it ended on
      state.counts.attention++;
      log(`⚠ ${label}: ${reason}. Tab left open for you; press Apply again after fixing it.`, 'warn');
      if (bid) await rpc('bot:log', bid.id, `Needs attention: ${reason}`, { bot_status: 'needs_attention' }).catch(() => {});
      const last = tabs[tabs.length - 1];
      if (last !== tabId) await forgetAttention(tabId);
      await rememberAttention(last, {
        ...entry, bidId: bid?.id || entry.bidId,
        progress: { step: progress.step, history: progress.history.slice(-20), answers: progress.answers, fixed: reason }
      });
      await notify('Application needs you', `${job.role} at ${job.company}: ${reason}`, { tabId: last, sticky: true });
    }
  } catch (err) {
    if (err instanceof SkipError || ctx.skipped) {
      outcome = 'skipped';
      reason = 'you pressed Skip';
      state.counts.skipped++;
      await forgetAttention(tabId);
    } else {
      outcome = 'failed';
      reason = err.message;
      state.counts.failed++;
    }
    log(`${outcome === 'failed' ? '✗' : '⏭'} ${label}: ${reason}`, outcome === 'failed' ? 'error' : 'warn');
    if (bid) await rpc('bot:log', bid.id, `${outcome === 'failed' ? 'Failed' : 'Skipped'}: ${reason}`, { bot_status: outcome }).catch(() => {});
  } finally {
    if (currentJob === ctx) currentJob = null;
    if (entry.cardId) await markProcessed(entry.cardId, outcome);
    await addHistory({
      status: outcome, reason: outcome === 'applied' ? 'applied again after your fix' : reason, site: entry.site, bidId: bid?.id || entry.bidId || null,
      title: job.role, company: job.company, location: job.location, salary: job.salary, postingUrl: entry.postingUrl, applyUrl: job.job_link
    }, { replaceBid: true }).catch(() => {});
    state.current = null;
    save();
  }
}

async function processJob(card, { listId, settings: s, site = siteOf(s), detailTabId }) {
  const label = `${card.title || 'job'} @ ${card.company || '?'}`;
  setPhase('opening', { title: card.title, company: card.company, url: card.url, step: 0 });
  log(`Opening ${label}`);
  const tabs = [];
  let detailId = detailTabId;
  let bid = null;
  let outcome = 'failed';
  const rec = { reason: '', applyUrl: '', detail: null }; // for the History page
  const ctx = jobContext();
  currentJob = ctx;
  const g = ctx.guard;

  try {
    const work = (async () => {
      // 1. job page on the board (HiringCafe has none: the card on the list is used)
      if (!site.detailPage) {
        detailId = listId;
      } else if (!detailId) {
        if (card.url) {
          detailId = (await chrome.tabs.create({ url: card.url, active: true })).id;
        } else {
          const t0 = Date.now();
          await board(site, listId, 'openCard', card.index);
          detailId = await waitNewTab(listId, t0, 10000);
          if (!detailId) throw new Error('Could not open the job card.');
        }
        tabs.push(detailId);
      }
      if (site.detailPage) {
        await g(waitComplete(detailId));
        await g(sleep(2000));
      }
      const detail = { ...card, ...((await g(board(site, detailId, 'scrapeDetail', card))) || {}) };
      detail.company = detail.company || card.company;
      detail.title = detail.title || card.title;
      rec.detail = detail;
      if (s.filters.remoteOnly && !detail.remote && !card.remote) {
        rec.reason = 'not remote';
        log(`Skipped ${label}: not remote`);
        state.counts.skipped++;
        outcome = 'skipped';
        return;
      }
      state.current = { ...state.current, title: detail.title, company: detail.company, url: detail.url };

      // 2. company application page
      setPhase('opening_apply');
      let applyId = null;
      if (!site.detailPage) await focusTab(listId); // the hover and click happen on the visible list
      const t0 = Date.now();
      let clicked = null;
      if (site.pointer) {
        log('  Moving the cursor to the job box and clicking "Apply Directly"');
        clicked = await g(pointerClick(site, detailId, card, 'apply').catch((err) => ({ clicked: false, error: `real cursor failed (${err.message})` })));
        if (clicked.saved) {
          // saved on HiringCafe = bid on before: never again
          rec.reason = `already saved on ${site.label}`;
          log(`Skipped ${label}: already saved on ${site.label}`);
          state.counts.skipped++;
          outcome = 'skipped';
          return;
        }
        if (!clicked.clicked) {
          log(`  ${clicked.error}; trying a scripted click`);
          clicked = await g(board(site, detailId, 'clickApply', card));
        }
      } else {
        clicked = await g(board(site, detailId, 'clickApply', card));
      }
      if (clicked && clicked.clicked !== false) {
        // on the list, a link with rel="noopener" may open a tab that does not name the list as its opener
        applyId = await g(waitNewTab(site.detailPage ? detailId : null, t0, 15000));
      } else log(`  Could not click Apply${clicked && clicked.error ? `: ${clicked.error}` : ''}`);
      if (!applyId) {
        const cur = await tabExists(detailId);
        if (cur && !site.host.test(cur.url)) {
          // same-tab redirect; on a board without job pages the list must stay, so move it to its own tab
          if (site.detailPage) applyId = detailId;
          else {
            applyId = (await chrome.tabs.create({ url: cur.url, active: true })).id;
            await chrome.tabs.goBack(listId).catch(() => {});
          }
        }
      }
      const applyUrl = detail.applyUrl || (clicked && clicked.href) || '';
      if (!applyId && applyUrl) applyId = (await chrome.tabs.create({ url: applyUrl, active: true })).id;
      if (!applyId) throw new Error('Could not open the company application page.');
      if (applyId !== detailId) tabs.push(applyId);
      // wait out the board's redirect to the real posting
      for (let i = 0; i < 30; i++) {
        const t = await tabExists(applyId);
        if (t && t.url && !site.host.test(t.url) && !/^about:|^chrome/.test(t.url)) break;
        await g(sleep(700));
      }
      await focusTab(applyId);
      await g(settle(applyId));
      const applyTab = await tabExists(applyId);
      rec.applyUrl = applyTab?.url || '';
      // LinkedIn applications (Easy Apply / sign in with LinkedIn) are skipped
      if (LINKEDIN.test(applyTab.url)) {
        rec.reason = 'the application is on LinkedIn';
        log(`Skipped ${label}: the application is on LinkedIn`);
        state.counts.skipped++;
        outcome = 'skipped';
        return;
      }
      const blocked = skippedSite(applyTab.url, s);
      if (blocked) {
        rec.reason = `the site is on your skip list (${blocked})`;
        log(`Skipped ${label}: the site is on your skip list (${blocked})`);
        state.counts.skipped++;
        outcome = 'skipped';
        return;
      }
      log(`Application page: ${new URL(applyTab.url).host}`);

      // 3. exact job description from the company page
      const page = await g(call(applyId, '__abAgent', 'pageText').catch(() => null));
      // posting pages with the form on a separate tab/page (Ashby "Application", Lever "Apply"): go straight to the form
      const formUrl = directApplyUrl(applyTab.url);
      if (formUrl) {
        log('  Job description read; opening the application form');
        await chrome.tabs.update(applyId, { url: formUrl });
        await g(settle(applyId));
      }
      const job = {
        company: detail.company, role: detail.title, location: detail.location || 'Remote (US)', salary: detail.salary || '',
        job_link: applyTab.url,
        jd: [detail.jd, page && page.text.length > 300 ? `\n\n--- From the company posting (${new URL(page.url).host}) ---\n${page.text}` : ''].join('').slice(0, 30000)
      };

      // 4. bid row in Supabase; resume + cover letter only once a form needs them
      const docs = { bid: null, files: null, resume: null, coverLetter: '' };
      const ensureBid = async () => {
        if (docs.bid) return docs.bid;
        ctx.check();
        docs.bid = await g(rpc('resumes:create', {
          company: detail.company, role: detail.title, job_link: job.job_link, apply_url: job.job_link,
          location: detail.location || (detail.remote ? 'Remote (US)' : ''), salary: detail.salary || '', jd: job.jd,
          status: 'draft', template: s.behavior.template,
          notes: `Auto-bid from ${site.label}${detail.match ? ` (match ${detail.match}%)` : ''}. Posted ${detail.posted || '?'}.`,
          source: s.site || 'jobright', source_url: detail.url, bot_status: 'applying'
        }));
        bid = docs.bid;
        state.current.bidId = bid.id;
        log(`Bid #${bid.id} created for ${label}`);
        return bid;
      };
      const ensureDocs = async () => {
        if (docs.files) return false;
        await ensureBid();
        setPhase('generating');
        Object.assign(docs, await generateDocs(bid, job, s, g));
        bid = docs.bid;
        setPhase('applying');
        return true;
      };

      // 5. fill and submit the application
      setPhase('applying');
      const progress = { step: 0, history: [], answers: [] };
      const result = await g(runApplication(applyId, { job, docs, ensureDocs, settings: s, site, tabs, ctx, progress }));
      ctx.check();
      rec.reason = result.reason || result.note || '';
      if (result.status === 'skipped') {
        log(`Skipped ${label}: ${result.reason}`);
        state.counts.skipped++;
        outcome = 'skipped';
        if (bid) await rpc('bot:log', bid.id, `Skipped: ${result.reason}`, { bot_status: 'skipped' }).catch(() => {});
        return;
      }
      await ensureBid();
      await rpc('resumes:update', bid.id, { answers: result.answers }).catch(() => {});

      if (result.status === 'applied') {
        outcome = 'applied';
        state.counts.applied++;
        await rpc('resumes:update', bid.id, { status: 'applied', bot_status: 'applied', applied_at: new Date().toISOString() });
        log(`✓ Applied: ${label}`, 'ok');
        notify('Application submitted', `${detail.title} at ${detail.company} (bid #${bid.id})`);
        // 7. tell the job board
        if (await tabExists(detailId)) {
          if (applyId === detailId && site.detailPage) {
            await chrome.tabs.update(detailId, { url: detail.url });
            await waitComplete(detailId);
            await sleep(1500);
          }
          await focusTab(detailId);
          let yes = false;
          if (site.pointer) yes = (await pointerClick(site, detailId, card, 'save').catch(() => ({}))).clicked;
          if (!yes) yes = await board(site, detailId, 'confirmApplied', 12000, card);
          log(site.detailPage
            ? (yes ? `Marked as applied on ${site.label}` : `${site.label} "Did you apply?" dialog did not appear`)
            : (yes ? `Saved the job on ${site.label}` : `Could not click "Save" on ${site.label}`));
        }
      } else if (result.status === 'stopped') {
        outcome = 'stopped';
        rec.reason = 'stopped by you';
        await rpc('bot:log', bid.id, 'Stopped by user', { bot_status: 'needs_attention' });
      } else {
        outcome = 'attention';
        state.counts.attention++;
        await rpc('bot:log', bid.id, `Needs attention: ${result.reason}`, { bot_status: 'needs_attention' });
        log(`⚠ ${label}: ${result.reason}. Tab left open for you; press Apply again after fixing it.`, 'warn');
        await rememberAttention(tabs[tabs.length - 1], {
          job, bidId: bid.id, site: s.site || 'jobright', card: { index: card.index, title: card.title, company: card.company },
          cardId: card.id || '', postingUrl: detail.url, listId: listId || null, detailId: site.detailPage ? detailId : null,
          progress: { step: progress.step, history: progress.history.slice(-20), answers: progress.answers, fixed: result.reason }
        });
        await notify('Application needs you', `${detail.title} at ${detail.company}: ${result.reason}`, { tabId: tabs[tabs.length - 1], sticky: true });
      }
    })();
    work.catch(() => {});
    await g(work);
  } catch (err) {
    if (outcome === 'applied') {
      // Skip pressed after the application went through (while confirming on the board): keep it applied
    } else if (err instanceof SkipError || ctx.skipped) {
      outcome = 'skipped';
      state.counts.skipped++;
      rec.reason = 'you pressed Skip';
      log(`⏭ Skipped ${label}: you pressed Skip`, 'warn');
      if (bid) await rpc('bot:log', bid.id, 'Skipped by you', { bot_status: 'skipped' }).catch(() => {});
    } else {
      outcome = 'failed';
      state.counts.failed++;
      rec.reason = err.message;
      log(`✗ ${label}: ${err.message}`, 'error');
      if (bid) await rpc('bot:log', bid.id, `Failed: ${err.message}`, { bot_status: 'failed' }).catch(() => {});
    }
  } finally {
    if (currentJob === ctx) currentJob = null;
    if (ctx.skipped) for (const t of tabs) chrome.notifications.clear(`ab-captcha-${t}`);
    if (bid) {
      const cost = await rpc('usage:forBid', bid.id).catch(() => null);
      if (cost?.calls) log(`  AI for this bid: ${cost.calls} calls, ${cost.tokens.toLocaleString()} tokens, ${cost.unpriced ? 'cost unknown for some models' : `$${cost.cost.toFixed(4)}`}`);
    }
    if (card.id) await markProcessed(card.id, outcome);
    const d = rec.detail || card;
    await addHistory({
      status: outcome, reason: rec.reason, site: s.site || 'jobright', bidId: bid?.id || null,
      title: d.title || card.title || '', company: d.company || card.company || '', location: d.location || '', salary: d.salary || '',
      postingUrl: d.url || card.url || '', applyUrl: rec.applyUrl
    }).catch(() => {});
    state.current = null;
    save();
    // close the job's tabs after a clean finish; keep them when you need to look
    if ((outcome === 'applied' || outcome === 'skipped') && s.behavior.closeTabs) {
      for (const id of tabs) await chrome.tabs.remove(id).catch(() => {});
    }
    if (listId && (await tabExists(listId)) && outcome !== 'attention') await chrome.tabs.update(listId, { active: true }).catch(() => {});
  }
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

async function acceptCookies(tabId) {
  const res = await call(tabId, '__abAgent', 'acceptCookies', [], { allFrames: true }).catch(() => []);
  const hit = res.find((r) => r.result);
  if (hit) {
    log(`Accepted cookie banner ("${hit.result}")`);
    await sleep(1200);
  }
  return !!hit;
}

async function waitForCaptcha(tabId, s, what, ctx) {
  setPhase('waiting_human');
  await focusTab(tabId, true);
  await call(tabId, '__abAgent', 'showCaptcha', [], { allFrames: true }).catch(() => {});
  await notify('Captcha: your turn', `Solve the ${what}. The bot continues by itself when it is done.`, { id: `ab-captcha-${tabId}`, tabId, sticky: true });
  log(`Waiting up to ${s.behavior.captchaWaitMin} min for you to solve the ${what}…`, 'warn');
  const end = Date.now() + s.behavior.captchaWaitMin * 60000;
  while (Date.now() < end && !stopRequested && !ctx?.skipped) {
    await sleep(3000);
    const res = await call(tabId, '__abAgent', 'captchaSolved', [], { allFrames: true }).catch(() => []);
    if (res.length && res.every((r) => r.result !== false)) {
      log('Captcha solved, continuing');
      setPhase('applying');
      return true;
    }
  }
  setPhase('applying');
  return false;
}

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

// `progress` ({ step, history, answers, fixed }) is owned by the caller and kept up to date here,
// so "Apply again" can carry on at the step where the job stopped, with what was done so far.
async function runApplication(startTabId, { job, docs, ensureDocs, settings: s, site = siteOf(s), tabs, ctx = jobContext(), progress = { step: 0, history: [], answers: [] } }) {
  const g = ctx.guard;
  const profile = await getProfile();
  // "How did you hear about us": the board the bot is on, unless you set something else yourself
  if (!profile.how_heard || Object.values(SITES).some((x) => x.howHeard === profile.how_heard)) profile.how_heard = site.howHeard;
  const baseResume = await rpc('base:get').catch(() => null);
  const aiProfile = profileForAI(profile);
  const secrets = secretsFor(profile);
  progress.history ||= [];
  progress.answers ||= [];
  const history = progress.history;
  const answers = progress.answers;
  const firstStep = (progress.step || 0) + 1;
  // continuing after you fixed the page: tell the AI, and let it look at the whole page first
  let fixedByYou = !!progress.fixed;
  if (fixedByYou) {
    history.push(`The bot stopped here before ("${progress.fixed}"). The user has now fixed that on this page (for example passed the bot check or filled what was missing). Continue from the current page; do not start the application over.`);
    progress.fixed = '';
  }
  let tabId = startTabId;
  let lastKey = '';
  let repeats = 0;
  let captchaWaits = 0;
  let overrides = 0; // times the bot decided by itself instead of asking you
  let idle = 0; // pages that looked empty / still loading
  let lastRulesKey = ''; // page handled by rules alone last step; if it is still there, ask the AI
  let lastClick = ''; // button clicked last step: a dropdown "Apply Now ▾" is followed by its menu's "Apply Now"

  for (let step = firstStep; step < firstStep + s.behavior.stepLimit; step++) {
    progress.step = step;
    if (stopRequested) return { status: 'stopped', answers };
    ctx.check();
    state.current = { ...state.current, step };
    save();
    const tab = await tabExists(tabId);
    if (!tab) return { status: 'attention', reason: 'the application tab was closed', answers };
    if (LINKEDIN.test(tab.url)) return { status: 'skipped', reason: 'it sends the application to LinkedIn', answers };
    const blocked = skippedSite(tab.url, s);
    if (blocked) return { status: 'skipped', reason: `the site is on your skip list (${blocked})`, answers };

    await g(settle(tabId));
    if (await g(acceptCookies(tabId))) await g(settle(tabId));

    let snap = await g(readyPage(tabId));
    // a password box means the site wants an account: skip before spending an AI call
    if (s.behavior.skipAccounts && snap.fields.some((f) => f.kind === 'password')) {
      return { status: 'skipped', reason: 'it requires an account (sign up / sign in)', answers };
    }
    if (snap.success && !snap.fields.some((f) => f.required && !f.value)) {
      log(`Confirmation page: "${(snap.text.match(/[^.]*(thank|submitted|received|congratulations)[^.]*/i) || [''])[0].slice(0, 120)}"`);
      return { status: 'applied', answers };
    }
    if (snap.already) return { status: 'applied', answers, note: 'already applied' };
    if (snap.captcha && /checkbox|challenge|Turnstile|hCaptcha|verification/i.test(snap.captcha)) {
      if (captchaWaits++ < 2 && (await g(waitForCaptcha(tabId, s, snap.captcha, ctx)))) continue;
      return { status: 'attention', reason: `${snap.captcha} not solved`, answers };
    }

    // read the real choices of custom dropdowns so the AI only picks values the form offers
    if (snap.fields.some((f) => f.kind === 'combobox' && !f.options)) {
      const probed = await g(call(tabId, '__abAgent', 'probeOptions', [], { allFrames: true }).catch(() => []));
      if (probed.some((r) => r.result)) snap = await g(snapshotTab(tabId));
    }

    // 1. rules first: the profile answers the usual questions and the obvious button needs no AI.
    //    If the last rules-only step did not move the page on, let the AI look at the whole form.
    const pageKey = `${snap.url}|${snap.fields.map((f) => f.id).join(',')}|${snap.buttons.map((b) => b.text).join(',')}`;
    const forceAI = pageKey === lastRulesKey || fixedByYou;
    fixedByYou = false;
    const ruleArgs = () => ({ profile, saved, uploadCover: s.behavior.uploadCover, coverLetter: docs.coverLetter, forceAI, lastClick });
    const saved = await savedAnswers();
    let rp = rulePlan(snap, ruleArgs());
    // a real application form: write the tailored resume + cover letter before filling it
    if (!docs.files && rp.page_type === 'application_form' && (rp.needsDocs || snap.fields.length >= 3)) {
      log('  Application form found; preparing the resume and cover letter');
      await g(ensureDocs());
      rp = rulePlan(snap, ruleArgs());
    }

    let plan;
    if (!rp.askAI) {
      plan = { page_type: rp.page_type, summary: rp.summary, actions: rp.actions, next_click: rp.next_click, done: false, needs_human: '' };
      lastRulesKey = pageKey;
      log(`Step ${step} [${plan.page_type}] no AI: ${plan.summary}`);
    } else {
      // 2. the AI only sees the questions the rules could not answer
      setPhase('thinking');
      const aiSnap = { ...snap, fields: rp.aiFields };
      const ai = await g(rpc('bot:plan', {
        snapshot: aiSnap, prefilled: snap.fields.length - rp.aiFields.length, job, profile: aiProfile, resume: docs.resume || baseResume,
        coverLetter: rp.aiFields.some((f) => /cover letter/i.test(`${f.label} ${f.question || ''}`)) ? docs.coverLetter : '',
        history: history.slice(-5), files: { resume: true, cover: !docs.files || !!docs.files.cover }, bidId: docs.bid?.id
      }));
      setPhase('applying');
      lastRulesKey = '';
      const aiIds = new Set(ai.actions.map((a) => a.id));
      plan = { ...ai, actions: [...rp.actions.filter((a) => !aiIds.has(a.id)), ...ai.actions], next_click: ai.next_click || rp.next_click };
      rememberAnswers(answersToSave(snap, ai.actions)).catch(() => {});
      log(`Step ${step} [${plan.page_type}]: ${plan.summary}${rp.actions.length ? ` (+${rp.actions.length} filled by rules)` : ''}`);
    }

    if (plan.page_type === 'requires_linkedin') return { status: 'skipped', reason: 'it can only be applied to through LinkedIn', answers };
    // an "Enter your information" (iCIMS) / "Tell us about yourself" (ADP) step only asks who you are
    // (email, name, phone, maybe a code sent to your email): not an account, so it is filled, not skipped
    const identifyStep = !snap.fields.some((f) => f.kind === 'password')
      && snap.fields.some((f) => f.kind === 'email' || /e-?mail/i.test(`${f.label} ${f.question} ${f.name || ''}`) || /verification code|receive (a |the |your )?code/i.test(`${f.label} ${f.question}`));
    if (s.behavior.skipAccounts && (plan.page_type === 'register' || plan.page_type === 'login') && !identifyStep) {
      return { status: 'skipped', reason: 'it requires an account (sign up / sign in)', answers };
    }
    // never follow a LinkedIn button
    const nextBtn = snap.buttons.find((b) => b.id === plan.next_click);
    if (nextBtn && /linkedin/i.test(`${nextBtn.text} ${nextBtn.href}`)) return { status: 'skipped', reason: 'it wants a LinkedIn sign-in', answers };

    // first real form: write the tailored resume + cover letter now, then plan this page again with them
    if (!docs.files && (NEEDS_DOCS.has(plan.page_type) || plan.actions.some((a) => a.op.startsWith('upload')))) {
      log('  Application form found; preparing the resume and cover letter');
      await g(ensureDocs());
      step--;
      continue;
    }

    if (plan.done || plan.page_type === 'success' || plan.page_type === 'already_applied') return { status: 'applied', answers };
    if (plan.page_type === 'job_closed') return { status: 'attention', reason: 'the posting is closed', answers };
    if (plan.needs_human) {
      if (plan.page_type === 'captcha' && captchaWaits++ < 2 && (await g(waitForCaptcha(tabId, s, 'captcha', ctx)))) continue;
      // only real blockers stop the bot; anything else it works out itself
      if (HARD_STOP.test(`${plan.needs_human} ${plan.page_type}`) || overrides >= 3) return { status: 'attention', reason: plan.needs_human, answers };
      overrides++;
      log(`  Deciding without you (AI wanted help: ${plan.needs_human.slice(0, 120)})`);
      history.push(`You asked for a human ("${plan.needs_human.slice(0, 160)}") but that is not allowed here. Decide yourself: choose the most likely button to move forward (Apply, Autofill with Resume, Create Account, Sign In, Continue, Next) or fill what you can.`);
      if (!plan.actions.length && !plan.next_click) {
        await sleep(2500);
        continue;
      }
    }

    // empty or still-loading page: wait and look again
    if (!plan.actions.length && !plan.next_click) {
      if (++idle > 4) return { status: 'attention', reason: 'the page never showed anything to fill or click', answers };
      log('  Nothing to do yet; waiting for the page');
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

    // never upload into an "Autofill from resume" box when the form has its own resume field
    // (the site would re-type the form from the file, over the answers)
    if (snap.fields.some((f) => f.kind === 'file' && !isAutofillUpload(f))) {
      const autofill = new Set(snap.fields.filter(isAutofillUpload).map((f) => f.id));
      plan.actions = plan.actions.filter((a) => !(a.op.startsWith('upload') && autofill.has(a.id)));
    }

    // carry out the actions, frame by frame, in order
    const results = [];
    for (const a of plan.actions) {
      const { frameId, local } = splitId(a.id);
      ctx.check();
      const r = await call(tabId, '__abAgent', 'act', [[{ ...a, id: local }], docs.files, secrets], { frameId }).catch((err) => [{ ok: false, note: err.message }]);
      results.push({ ...a, ...(r?.[0] || {}) });
      if (a.question && a.value) answers.push({ question: a.question, answer: a.value });
    }
    const failed = results.filter((r) => !r.ok);
    if (failed.length) log(`  ${failed.length} of ${results.length} actions failed: ${failed.slice(0, 3).map((f) => `${f.op} ${f.id} (${f.note})`).join('; ')}`, 'warn');
    // sites parse an uploaded resume (Workday "Autofill with Resume") before Continue works
    if (results.some((r) => r.ok && r.op.startsWith('upload'))) {
      log('  Resume uploaded; waiting for the site to read it');
      await sleep(3000);
      await call(tabId, '__abAgent', 'waitStable', [1500, 15000]).catch(() => {});
    }

    let clicked = '';
    lastClick = plan.next_click || '';
    if (plan.next_click) {
      const btn = snap.buttons.find((b) => b.id === plan.next_click);
      clicked = btn?.text || plan.next_click;
      const isSubmit = plan.page_type === 'review' || /submit|send application|finish/i.test(clicked);
      if (isSubmit && !s.behavior.autoSubmit) return { status: 'attention', reason: 'ready to submit (auto-submit is off): review and press Submit', answers };
      await g(sleep(500 + Math.random() * 700));
      ctx.check();
      const t0 = Date.now();
      const { frameId, local } = splitId(plan.next_click);
      await call(tabId, '__abAgent', 'click', [local], { frameId }).catch(() => {});
      // some sites continue in a new tab
      const newTab = await waitNewTab(tabId, t0, 2500);
      if (newTab) {
        tabs.push(newTab);
        tabId = newTab;
        const nt = await tabExists(newTab);
        if (nt && LINKEDIN.test(nt.pendingUrl || nt.url || '')) return { status: 'skipped', reason: 'it opened LinkedIn', answers };
        await focusTab(tabId);
        log('  Continued in a new tab');
      }
      await sleep(1500);
    }
    history.push(`${plan.page_type} at ${snap.url.slice(0, 100)}: ${results.length} actions${failed.length ? ` (${failed.length} failed: ${failed.map((f) => f.note).join(', ').slice(0, 160)})` : ''}${clicked ? `, clicked "${clicked}"` : ''}${snap.errors.length ? `; page errors: ${snap.errors.join(' | ').slice(0, 200)}` : ''}`);
  }
  return { status: 'attention', reason: `no confirmation after ${progress.step} steps`, answers };
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
  // "Apply to this job" works on Jobright job pages, whichever board the bot runs on
  processJob({ id, url: tab.url.split('?')[0], title: tab.title }, { settings: { ...s, site: 'jobright', filters: { ...s.filters, remoteOnly: false } }, site: SITES.jobright, detailTabId: tabId })
    .finally(finish);
}

// ---------------- messages from popup / options ----------------

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  (async () => {
    switch (msg.type) {
      case 'start': await start(); return { ok: true };
      case 'stop': stop(); return { ok: true };
      case 'skip': skipCurrent(); return { ok: true };
      case 'runSingle': await runSingle(msg.tabId); return { ok: true };
      case 'applyAgain': return { ok: true, ...(await applyAgain(msg.tabId)) };
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
  // 1.2.1 skipped every HiringCafe job as "not remote" (and could not click Apply): let those be tried again
  const done = await processed();
  const keep = Object.fromEntries(Object.entries(done).filter(([id, v]) => !(id.startsWith('hc:') && /skipped|failed/.test(v.status))));
  if (Object.keys(keep).length !== Object.keys(done).length) await chrome.storage.local.set({ done: keep });
  if (await seedProfile()) console.log('[auto-bid] profile loaded from profile.local.json');
});

// A restarted service worker cannot resume a half-done bid; reset to idle.
loadState().then(() => {
  if (state.running) {
    state.running = false;
    log('Service worker restarted; bot stopped. Press Start to continue.', 'warn');
  }
  state.phase = 'idle';
  state.current = null;
  save();
  updateBadge();
});
