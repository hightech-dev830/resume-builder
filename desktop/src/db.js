const fs = require('fs');
const path = require('path');
const { Pool } = require('pg');
const baseResume = require('./baseResume');

// Packaged builds keep the config in %APPDATA%/Resume Builder (main.js sets this);
// in development it sits in the project root.
const CONFIG_PATH = path.join(process.env.RESUME_BUILDER_CONFIG_DIR || path.join(__dirname, '..'), 'db.config.json');

const DEFAULT_CONFIG = {
  host: 'localhost',
  port: 5432,
  user: 'postgres',
  password: 'postgres',
  database: 'resume'
};

function loadConfig() {
  let fileCfg = {};
  if (fs.existsSync(CONFIG_PATH)) {
    fileCfg = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
  } else {
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(DEFAULT_CONFIG, null, 2));
  }
  return {
    ...DEFAULT_CONFIG,
    ...fileCfg,
    host: process.env.PGHOST || fileCfg.host || DEFAULT_CONFIG.host,
    port: Number(process.env.PGPORT || fileCfg.port || DEFAULT_CONFIG.port),
    user: process.env.PGUSER || fileCfg.user || DEFAULT_CONFIG.user,
    password: process.env.PGPASSWORD ?? fileCfg.password ?? DEFAULT_CONFIG.password,
    database: process.env.PGDATABASE || fileCfg.database || DEFAULT_CONFIG.database
  };
}

let pool;

function getPool() {
  if (!pool) pool = new Pool({ ...loadConfig(), max: 5, connectionTimeoutMillis: 5000 });
  return pool;
}

