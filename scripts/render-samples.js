// Renders the base profile with every template through the web PDF pipeline
// (web fonts + headless Chromium): node scripts/render-samples.js <outDir>
const fs = require('fs');
const path = require('path');
const templates = require('../lib/shared/templates');
const base = require('../lib/shared/baseResume');
const { webFontsInline } = require('../lib/fonts');
const { htmlToPdf } = require('../lib/pdf');

(async () => {
  const out = process.argv[2] || path.join(__dirname, '..', 'samples');
  fs.mkdirSync(out, { recursive: true });
  for (const t of templates.listTemplates()) {
    const t0 = Date.now();
    fs.writeFileSync(path.join(out, `${t.id}.pdf`), await htmlToPdf(await webFontsInline(templates.renderResumeHtml(base, t.id))));
    console.log(`rendered ${t.id} (${Date.now() - t0} ms)`);
  }
  const letter = 'Dear Acme team,\n\nThis is a sample cover letter body.\n\nBest regards,\nKevin Burgess';
  fs.writeFileSync(path.join(out, 'cover_kevin.pdf'), await htmlToPdf(await webFontsInline(templates.renderCoverLetterHtml(base, letter, 'kevin', { company: 'Acme', role: 'Senior Engineer' }))));
  console.log('rendered cover letter');
  process.exit(0);
})().catch((e) => {
  console.error('FAILED:', e.message);
  process.exit(1);
});
