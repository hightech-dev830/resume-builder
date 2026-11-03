const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
// `api` is the global exposed by preload.js (contextBridge); don't redeclare it.

const STATUSES = ['draft', 'generated', 'applied', 'interview', 'offer', 'rejected', 'archived'];
const JOB_FIELDS = ['company', 'role', 'job_link', 'location', 'salary', 'jd', 'notes', 'instructions'];
const PAGE_WIDTH_PX = 816; // 8.5in at 96dpi

// Dropdown presets; values used in past bids are added on top of these.
const PRESETS = {
  role: [
    'Senior Software Engineer', 'Senior Backend Engineer', 'Senior Full-Stack Engineer', 'Staff Software Engineer',
    'Lead Software Engineer', 'Principal Software Engineer', 'Senior Python Engineer', 'Senior Go Engineer',
    'Senior Node.js Engineer', 'Senior AI Engineer', 'Senior ML / LLM Engineer', 'Senior Data Engineer',
    'Senior Platform Engineer', 'Senior DevOps Engineer', 'Senior Frontend Engineer', 'Software Engineer'
  ],
  location: [
    'Remote (US)', 'Remote (Worldwide)', 'Remote (Americas)', 'Remote (US) – Eastern Time', 'Remote (US) – Pacific Time',
    'Hybrid', 'On-site', 'Los Angeles, CA', 'San Francisco, CA', 'New York, NY'
  ],
  salary: [
    'Not listed', '$120k–$150k', '$140k–$170k', '$150k–$180k', '$170k–$200k', '$180k–$220k', '$200k–$250k',
    '$250k+', '$60–$80/hr', '$80–$100/hr', '$100–$120/hr', '$120+/hr'
  ]
};

// Merged dropdown lists: your added values first, then values from past bids, then presets.
const fieldOptions = { role: [], location: [], salary: [] };
const combos = {};

async function loadFieldOptions() {
  let data = { custom: {}, used: {} };
  try {
    data = await api.getOptions();
  } catch (err) {
    console.warn('Could not load dropdown options', err);
  }
  for (const key of Object.keys(PRESETS)) {
    const custom = data.custom[key] || [];
    const seen = new Set();
    fieldOptions[key] = [
      ...custom.map((v) => ({ value: v, custom: true })),
      ...(data.used[key] || []).map((v) => ({ value: v })),
      ...PRESETS[key].map((v) => ({ value: v }))
    ].filter((o) => {
      const k = o.value.toLowerCase();
      return seen.has(k) ? false : seen.add(k);
    });
    combos[key]?.refresh();
  }
}

function setupCombos() {
  for (const key of Object.keys(PRESETS)) {
    combos[key] = createCombobox($(`#f-${key}`), {
      options: () => fieldOptions[key],
      onAdd: async (value) => {
        await api.addOption(key, value);
        await loadFieldOptions();
        toast(`Added “${escapeHtml(value)}” to ${key} list`, 'ok');
      },
      onRemove: async (value) => {
        await api.removeOption(key, value);
        await loadFieldOptions();
      }
    });
  }
}

const state = {
  id: null,
  row: null,
  resume: null,
  template: 'kevin',
  templates: [],
  settings: null,
  dirty: false,
  keywords: [],
  previewMode: 'fast',
  clPreviewMode: 'fast',
  pdfUrls: {}
};

// ---------------- utilities ----------------

function toast(msg, kind = 'info', ms = 3500) {
  const t = $('#toast');
  t.innerHTML = msg;
  t.className = `toast show ${kind}`;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = 'toast'), ms);
}

async function busy(text, fn) {
  $('#busyText').textContent = text;
  $('#busy').classList.remove('hidden');
  try {
    return await fn();
  } catch (err) {
    toast(escapeHtml(err.message), 'error', 7000);
    throw err;
  } finally {
    $('#busy').classList.add('hidden');
  }
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function debounce(fn, ms) {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}

function fmtDateTime(d) {
  const dt = new Date(d);
  return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) +
    ' ' + dt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}

function formData() {
  const o = {};
  for (const f of JOB_FIELDS) o[f] = $(`#f-${f}`).value.trim();
  return o;
}

function setForm(row = {}) {
  for (const f of JOB_FIELDS) $(`#f-${f}`).value = row[f] || '';
}

function setDirty(v = true) {
  state.dirty = v;
  updateHeader();
}

function updateHeader() {
  const f = formData();
  const label = state.id
    ? `<b>#${state.id}</b> · ${escapeHtml(f.company || 'No company')}${f.role ? ' — ' + escapeHtml(f.role) : ''}`
    : 'New bid (unsaved)';
  $('#currentBid').innerHTML = label + (state.dirty ? ' <span class="dot" title="Unsaved changes">●</span>' : '');
  $('#clOpenBtn').disabled = !state.row?.cover_letter_path;
  $('#clFolderBtn').disabled = !state.row?.resume_dir;
  renderResultBar();
}

// Under the Generate button: where the PDF went and which AI made it.
function renderResultBar() {
  const row = state.row;
  const has = !!row?.pdf_path;
  $('#resultBar').classList.toggle('hidden', !has);
  if (!has) return;
  const file = row.pdf_path.split(/[\\/]/).pop();
  $('#resultText').innerHTML = `✓ <b>#${row.id}</b> saved · <span title="${escapeHtml(row.pdf_path)}">${escapeHtml(file)}</span>`;
  $('#resultModel').textContent = state.aiMeta?.ai_model ? `by ${state.aiMeta.ai_model}` : 'from base profile';
  $('#updatePdfBtn').classList.toggle('hidden', !state.dirty);
}

