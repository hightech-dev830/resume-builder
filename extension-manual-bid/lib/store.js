// Data layer. Everything lives in chrome.storage.local first, so the extension works with no backend.
// With Supabase sync turned on (sign-in screen) and signed in, profiles and applications are also written
// to Supabase (tables bid_profiles / bid_applications, supabase/migrations/004_extension_v2.sql) and pulled
// from it on login and refresh, so several PCs share them.
// Never synced: API keys, profile secrets (password, SSN last 4, date of birth) and the resume files
// (they are saved to your Downloads folder).
import * as cloudDb from './supabase.js';
import { sha256Hex, uid } from './util.js';

export const DEFAULT_SETTINGS = {
  // this repo's Supabase project (the same one the web app uses); the publishable key is meant to be public
  supabase: { enabled: false, url: 'https://tlkatzpkwczzzkfnopsz.supabase.co', key: 'sb_publishable_QCxCJoDrl5vJICXVcABfdA_ejKF9q4N' },
  ai: {
    openaiKey: '',
    openaiModel: 'gpt-5-mini',      // answers on forms, cover letters
    resumeEngine: 'deepseek',       // deepseek | chatgpt | openai
    resumeModel: 'gpt-5',           // when resumeEngine = openai
    coverEngine: 'openai',          // openai | deepseek | chatgpt
    autoFixRounds: 1,               // after a resume is generated: ask the chat to fix validation issues this many times
    atsTarget: 95,
    keepChatTab: true,              // leave the ChatGPT / DeepSeek tab open after generating
    chatThread: 'same'              // same: keep using the open chat thread (pinned tab first, no reload) | new: a new chat per job
  },
  jobs: {
    query: 'software engineer',
    adzunaId: '', adzunaKey: '', adzunaCountry: 'us',
    jsearchKey: '',
    sources: { remotive: true, himalayas: true, jobicy: true, arbeitnow: false, remoteok: true, adzuna: true, jsearch: true, boards: true },
    // company job boards (no key needed): greenhouse:<slug>, lever:<slug>, ashby:<slug>, one per line
    boards: [
      'greenhouse:stripe', 'greenhouse:cloudflare', 'greenhouse:datadog', 'greenhouse:gitlab', 'greenhouse:airbnb', 'greenhouse:dropbox',
      'greenhouse:figma', 'greenhouse:discord', 'greenhouse:robinhood', 'greenhouse:mongodb', 'greenhouse:elastic', 'greenhouse:samsara',
      'greenhouse:databricks', 'greenhouse:anthropic', 'greenhouse:vercel', 'greenhouse:grafanalabs', 'greenhouse:pagerduty', 'greenhouse:twilio',
      'greenhouse:asana', 'greenhouse:instacart', 'greenhouse:lyft', 'greenhouse:pinterest', 'greenhouse:reddit', 'greenhouse:duolingo',
      'lever:palantir', 'lever:spotify', 'ashby:openai', 'ashby:ramp', 'ashby:linear', 'ashby:notion', 'ashby:posthog'
    ].join('\n'),
    techOnly: true,
    maxAgeDays: 14,
    refreshHours: 6
  },
  fill: { showButton: true, uploadCover: true, fileType: 'pdf' },
  downloads: { folder: 'AutoBid' }
};

// Local login when Supabase sync is off: jkrjkr / jkrjkr
const LOCAL_LOGIN = '03d2889f545da6239fdb131337ca94c1a8eea82c9b7aa065f9c571d5c838a70d';

const get = async (k, fallback) => (await chrome.storage.local.get(k))[k] ?? fallback;
const set = (k, v) => chrome.storage.local.set({ [k]: v });

function merge(def, cur) {
  const out = { ...def };
  for (const [k, v] of Object.entries(cur || {})) {
    out[k] = v && typeof v === 'object' && !Array.isArray(v) && def[k] && typeof def[k] === 'object' ? merge(def[k], v) : v;
  }
  return out;
}

export async function getSettings() {
  return merge(DEFAULT_SETTINGS, await get('settings', {}));
}

export async function saveSettings(patch) {
  const next = merge(await getSettings(), patch);
  await set('settings', next);
  return next;
}

// ---------- session ----------

export const getSession = () => get('session', null);

export async function login(username, password) {
  const s = await getSettings();
  if (cloudDb.configured(s.supabase)) {
    const auth = await cloudDb.signIn(s.supabase, username, password);
    const sess = { ...auth, user: String(username).split('@')[0], mode: 'supabase' };
    await set('session', sess);
    try {
      await pull();
    } catch (err) {
      // missing tables or policies: say so now instead of failing quietly on every save
      if (err.code === 'PGRST205' || err.code === '42501' || err.status === 404) {
        await chrome.storage.local.remove('session');
        throw err;
      }
      console.warn('First sync failed:', err);
    }
    return sess;
  }
  if ((await sha256Hex(`${username}:${password}`)) !== LOCAL_LOGIN) throw new Error('Invalid username or password');
  const sess = { user: username, mode: 'local', uid: 'local' };
  await set('session', sess);
  return sess;
}

