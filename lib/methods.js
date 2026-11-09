// RPC methods for the web app. Names and behaviour match the desktop IPC handlers in
// ../main.js, so the shared UI (public/app.js) works unchanged.
const { env } = require('./env');
const db = require('./db');
const storage = require('./storage');
const ai = require('./shared/ai');
const templates = require('./shared/templates');
const seedResume = require('./shared/baseResume');
const { htmlToPdf } = require('./pdf');
const agent = require('./agent');
const { webFonts, webFontsInline } = require('./fonts');

const OPTION_KEYS = ['role', 'location', 'salary'];

// Same HTML as the desktop app, with web fonts swapped in.
const resumeHtml = (resume, tpl) => webFonts(templates.renderResumeHtml(resume, tpl));
const coverHtml = (resume, letter, tpl, job) => webFonts(templates.renderCoverLetterHtml(resume, letter, tpl, job));
// PDFs embed the fonts so rendering needs no network.
const resumePdf = async (resume, tpl) => htmlToPdf(await webFontsInline(templates.renderResumeHtml(resume, tpl)));
const coverPdf = async (resume, letter, tpl, job) => htmlToPdf(await webFontsInline(templates.renderCoverLetterHtml(resume, letter, tpl, job)));

function slug(s, max = 40) {
  return String(s || '')
    .normalize('NFKD')
    .replace(/[^\w\s-]/g, '')
    .trim()
    .replace(/[\s-]+/g, '_')
    .slice(0, max) || 'Untitled';
}

// Folder dates use the owner's time zone (Vercel runs in UTC).
function localDate(d) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: env('APP_TIMEZONE', 'America/Los_Angeles'), year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(d));
}

async function getBaseResume() {
  const raw = await db.getSetting('base_resume', '');
  try {
    return raw ? JSON.parse(raw) : seedResume;
  } catch {
    return seedResume;
  }
}

