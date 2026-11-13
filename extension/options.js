import { getSettings, saveSettings, getProfile, saveProfile, seedProfile, DEFAULT_PROFILE, SECRET_KEYS, rpc, login, SITES } from './lib/config.js';

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
  interview_language: 'Preferred programming language (interviews)',
  other_info: 'Anything else the bot should know',
  password: 'Password for job-site accounts', ssn_last4: 'SSN last 4 digits', dob: 'Date of birth (YYYY-MM-DD)'
};

let server = null; // server settings (AI, prompts, base resume)
const ai = { provider: 'anthropic', agentProvider: '', keys: {}, models: {}, lists: {} };
const PROVIDER_LABEL = { anthropic: 'Claude', openai: 'OpenAI', gemini: 'Gemini' };
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
// shown as dots on this page; the date of birth stays readable (it is still kept from the AI, see SECRET_KEYS)
const MASKED = SECRET_KEYS.filter((k) => k !== 'dob');

function renderProfile(p) {
  $('#profileGrid').innerHTML = Object.keys(DEFAULT_PROFILE).map((k) => {
    const wide = k === 'other_info' ? ' class="wide"' : '';
    const input = k === 'other_info'
      ? `<textarea id="p-${k}" rows="3"></textarea>`
      : `<input id="p-${k}" ${MASKED.includes(k) ? 'type="password" class="secret"' : ''} autocomplete="off">`;
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
  $('#site').innerHTML = Object.entries(SITES).map(([k, v]) => `<option value="${k}">${v.label}</option>`).join('');
  $('#site').value = s.site;
  for (const k of Object.keys(SITES)) $(`#listUrl-${k}`).value = s.listUrls[k] || SITES[k].listUrl;
  for (const [k, v] of Object.entries(s.filters)) {
    const el = $(`#f-${k}`);
    if (el) el.type === 'checkbox' ? (el.checked = !!v) : (el.value = v);
  }
  for (const [k, v] of Object.entries(s.behavior)) {
    const el = $(`#b-${k}`);
    if (el && el.tagName !== 'SELECT') el.type === 'checkbox' ? (el.checked = !!v) : (el.value = v);
  }
  $('#b-template').dataset.value = s.behavior.template;
  syncCoverBoxes();
}

function collectSettings() {
  const read = (prefix, obj) => Object.fromEntries(Object.keys(obj).map((k) => {
    const el = $(`#${prefix}-${k}`);
    if (!el) return [k, obj[k]];
    return [k, el.type === 'checkbox' ? el.checked : el.type === 'number' ? Number(el.value) : el.value.trim()];
  }));
  return { serverUrl: $('#serverUrl').value.trim().replace(/\/+$/, ''), site: $('#site').value, listUrls: Object.fromEntries(Object.keys(SITES).map((k) => [k, $(`#listUrl-${k}`).value.trim() || SITES[k].listUrl])), filtersRaw: read('f', current.filters), behaviorRaw: read('b', current.behavior) };
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
    ai.agentProvider = s.agent_provider || '';
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
    if (ai.keys[ai.provider] || ai.keys[ai.agentProvider]) loadModels(true);
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
  // the page reader's own provider key (only shown when it differs from the provider above)
  const ap = ai.agentProvider;
  if (ap && ap !== ai.provider && !$('#agentKeyRow').classList.contains('hidden')) ai.keys[ap] = $('#agentKey').value.trim();
}

// the provider the page reader actually uses
const agentProv = () => ai.agentProvider || ai.provider;

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
  // page reader: its own provider, model and (when different from above) API key
  const ap = agentProv();
  $('#agentProvider').value = ai.agentProvider;
  $('#agentModel').innerHTML = modelOptions(ai.lists[ap], ai.agentModel,
    ap === ai.provider ? 'Same as above' : `${PROVIDER_LABEL[ap]} default (${ai.models[ap] || 'its default model'})`);
  const ownKey = ap !== ai.provider;
  $('#agentKeyRow').classList.toggle('hidden', !ownKey);
  if (ownKey) {
    $('#agentKeyLabel').textContent = `${PROVIDER_LABEL[ap]} API key (for reading application pages)`;
    $('#agentKey').value = ai.keys[ap] || '';
  }
}

// Loads the model lists of the provider above and of the page reader's provider.
async function loadModels(quiet) {
  captureAi();
  const provs = [...new Set([ai.provider, agentProv()])].filter((p) => ai.keys[p]);
  if (!provs.length) return !quiet && ($('#aiStatus').textContent = 'Enter the API key first.');
  $('#aiStatus').textContent = 'Loading models…';
  const done = [];
  for (const p of provs) {
    try {
      ai.lists[p] = await rpc('settings:listModels', { provider: p, apiKey: ai.keys[p] });
      done.push(`✓ ${PROVIDER_LABEL[p]}: ${ai.lists[p].length} models`);
    } catch (err) {
      done.push(`${PROVIDER_LABEL[p]}: ${err.message}`);
    }
  }
  renderProvider();
  $('#aiStatus').textContent = done.join('  ·  ');
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
$('#agentProvider').addEventListener('change', () => {
  captureAi();
  ai.agentProvider = $('#agentProvider').value;
  ai.agentModel = ''; // a model of the old provider does not apply
  renderProvider();
  if (ai.keys[agentProv()] && !ai.lists[agentProv()]) loadModels(true);
});
$('#agentKey').addEventListener('change', () => {
  captureAi();
  if (ai.keys[agentProv()]) loadModels(true);
});
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
    current = await saveSettings({ serverUrl: c.serverUrl, site: c.site, listUrls: c.listUrls, filters: c.filtersRaw, behavior: c.behaviorRaw });
    await saveProfile(collectProfile());
    if (server) {
      captureAi();
      JSON.parse($('#baseResume').value); // validate first
      await rpc('settings:save', {
        ai_provider: ai.provider,
        [KEY_FIELD.anthropic]: ai.keys.anthropic || '', [MODEL_FIELD.anthropic]: ai.models.anthropic || '',
        [KEY_FIELD.openai]: ai.keys.openai || '', [MODEL_FIELD.openai]: ai.models.openai || '',
        [KEY_FIELD.gemini]: ai.keys.gemini || '', [MODEL_FIELD.gemini]: ai.models.gemini || '',
        agent_provider: ai.agentProvider || '',
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

// version label: "1.3.01_R" (manifest version_name)
{ const m = chrome.runtime.getManifest(); const v = document.querySelector("#ver"); if (v) v.textContent = m.version_name || m.version; }

// "Attach the cover letter" only matters when a cover letter is written
function syncCoverBoxes() {
  const gen = $('#b-generateCover');
  const att = $('#b-uploadCover');
  if (!gen || !att) return;
  att.disabled = !gen.checked;
  att.closest('label').style.opacity = gen.checked ? '' : '.5';
}
$('#b-generateCover')?.addEventListener('change', syncCoverBoxes);
