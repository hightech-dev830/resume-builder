// Supabase data layer with the same functions as the desktop src/db.js.
const { createClient } = require('@supabase/supabase-js');
const { env, requireEnv } = require('./env');
const baseResume = require('./shared/baseResume');

let client;

// Supabase calls retry on dropped connections ("fetch failed", "terminated", resets).
// Inserts (POST) are retried only when the request never reached the server, so no duplicate rows.
const NEVER_SENT = /ECONNREFUSED|ENOTFOUND|EAI_AGAIN|ETIMEDOUT|UND_ERR_CONNECT|connect/i;

async function retryingFetch(input, init = {}) {
  const method = String(init.method || 'GET').toUpperCase();
  for (let attempt = 1; ; attempt++) {
    try {
      return await fetch(input, init);
    } catch (err) {
      const why = `${err.message} ${err.cause?.code || ''} ${err.cause?.message || ''}`;
      const safe = method !== 'POST' || NEVER_SENT.test(why);
      if (attempt >= 4 || !safe || init.signal?.aborted) throw err;
      await new Promise((r) => setTimeout(r, 400 * 2 ** (attempt - 1)));
    }
  }
}

function sb() {
  if (!client) {
    const key = env('SUPABASE_SECRET_KEY') || env('SUPABASE_SERVICE_ROLE_KEY');
    if (!key) throw new Error('SUPABASE_SECRET_KEY is not set. The server needs the secret key (Supabase → Project Settings → API keys).');
    client = createClient(requireEnv('SUPABASE_URL'), key, { auth: { persistSession: false, autoRefreshToken: false }, global: { fetch: retryingFetch } });
  }
  return client;
}

function check({ data, error }) {
  if (error) {
    const hint = error.code === 'PGRST205' ? ' Run web/supabase/schema.sql in the Supabase SQL editor first.' : '';
    throw new Error(`Supabase: ${error.message}${hint}`);
  }
  return data;
}

async function init() {
  const rows = check(await sb().from('settings').select('key').eq('key', 'base_resume'));
  if (!rows.length) await setSetting('base_resume', JSON.stringify(baseResume, null, 2));
}

async function getSetting(key, fallback = '') {
  const rows = check(await sb().from('settings').select('value').eq('key', key));
  return rows.length ? rows[0].value ?? fallback : fallback;
}

async function setSetting(key, value) {
  check(await sb().from('settings').upsert({ key, value, updated_at: new Date().toISOString() }));
}

async function getAllSettings() {
  const rows = check(await sb().from('settings').select('key, value'));
  return Object.fromEntries(rows.map((r) => [r.key, r.value]));
}

const RESUME_FIELDS = [
  'company', 'role', 'job_link', 'location', 'salary', 'jd', 'notes', 'instructions',
  'template', 'resume_json', 'cover_letter', 'resume_dir', 'pdf_path', 'cover_letter_path', 'status',
  'ai_provider', 'ai_model', 'prompt_used'
];

// Written by the Chrome auto-bid extension (supabase/migrations/002_auto_bid.sql adds the columns).
const BOT_FIELDS = ['source', 'source_url', 'apply_url', 'bot_status', 'bot_log', 'answers', 'applied_at'];
let botColumns = true;

function pickFields(data) {
  const out = {};
  for (const f of [...RESUME_FIELDS, ...(botColumns ? BOT_FIELDS : [])]) if (data[f] !== undefined) out[f] = data[f];
  return out;
}

// Before the migration runs, bot fields are kept as a "[auto-bid]" text block at the end of notes
// (merged with what is already there), so nothing is lost. Logs and answers need the columns.
const NOTE_FIELDS = BOT_FIELDS.filter((f) => f !== 'bot_log' && f !== 'answers');

function botNotes(data, notes = '') {
  if (!NOTE_FIELDS.some((f) => data[f] !== undefined)) return undefined;
  const text = String(notes || '');
  const at = text.indexOf('[auto-bid]');
  const kept = new Map(at < 0 ? [] : text.slice(at).split('\n').slice(1).map((l) => /^(\w+): (.*)$/.exec(l)).filter(Boolean).map((m) => [m[1], m[2]]));
  for (const f of NOTE_FIELDS) if (data[f] !== undefined) kept.set(f, data[f]);
  return `${(at < 0 ? text : text.slice(0, at)).trim()}\n\n[auto-bid]\n${[...kept].map(([k, v]) => `${k}: ${v}`).join('\n')}`.trim();
}

const missingColumn = (error) => error && (error.code === 'PGRST204' || /column .* does not exist|Could not find the '.*' column/i.test(error.message));

// Without the columns: plain fields, plus the bot fields folded into notes.
function fallbackFields(data, currentNotes) {
  const fields = pickFields(data);
  const n = botNotes(data, data.notes ?? currentNotes);
  if (n !== undefined) fields.notes = n;
  return fields;
}

async function createResume(data) {
  const insert = (fields) => sb().from('resumes').insert(fields).select().single();
  if (botColumns) {
    const res = await insert(pickFields(data));
    if (!missingColumn(res.error)) return check(res);
    noBotColumns();
  }
  return check(await insert(fallbackFields(data, '')));
}