async function aiConfig(over = {}) {
  const s = await db.getAllSettings();
  const provider = over.provider || s.ai_provider || 'anthropic';
  const keyName = ai.KEY_SETTING[provider] || 'claude_api_key';
  const modelName = ai.MODEL_SETTING[provider] || 'claude_model';
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

const resumeDir = (row) => row.resume_dir || `${localDate(row.created_at)}/${row.id}_${slug(row.company)}`;
const fileBase = (row) => `${slug(row.resume_json?.basics?.name || 'Resume')}_${slug(row.company)}`;

function jobInfo(row) {
  return [
    `Resume ID: ${row.id}`,
    `Company:   ${row.company}`,
    `Role:      ${row.role}`,
    `Location:  ${row.location}`,
    `Salary:    ${row.salary}`,
    `Job link:  ${row.job_link}`,
    `Template:  ${row.template}`,
    `AI:        ${row.ai_model ? `${row.ai_provider} / ${row.ai_model}` : 'none (base profile)'}`,
    `Created:   ${new Date(row.created_at).toLocaleString('en-US', { timeZone: env('APP_TIMEZONE', 'America/Los_Angeles') })}`,
    '',
    'Notes:',
    row.notes || '',
    '',
    '---------------- JOB DESCRIPTION ----------------',
    row.jd || ''
  ].join('\n');
}

async function getCustomOptions() {
  try {
    return { role: [], location: [], salary: [], ...JSON.parse(await db.getSetting('field_options', '{}')) };
  } catch {
    return { role: [], location: [], salary: [] };
  }
}

const SETTING_KEYS = ['ai_provider', 'claude_api_key', 'claude_model', 'openai_api_key', 'openai_model', 'gemini_api_key', 'gemini_model', 'claude_proxy', 'proxy_enabled',
  'prompt_resume', 'prompt_cover', 'output_dir', 'default_template', 'base_resume', 'agent_model', 'local_output_dir'];

const methods = {
  // ---------- db info ----------
  'db:getConfig': async () => {
    const host = new URL(env('SUPABASE_URL', 'https://unknown.supabase.co')).host;
    const dir = await storage.localDir();
    const files = dir ? `Bid files are saved in ${dir}.` : `PDFs are kept in the “${storage.bucket()}” storage bucket.`;
    return { label: `Connected to Supabase (${host}). ${files}`, host, user: 'supabase', port: '', database: 'postgres' };
  },

  // ---------- settings ----------
  'settings:get': async () => {
    const s = await db.getAllSettings();
    return {
      ai_provider: s.ai_provider || 'anthropic',
      claude_api_key: s.claude_api_key || '',
      claude_model: s.claude_model || ai.PROVIDERS.anthropic.defaultModel,
      openai_api_key: s.openai_api_key || '',
      openai_model: s.openai_model || '',
      gemini_api_key: s.gemini_api_key || '',
      gemini_model: s.gemini_model || '',
      claude_proxy: s.claude_proxy || '',
      proxy_enabled: s.proxy_enabled === '0' ? '0' : '1',
      prompt_resume: (s.prompt_resume || '').trim() || ai.DEFAULT_RESUME_PROMPT,
      prompt_cover: (s.prompt_cover || '').trim() || ai.DEFAULT_COVER_PROMPT,
      output_dir: s.output_dir || 'G:/Document/Resume',
      default_template: s.default_template || 'kevin',
      agent_model: s.agent_model || '',
      // local folder for bid files when the server runs on this PC ('' = Supabase Storage)
      local_output_dir: process.env.VERCEL ? '' : s.local_output_dir ?? storage.DEFAULT_LOCAL_DIR,
      files_location: process.env.VERCEL ? 'supabase' : 'local',
      base_resume: s.base_resume || JSON.stringify(seedResume, null, 2)
    };
  },
  'settings:save': async (values) => {
    for (const [k, v] of Object.entries(values || {})) {
      if (!SETTING_KEYS.includes(k)) continue;
      if (k === 'base_resume') JSON.parse(v);
      if (k === 'claude_proxy') ai.parseProxy(v);
      await db.setSetting(k, v);
    }
    storage.resetCache();
    return true;
  },
  'settings:resetBase': async () => {
    const json = JSON.stringify(seedResume, null, 2);
    await db.setSetting('base_resume', json);
    return json;
  },
  'settings:defaultPrompts': async () => ({ resume: ai.DEFAULT_RESUME_PROMPT, cover: ai.DEFAULT_COVER_PROMPT }),
  'settings:testKey': async (over) => ai.testKey(await aiConfig(over)),
  'settings:listModels': async (over) => ai.listModels(await aiConfig(over)),
  'settings:testProxy': async (proxy, provider) => ai.testProxy(proxy, provider),

  // ---------- templates & rendering ----------
  'templates:list': async () => templates.listTemplates(),
  'base:get': async () => getBaseResume(),
  'render:resumeHtml': async (resume, tpl) => resumeHtml(resume, tpl),
  'render:coverHtml': async (resume, letter, tpl, job) => coverHtml(resume, letter, tpl, job),
  'render:resumePdf': async (resume, tpl) => resumePdf(resume, tpl),
  'render:coverPdf': async (resume, letter, tpl, job) => coverPdf(resume, letter, tpl, job),

  // ---------- AI ----------
  'ai:tailor': async (job) => {
    const cfg = await aiConfig();
    const prompt = await getPrompt('resume');
    const out = await ai.tailorResume({ cfg, prompt, base: await getBaseResume(), job });
    return { ...out, meta: { ai_provider: cfg.provider, ai_model: cfg.model, prompt_used: prompt } };
  },
  'ai:coverLetter': async ({ resume, job, instructions }) => {
    const cfg = await aiConfig();
    const prompt = await getPrompt('cover');
    return ai.generateCoverLetter({ cfg, prompt, resume: resume || (await getBaseResume()), job, instructions });
  },

  // ---------- bids ----------
  'resumes:list': async (filter) => db.listResumes(filter),
  'resumes:dates': async (tz) => db.listDates(tz),
  'resumes:get': async (id) => db.getResume(id),
  'resumes:create': async (data) => db.createResume(data),
  'resumes:update': async (id, data) => db.updateResume(id, data),
  'resumes:delete': async (id) => db.deleteResume(id),

  'options:get': async () => ({ custom: await getCustomOptions(), used: await db.distinctValues() }),
  'options:add': async (key, value) => {
    if (!OPTION_KEYS.includes(key)) throw new Error('Unknown field ' + key);
    const v = String(value || '').trim();
    if (!v) throw new Error('Value is empty');
    const opts = await getCustomOptions();
    if (!opts[key].some((x) => x.toLowerCase() === v.toLowerCase())) opts[key].unshift(v);
    await db.setSetting('field_options', JSON.stringify(opts));
    return opts;
  },
  'options:remove': async (key, value) => {
    if (!OPTION_KEYS.includes(key)) throw new Error('Unknown field ' + key);
    const opts = await getCustomOptions();
    opts[key] = opts[key].filter((x) => x !== value);
    await db.setSetting('field_options', JSON.stringify(opts));
    return opts;
  },

  'resumes:exportPdf': async (id) => {
    let row = await db.getResume(id);
    if (!row) throw new Error(`Resume #${id} not found`);
    if (!row.resume_json) throw new Error('Nothing to export yet. Generate or load resume content first.');
    const dir = resumeDir(row);
    const pdfPath = `${dir}/${fileBase(row)}_Resume.pdf`;
    await storage.put(pdfPath, await resumePdf(row.resume_json, row.template));
    row = await db.updateResume(id, { resume_dir: dir, pdf_path: pdfPath, status: row.status === 'draft' ? 'generated' : row.status });
    await Promise.all([
      storage.put(`${dir}/resume.json`, JSON.stringify(row.resume_json, null, 2)),
      storage.put(`${dir}/job.txt`, jobInfo(row))
    ]);
    return row;
  },
  'resumes:exportCover': async (id) => {
    let row = await db.getResume(id);
    if (!row) throw new Error(`Resume #${id} not found`);
    if (!row.cover_letter.trim()) throw new Error('Cover letter is empty.');
    const resume = row.resume_json || (await getBaseResume());
    const dir = resumeDir(row);
    const coverPath = `${dir}/${fileBase({ ...row, resume_json: resume })}_Cover_Letter.pdf`;
    await storage.put(coverPath, await coverPdf(resume, row.cover_letter, row.template, row));
    await storage.put(`${dir}/cover_letter.txt`, row.cover_letter);
    return db.updateResume(id, { resume_dir: dir, cover_letter_path: coverPath });
  },

  // ---------- files (replace desktop shell:openPath) ----------
  'files:url': async (path) => {
    if (!path) throw new Error('No file yet.');
    return storage.signedUrl(path);
  },
  'files:list': async (dir) => {
    if (!dir) throw new Error('No files yet.');
    return storage.listFolder(dir);
  },
  'files:removeFolder': async (dir) => {
    if (!/^\d{4}-\d{2}-\d{2}\/\d+_[^/]+$/.test(dir || '')) throw new Error('Not a bid folder: ' + dir);
    return storage.removeFolder(dir);
  },
  // raw bytes of a stored file (the extension attaches the PDFs to application forms)
  'files:get': async (path) => {
    if (!path) throw new Error('No file yet.');
    return storage.get(path);
  },

  // ---------- Chrome auto-bid extension ----------
  // job-board links that already have a bid
  'bot:seen': async (urls) => db.findBySourceUrls((urls || []).filter(Boolean).slice(0, 500)),
  // one page of an application: what to fill and what to click
  'bot:plan': async (payload) => {
    const s = await db.getAllSettings();
    const cfg = await aiConfig(s.agent_model ? { model: s.agent_model } : {});
    return agent.planStep({ cfg, ...payload });
  },
  // appends a log line to the bid (and updates any bot fields passed along)
  'bot:log': async (id, msg, fields = {}) => {
    const row = await db.getResume(id);
    if (!row) throw new Error(`Bid #${id} not found`);
    const log = Array.isArray(row.bot_log) ? row.bot_log : [];
    if (msg) log.push({ t: new Date().toISOString(), msg: String(msg).slice(0, 500) });
    return db.updateResume(id, { ...fields, bot_log: log.slice(-200) });
  }
};

module.exports = { methods, init: db.init };
