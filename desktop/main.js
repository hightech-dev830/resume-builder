const fs = require('fs');
const path = require('path');
const { app, BrowserWindow, ipcMain, shell, Menu } = require('electron');
if (app.isPackaged) process.env.RESUME_BUILDER_CONFIG_DIR = app.getPath('userData');
const db = require('./src/db');
const ai = require('./src/ai');
const templates = require('./src/templates');
const { htmlToPdf } = require('./src/pdf');
const seedResume = require('./src/baseResume');

const LOGIN = { username: 'jkr', password: 'jkr' };
const DEFAULT_OUTPUT_DIR = 'G:/Document/Resume';

let win;
let authed = false;

function createWindow() {
  win = new BrowserWindow({
    width: 1500,
    height: 940,
    minWidth: 1100,
    minHeight: 700,
    title: 'Resume Builder',
    backgroundColor: '#eef1f6',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      plugins: true // built-in PDF viewer for the "Exact PDF" preview
    }
  });
  Menu.setApplicationMenu(null);
  win.webContents.on('console-message', (_e, level, message, line, source) => {
    if (level >= 2) console.error(`[renderer] ${message} (${path.basename(source || '')}:${line})`);
  });
  win.loadFile(path.join(__dirname, 'renderer', 'login.html'));
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

app.whenReady().then(createWindow);
app.on('window-all-closed', () => app.quit());

// ---------- helpers ----------

function handle(channel, fn, { requireAuth = true } = {}) {
  ipcMain.handle(channel, async (_e, ...args) => {
    try {
      if (requireAuth && !authed) throw new Error('Not logged in');
      return { ok: true, data: await fn(...args) };
    } catch (err) {
      console.error(`[${channel}]`, err);
      return { ok: false, error: err.message || String(err) };
    }
  });
}

function slug(s, max = 40) {
  return String(s || '')
    .normalize('NFKD')
    .replace(/[^\w\s-]/g, '')
    .trim()
    .replace(/[\s-]+/g, '_')
    .slice(0, max) || 'Untitled';
}