function providerName() {
  const s = state.settings || {};
  const model = s.ai_provider === 'openai' ? s.openai_model : s.claude_model;
  return model || (s.ai_provider === 'openai' ? 'OpenAI' : 'Claude');
}

// ---------------- preview ----------------

const SCREEN_CSS = '<style>@media screen{html{background:#fff;overflow:hidden}body{padding:0.55in 0.6in;min-height:11in}}</style>';

function loadPaper(frame, holder, html) {
  return new Promise((resolve) => {
    frame.onload = async () => {
      const doc = frame.contentDocument;
      const fit = () => (frame.style.height = Math.max(1056, doc.documentElement.scrollHeight) + 'px');
      fit();
      await doc.fonts.ready; // fonts can change the height after first layout
      fit();
      resolve();
    };
    frame.srcdoc = html.replace('</head>', SCREEN_CSS + '</head>');
  });
}

function applyZoom(holder, value) {
  const wrap = holder.parentElement;
  const z = value === 'fit' ? Math.min(1.4, (wrap.clientWidth - 48) / PAGE_WIDTH_PX) : Number(value);
  holder.style.zoom = z;
}

function setPdf(frame, key, bytes) {
  if (state.pdfUrls[key]) URL.revokeObjectURL(state.pdfUrls[key]);
  const url = URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' }));
  state.pdfUrls[key] = url;
  frame.src = url + '#toolbar=1&navpanes=0&view=FitH';
}

async function renderPreview() {
  const empty = !state.resume;
  $('#emptyState').classList.toggle('hidden', !empty);
  const holder = $('#paperHolder');
  const pdf = $('#pdfFrame');
  if (empty) {
    holder.classList.add('hidden');
    pdf.classList.add('hidden');
    return;
  }
  try {
    if (state.previewMode === 'pdf') {
      holder.classList.add('hidden');
      pdf.classList.remove('hidden');
      setPdf(pdf, 'resume', await api.renderResumePdf(state.resume, state.template));
    } else {
      pdf.classList.add('hidden');
      holder.classList.remove('hidden');
      await loadPaper($('#previewFrame'), holder, await api.renderResumeHtml(state.resume, state.template));
      applyZoom(holder, $('#zoom').value);
    }
  } catch (err) {
    toast('Preview failed: ' + escapeHtml(err.message), 'error');
  }
}
const renderPreviewSoon = debounce(renderPreview, 350);

async function renderCoverPreview() {
  const resume = state.resume || (await api.getBaseResume());
  const letter = $('#cl-text').value;
  const job = formData();
  const holder = $('#clPaperHolder');
  const pdf = $('#clPdfFrame');
  try {
    if (state.clPreviewMode === 'pdf') {
      holder.classList.add('hidden');
      pdf.classList.remove('hidden');
      setPdf(pdf, 'cover', await api.renderCoverPdf(resume, letter, state.template, job));
    } else {
      pdf.classList.add('hidden');
      holder.classList.remove('hidden');
      await loadPaper($('#clFrame'), holder, await api.renderCoverHtml(resume, letter, state.template, job));
      applyZoom(holder, 'fit');
    }
  } catch (err) {
    toast('Preview failed: ' + escapeHtml(err.message), 'error');
  }
}
const renderCoverPreviewSoon = debounce(renderCoverPreview, 400);

function setResume(resume, { render = true } = {}) {
  state.resume = resume;
  $('#f-json').value = resume ? JSON.stringify(resume, null, 2) : '';
  $('#jsonStatus').textContent = '';
  if (render) renderPreview();
}

function renderKeywords() {
  $('#keywords').innerHTML = state.keywords.length
    ? `<div class="muted small">Matched JD keywords</div>` + state.keywords.map((k) => `<span class="chip">${escapeHtml(k)}</span>`).join('')
    : '';
}

// ---------------- templates ----------------

function renderTemplateList() {
  $('#templateList').innerHTML = state.templates
    .map((t) => `<div class="tpl-card ${t.id === state.template ? 'active' : ''}" data-id="${t.id}">
        <div class="tpl-name">${escapeHtml(t.name)}</div><div class="tpl-desc">${escapeHtml(t.description)}</div></div>`)
    .join('');
}

$('#templateList').addEventListener('click', (e) => {
  const card = e.target.closest('.tpl-card');
  if (!card) return;
  state.template = card.dataset.id;
  renderTemplateList();
  setDirty();
  renderPreview();
  if ($('#view-cover').classList.contains('active')) renderCoverPreview();
  if (state.row?.pdf_path && state.resume) autoResavePdf();
});

// Re-save the PDF in the background after a template switch, so the file always matches.
const autoResavePdf = debounce(async () => {
  try {
    await saveCurrent({ quiet: true });
    state.row = await api.exportPdf(state.id);
    if (state.row.cover_letter_path && state.row.cover_letter) state.row = await api.exportCover(state.id);
    updateHeader();
    toast(`PDF updated with the <b>${escapeHtml(state.templates.find((t) => t.id === state.template)?.name || state.template)}</b> template`, 'ok');
  } catch (err) {
    toast('Could not update PDF: ' + escapeHtml(err.message), 'error');
  }
}, 700);

