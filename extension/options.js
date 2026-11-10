import { getSettings, saveSettings, getProfile, saveProfile, seedProfile, DEFAULT_PROFILE, SECRET_KEYS, rpc, login } from './lib/config.js';

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const PROFILE_LABELS = {
  first_name: 'First name', last_name: 'Last name', full_name: 'Full name', preferred_name: 'Preferred name',
  email: 'Email', phone: 'Phone', phone_country_code: 'Phone country code',
  address_line1: 'Street address', city: 'City', state: 'State (abbr.)', state_full: 'State', zip: 'ZIP', county: 'County', country: 'Country',
  current_location: 'Location (as shown on forms)',
  linkedin: 'LinkedIn URL', github: 'GitHub URL', website: 'Website / portfolio',
  current_title: 'Current title', current_company: 'Current company', years_experience: 'Years of experience',
  highest_degree: 'Highest degree', degree_field: 'Field of study', school: 'School', graduation_year: 'Graduation year',
  work_authorized_us: 'Authorized to work in the US', needs_sponsorship: 'Needs visa sponsorship', citizenship: 'Citizenship',
  willing_to_relocate: 'Willing to relocate', remote_preference: 'Work preference',
  start_date: 'Earliest start date', notice_period: 'Notice period', salary_expectation: 'Salary expectation',
  gender: 'Gender', pronouns: 'Pronouns', sexual_orientation: 'Sexual orientation', race_ethnicity: 'Race / ethnicity', hispanic_latino: 'Hispanic or Latino', veteran_status: 'Veteran status', disability_status: 'Disability status',
  sms_consent: 'Consent to text messages', how_heard: 'How did you hear about us', licenses_certifications: 'Licenses / certifications',
  other_info: 'Anything else the bot should know',
  password: 'Password for job-site accounts', ssn_last4: 'SSN last 4 digits', dob: 'Date of birth (YYYY-MM-DD)'
};

let server = null; // server settings (AI, prompts, base resume)
const ai = { provider: 'anthropic', keys: {}, models: {}, lists: {} };
const KEY_FIELD = { anthropic: 'claude_api_key', openai: 'openai_api_key', gemini: 'gemini_api_key' };
const MODEL_FIELD = { anthropic: 'claude_model', openai: 'openai_model', gemini: 'gemini_model' };

function status(text, kind = '') {
  $('#status').textContent = text;
  $('#status').className = kind || 'muted';
}

// ---------- tabs ----------
$('#nav').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-tab]');
  if (!b) return;
  $$('#nav button').forEach((x) => x.classList.toggle('active', x === b));
  $$('section.tab').forEach((s) => s.classList.toggle('active', s.id === 'tab-' + b.dataset.tab));
});

// ---------- profile ----------
function renderProfile(p) {
  $('#profileGrid').innerHTML = Object.keys(DEFAULT_PROFILE).map((k) => {
    const wide = k === 'other_info' ? ' class="wide"' : '';
    const input = k === 'other_info'
      ? `<textarea id="p-${k}" rows="3"></textarea>`
      : `<input id="p-${k}" ${SECRET_KEYS.includes(k) ? 'type="password" class="secret"' : ''} autocomplete="off">`;
    return `<label${wide}>${PROFILE_LABELS[k] || k}${input}</label>`;
  }).join('');
  for (const k of Object.keys(DEFAULT_PROFILE)) $(`#p-${k}`).value = p[k] ?? '';
}

function collectProfile() {
  return Object.fromEntries(Object.keys(DEFAULT_PROFILE).map((k) => [k, $(`#p-${k}`).value.trim()]));
}

// ---------- local settings ----------
function renderSettings(s) {
  $('#serverUrl').value = s.serverUrl;
  $('#listUrl').value = s.listUrl;
  for (const [k, v] of Object.entries(s.filters)) {
    const el = $(`#f-${k}`);
    if (el) el.type === 'checkbox' ? (el.checked = !!v) : (el.value = v);
  }
  for (const [k, v] of Object.entries(s.behavior)) {
    const el = $(`#b-${k}`);
    if (el && el.tagName !== 'SELECT') el.type === 'checkbox' ? (el.checked = !!v) : (el.value = v);
  }
  $('#b-template').dataset.value = s.behavior.template;
}

function collectSettings() {
  const read = (prefix, obj) => Object.fromEntries(Object.keys(obj).map((k) => {
    const el = $(`#${prefix}-${k}`);
    if (!el) return [k, obj[k]];
    return [k, el.type === 'checkbox' ? el.checked : el.type === 'number' ? Number(el.value) : el.value.trim()];
  }));
  return { serverUrl: $('#serverUrl').value.trim().replace(/\/+$/, ''), listUrl: $('#listUrl').value.trim(), filtersRaw: read('f', current.filters), behaviorRaw: read('b', current.behavior) };
}

let current = null;

