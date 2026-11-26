// Service worker: opens the side panel from the toolbar, runs "fill this page" for the page button and
// the side panel, notices when a filled tab reaches a thank-you page (marks the application applied),
// and refreshes the job feed on a timer.
import { fillTab, tabSucceeded, readJob } from './lib/fill.js';
import * as store from './lib/store.js';
import * as jobs from './lib/jobs.js';
import { keepAlive } from './lib/util.js';

chrome.runtime.onInstalled.addListener(async () => {
  await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
  const s = await store.getSettings();
  chrome.alarms.create('jobs', { periodInMinutes: Math.max(60, (s.jobs.refreshHours || 6) * 60) });
});
chrome.runtime.onStartup.addListener(() => chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {}));

const broadcast = (msg) => chrome.runtime.sendMessage(msg).catch(() => {});

// tabs filled for an application, watched for the confirmation page: { [tabId]: appId }
async function watch(tabId, appId) {
  const { watch: w = {} } = await chrome.storage.session.get('watch');
  w[tabId] = appId;
  await chrome.storage.session.set({ watch: w });
}

async function markApplied(appId, tabId) {
  const app = appId && (await store.getDoc('applications', appId));
  if (!app || app.status === 'applied') return null;
  const tab = tabId ? await chrome.tabs.get(tabId).catch(() => null) : null;
  const saved = await store.put('applications', { ...app, status: 'applied', appliedAt: Date.now(), applyUrl: app.applyUrl || tab?.url || '' });
  chrome.notifications.create(`applied-${appId}`, { type: 'basic', iconUrl: 'icons/icon128.png', title: 'Application submitted', message: `${app.title} at ${app.company} saved as applied.` });
  broadcast({ type: 'app:changed', id: appId });
  return saved;
}

async function runFill(tabId) {
  const res = await keepAlive(fillTab(tabId, { onLog: (text, level) => broadcast({ type: 'fill:log', text, level }) }));
  if (res.app) {
    if (res.status === 'success') await markApplied(res.app.id, tabId);
    else await watch(tabId, res.app.id);
  }
  broadcast({ type: 'fill:done', data: slim(res) });
  return slim(res);
}

// the page button only needs a summary (profiles and applications are large)
const slim = (r) => ({ ...r, profile: r.profile ? { id: r.profile.id, name: r.profile.name } : null, app: r.app ? { id: r.app.id, title: r.app.title, company: r.app.company } : null });

chrome.tabs.onUpdated.addListener(async (tabId, info) => {
  if (info.status !== 'complete') return;
  const { watch: w = {} } = await chrome.storage.session.get('watch');
  const appId = w[tabId];
  if (!appId) return;
  // give single-page apps a moment to show their thank-you message
  setTimeout(async () => {
    if (await tabSucceeded(tabId)) {
      await markApplied(appId, tabId);
      delete w[tabId];
      await chrome.storage.session.set({ watch: w });
      chrome.tabs.sendMessage(tabId, { type: 'fab:toast', text: 'Application submitted\nSaved as applied in Auto Bid.' }).catch(() => {});
    }
  }, 2500);
});
chrome.tabs.onRemoved.addListener(async (tabId) => {
  const { watch: w = {} } = await chrome.storage.session.get('watch');
  if (w[tabId]) {
    delete w[tabId];
    await chrome.storage.session.set({ watch: w });
  }
});

chrome.alarms.onAlarm.addListener(async (a) => {
  if (a.name !== 'jobs' || !(await store.getSession())) return;
  const s = await store.getSettings();
  const cache = await jobs.getCache();
  if (Date.now() - cache.fetchedAt < (s.jobs.refreshHours || 6) * 3600000 - 60000) return;
  await keepAlive(jobs.refresh(s)).then(() => broadcast({ type: 'jobs:changed' })).catch((e) => console.warn('Job refresh failed:', e));
});

const handlers = {
  'fill:sender': (msg, sender) => runFill(sender.tab.id),
  'fill:tab': async (msg) => {
    const tabId = msg.tabId || (await chrome.tabs.query({ active: true, lastFocusedWindow: true }))[0]?.id;
    if (!tabId) throw new Error('No tab to fill.');
    return runFill(tabId);
  },
  'job:read': async (msg) => {
    const tabId = msg.tabId || (await chrome.tabs.query({ active: true, lastFocusedWindow: true }))[0]?.id;
    return readJob(tabId);
  },
  'app:applied': (msg) => markApplied(msg.id, msg.tabId),
  'jobs:refresh': async () => {
    const s = await store.getSettings();
    const cache = await keepAlive(jobs.refresh(s, (text) => broadcast({ type: 'jobs:progress', text })));
    broadcast({ type: 'jobs:changed' });
    return { count: cache.items.length, report: cache.report };
  }
};

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const h = handlers[msg?.type];
  if (!h) return false;
  Promise.resolve()
    .then(() => h(msg, sender))
    .then((data) => sendResponse({ ok: true, data }))
    .catch((err) => sendResponse({ ok: false, error: err.message || String(err) }));
  return true;
});