// ---------------- save / generate / export ----------------

async function saveCurrent({ quiet = false } = {}) {
  const payload = {
    ...formData(),
    template: state.template,
    resume_json: state.resume,
    cover_letter: $('#cl-text').value,
    ...(state.aiMeta || {})
  };
  const row = state.id ? await api.updateResume(state.id, payload) : await api.createResume({ ...payload, status: 'draft' });
  state.id = row.id;
  state.row = row;
  setDirty(false);
  loadFieldOptions();
  if (!quiet) toast(`Saved bid #${row.id}`, 'ok');
  return row;
}

async function exportResumePdf() {
  if (!state.resume) throw new Error('Generate or load resume content first.');
  await saveCurrent({ quiet: true });
  state.row = await api.exportPdf(state.id);
  updateHeader();
  return state.row;
}

$('#generateBtn').addEventListener('click', () =>
  busy(`Tailoring your resume with ${providerName()}… (this can take a minute)`, async () => {
    const job = formData();
    if (!job.jd) throw new Error('Paste the job description first.');
    if (!state.id) await saveCurrent({ quiet: true });
    const { resume, keywords, meta } = await api.tailor(job);
    state.keywords = keywords;
    state.aiMeta = meta;
    renderKeywords();
    setResume(resume, { render: false });
    if (!job.role && resume.basics?.label) $('#f-role').value = resume.basics.label;
    await exportResumePdf();
    await renderPreview();
    toast(`Resume #${state.id} generated and saved`, 'ok');
  }).catch(() => {})
);

$('#useBaseBtn').addEventListener('click', async () => {
  if (state.resume && !confirm('Replace the current resume content with your base profile?')) return;
  state.keywords = [];
  state.aiMeta = { ai_provider: '', ai_model: '', prompt_used: '' };
  renderKeywords();
  setResume(await api.getBaseResume());
  setDirty();
});

const savePdfAction = () =>
  busy('Saving PDF…', async () => {
    await exportResumePdf();
    toast(`PDF saved: <a href="#" data-open="${escapeHtml(state.row.pdf_path)}">${escapeHtml(state.row.pdf_path)}</a>`, 'ok', 7000);
  }).catch(() => {});
$('#exportBtn').addEventListener('click', savePdfAction);
$('#updatePdfBtn').addEventListener('click', savePdfAction);
$('#openPdfBtn').addEventListener('click', () => api.openPath(state.row?.pdf_path).catch((e) => toast(e.message, 'error')));
$('#clFolderBtn').addEventListener('click', () => api.openPath(state.row?.resume_dir).catch((e) => toast(e.message, 'error')));
$('#saveBtn').addEventListener('click', () => busy('Saving…', () => saveCurrent()).catch(() => {}));
$('#openFolderBtn').addEventListener('click', () => api.openPath(state.row?.resume_dir).catch((e) => toast(e.message, 'error')));
$('#logoutBtn').addEventListener('click', () => {
  if ((state.dirty || baseIsDirty()) && !confirm('You have unsaved changes. Log out anyway?')) return;
  api.logout();
});

function newBid() {
  if (state.dirty && !confirm('Discard unsaved changes and start a new bid?')) return;
  state.id = null;
  state.row = null;
  state.aiMeta = null;
  state.keywords = [];
  state.template = state.settings?.default_template || state.template;
  setForm({});
  $('#cl-text').value = '';
  $('#cl-instructions').value = '';
  renderKeywords();
  renderTemplateList();
  setResume(null);
  setDirty(false);
  switchTab('builder');
  $('#f-company').focus();
}
$('#newBidBtn').addEventListener('click', newBid);

document.addEventListener('click', (e) => {
  const a = e.target.closest('[data-open]');
  if (a) {
    e.preventDefault();
    api.openPath(a.dataset.open).catch((err) => toast(err.message, 'error'));
  }
});

document.addEventListener('keydown', (e) => {
  if (e.ctrlKey && e.key.toLowerCase() === 's') {
    e.preventDefault();
    if ($('#view-base').classList.contains('active')) $('#baseSaveBtn').click();
    else busy('Saving…', () => saveCurrent()).catch(() => {});
  }
  if (e.ctrlKey && e.key.toLowerCase() === 'n') {
    e.preventDefault();
    newBid();
  }
});

for (const f of JOB_FIELDS) $(`#f-${f}`).addEventListener('input', () => setDirty());

$('#f-json').addEventListener('input', debounce(() => {
  try {
    state.resume = JSON.parse($('#f-json').value);
    $('#jsonStatus').textContent = '✓ Valid JSON';
    $('#jsonStatus').className = 'hint ok';
    setDirty();
    renderPreview();
  } catch (err) {
    $('#jsonStatus').textContent = 'Invalid JSON: ' + err.message;
    $('#jsonStatus').className = 'hint err';
  }
}, 400));

// preview controls
$('#previewMode').addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  $$('#previewMode button').forEach((x) => x.classList.toggle('active', x === b));
  state.previewMode = b.dataset.mode;
  renderPreview();
});
$('#zoom').addEventListener('change', () => applyZoom($('#paperHolder'), $('#zoom').value));
window.addEventListener('resize', debounce(() => {
  if ($('#zoom').value === 'fit') applyZoom($('#paperHolder'), 'fit');
  applyZoom($('#clPaperHolder'), 'fit');
  applyZoom($('#basePaperHolder'), 'fit');
}, 150));