export const logout = () => chrome.storage.local.remove('session');

async function cloud() {
  const s = await getSettings();
  let sess = await getSession();
  if (!sess || sess.mode !== 'supabase' || !cloudDb.configured(s.supabase)) return null;
  if (Date.now() > sess.expiresAt) {
    sess = { ...sess, ...(await cloudDb.refresh(s.supabase, sess)) };
    await set('session', sess);
  }
  return { db: s.supabase, token: sess.accessToken, uid: sess.uid };
}

// ---------- collections: profiles, applications ----------

export const SECRET_KEYS = ['password', 'ssn_last4', 'dob'];
const colKey = (col) => `col:${col}`;

async function localCol(col) {
  return get(colKey(col), {});
}

// what goes to Supabase: no secrets, no local-only fields (names starting with _)
function forCloud(col, doc) {
  const out = {};
  for (const [k, v] of Object.entries(doc)) if (!k.startsWith('_')) out[k] = v;
  if (col === 'profiles' && out.personal) {
    out.personal = { ...out.personal };
    for (const k of SECRET_KEYS) delete out.personal[k];
  }
  return out;
}

export async function list(col) {
  return Object.values(await localCol(col)).sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
}

export async function getDoc(col, id) {
  return (await localCol(col))[id] || null;
}

export async function put(col, doc) {
  const d = { ...doc, id: doc.id || uid(col.slice(0, 2) + '_'), updatedAt: Date.now() };
  d.createdAt = d.createdAt || d.updatedAt;
  const all = await localCol(col);
  all[d.id] = d;
  await set(colKey(col), all);
  try {
    const c = await cloud();
    if (c) await cloudDb.setDoc(c.db, c.token, c.uid, col, d.id, forCloud(col, d));
  } catch (err) {
    console.warn(`Could not save ${col}/${d.id} to Supabase:`, err);
    d._syncError = err.message;
  }
  return d;
}

export async function remove(col, id) {
  const all = await localCol(col);
  delete all[id];
  await set(colKey(col), all);
  const c = await cloud().catch(() => null);
  if (c) await cloudDb.deleteDoc(c.db, c.token, c.uid, col, id).catch((e) => console.warn('Supabase delete failed:', e));
}

// Pulls Supabase into the local cache. Local secrets and local-only fields are kept.
export async function pull() {
  const c = await cloud();
  if (!c) return false;
  for (const col of ['profiles', 'applications']) {
    const remote = await cloudDb.listDocs(c.db, c.token, c.uid, col);
    const local = await localCol(col);
    const next = {};
    for (const r of remote) {
      const l = local[r.id] || {};
      const keep = Object.fromEntries(Object.entries(l).filter(([k]) => k.startsWith('_')));
      next[r.id] = { ...r, ...keep };
      if (col === 'profiles') next[r.id].personal = { ...(r.personal || {}), ...Object.fromEntries(SECRET_KEYS.map((k) => [k, l.personal?.[k] || ''])) };
    }
    // local docs Supabase does not have yet (made offline or before sync was on): push them up
    for (const [id, l] of Object.entries(local)) {
      if (next[id]) continue;
      next[id] = l;
      await cloudDb.setDoc(c.db, c.token, c.uid, col, id, forCloud(col, l)).catch((e) => console.warn('Supabase upload failed:', e));
    }
    await set(colKey(col), next);
  }
  return true;
}

// ---------- local-only data ----------

// tailored resume + generated files of one application (kept on this PC)
export const getResume = (appId) => get(`resume:${appId}`, null);
export const saveResume = (appId, r) => set(`resume:${appId}`, { ...r, t: Date.now() });
export const getFiles = (appId) => get(`files:${appId}`, {});
export async function saveFile(appId, kind, file) {
  const f = await getFiles(appId);
  f[kind] = file;
  await set(`files:${appId}`, f);
}
export async function dropLocal(appId) {
  await chrome.storage.local.remove([`resume:${appId}`, `files:${appId}`]);
}

// what the side panel is working on: the FAB uses it to know which profile / application to fill
export const getActive = () => get('active', { profileId: '', appId: '' });
export async function setActive(patch) {
  const next = { ...(await getActive()), ...patch };
  await set('active', next);
  return next;
}

// answers the AI gave on earlier forms, per profile (reused without AI next time)
export const getSaved = (profileId) => get(`answers:${profileId}`, {});
export async function addSaved(profileId, add) {
  if (!add || !Object.keys(add).length) return;
  await set(`answers:${profileId}`, { ...(await getSaved(profileId)), ...add });
}
export const getLearned = (profileId) => get(`learned:${profileId}`, []);
export const setLearned = (profileId, l) => set(`learned:${profileId}`, l);
