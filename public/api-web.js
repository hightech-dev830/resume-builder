// Web stand-in for the desktop preload.js: the same window.api, backed by POST /api/rpc.
(function () {
  document.documentElement.classList.add('web');
  document.addEventListener('DOMContentLoaded', () => document.body.classList.add('web'));

  function revive(v) {
    if (v && typeof v === 'object') {
      if (typeof v.__b64 === 'string') return Uint8Array.from(atob(v.__b64), (c) => c.charCodeAt(0));
      for (const k of Object.keys(v)) v[k] = revive(v[k]);
    }
    return v;
  }

  async function call(method, ...args) {
    const res = await fetch('/api/rpc', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ method, args })
    });
    if (res.status === 401) {
      location.href = '/login.html';
      throw new Error('Session expired — please log in again.');
    }
    let body;
    try {
      body = await res.json();
    } catch {
      throw new Error(`Server error (HTTP ${res.status}). ${res.status === 504 ? 'The request took too long.' : ''}`);
    }
    if (!body.ok) throw new Error(body.error);
    return revive(body.data);
  }

  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // "Open folder" on the web: a small page listing the bid's files with download links.
  async function openFolder(dir) {
    const win = window.open('', '_blank');
    const files = await call('files:list', dir);
    const rows = files.map((f) => `<li><a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.name)}</a> <span>${(f.size / 1024).toFixed(0)} KB</span></li>`).join('');
    const html = `<!doctype html><meta charset="utf-8"><title>${esc(dir)}</title>
      <style>body{font:14px 'Segoe UI',system-ui,sans-serif;margin:32px;color:#1c2433;background:#fff}h1{font-size:16px;color:#1e3a6e}
      li{margin:8px 0}a{color:#2f6fd6}span{color:#6b7486;margin-left:8px}p{color:#6b7486}</style>
      <h1>📁 ${esc(dir)}</h1><ul>${rows || '<li>No files yet.</li>'}</ul><p>Links are valid for 10 minutes.</p>`;
    if (win) {
      win.document.write(html);
      win.document.close();
    }
    return true;
  }

  // Files are paths in Supabase Storage; folders have no extension.
  async function openPath(p) {
    if (!p) throw new Error('No file yet.');
    if (!/\.[a-z0-9]+$/i.test(p)) return openFolder(p);
    const win = window.open('', '_blank');
    const url = await call('files:url', p);
    if (win) win.location.href = url;
    else location.href = url;
    return true;
  }

  window.api = {
    login: (username, password) => call('auth:login', { username, password }).then(() => (location.href = '/app.html')),
    logout: () => call('auth:logout').then(() => (location.href = '/login.html')),
    getDbConfig: () => call('db:getConfig'),
    saveDbConfig: () => Promise.reject(new Error('The web version uses Supabase; configure it with environment variables.')),

    getSettings: () => call('settings:get'),
    saveSettings: (values) => call('settings:save', values),
    resetBaseResume: () => call('settings:resetBase'),
    testKey: (cfg) => call('settings:testKey', cfg),
    listModels: (cfg) => call('settings:listModels', cfg),
    testProxy: (proxy, provider) => call('settings:testProxy', proxy, provider),
    defaultPrompts: () => call('settings:defaultPrompts'),
    getUsage: () => call('usage:summary', Intl.DateTimeFormat().resolvedOptions().timeZone),
    getPrices: () => call('usage:prices'),

    listTemplates: () => call('templates:list'),
    getBaseResume: () => call('base:get'),
    renderResumeHtml: (resume, tpl) => call('render:resumeHtml', resume, tpl),
    renderCoverHtml: (resume, letter, tpl, job) => call('render:coverHtml', resume, letter, tpl, job),
    renderResumePdf: (resume, tpl) => call('render:resumePdf', resume, tpl),
    renderCoverPdf: (resume, letter, tpl, job) => call('render:coverPdf', resume, letter, tpl, job),

    tailor: (job) => call('ai:tailor', job),
    coverLetter: (payload) => call('ai:coverLetter', payload),

    listResumes: (filter) => call('resumes:list', filter),
    listDates: (tz) => call('resumes:dates', tz),
    getOptions: () => call('options:get'),
    addOption: (key, value) => call('options:add', key, value),
    removeOption: (key, value) => call('options:remove', key, value),
    getResume: (id) => call('resumes:get', id),
    createResume: (data) => call('resumes:create', data),
    updateResume: (id, data) => call('resumes:update', id, data),
    deleteResume: (id) => call('resumes:delete', id),
    exportPdf: (id) => call('resumes:exportPdf', id),
    exportCover: (id) => call('resumes:exportCover', id),

    openPath,
    showItem: openPath,
    openExternal: async (url) => {
      if (!/^https?:\/\//i.test(url || '')) throw new Error('Not a web link');
      window.open(url, '_blank', 'noopener');
      return true;
    }
  };

  // Keep logged-out visitors on the login page.
  call('auth:check').then((ok) => {
    if (!ok) location.href = '/login.html';
  });
})();