// ---------------- cover letter ----------------

$('#clGenerateBtn').addEventListener('click', () =>
  busy(`Writing your cover letter with ${providerName()}…`, async () => {
    const job = formData();
    if (!job.jd && !job.company) throw new Error('Fill in the job details on the Resume tab first.');
    const letter = await api.coverLetter({ resume: state.resume, job, instructions: $('#cl-instructions').value.trim() });
    $('#cl-text').value = letter;
    await saveCurrent({ quiet: true });
    state.row = await api.exportCover(state.id);
    updateHeader();
    await renderCoverPreview();
    toast(`Cover letter saved: <a href="#" data-open="${escapeHtml(state.row.cover_letter_path)}">${escapeHtml(state.row.cover_letter_path)}</a>`, 'ok', 7000);
  }).catch(() => {})
);

$('#cl-text').addEventListener('input', () => {
  setDirty();
  renderCoverPreviewSoon();
});

$('#clSaveBtn').addEventListener('click', () => busy('Saving…', () => saveCurrent()).catch(() => {}));

$('#clExportBtn').addEventListener('click', () =>
  busy('Creating cover letter PDF…', async () => {
    if (!$('#cl-text').value.trim()) throw new Error('The cover letter is empty.');
    await saveCurrent({ quiet: true });
    state.row = await api.exportCover(state.id);
    updateHeader();
    toast(`Cover letter saved: <a href="#" data-open="${escapeHtml(state.row.cover_letter_path)}">${escapeHtml(state.row.cover_letter_path)}</a>`, 'ok', 7000);
  }).catch(() => {})
);

$('#clCopyBtn').addEventListener('click', async () => {
  await navigator.clipboard.writeText($('#cl-text').value);
  toast('Copied to clipboard', 'ok');
});
$('#clOpenBtn').addEventListener('click', () => api.openPath(state.row?.cover_letter_path).catch((e) => toast(e.message, 'error')));

$('#clPreviewMode').addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  $$('#clPreviewMode button').forEach((x) => x.classList.toggle('active', x === b));
  state.clPreviewMode = b.dataset.mode;
  renderCoverPreview();
});

// ---------------- history ----------------

const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;
const hist = { page: 1, size: 50, total: 0 };

const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const ymdToDate = (s) => {
  const [y, m, d] = s.split('-').map(Number);
  return new Date(y, m - 1, d);
};
const fmtDay = (s) => ymdToDate(s).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });

// Turns the date dropdown into a [from, to) range in local time.
function historyRange() {
  const v = $('#h-date').value;
  const today = startOfDay(new Date());
  const iso = (d) => d.toISOString();
  if (v === 'today') return { from: iso(today), to: iso(addDays(today, 1)) };
  if (v === 'yesterday') return { from: iso(addDays(today, -1)), to: iso(today) };
  if (v === 'last7') return { from: iso(addDays(today, -6)), to: iso(addDays(today, 1)) };
  if (v === 'last30') return { from: iso(addDays(today, -29)), to: iso(addDays(today, 1)) };
  if (v === 'thisMonth') return { from: iso(new Date(today.getFullYear(), today.getMonth(), 1)), to: iso(addDays(today, 1)) };
  if (v === 'lastMonth') {
    return { from: iso(new Date(today.getFullYear(), today.getMonth() - 1, 1)), to: iso(new Date(today.getFullYear(), today.getMonth(), 1)) };
  }
  if (v === 'custom') {
    const f = $('#h-from').value;
    const t = $('#h-to').value;
    return { from: f ? iso(ymdToDate(f)) : '', to: t ? iso(addDays(ymdToDate(t), 1)) : '' };
  }
  if (v.startsWith('day:')) {
    const d = ymdToDate(v.slice(4));
    return { from: iso(d), to: iso(addDays(d, 1)) };
  }
  return { from: '', to: '' };
}

async function loadHistoryDates() {
  const current = $('#h-date').value;
  const days = await api.listDates(TZ);
  const total = days.reduce((n, d) => n + d.count, 0);
  $('#h-date').innerHTML = `
    <option value="">All dates (${total})</option>
    <option value="today">Today</option>
    <option value="yesterday">Yesterday</option>
    <option value="last7">Last 7 days</option>
    <option value="last30">Last 30 days</option>
    <option value="thisMonth">This month</option>
    <option value="lastMonth">Last month</option>
    <option value="custom">Custom range…</option>
    ${days.length ? `<optgroup label="By day">${days.map((d) => `<option value="day:${d.day}">${fmtDay(d.day)} (${d.count})</option>`).join('')}</optgroup>` : ''}`;
  if ([...$('#h-date').options].some((o) => o.value === current)) $('#h-date').value = current;
  $('#h-range').classList.toggle('hidden', $('#h-date').value !== 'custom');
}

