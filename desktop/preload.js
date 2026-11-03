const { contextBridge, ipcRenderer } = require('electron');

// Every call resolves to the data or throws an Error with the main-process message.
async function call(channel, ...args) {
  const res = await ipcRenderer.invoke(channel, ...args);
  if (!res.ok) throw new Error(res.error);
  return res.data;
}

contextBridge.exposeInMainWorld('api', {
  login: (username, password) => call('auth:login', { username, password }),
  logout: () => call('auth:logout'),
  getDbConfig: () => call('db:getConfig'),
  saveDbConfig: (cfg) => call('db:saveConfig', cfg),

  getSettings: () => call('settings:get'),
  saveSettings: (values) => call('settings:save', values),
  resetBaseResume: () => call('settings:resetBase'),
  testKey: (cfg) => call('settings:testKey', cfg),
  listModels: (cfg) => call('settings:listModels', cfg),
  testProxy: (proxy, provider) => call('settings:testProxy', proxy, provider),
  defaultPrompts: () => call('settings:defaultPrompts'),

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

  openPath: (p) => call('shell:openPath', p),
  showItem: (p) => call('shell:showItem', p),
  openExternal: (url) => call('shell:openExternal', url)
});
