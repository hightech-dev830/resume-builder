// Auto Bid service worker: walks the Jobright job list, and for each new remote job posted today
// reads the job, generates a tailored resume + cover letter through the Resume Builder server,
// opens the company application, fills it page by page with the AI planner, and finally
// confirms "Yes, I applied!" on Jobright. Every bid is saved to Supabase by the server.
import { getSettings, getProfile, seedProfile, profileForAI, secretsFor, rpc } from './lib/config.js';

const JOBRIGHT = /^https:\/\/([a-z]+\.)?jobright\.ai\//;
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
    const hit = createdTabs.find((c) => c.opener === openerId && c.t >= since);
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

// ---------------- helpers ----------------

function slug(s, max = 40) {
  return String(s || '').normalize('NFKD').replace(/[^\w\s-]/g, '').trim().replace(/[\s-]+/g, '_').slice(0, max) || 'Untitled';
}

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
    setPhase('scanning', null);
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
        setPhase('waiting_jobs', null);
        while (!stopRequested && Date.now() < at.getTime()) await sleep(3000);
        if (stopRequested) break;
        await refreshList();
      }
      continue;
    }

    await processJob(next, { listId, settings: s });
    if (!stopRequested) await sleep(s.behavior.delaySec * 1000);
  }
  if (state.counts.applied >= s.filters.maxBids) log(`Reached the limit of ${s.filters.maxBids} bids for this run.`);
}

// ---------------- one job ----------------