async function loadHistory() {
  const { from, to } = historyRange();
  const { rows, total } = await api.listResumes({
    search: $('#h-search').value.trim(),
    status: $('#h-status').value,
    from,
    to,
    tz: TZ,
    limit: hist.size,
    offset: hist.size ? (hist.page - 1) * hist.size : 0
  });
  hist.total = total;
  const pages = hist.size ? Math.max(1, Math.ceil(total / hist.size)) : 1;
  if (hist.page > pages) {
    hist.page = pages;
    return loadHistory();
  }

  const start = total ? (hist.size ? (hist.page - 1) * hist.size + 1 : 1) : 0;
  const end = hist.size ? Math.min(total, hist.page * hist.size) : total;
  $('#h-count').textContent = total ? `Showing ${start}–${end} of ${total} bid${total === 1 ? '' : 's'}` : 'No bids';
  $('#h-page').textContent = `Page ${hist.page} of ${pages}`;
  $('#h-first').disabled = $('#h-prev').disabled = hist.page <= 1;
  $('#h-next').disabled = $('#h-last').disabled = hist.page >= pages;

  const perDay = {};
  for (const r of rows) perDay[r.local_date] = (perDay[r.local_date] || 0) + 1;
  const tplName = (id) => state.templates.find((t) => t.id === id)?.name || id;
  const fmtTime = (d) => new Date(d).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  let lastDay = null;
  const html = [];
  for (const r of rows) {
    if (r.local_date !== lastDay) {
      lastDay = r.local_date;
      html.push(`<tr class="group-row"><td colspan="9">${fmtDay(r.local_date)} <span class="muted">· ${perDay[r.local_date]} bid${perDay[r.local_date] === 1 ? '' : 's'}</span></td></tr>`);
    }
    html.push(`<tr data-id="${r.id}">
        <td class="mono">#${r.id}</td>
        <td class="nowrap">${fmtTime(r.created_at)}</td>
        <td><b>${escapeHtml(r.company || '—')}</b>${r.job_link ? ` <a href="#" class="ext" data-url="${escapeHtml(r.job_link)}" title="Open ${escapeHtml(r.job_link)}">↗</a><button class="icon-btn copy-link" data-copy="${escapeHtml(r.job_link)}" title="Copy job link">⧉</button>` : ''}</td>
        <td>${escapeHtml(r.role || '')}</td>
        <td class="small">${escapeHtml(r.location || '')}${r.salary ? `<div class="muted">${escapeHtml(r.salary)}</div>` : ''}</td>
        <td class="small">${escapeHtml(tplName(r.template))}</td>
        <td><select class="status-select st-${r.status}" data-status>${STATUSES.map((s) => `<option ${s === r.status ? 'selected' : ''}>${s}</option>`).join('')}</select></td>
        <td class="nowrap">
          ${r.pdf_path ? `<a href="#" data-open="${escapeHtml(r.pdf_path)}">Resume</a>` : '<span class="muted">—</span>'}
          ${r.cover_letter_path ? ` · <a href="#" data-open="${escapeHtml(r.cover_letter_path)}">Cover</a>` : ''}
          ${r.resume_dir ? ` · <a href="#" data-open="${escapeHtml(r.resume_dir)}">Folder</a>` : ''}
        </td>
        <td class="nowrap ta-right"><button class="btn small" data-load>Open</button> <button class="btn small danger" data-del>Delete</button></td>
      </tr>`);
  }
  const filtered = $('#h-search').value || $('#h-status').value || $('#h-date').value;
  $('#historyBody').innerHTML = html.length
    ? html.join('')
    : `<tr><td colspan="9" class="empty">${filtered ? 'No bids match these filters.' : 'No bids yet. Generate your first resume on the Resume tab.'}</td></tr>`;
}

function reloadHistoryFromFirstPage() {
  hist.page = 1;
  loadHistory();
}

$('#h-search').addEventListener('input', debounce(reloadHistoryFromFirstPage, 250));
$('#h-status').addEventListener('change', reloadHistoryFromFirstPage);
$('#h-date').addEventListener('change', () => {
  $('#h-range').classList.toggle('hidden', $('#h-date').value !== 'custom');
  reloadHistoryFromFirstPage();
});
$('#h-from').addEventListener('change', reloadHistoryFromFirstPage);
$('#h-to').addEventListener('change', reloadHistoryFromFirstPage);
$('#h-size').addEventListener('change', () => {
  hist.size = Number($('#h-size').value);
  reloadHistoryFromFirstPage();
});
$('#h-first').addEventListener('click', () => { hist.page = 1; loadHistory(); });
$('#h-prev').addEventListener('click', () => { hist.page = Math.max(1, hist.page - 1); loadHistory(); });
$('#h-next').addEventListener('click', () => { hist.page += 1; loadHistory(); });
$('#h-last').addEventListener('click', () => { hist.page = Math.max(1, Math.ceil(hist.total / (hist.size || hist.total || 1))); loadHistory(); });

$('#historyBody').addEventListener('click', async (e) => {
  const tr = e.target.closest('tr[data-id]');
  if (!tr) return;
  const id = Number(tr.dataset.id);
  const copy = e.target.closest('[data-copy]');
  if (copy) {
    await navigator.clipboard.writeText(copy.dataset.copy);
    toast('Job link copied', 'ok', 2000);
    return;
  }
  const ext = e.target.closest('[data-url]');
  if (ext) {
    e.preventDefault();
    api.openExternal(ext.dataset.url).catch((err) => toast(err.message, 'error'));
  } else if (e.target.closest('[data-load]')) {
    await loadBid(id);
  } else if (e.target.closest('[data-del]')) {
    if (!confirm(`Delete bid #${id} from the database? (Files on disk are kept.)`)) return;
    await api.deleteResume(id);
    if (state.id === id) {
      state.dirty = false;
      newBid();
      switchTab('history');
    }
    loadHistoryDates();
    loadHistory();
  }
});