// ---------- server (AI, prompts, resume) ----------
async function loadServer() {
  try {
    const [s, templates] = await Promise.all([rpc('settings:get'), rpc('templates:list')]);
    server = s;
    ai.provider = s.ai_provider;
    for (const p of Object.keys(KEY_FIELD)) {
      ai.keys[p] = s[KEY_FIELD[p]] || '';
      ai.models[p] = s[MODEL_FIELD[p]] || '';
    }
    ai.agentModel = s.agent_model || '';
    $('#promptResume').value = s.prompt_resume;
    $('#promptCover').value = s.prompt_cover;
    $('#baseResume').value = s.base_resume;
    $('#filesDir').value = s.local_output_dir;
    $('#filesDir').disabled = s.files_location !== 'local';
    if (s.files_location !== 'local') $('#filesDir').placeholder = 'The server runs on Vercel, so files go to Supabase Storage';
    const sel = $('#b-template');
    sel.innerHTML = `<option value="">Web app default (${s.default_template})</option>` + templates.map((t) => `<option value="${t.id}">${t.name}</option>`).join('');
    sel.value = sel.dataset.value || '';
    renderProvider();
    $('#connStatus').textContent = '✓ Connected';
    $('#connStatus').className = 'ok';
    if (ai.keys[ai.provider]) loadModels(true);
    loadUsage();
  } catch (err) {
    $('#connStatus').textContent = err.message;
    $('#connStatus').className = 'err';
    $$('#tab-ai textarea, #tab-ai input, #tab-ai select, #baseResume').forEach((el) => (el.disabled = true));
  }
}

function captureAi() {
  ai.keys[ai.provider] = $('#aiKey').value.trim();
  if ($('#aiModel').value) ai.models[ai.provider] = $('#aiModel').value;
  ai.agentModel = $('#agentModel').value;
}

function modelOptions(list, currentId, blankLabel) {
  const opts = list ? [...list] : [];
  if (currentId && !opts.some((m) => m.id === currentId)) opts.unshift({ id: currentId, name: currentId });
  return (blankLabel ? `<option value="">${blankLabel}</option>` : '') + opts.map((m) => `<option value="${m.id}" ${m.id === currentId ? 'selected' : ''}>${m.name && m.name !== m.id ? `${m.name} (${m.id})` : m.id}</option>`).join('');
}

function renderProvider() {
  $$('#provider button').forEach((b) => b.classList.toggle('active', b.dataset.p === ai.provider));
  $('#keyLabel').textContent = { anthropic: 'Claude API key', openai: 'OpenAI API key', gemini: 'Gemini API key' }[ai.provider];
  $('#aiKey').value = ai.keys[ai.provider] || '';
  const list = ai.lists[ai.provider];
  $('#aiModel').innerHTML = modelOptions(list, ai.models[ai.provider]) || '<option value="">Load models first</option>';
  $('#agentModel').innerHTML = modelOptions(list, ai.agentModel, 'Same as above');
}

async function loadModels(quiet) {
  captureAi();
  if (!ai.keys[ai.provider]) return !quiet && ($('#aiStatus').textContent = 'Enter the API key first.');
  $('#aiStatus').textContent = 'Loading models…';
  try {
    ai.lists[ai.provider] = await rpc('settings:listModels', { provider: ai.provider, apiKey: ai.keys[ai.provider] });
    renderProvider();
    $('#aiStatus').textContent = `✓ ${ai.lists[ai.provider].length} models`;
  } catch (err) {
    $('#aiStatus').textContent = err.message;
  }
}

$('#provider').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-p]');
  if (!b) return;
  captureAi();
  ai.provider = b.dataset.p;
  renderProvider();
  if (ai.keys[ai.provider] && !ai.lists[ai.provider]) loadModels(true);
});
$('#loadModels').addEventListener('click', () => loadModels(false));
$$('[data-reset]').forEach((b) => b.addEventListener('click', async () => {
  const d = await rpc('settings:defaultPrompts');
  $(b.dataset.reset === 'resume' ? '#promptResume' : '#promptCover').value = d[b.dataset.reset];
}));

// ---------- AI usage ----------
const fmtCost = (t) => (t.unpriced && !t.cost ? '?' : '$' + t.cost.toFixed(t.cost < 1 ? 4 : 2) + (t.unpriced ? '+' : ''));
const fmtN = (n) => Number(n || 0).toLocaleString();

