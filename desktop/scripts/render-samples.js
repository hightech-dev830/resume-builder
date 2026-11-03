// Renders the base profile with every template to PDFs: npx electron scripts/render-samples.js <outDir>
const fs = require('fs');
const path = require('path');
const { app, BrowserWindow } = require('electron');
const templates = require('../src/templates');
const { htmlToPdf } = require('../src/pdf');
const base = require('../src/baseResume');

// No main window here, so keep the app alive between hidden render windows.
app.on('window-all-closed', () => {});

app.whenReady().then(async () => {
  const out = process.argv[2] || path.join(__dirname, '..', 'samples');
  fs.mkdirSync(out, { recursive: true });
  for (const t of templates.listTemplates()) {
    fs.writeFileSync(path.join(out, `${t.id}.pdf`), await htmlToPdf(templates.renderResumeHtml(base, t.id)));
    console.log('rendered', t.id);
  }
  const letter = 'Dear Acme team,\n\nThis is a sample cover letter body.\n\nSincerely,\nKevin Burgess';
  fs.writeFileSync(path.join(out, 'cover_kevin.pdf'), await htmlToPdf(templates.renderCoverLetterHtml(base, letter, 'kevin', { company: 'Acme', role: 'Senior Engineer' })));
  fs.writeFileSync(path.join(out, 'cover_modern.pdf'), await htmlToPdf(templates.renderCoverLetterHtml(base, letter, 'modern', { company: 'Acme' })));

  // Contact sheet: first page of every template, side by side.
  const pad = '<style>@media screen{body{padding:0.5in 0.6in}}</style>';
  const cells = templates.listTemplates().map((t) => {
    const html = templates.renderResumeHtml(base, t.id).replace('</head>', pad + '</head>');
    return `<div class="c"><div class="t">${t.name}</div><iframe srcdoc="${html.replace(/&/g, '&amp;').replace(/"/g, '&quot;')}"></iframe></div>`;
  }).join('');
  const sheet = new BrowserWindow({ show: false, width: 2000, height: 1700, webPreferences: { offscreen: false } });
  await sheet.loadURL('data:text/html;charset=utf-8;base64,' + Buffer.from(`<!doctype html><html><head><style>
    body{margin:0;background:#ccc;display:grid;grid-template-columns:repeat(6,320px);gap:10px;padding:10px;font:14px Segoe UI}
    .c{width:320px;height:440px;overflow:hidden;background:#fff}.t{background:#333;color:#fff;padding:3px 6px}
    iframe{width:816px;height:1056px;border:0;transform:scale(.392);transform-origin:0 0}</style></head><body>${cells}</body></html>`).toString('base64'));
  await new Promise((r) => setTimeout(r, 1500));
  fs.writeFileSync(path.join(out, 'contact-sheet.png'), (await sheet.webContents.capturePage()).toPNG());
  sheet.destroy();
  app.quit();
});