$('#historyBody').addEventListener('change', async (e) => {
  const sel = e.target.closest('[data-status]');
  if (!sel) return;
  const id = Number(sel.closest('tr').dataset.id);
  await api.updateResume(id, { status: sel.value });
  sel.className = `status-select st-${sel.value}`;
  if (state.id === id && state.row) state.row.status = sel.value;
  toast(`#${id} marked ${sel.value}`, 'ok');
});

async function loadBid(id) {
  if (state.dirty && !confirm('Discard unsaved changes and open this bid?')) return;
  const row = await api.getResume(id);
  state.id = row.id;
  state.row = row;
  state.aiMeta = { ai_provider: row.ai_provider, ai_model: row.ai_model, prompt_used: row.prompt_used };
  state.template = row.template || state.template;
  state.keywords = [];
  setForm(row);
  $('#cl-text').value = row.cover_letter || '';
  renderKeywords();
  renderTemplateList();
  setResume(row.resume_json, { render: false });
  setDirty(false);
  switchTab('builder');
  renderPreview();
}

// ---------------- base profile ----------------

// Edits live in base.model until saved; base.saved is the stored version for dirty checks.
const base = { saved: null, model: null, mode: 'form', jsonError: null, loaded: false };
const baseEditor = createProfileEditor($('#baseForm'), (model) => {
  base.model = model;
  updateBaseState();
  renderBasePreview();
});

function baseIsDirty() {
  return base.loaded && (base.jsonError != null || JSON.stringify(base.model) !== base.saved);
}

function updateBaseState() {
  const dirty = baseIsDirty();
  $('#baseSaveBtn').disabled = !dirty || base.jsonError != null;
  $('#baseDiscardBtn').disabled = !dirty;
  const st = $('#baseStatus');
  if (base.jsonError) {
    st.textContent = 'Invalid JSON: ' + base.jsonError;
    st.className = 'hint err';
  } else if (dirty) {
    st.textContent = '● Unsaved changes';
    st.className = 'hint warn';
  } else {
    st.textContent = '✓ All changes saved';
    st.className = 'hint ok';
  }
  $('.tab[data-tab="base"]').classList.toggle('has-changes', dirty);
}

function setBaseModel(model, { saved = false } = {}) {
  base.model = JSON.parse(JSON.stringify(model));
  if (saved) base.saved = JSON.stringify(base.model);
  base.jsonError = null;
  baseEditor.load(base.model);
  $('#base-json').value = JSON.stringify(base.model, null, 2);
  base.loaded = true;
  updateBaseState();
  renderBasePreview();
}

async function loadBase({ force = false } = {}) {
  if (base.loaded && !force) {
    renderBasePreview();
    return;
  }
  const s = await api.getSettings();
  setBaseModel(JSON.parse(s.base_resume), { saved: true });
}

const renderBasePreview = debounce(async () => {
  if (!base.model || base.jsonError) return;
  try {
    await loadPaper($('#baseFrame'), $('#basePaperHolder'), await api.renderResumeHtml(base.model, state.template));
    applyZoom($('#basePaperHolder'), 'fit');
  } catch (err) {
    toast('Preview failed: ' + escapeHtml(err.message), 'error');
  }
}, 400);

$('#baseMode').addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b || b.dataset.mode === base.mode) return;
  if (base.mode === 'json' && base.jsonError) {
    toast('Fix the JSON error before switching to the form.', 'error');
    return;
  }
  base.mode = b.dataset.mode;
  $$('#baseMode button').forEach((x) => x.classList.toggle('active', x === b));
  if (base.mode === 'form') baseEditor.load(base.model);
  else $('#base-json').value = JSON.stringify(base.model, null, 2);
  $('#baseForm').classList.toggle('hidden', base.mode !== 'form');
  $('#baseJsonWrap').classList.toggle('hidden', base.mode !== 'json');
});

$('#base-json').addEventListener('input', debounce(() => {
  try {
    base.model = JSON.parse($('#base-json').value);
    base.jsonError = null;
    renderBasePreview();
  } catch (err) {
    base.jsonError = err.message;
  }
  updateBaseState();
}, 300));

$('#baseSaveBtn').addEventListener('click', () => {
  if ($('#baseSaveBtn').disabled) return;
  busy('Saving base profile…', async () => {
    const json = JSON.stringify(base.model, null, 2);
    await api.saveSettings({ base_resume: json });
    base.saved = JSON.stringify(base.model);
    updateBaseState();
    toast('Base profile saved', 'ok');
  }).catch(() => {});
});

$('#baseDiscardBtn').addEventListener('click', () => {
  if (!confirm('Discard your unsaved changes to the base profile?')) return;
  setBaseModel(JSON.parse(base.saved));
});

$('#baseResetBtn').addEventListener('click', async () => {
  if (!confirm('Replace your base profile with the original CV content? This saves immediately.')) return;
  setBaseModel(JSON.parse(await api.resetBaseResume()), { saved: true });
  toast('Base profile reset to your original CV', 'ok');
});

// ---------------- settings ----------------

// Keys and models are kept per provider so switching the provider tab loses nothing.
const sf = {
  provider: 'anthropic',
  keys: { anthropic: '', openai: '' },
  models: { anthropic: '', openai: '' },
  modelLists: { anthropic: null, openai: null },
  saved: null
};
const PROVIDER_LABELS = { anthropic: 'Claude API key', openai: 'OpenAI API key' };
const KEY_PLACEHOLDERS = { anthropic: 'sk-ant-…', openai: 'sk-…' };