async function updateResume(id, data) {
  const update = (fields) => sb().from('resumes').update({ ...fields, updated_at: new Date().toISOString() }).eq('id', id).select().single();
  if (botColumns) {
    const fields = pickFields(data);
    if (!Object.keys(fields).length) return getResume(id);
    const res = await update(fields);
    if (!missingColumn(res.error)) return check(res);
    noBotColumns();
  }
  const touchesNotes = data.notes !== undefined || NOTE_FIELDS.some((f) => data[f] !== undefined);
  const fields = fallbackFields(data, touchesNotes ? (await getResume(id))?.notes : '');
  if (!Object.keys(fields).length) return getResume(id);
  return check(await update(fields));
}

function noBotColumns() {
  botColumns = false;
  console.warn('resumes has no auto-bid columns yet; run supabase/migrations/002_auto_bid.sql. Keeping bot fields in notes for now.');
}

// Which of these job-board links already have a bid (so the bot never applies twice).
async function findBySourceUrls(urls) {
  if (!urls.length) return [];
  if (botColumns) {
    const res = await sb().from('resumes').select('id, source_url, bot_status').in('source_url', urls);
    if (!missingColumn(res.error)) return check(res);
    botColumns = false;
  }
  const rows = check(await sb().from('resumes').select('id, notes').ilike('notes', '%[auto-bid]%').order('created_at', { ascending: false }).limit(5000));
  return rows.map((r) => ({ id: r.id, source_url: /source_url: (\S+)/.exec(r.notes)?.[1], bot_status: /bot_status: (\S+)/.exec(r.notes)?.[1] }))
    .filter((r) => urls.includes(r.source_url));
}

// Bids made by the auto-bid extension, newest first, for its History page.
async function listBotBids(limit = 1000) {
  const n = Math.min(Math.max(Number(limit) || 1000, 1), 5000);
  if (botColumns) {
    const res = await sb().from('resumes')
      .select('id, company, role, location, salary, job_link, apply_url, source, source_url, bot_status, status, created_at, applied_at')
      .neq('source', 'manual').order('created_at', { ascending: false }).limit(n);
    if (!missingColumn(res.error)) return check(res);
    botColumns = false;
  }
  const rows = check(await sb().from('resumes').select('id, company, role, location, salary, job_link, status, notes, created_at')
    .ilike('notes', '%[auto-bid]%').order('created_at', { ascending: false }).limit(n));
  return rows.map(({ notes, ...r }) => ({
    ...r, source: /source: (\S+)/.exec(notes)?.[1] || '', source_url: /source_url: (\S+)/.exec(notes)?.[1] || '',
    bot_status: /bot_status: (\S+)/.exec(notes)?.[1] || ''
  }));
}

async function getResume(id) {
  const rows = check(await sb().from('resumes').select('*').eq('id', id));
  return rows[0] || null;
}

// "YYYY-MM-DD" of a timestamp in the viewer's time zone.
function localDay(ts, tz) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: tz || 'UTC', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(ts));
}

const LIST_COLUMNS = 'id, company, role, job_link, location, salary, template, status, resume_dir, pdf_path, ai_provider, ai_model, cover_letter_path, created_at, updated_at';

async function listResumes({ search = '', status = '', from = '', to = '', limit = 50, offset = 0, tz = 'UTC' } = {}) {
  let q = sb().from('resumes').select(LIST_COLUMNS, { count: 'exact' });
  if (search) {
    const s = search.replace(/^#/, '').replace(/[,()]/g, ' ').trim();
    const like = `%${s}%`;
    const ors = [`company.ilike.${like}`, `role.ilike.${like}`, `jd.ilike.${like}`];
    if (/^\d+$/.test(s)) ors.push(`id.eq.${s}`);
    q = q.or(ors.join(','));
  }
  if (status) q = q.eq('status', status);
  if (from) q = q.gte('created_at', from);
  if (to) q = q.lt('created_at', to);
  q = q.order('created_at', { ascending: false });
  if (Number(limit) > 0) q = q.range(Number(offset) || 0, (Number(offset) || 0) + Number(limit) - 1);
  const { data, error, count } = await q;
  check({ data, error });
  return { rows: data.map((r) => ({ ...r, local_date: localDay(r.created_at, tz) })), total: count ?? data.length };
}

// Every day that has bids, newest first, with counts (in the viewer's time zone).
async function listDates(tz = 'UTC') {
  const rows = check(await sb().from('resumes').select('created_at').order('created_at', { ascending: false }).limit(20000));
  const counts = new Map();
  for (const r of rows) {
    const d = localDay(r.created_at, tz);
    counts.set(d, (counts.get(d) || 0) + 1);
  }
  return [...counts].map(([day, count]) => ({ day, count }));
}

// Previously used values for the Role / Location / Salary dropdowns, most used first.
async function distinctValues() {
  const rows = check(await sb().from('resumes').select('role, location, salary, created_at').order('created_at', { ascending: false }).limit(20000));
  const out = {};
  for (const col of ['role', 'location', 'salary']) {
    const stats = new Map();
    rows.forEach((r, i) => {
      const v = r[col];
      if (!v) return;
      const s = stats.get(v) || { count: 0, first: i };
      s.count++;
      stats.set(v, s);
    });
    out[col] = [...stats].sort((a, b) => b[1].count - a[1].count || a[1].first - b[1].first).slice(0, 50).map(([v]) => v);
  }
  return out;
}

async function deleteResume(id) {
  check(await sb().from('resumes').delete().eq('id', id));
}

module.exports = {
  sb, init, getSetting, setSetting, getAllSettings,
  createResume, updateResume, getResume, listResumes, listDates, deleteResume, distinctValues, findBySourceUrls, listBotBids
};