async function saveConfig(cfg) {
  const clean = {
    host: cfg.host || DEFAULT_CONFIG.host,
    port: Number(cfg.port) || DEFAULT_CONFIG.port,
    user: cfg.user || DEFAULT_CONFIG.user,
    password: cfg.password ?? '',
    database: cfg.database || DEFAULT_CONFIG.database
  };
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(clean, null, 2));
  if (pool) {
    const old = pool;
    pool = null;
    await old.end().catch(() => {});
  }
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS settings (
  key        TEXT PRIMARY KEY,
  value      TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS resumes (
  id                 SERIAL PRIMARY KEY,
  company            TEXT NOT NULL DEFAULT '',
  role               TEXT NOT NULL DEFAULT '',
  job_link           TEXT NOT NULL DEFAULT '',
  location           TEXT NOT NULL DEFAULT '',
  salary             TEXT NOT NULL DEFAULT '',
  jd                 TEXT NOT NULL DEFAULT '',
  notes              TEXT NOT NULL DEFAULT '',
  instructions       TEXT NOT NULL DEFAULT '',
  template           TEXT NOT NULL DEFAULT 'professional',
  resume_json        JSONB,
  cover_letter       TEXT NOT NULL DEFAULT '',
  resume_dir         TEXT NOT NULL DEFAULT '',
  pdf_path           TEXT NOT NULL DEFAULT '',
  cover_letter_path  TEXT NOT NULL DEFAULT '',
  status             TEXT NOT NULL DEFAULT 'draft',
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- which AI + prompt produced the resume, so results can be compared per prompt
ALTER TABLE resumes ADD COLUMN IF NOT EXISTS ai_provider TEXT NOT NULL DEFAULT '';
ALTER TABLE resumes ADD COLUMN IF NOT EXISTS ai_model    TEXT NOT NULL DEFAULT '';
ALTER TABLE resumes ADD COLUMN IF NOT EXISTS prompt_used TEXT NOT NULL DEFAULT '';
ALTER TABLE resumes ADD COLUMN IF NOT EXISTS source     TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE resumes ADD COLUMN IF NOT EXISTS source_url TEXT NOT NULL DEFAULT '';
ALTER TABLE resumes ADD COLUMN IF NOT EXISTS apply_url  TEXT NOT NULL DEFAULT '';
ALTER TABLE resumes ADD COLUMN IF NOT EXISTS bot_status TEXT NOT NULL DEFAULT '';
ALTER TABLE resumes ADD COLUMN IF NOT EXISTS bot_log    JSONB;
ALTER TABLE resumes ADD COLUMN IF NOT EXISTS answers    JSONB;
ALTER TABLE resumes ADD COLUMN IF NOT EXISTS applied_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS resumes_source_url_idx ON resumes (source_url);
CREATE INDEX IF NOT EXISTS resumes_bot_status_idx ON resumes (bot_status);

-- tokens and US$ of every AI call
CREATE TABLE IF NOT EXISTS ai_usage (
  id             BIGSERIAL PRIMARY KEY,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  provider       TEXT NOT NULL DEFAULT '',
  model          TEXT NOT NULL DEFAULT '',
  task           TEXT NOT NULL DEFAULT '',
  bid_id         INTEGER,
  input_tokens   INTEGER NOT NULL DEFAULT 0,
  cached_tokens  INTEGER NOT NULL DEFAULT 0,
  output_tokens  INTEGER NOT NULL DEFAULT 0,
  cost_usd       NUMERIC(12, 6)
);
CREATE INDEX IF NOT EXISTS ai_usage_created_at_idx ON ai_usage (created_at DESC);

CREATE INDEX IF NOT EXISTS resumes_created_at_idx ON resumes (created_at DESC);
CREATE INDEX IF NOT EXISTS resumes_company_idx ON resumes (lower(company));
`;

async function init() {
  const p = getPool();
  await p.query(SCHEMA);
  const { rows } = await p.query(`SELECT 1 FROM settings WHERE key = 'base_resume'`);
  if (!rows.length) {
    await setSetting('base_resume', JSON.stringify(baseResume, null, 2));
  }
}

async function getSetting(key, fallback = '') {
  const { rows } = await getPool().query('SELECT value FROM settings WHERE key = $1', [key]);
  return rows.length ? rows[0].value : fallback;
}

async function setSetting(key, value) {
  await getPool().query(
    `INSERT INTO settings (key, value, updated_at) VALUES ($1, $2, now())
     ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()`,
    [key, value]
  );
}

async function getAllSettings() {
  const { rows } = await getPool().query('SELECT key, value FROM settings');
  return Object.fromEntries(rows.map((r) => [r.key, r.value]));
}

const RESUME_FIELDS = [
  'company', 'role', 'job_link', 'location', 'salary', 'jd', 'notes', 'instructions',
  'template', 'resume_json', 'cover_letter', 'resume_dir', 'pdf_path', 'cover_letter_path', 'status',
  'ai_provider', 'ai_model', 'prompt_used'
];

function pickFields(data) {
  const out = {};
  for (const f of RESUME_FIELDS) {
    if (data[f] === undefined) continue;
    out[f] = f === 'resume_json' && data[f] != null ? JSON.stringify(data[f]) : data[f];
  }
  return out;
}

async function createResume(data) {
  const fields = pickFields(data);
  const cols = Object.keys(fields);
  const params = cols.map((_, i) => `$${i + 1}`);
  const { rows } = await getPool().query(
    `INSERT INTO resumes (${cols.join(', ')}) VALUES (${params.join(', ')}) RETURNING *`,
    Object.values(fields)
  );
  return rows[0];
}

async function updateResume(id, data) {
  const fields = pickFields(data);
  const cols = Object.keys(fields);
  if (!cols.length) return getResume(id);
  const sets = cols.map((c, i) => `${c} = $${i + 2}`);
  const { rows } = await getPool().query(
    `UPDATE resumes SET ${sets.join(', ')}, updated_at = now() WHERE id = $1 RETURNING *`,
    [id, ...Object.values(fields)]
  );
  return rows[0];
}

async function getResume(id) {
  const { rows } = await getPool().query('SELECT * FROM resumes WHERE id = $1', [id]);
  return rows[0] || null;
}

// Filters: search text, status, date range (ISO timestamps, from inclusive / to exclusive),
// and paging. limit 0 = all rows. tz is the viewer's IANA zone for the per-row local date.
async function listResumes({ search = '', status = '', from = '', to = '', limit = 50, offset = 0, tz = 'UTC' } = {}) {
  const where = [];
  const params = [];
  const add = (v) => (params.push(v), `$${params.length}`);
  if (search) {
    const s = search.replace(/^#/, '');
    const like = add(`%${s.toLowerCase()}%`);
    const idCond = /^\d+$/.test(s) ? ` OR id = ${add(Number(s))}` : '';
    where.push(`(lower(company) LIKE ${like} OR lower(role) LIKE ${like} OR lower(jd) LIKE ${like}${idCond})`);
  }
  if (status) where.push(`status = ${add(status)}`);
  if (from) where.push(`created_at >= ${add(from)}`);
  if (to) where.push(`created_at < ${add(to)}`);
  const whereSql = where.length ? 'WHERE ' + where.join(' AND ') : '';

  const total = Number((await getPool().query(`SELECT count(*) FROM resumes ${whereSql}`, params)).rows[0].count);
  const tzParam = add(tz);
  let paging = '';
  if (Number(limit) > 0) paging = `LIMIT ${add(Number(limit))} OFFSET ${add(Number(offset) || 0)}`;
  const { rows } = await getPool().query(
    `SELECT id, company, role, job_link, location, salary, template, status, resume_dir, pdf_path, ai_provider, ai_model,
            cover_letter_path, created_at, updated_at,
            to_char(created_at AT TIME ZONE ${tzParam}, 'YYYY-MM-DD') AS local_date
       FROM resumes ${whereSql}
      ORDER BY created_at DESC ${paging}`,
    params
  );
  return { rows, total };
}

// Every day that has bids, newest first, with counts (in the viewer's time zone).
async function listDates(tz = 'UTC') {
  const { rows } = await getPool().query(
    `SELECT to_char(created_at AT TIME ZONE $1, 'YYYY-MM-DD') AS day, count(*)::int AS count
       FROM resumes GROUP BY 1 ORDER BY 1 DESC`,
    [tz]
  );
  return rows;
}

// Previously used values for the Role / Location / Salary dropdowns, most used first.
async function distinctValues() {
  const out = {};
  for (const col of ['role', 'location', 'salary']) {
    const { rows } = await getPool().query(
      `SELECT ${col} AS v FROM resumes WHERE ${col} <> '' GROUP BY ${col} ORDER BY count(*) DESC, max(created_at) DESC LIMIT 50`
    );
    out[col] = rows.map((r) => r.v);
  }
  return out;
}

async function deleteResume(id) {
  await getPool().query('DELETE FROM resumes WHERE id = $1', [id]);
}

async function logUsage(u) {
  await getPool().query(
    'INSERT INTO ai_usage (provider, model, task, bid_id, input_tokens, cached_tokens, output_tokens, cost_usd) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)',
    [u.provider || '', u.model || '', u.task || '', u.bid_id || null, u.input || 0, u.cached || 0, u.output || 0, u.cost_usd]
  );
}

module.exports = {
  logUsage, CONFIG_PATH, loadConfig, saveConfig, init, getSetting, setSetting, getAllSettings,
  createResume, updateResume, getResume, listResumes, listDates, deleteResume, distinctValues
};