function settingsStatus(text, kind = '') {
  $('#s-testStatus').textContent = text;
  $('#s-testStatus').className = 'hint ' + kind;
}

// Proxy is stored as one "host:port:user:pass" string; the form edits its parts.
function splitProxy(raw) {
  const v = String(raw || '').trim();
  if (!v) return { host: '', port: '', user: '', pass: '' };
  if (/^https?:\/\//i.test(v)) {
    try {
      const u = new URL(v);
      return { host: u.hostname, port: u.port, user: decodeURIComponent(u.username), pass: decodeURIComponent(u.password) };
    } catch {
      return { host: '', port: '', user: '', pass: '' };
    }
  }
  const [host = '', port = '', user = '', ...rest] = v.split(':');
  return { host, port, user, pass: rest.join(':') };
}

function proxyFromFields() {
  const host = $('#s-proxyHost').value.trim();
  const port = $('#s-proxyPort').value.trim();
  const user = $('#s-proxyUser').value.trim();
  const pass = $('#s-proxyPass').value;
  if (!host && !port) return '';
  return user || pass ? `${host}:${port}:${user}:${pass}` : `${host}:${port}`;
}

// The proxy the AI calls should use right now ('' when switched off).
function activeProxy() {
  return $('#s-proxyOn').checked ? proxyFromFields() : '';
}

function renderProxyEnabled() {
  $('#s-proxyFields').classList.toggle('disabled', !$('#s-proxyOn').checked);
}

function captureProviderInputs() {
  sf.keys[sf.provider] = $('#s-key').value.trim();
  if ($('#s-model').value) sf.models[sf.provider] = $('#s-model').value;
}

function renderModelSelect() {
  const list = sf.modelLists[sf.provider];
  const current = sf.models[sf.provider];
  const opts = list ? [...list] : [];
  if (current && !opts.some((m) => m.id === current)) opts.unshift({ id: current, name: current + (list ? ' (not in list)' : '') });
  $('#s-model').innerHTML = opts.length
    ? opts.map((m) => `<option value="${escapeHtml(m.id)}" ${m.id === current ? 'selected' : ''}>${escapeHtml(m.name && m.name !== m.id ? `${m.name} — ${m.id}` : m.id)}</option>`).join('')
    : '<option value="">Enter an API key, then Load models</option>';
  if (!current && opts.length) sf.models[sf.provider] = opts[0].id;
  $('#s-modelHint').textContent = list ? `${list.length} models available for this key.` : 'Click “Load models” to see every model your key can use.';
}

function renderProvider() {
  $$('#s-provider button').forEach((b) => b.classList.toggle('active', b.dataset.provider === sf.provider));
  $('#s-keyLabel').textContent = PROVIDER_LABELS[sf.provider];
  $('#s-key').placeholder = KEY_PLACEHOLDERS[sf.provider];
  $('#s-key').value = sf.keys[sf.provider];
  renderModelSelect();
}

function collectSettings() {
  captureProviderInputs();
  return {
    ai_provider: sf.provider,
    claude_api_key: sf.keys.anthropic,
    claude_model: sf.models.anthropic,
    openai_api_key: sf.keys.openai,
    openai_model: sf.models.openai,
    claude_proxy: proxyFromFields(),
    proxy_enabled: $('#s-proxyOn').checked ? '1' : '0',
    prompt_resume: $('#s-promptResume').value.trim(),
    prompt_cover: $('#s-promptCover').value.trim(),
    output_dir: $('#s-out').value.trim() || 'G:/Document/Resume',
    default_template: $('#s-template').value
  };
}

function updateSettingsDirty() {
  if (!sf.saved) return;
  const dirty = JSON.stringify(collectSettings()) !== sf.saved;
  $('#s-save').disabled = !dirty;
  $('#s-status').textContent = dirty ? '● Unsaved changes' : '✓ Saved';
  $('#s-status').className = 'hint ' + (dirty ? 'warn' : 'ok');
  $('.tab[data-tab="settings"]').classList.toggle('has-changes', dirty);
}

async function loadModels({ quiet = false } = {}) {
  captureProviderInputs();
  const provider = sf.provider;
  if (!sf.keys[provider]) {
    if (!quiet) settingsStatus('Enter the API key first.', 'err');
    return;
  }
  if (!quiet) settingsStatus('Loading models…');
  try {
    sf.modelLists[provider] = await api.listModels({ provider, apiKey: sf.keys[provider], proxy: activeProxy() });
    if (provider === sf.provider) renderModelSelect();
    if (!quiet) settingsStatus(`✓ Loaded ${sf.modelLists[provider].length} models`, 'ok');
    updateSettingsDirty();
  } catch (err) {
    if (!quiet) settingsStatus('Could not load models: ' + err.message, 'err');
  }
}