async function processJob(card, { listId, settings: s, detailTabId }) {
  const label = `${card.title || 'job'} @ ${card.company || '?'}`;
  setPhase('opening', { title: card.title, company: card.company, url: card.url, step: 0 });
  log(`Opening ${label}`);
  const tabs = [];
  let detailId = detailTabId;
  let bid = null;
  let outcome = 'failed';

  try {
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
      tabs.push(detailId);
    }
    await waitComplete(detailId);
    await sleep(2000);
    const detail = { ...card, ...((await jobright(detailId, 'scrapeDetail')) || {}) };
    detail.company = detail.company || card.company;
    detail.title = detail.title || card.title;
    if (s.filters.remoteOnly && !detail.remote && !card.remote) {
      log(`Skipped ${label}: not remote`);
      state.counts.skipped++;
      outcome = 'skipped';
      return;
    }
    state.current = { ...state.current, title: detail.title, company: detail.company, url: detail.url };

    // 2. company application page
    setPhase('opening_apply');
    let applyId = null;
    const t0 = Date.now();
    if (await jobright(detailId, 'clickApply')) applyId = await waitNewTab(detailId, t0, 15000);
    if (!applyId) {
      const cur = await tabExists(detailId);
      if (cur && !JOBRIGHT.test(cur.url)) applyId = detailId; // same-tab redirect
    }
    if (!applyId && detail.applyUrl) applyId = (await chrome.tabs.create({ url: detail.applyUrl, active: true })).id;
    if (!applyId) throw new Error('Could not open the company application page.');
    if (applyId !== detailId) tabs.push(applyId);
    // wait out Jobright's redirect to the real posting
    for (let i = 0; i < 30; i++) {
      const t = await tabExists(applyId);
      if (t && t.url && !JOBRIGHT.test(t.url) && !/^about:|^chrome/.test(t.url)) break;
      await sleep(700);
    }
    await focusTab(applyId);
    await settle(applyId);
    const applyTab = await tabExists(applyId);
    // LinkedIn applications (Easy Apply / sign in with LinkedIn) are skipped
    if (LINKEDIN.test(applyTab.url)) {
      log(`Skipped ${label}: the application is on LinkedIn`);
      state.counts.skipped++;
      outcome = 'skipped';
      return;
    }
    log(`Application page: ${new URL(applyTab.url).host}`);

    // 3. exact job description from the company page
    const page = await call(applyId, '__abAgent', 'pageText').catch(() => null);
    const job = {
      company: detail.company, role: detail.title, location: detail.location || 'Remote (US)', salary: detail.salary || '',
      job_link: applyTab.url,
      jd: [detail.jd, page && page.text.length > 300 ? `\n\n--- From the company posting (${new URL(page.url).host}) ---\n${page.text}` : ''].join('').slice(0, 30000)
    };

    // 4. bid row in Supabase; resume + cover letter only once a form needs them
    const docs = { bid: null, files: null, resume: null, coverLetter: '' };
    const ensureBid = async () => {
      if (docs.bid) return docs.bid;
      docs.bid = await rpc('resumes:create', {
        company: detail.company, role: detail.title, job_link: job.job_link, apply_url: job.job_link,
        location: detail.location || (detail.remote ? 'Remote (US)' : ''), salary: detail.salary || '', jd: job.jd,
        status: 'draft', template: s.behavior.template,
        notes: `Auto-bid from Jobright${detail.match ? ` (match ${detail.match}%)` : ''}. Posted ${detail.posted || '?'}.`,
        source: 'jobright', source_url: detail.url, bot_status: 'applying'
      });
      bid = docs.bid;
      state.current.bidId = bid.id;
      log(`Bid #${bid.id} created for ${label}`);
      return bid;
    };
    const ensureDocs = async () => {
      if (docs.files) return false;
      await ensureBid();
      setPhase('generating');
      const jobForAi = { ...job, bid_id: bid.id };
      log('Tailoring the resume…');
      const tailored = await rpc('ai:tailor', jobForAi);
      log('Writing the cover letter…');
      const letter = await rpc('ai:coverLetter', { resume: tailored.resume, job: jobForAi });
      bid = await rpc('resumes:update', bid.id, { resume_json: tailored.resume, cover_letter: letter, ...tailored.meta });
      bid = await rpc('resumes:exportPdf', bid.id);
      bid = await rpc('resumes:exportCover', bid.id);
      docs.bid = bid;
      const [resumePdf, coverPdf] = await Promise.all([rpc('files:get', bid.pdf_path), rpc('files:get', bid.cover_letter_path)]);
      docs.files = {
        resume: { b64: resumePdf.__b64, name: bid.pdf_path.split('/').pop() },
        cover: s.behavior.uploadCover ? { b64: coverPdf.__b64, name: bid.cover_letter_path.split('/').pop() } : null
      };
      docs.resume = tailored.resume;
      docs.coverLetter = letter;
      log(`Resume and cover letter ready (${tailored.keywords?.length || 0} JD keywords matched)${s.filesDir ? `, saved in ${s.filesDir}/${bid.resume_dir}` : ''}`);
      setPhase('applying');
      return true;
    };

    // 5. fill and submit the application
    setPhase('applying');
    const result = await runApplication(applyId, { job, docs, ensureDocs, settings: s, tabs });
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
      // 7. tell Jobright
      if (await tabExists(detailId)) {
        if (applyId === detailId) {
          await chrome.tabs.update(detailId, { url: detail.url });
          await waitComplete(detailId);
          await sleep(1500);
        }
        await focusTab(detailId);
        const yes = await jobright(detailId, 'confirmApplied', 12000);
        log(yes ? 'Marked as applied on Jobright' : 'Jobright "Did you apply?" dialog did not appear');
      }
    } else if (result.status === 'stopped') {
      outcome = 'stopped';
      await rpc('bot:log', bid.id, 'Stopped by user', { bot_status: 'needs_attention' });
    } else {
      outcome = 'attention';
      state.counts.attention++;
      await rpc('bot:log', bid.id, `Needs attention: ${result.reason}`, { bot_status: 'needs_attention' });
      log(`⚠ ${label}: ${result.reason}. Tab left open for you.`, 'warn');
      await notify('Application needs you', `${detail.title} at ${detail.company}: ${result.reason}`, { tabId: tabs[tabs.length - 1], sticky: true });
    }
  } catch (err) {
    outcome = 'failed';
    state.counts.failed++;
    log(`✗ ${label}: ${err.message}`, 'error');
    if (bid) await rpc('bot:log', bid.id, `Failed: ${err.message}`, { bot_status: 'failed' }).catch(() => {});
  } finally {
    if (bid) {
      const cost = await rpc('usage:forBid', bid.id).catch(() => null);
      if (cost?.calls) log(`  AI for this bid: ${cost.calls} calls, ${cost.tokens.toLocaleString()} tokens, ${cost.unpriced ? 'cost unknown for some models' : `$${cost.cost.toFixed(4)}`}`);
    }
    if (card.id) await markProcessed(card.id, outcome);
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

async function waitForCaptcha(tabId, s, what) {
  setPhase('waiting_human');
  await focusTab(tabId, true);
  await call(tabId, '__abAgent', 'showCaptcha', [], { allFrames: true }).catch(() => {});
  await notify('Captcha: your turn', `Solve the ${what}. The bot continues by itself when it is done.`, { id: `ab-captcha-${tabId}`, tabId, sticky: true });
  log(`Waiting up to ${s.behavior.captchaWaitMin} min for you to solve the ${what}…`, 'warn');
  const end = Date.now() + s.behavior.captchaWaitMin * 60000;
  while (Date.now() < end && !stopRequested) {
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

async function runApplication(startTabId, { job, docs, ensureDocs, settings: s, tabs }) {
  const profile = await getProfile();
  const baseResume = await rpc('base:get').catch(() => null);
  const aiProfile = profileForAI(profile);
  const secrets = secretsFor(profile);
  const history = [];
  const answers = [];
  let tabId = startTabId;
  let lastKey = '';
  let repeats = 0;
  let captchaWaits = 0;
  let overrides = 0; // times the bot decided by itself instead of asking you
  let idle = 0; // pages that looked empty / still loading

  for (let step = 1; step <= s.behavior.stepLimit; step++) {
    if (stopRequested) return { status: 'stopped', answers };
    state.current = { ...state.current, step };
    save();
    const tab = await tabExists(tabId);
    if (!tab) return { status: 'attention', reason: 'the application tab was closed', answers };
    if (LINKEDIN.test(tab.url)) return { status: 'skipped', reason: 'it sends the application to LinkedIn', answers };

    await settle(tabId);
    if (await acceptCookies(tabId)) await settle(tabId);

    const snap = await readyPage(tabId);
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
      if (captchaWaits++ < 2 && (await waitForCaptcha(tabId, s, snap.captcha))) continue;
      return { status: 'attention', reason: `${snap.captcha} not solved`, answers };
    }

    setPhase('thinking');
    const plan = await rpc('bot:plan', {
      snapshot: snap, job, profile: aiProfile, resume: docs.resume || baseResume, coverLetter: docs.coverLetter,
      history: history.slice(-8), files: { resume: true, cover: !docs.files || !!docs.files.cover }, bidId: docs.bid?.id
    });
    setPhase('applying');
    log(`Step ${step} [${plan.page_type}]: ${plan.summary}`);

    if (plan.page_type === 'requires_linkedin') return { status: 'skipped', reason: 'it can only be applied to through LinkedIn', answers };
    if (s.behavior.skipAccounts && (plan.page_type === 'register' || plan.page_type === 'login')) {
      return { status: 'skipped', reason: 'it requires an account (sign up / sign in)', answers };
    }
    // never follow a LinkedIn button
    const nextBtn = snap.buttons.find((b) => b.id === plan.next_click);
    if (nextBtn && /linkedin/i.test(`${nextBtn.text} ${nextBtn.href}`)) return { status: 'skipped', reason: 'it wants a LinkedIn sign-in', answers };

    // first real form: write the tailored resume + cover letter now, then plan this page again with them
    if (!docs.files && (NEEDS_DOCS.has(plan.page_type) || plan.actions.some((a) => a.op.startsWith('upload')))) {
      log('  Application form found; preparing the resume and cover letter');
      await ensureDocs();
      step--;
      continue;
    }

    if (plan.done || plan.page_type === 'success' || plan.page_type === 'already_applied') return { status: 'applied', answers };
    if (plan.page_type === 'job_closed') return { status: 'attention', reason: 'the posting is closed', answers };
    if (plan.needs_human) {
      if (plan.page_type === 'captcha' && captchaWaits++ < 2 && (await waitForCaptcha(tabId, s, 'captcha'))) continue;
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

    // carry out the actions, frame by frame, in order
    const results = [];
    for (const a of plan.actions) {
      const { frameId, local } = splitId(a.id);
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
    if (plan.next_click) {
      const btn = snap.buttons.find((b) => b.id === plan.next_click);
      clicked = btn?.text || plan.next_click;
      const isSubmit = plan.page_type === 'review' || /submit|send application|finish/i.test(clicked);
      if (isSubmit && !s.behavior.autoSubmit) return { status: 'attention', reason: 'ready to submit (auto-submit is off): review and press Submit', answers };
      await sleep(500 + Math.random() * 700);
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
  return { status: 'attention', reason: `no confirmation after ${s.behavior.stepLimit} steps`, answers };
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
  processJob({ id, url: tab.url.split('?')[0], title: tab.title }, { settings: { ...s, filters: { ...s.filters, remoteOnly: false } }, detailTabId: tabId })
    .finally(finish);
}

// ---------------- messages from popup / options ----------------

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  (async () => {
    switch (msg.type) {
      case 'start': await start(); return { ok: true };
      case 'stop': stop(); return { ok: true };
      case 'runSingle': await runSingle(msg.tabId); return { ok: true };
      case 'resetDone': await chrome.storage.local.remove('done'); return { ok: true };
      case 'clearLog': state.log = []; save(); return { ok: true };
      default: return { ok: false, error: 'unknown message' };
    }
  })().then(reply, (err) => reply({ ok: false, error: err.message }));
  return true;
});

chrome.runtime.onInstalled.addListener(async () => {
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