async function loadUsage() {
  $('#usageStatus').textContent = 'Loading…';
  try {
    const [u, p] = await Promise.all([rpc('usage:summary'), rpc('usage:prices')]);
    if (u.missingTable) {
      $('#usageStatus').textContent = 'Run supabase/migrations/003_ai_usage.sql in Supabase to start tracking.';
      return;
    }
    const head = '<tr><th></th><th>Calls</th><th>Input</th><th>Cached</th><th>Output</th><th>Total tokens</th><th>Cost (US$)</th></tr>';
    const row = (label, t) => `<tr><td>${label}</td><td>${fmtN(t.calls)}</td><td>${fmtN(t.input)}</td><td>${fmtN(t.cached)}</td><td>${fmtN(t.output)}</td><td>${fmtN(t.tokens)}</td><td>${fmtCost(t)}</td></tr>`;
    $('#usageTotals').innerHTML = head + row('Today', u.today) + row('Last 7 days', u.week) + row('Last 30 days', u.month);
    $('#usageModels').innerHTML = u.models.length
      ? head.replace('<th></th>', '<th>Model (30 days)</th>') + u.models.map((m) => row(m.model, m)).join('')
      : '';
    $('#aiPrices').value = Object.keys(p.saved || {}).length ? JSON.stringify(p.saved, null, 2) : '';
    $('#defaultPrices').textContent = 'Built-in: ' + Object.entries(p.defaults).map(([m, x]) => `${m} ${x.input}/${x.output}`).join(', ');
    const unpriced = u.models.filter((m) => m.unpriced).map((m) => m.model.split(' / ')[1]);
    $('#usageStatus').textContent = unpriced.length ? `No price yet for: ${unpriced.join(', ')}. Add it under Prices to see its cost.` : '';
  } catch (err) {
    $('#usageStatus').textContent = err.message;
  }
}
$('#usageRefresh').addEventListener('click', loadUsage);

// ---------- connection ----------
$('#loginBtn').addEventListener('click', async () => {
  $('#connStatus').textContent = 'Signing in…';
  $('#connStatus').className = 'muted';
  try {
    await login($('#serverUrl').value.trim(), $('#loginUser').value.trim(), $('#loginPass').value);
    $('#loginPass').value = '';
    current = await getSettings();
    $$('#tab-ai textarea, #tab-ai input, #tab-ai select, #baseResume').forEach((el) => (el.disabled = false));
    await loadServer();
  } catch (err) {
    $('#connStatus').textContent = err.message;
    $('#connStatus').className = 'err';
  }
});
$('#testBtn').addEventListener('click', loadServer);

$('#resetDone').addEventListener('click', async () => {
  if (!confirm('Forget every job the bot has handled? Supabase still prevents applying twice to a job with a saved bid.')) return;
  await chrome.runtime.sendMessage({ type: 'resetDone' });
  showDoneCount();
});

async function showDoneCount() {
  const { done, answers } = await chrome.storage.local.get(['done', 'answers']);
  $('#doneCount').textContent = `${Object.keys(done || {}).length} jobs handled so far`;
  $('#answerCount').textContent = `${Object.keys(answers || {}).length} answers saved`;
}

const escHtml = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
$('#showAnswers').addEventListener('click', async () => {
  const { answers } = await chrome.storage.local.get('answers');
  const rows = Object.values(answers || {}).sort((a, b) => b.t - a.t);
  $('#answerList').innerHTML = rows.length
    ? rows.map((r) => `<div><b>${escHtml(r.q)}</b> → ${escHtml(r.answer)}</div>`).join('')
    : 'No saved answers yet.';
});
$('#clearAnswers').addEventListener('click', async () => {
  if (!confirm('Forget every saved answer? The AI will answer those questions again (and save them again).')) return;
  await chrome.storage.local.remove('answers');
  $('#answerList').innerHTML = '';
  showDoneCount();
});

// ---------- save ----------
$('#saveBtn').addEventListener('click', async () => {
  status('Saving…');
  try {
    const c = collectSettings();
    current = await saveSettings({ serverUrl: c.serverUrl, listUrl: c.listUrl, filters: c.filtersRaw, behavior: c.behaviorRaw });
    await saveProfile(collectProfile());
    if (server) {
      captureAi();
      JSON.parse($('#baseResume').value); // validate first
      await rpc('settings:save', {
        ai_provider: ai.provider,
        [KEY_FIELD.anthropic]: ai.keys.anthropic || '', [MODEL_FIELD.anthropic]: ai.models.anthropic || '',
        [KEY_FIELD.openai]: ai.keys.openai || '', [MODEL_FIELD.openai]: ai.models.openai || '',
        [KEY_FIELD.gemini]: ai.keys.gemini || '', [MODEL_FIELD.gemini]: ai.models.gemini || '',
        agent_model: ai.agentModel || '',
        prompt_resume: $('#promptResume').value.trim(),
        prompt_cover: $('#promptCover').value.trim(),
        base_resume: $('#baseResume').value,
        ai_prices: $('#aiPrices').value.trim() ? JSON.stringify(JSON.parse($('#aiPrices').value)) : '{}',
        ...(server.files_location === 'local' ? { local_output_dir: $('#filesDir').value.trim() } : {})
      });
    }
    status('✓ Saved', 'ok');
  } catch (err) {
    status(err instanceof SyntaxError ? 'Resume JSON or prices JSON is not valid: ' + err.message : err.message, 'err');
  }
});

(async () => {
  current = await getSettings();
  renderSettings(current);
  await seedProfile(); // may not have finished on install yet
  renderProfile(await getProfile());
  showDoneCount();
  if (current.token) loadServer();
  else {
    $('#connStatus').textContent = 'Not signed in';
    $('#connStatus').className = 'warn';
  }
})();