async function loadSettings() {
  const s = await api.getSettings();
  state.settings = s;
  sf.provider = s.ai_provider;
  sf.keys = { anthropic: s.claude_api_key, openai: s.openai_api_key };
  sf.models = { anthropic: s.claude_model, openai: s.openai_model };
  const px = splitProxy(s.claude_proxy);
  $('#s-proxyHost').value = px.host;
  $('#s-proxyPort').value = px.port;
  $('#s-proxyUser').value = px.user;
  $('#s-proxyPass').value = px.pass;
  $('#s-proxyOn').checked = s.proxy_enabled !== '0';
  renderProxyEnabled();
  $('#s-promptResume').value = s.prompt_resume;
  $('#s-promptCover').value = s.prompt_cover;
  $('#s-out').value = s.output_dir;
  $('#s-template').innerHTML = state.templates.map((t) => `<option value="${t.id}" ${t.id === s.default_template ? 'selected' : ''}>${escapeHtml(t.name)}</option>`).join('');
  renderProvider();
  sf.saved = JSON.stringify(collectSettings());
  updateSettingsDirty();
  const db = await api.getDbConfig();
  $('#s-db').textContent = db.label || `Connected to ${db.user}@${db.host}:${db.port}/${db.database}. Change it from the login screen.`;
  if (sf.keys[sf.provider] && !sf.modelLists[sf.provider]) loadModels({ quiet: true });
}

$('#s-provider').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-provider]');
  if (!b || b.dataset.provider === sf.provider) return;
  captureProviderInputs();
  sf.provider = b.dataset.provider;
  renderProvider();
  updateSettingsDirty();
  if (sf.keys[sf.provider] && !sf.modelLists[sf.provider]) loadModels({ quiet: true });
});

$('#s-key').addEventListener('change', () => {
  captureProviderInputs();
  sf.modelLists[sf.provider] = null;
  renderModelSelect();
  if (sf.keys[sf.provider]) loadModels({ quiet: true });
});

$('#s-model').addEventListener('change', () => {
  captureProviderInputs();
  updateSettingsDirty();
});

$('#s-loadModels').addEventListener('click', () => loadModels());

$('#s-proxyOn').addEventListener('change', renderProxyEnabled);

for (const id of ['#s-key', '#s-proxyOn', '#s-proxyHost', '#s-proxyPort', '#s-proxyUser', '#s-proxyPass', '#s-promptResume', '#s-promptCover', '#s-out', '#s-template']) {
  $(id).addEventListener('input', updateSettingsDirty);
  $(id).addEventListener('change', updateSettingsDirty);
}

for (const [btn, input] of [['#s-showKey', '#s-key'], ['#s-showProxy', '#s-proxyPass']]) {
  $(btn).addEventListener('click', () => {
    const i = $(input);
    i.type = i.type === 'password' ? 'text' : 'password';
    $(btn).textContent = i.type === 'password' ? 'Show' : 'Hide';
  });
}

$$('[data-reset-prompt]').forEach((b) =>
  b.addEventListener('click', async () => {
    const kind = b.dataset.resetPrompt;
    if (!confirm(`Replace the ${kind === 'cover' ? 'cover letter' : 'resume'} prompt with the default? (Takes effect when you save.)`)) return;
    const d = await api.defaultPrompts();
    $(kind === 'cover' ? '#s-promptCover' : '#s-promptResume').value = d[kind];
    updateSettingsDirty();
  })
);

$('#s-testProxy').addEventListener('click', async () => {
  settingsStatus('Testing proxy…');
  try {
    const proxy = proxyFromFields();
    if (!proxy) throw new Error('Enter the proxy IP and port first.');
    const r = await api.testProxy(proxy, sf.provider);
    settingsStatus(`✓ Proxy reached ${r.host} (HTTP ${r.status}, ${r.ms} ms)`, 'ok');
  } catch (err) {
    settingsStatus('Proxy failed: ' + err.message, 'err');
  }
});

$('#s-test').addEventListener('click', async () => {
  captureProviderInputs();
  settingsStatus(`Testing ${sf.models[sf.provider] || 'model'}…`);
  try {
    const reply = await api.testKey({ provider: sf.provider, apiKey: sf.keys[sf.provider], model: sf.models[sf.provider], proxy: activeProxy() });
    settingsStatus(`✓ ${sf.models[sf.provider]} works (replied "${reply.slice(0, 40)}")`, 'ok');
  } catch (err) {
    settingsStatus(err.message, 'err');
  }
});

$('#s-save').addEventListener('click', () => {
  if ($('#s-save').disabled) return;
  busy('Saving settings…', async () => {
    await api.saveSettings(collectSettings());
    await loadSettings();
    toast('Settings saved', 'ok');
  }).catch(() => {});
});

// ---------------- tabs ----------------

function switchTab(name) {
  $$('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === `view-${name}`));
  if (name === 'history') loadHistoryDates().then(loadHistory);
  if (name === 'cover') renderCoverPreview();
  if (name === 'base') loadBase();
  if (name === 'settings') loadSettings();
  if (name === 'builder' && state.resume && $('#zoom').value === 'fit') applyZoom($('#paperHolder'), 'fit');
}

$$('.tab').forEach((t) => t.addEventListener('click', () => switchTab(t.dataset.tab)));

// ---------------- init ----------------

(async () => {
  $('#h-status').innerHTML += STATUSES.map((s) => `<option>${s}</option>`).join('');
  state.templates = await api.listTemplates();
  setupCombos();
  loadFieldOptions();
  await loadSettings();
  state.template = state.settings.default_template;
  renderTemplateList();
  updateHeader();
  renderPreview();
  const activeKey = state.settings.ai_provider === 'openai' ? state.settings.openai_api_key : state.settings.claude_api_key;
  if (!activeKey) toast('Add your API key in <b>Settings</b> to enable AI generation.', 'info', 6000);
})();