function localDate(d) {
  const dt = new Date(d);
  const p = (n) => String(n).padStart(2, '0');
  return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())}`;
}

async function getBaseResume() {
  const raw = await db.getSetting('base_resume', '');
  try {
    return raw ? JSON.parse(raw) : seedResume;
  } catch {
    return seedResume;
  }
}

// Active AI provider config from Settings; `over` lets the Settings page test unsaved values.
async function aiConfig(over = {}) {
  const s = await db.getAllSettings();
  const provider = over.provider || s.ai_provider || 'anthropic';
  const keyName = provider === 'openai' ? 'openai_api_key' : 'claude_api_key';
  const modelName = provider === 'openai' ? 'openai_model' : 'claude_model';
  return {
    provider,
    apiKey: over.apiKey ?? s[keyName] ?? '',
    model: over.model || s[modelName] || ai.PROVIDERS[provider].defaultModel,
    proxy: over.proxy ?? (s.proxy_enabled === '0' ? '' : s.claude_proxy || '')
  };
}

async function getPrompt(kind) {
  const key = kind === 'cover' ? 'prompt_cover' : 'prompt_resume';
  const fallback = kind === 'cover' ? ai.DEFAULT_COVER_PROMPT : ai.DEFAULT_RESUME_PROMPT;
  return (await db.getSetting(key, '')).trim() || fallback;
}

// <output>/<YYYY-MM-DD>/<id>_<Company>/ — created on first export, reused after.
async function ensureResumeDir(row) {
  if (row.resume_dir && fs.existsSync(row.resume_dir)) return row.resume_dir;
  const outRoot = (await db.getSetting('output_dir', '')) || DEFAULT_OUTPUT_DIR;
  const dir = path.join(outRoot, localDate(row.created_at), `${row.id}_${slug(row.company)}`);
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function fileBase(row) {
  const name = slug(row.resume_json?.basics?.name || 'Resume');
  return `${name}_${slug(row.company)}`;
}

function writeSidecars(dir, row) {
  fs.writeFileSync(path.join(dir, 'resume.json'), JSON.stringify(row.resume_json, null, 2));
  const info = [
    `Resume ID: ${row.id}`,
    `Company:   ${row.company}`,
    `Role:      ${row.role}`,
    `Location:  ${row.location}`,
    `Salary:    ${row.salary}`,
    `Job link:  ${row.job_link}`,
    `Template:  ${row.template}`,
    `AI:        ${row.ai_model ? `${row.ai_provider} / ${row.ai_model}` : 'none (base profile)'}`,
    `Created:   ${new Date(row.created_at).toLocaleString()}`,
    '',
    'Notes:',
    row.notes || '',
    '',
    '---------------- JOB DESCRIPTION ----------------',
    row.jd || ''
  ].join('\n');
  fs.writeFileSync(path.join(dir, 'job.txt'), info);
}

// ---------- auth & db config (available before login) ----------

handle('auth:login', async ({ username, password }) => {
  if (username !== LOGIN.username || password !== LOGIN.password) {
    throw new Error('Invalid username or password');
  }
  try {
    await db.init();
  } catch (e) {
    const err = new Error(`Database connection failed: ${e.message}`);
    throw err;
  }
  authed = true;
  await win.loadFile(path.join(__dirname, 'renderer', 'app.html'));
  return true;
}, { requireAuth: false });

handle('auth:logout', async () => {
  authed = false;
  await win.loadFile(path.join(__dirname, 'renderer', 'login.html'));
  return true;
});

handle('db:getConfig', async () => db.loadConfig(), { requireAuth: false });
handle('db:saveConfig', async (cfg) => {
  await db.saveConfig(cfg);
  await db.init();
  return true;
}, { requireAuth: false });

// ---------- settings ----------

handle('settings:get', async () => {
  const s = await db.getAllSettings();
  return {
    ai_provider: s.ai_provider || 'anthropic',
    claude_api_key: s.claude_api_key || '',
    claude_model: s.claude_model || ai.PROVIDERS.anthropic.defaultModel,
    openai_api_key: s.openai_api_key || '',
    openai_model: s.openai_model || '',
    claude_proxy: s.claude_proxy || '',
    proxy_enabled: s.proxy_enabled === '0' ? '0' : '1',
    prompt_resume: (s.prompt_resume || '').trim() || ai.DEFAULT_RESUME_PROMPT,
    prompt_cover: (s.prompt_cover || '').trim() || ai.DEFAULT_COVER_PROMPT,
    output_dir: s.output_dir || DEFAULT_OUTPUT_DIR,
    default_template: s.default_template || 'kevin',
    base_resume: s.base_resume || JSON.stringify(seedResume, null, 2)
  };
});

handle('settings:save', async (values) => {
  const allowed = ['ai_provider', 'claude_api_key', 'claude_model', 'openai_api_key', 'openai_model', 'claude_proxy', 'proxy_enabled',
    'prompt_resume', 'prompt_cover', 'output_dir', 'default_template', 'base_resume'];
  for (const [k, v] of Object.entries(values || {})) {
    if (!allowed.includes(k)) continue;
    if (k === 'base_resume') JSON.parse(v); // validate before saving
    if (k === 'claude_proxy') ai.parseProxy(v);
    await db.setSetting(k, v);
  }
  return true;
});

handle('settings:resetBase', async () => {
  const json = JSON.stringify(seedResume, null, 2);
  await db.setSetting('base_resume', json);
  return json;
});

handle('settings:defaultPrompts', async () => ({ resume: ai.DEFAULT_RESUME_PROMPT, cover: ai.DEFAULT_COVER_PROMPT }));
handle('settings:testKey', async (over) => ai.testKey(await aiConfig(over)));
handle('settings:listModels', async (over) => ai.listModels(await aiConfig(over)));
handle('settings:testProxy', async (proxy, provider) => ai.testProxy(proxy, provider));

// ---------- templates & rendering ----------

handle('templates:list', async () => templates.listTemplates());
handle('base:get', async () => getBaseResume());
handle('render:resumeHtml', async (resume, tpl) => templates.renderResumeHtml(resume, tpl));
handle('render:coverHtml', async (resume, letter, tpl, job) => templates.renderCoverLetterHtml(resume, letter, tpl, job));
handle('render:resumePdf', async (resume, tpl) => htmlToPdf(templates.renderResumeHtml(resume, tpl)));
handle('render:coverPdf', async (resume, letter, tpl, job) =>
  htmlToPdf(templates.renderCoverLetterHtml(resume, letter, tpl, job)));

// ---------- AI ----------

handle('ai:tailor', async (job) => {
  const cfg = await aiConfig();
  const prompt = await getPrompt('resume');
  const out = await ai.tailorResume({ cfg, prompt, base: await getBaseResume(), job });
  return { ...out, meta: { ai_provider: cfg.provider, ai_model: cfg.model, prompt_used: prompt } };
});

handle('ai:coverLetter', async ({ resume, job, instructions }) => {
  const cfg = await aiConfig();
  const prompt = await getPrompt('cover');
  return ai.generateCoverLetter({ cfg, prompt, resume: resume || (await getBaseResume()), job, instructions });
});

// ---------- resumes (bids) ----------

handle('resumes:list', async (filter) => db.listResumes(filter));
handle('resumes:dates', async (tz) => db.listDates(tz));

// Role / Location / Salary dropdowns: values you added yourself + values used in past bids.
const OPTION_KEYS = ['role', 'location', 'salary'];

async function getCustomOptions() {
  try {
    return { role: [], location: [], salary: [], ...JSON.parse(await db.getSetting('field_options', '{}')) };
  } catch {
    return { role: [], location: [], salary: [] };
  }
}

handle('options:get', async () => ({ custom: await getCustomOptions(), used: await db.distinctValues() }));

handle('options:add', async (key, value) => {
  if (!OPTION_KEYS.includes(key)) throw new Error('Unknown field ' + key);
  const v = String(value || '').trim();
  if (!v) throw new Error('Value is empty');
  const opts = await getCustomOptions();
  if (!opts[key].some((x) => x.toLowerCase() === v.toLowerCase())) opts[key].unshift(v);
  await db.setSetting('field_options', JSON.stringify(opts));
  return opts;
});

handle('options:remove', async (key, value) => {
  if (!OPTION_KEYS.includes(key)) throw new Error('Unknown field ' + key);
  const opts = await getCustomOptions();
  opts[key] = opts[key].filter((x) => x !== value);
  await db.setSetting('field_options', JSON.stringify(opts));
  return opts;
});
handle('resumes:get', async (id) => db.getResume(id));
handle('resumes:create', async (data) => db.createResume(data));
handle('resumes:update', async (id, data) => db.updateResume(id, data));
handle('resumes:delete', async (id) => db.deleteResume(id));

handle('resumes:exportPdf', async (id) => {
  let row = await db.getResume(id);
  if (!row) throw new Error(`Resume #${id} not found`);
  if (!row.resume_json) throw new Error('Nothing to export yet. Generate or load resume content first.');
  const dir = await ensureResumeDir(row);
  const pdfPath = path.join(dir, `${fileBase(row)}_Resume.pdf`);
  fs.writeFileSync(pdfPath, await htmlToPdf(templates.renderResumeHtml(row.resume_json, row.template)));
  row = await db.updateResume(id, { resume_dir: dir, pdf_path: pdfPath, status: row.status === 'draft' ? 'generated' : row.status });
  writeSidecars(dir, row);
  return row;
});

handle('resumes:exportCover', async (id) => {
  let row = await db.getResume(id);
  if (!row) throw new Error(`Resume #${id} not found`);
  if (!row.cover_letter.trim()) throw new Error('Cover letter is empty.');
  const resume = row.resume_json || (await getBaseResume());
  const dir = await ensureResumeDir(row);
  const coverPath = path.join(dir, `${fileBase({ ...row, resume_json: resume })}_Cover_Letter.pdf`);
  const html = templates.renderCoverLetterHtml(resume, row.cover_letter, row.template, row);
  fs.writeFileSync(coverPath, await htmlToPdf(html));
  fs.writeFileSync(path.join(dir, 'cover_letter.txt'), row.cover_letter);
  row = await db.updateResume(id, { resume_dir: dir, cover_letter_path: coverPath });
  return row;
});

// ---------- shell ----------

handle('shell:openPath', async (p) => {
  if (!p || !fs.existsSync(p)) throw new Error('File or folder not found: ' + p);
  const err = await shell.openPath(p);
  if (err) throw new Error(err);
  return true;
});
handle('shell:showItem', async (p) => {
  if (!p || !fs.existsSync(p)) throw new Error('File not found: ' + p);
  shell.showItemInFolder(p);
  return true;
});
handle('shell:openExternal', async (url) => {
  if (!/^https?:\/\//i.test(url || '')) throw new Error('Not a web link');
  await shell.openExternal(url);
  return true;
});
