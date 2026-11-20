// Bridge between the Python bots and the Resume Builder code, so a Python bot writes the
// SAME resume as the extension: same prompt, base profile, JSON schema, merge rules,
// HTML template and PDF renderer. Only the AI call differs (a web-browser AI instead of an API).
//
//   node resume_bridge.js settings
//       -> JSON: { prompt, base, template, outputDir, source, warning? }
//          prompt = your Settings prompt (Supabase) + the app's output rules
//   node resume_bridge.js save-prompt <file.txt>
//       -> saves the text as Resume Builder's resume prompt (Supabase setting prompt_resume)
//   node resume_bridge.js build <request.json>
//       request = { base, tailored, template, outDir, fileBase }
//       -> merges the AI answer into the base profile (same rules as the app), writes
//          <outDir>/<fileBase>_Resume.pdf and <outDir>/resume.json, prints { pdf, resume }
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..', '..');
const ai = require(path.join(ROOT, 'lib', 'shared', 'ai.js'));
const templates = require(path.join(ROOT, 'lib', 'shared', 'templates.js'));
const baseSeed = require(path.join(ROOT, 'lib', 'shared', 'baseResume.js'));
const { webFontsInline } = require(path.join(ROOT, 'lib', 'fonts.js'));
const { htmlToPdf } = require(path.join(ROOT, 'lib', 'pdf.js'));

const DEFAULT_DIR = process.platform === 'win32' ? 'G:/Document/Resume_bot' : path.join(require('os').homedir(), 'Documents', 'Resume_bot');

async function settings() {
  const out = {
    prompt: ai.DEFAULT_RESUME_PROMPT,
    base: baseSeed,
    template: 'kevin',
    outputDir: DEFAULT_DIR,
    source: 'built-in defaults',
  };
  try {
    const db = require(path.join(ROOT, 'lib', 'db.js'));
    const all = await db.getAllSettings();
    if ((all.prompt_resume || '').trim()) out.prompt = all.prompt_resume.trim();
    if (all.base_resume) out.base = JSON.parse(all.base_resume);
    if (all.default_template) out.template = all.default_template;
    const dir = all[`local_output_dir_${process.platform}`];
    if (dir !== undefined && String(dir).trim()) out.outputDir = String(dir).trim();
    out.source = 'Resume Builder settings (Supabase)';
  } catch (err) {
    out.warning = `Supabase not reachable (${err.message}); using built-in prompt and base profile.`;
  }
  out.instructions = out.prompt;          // the editable part (Settings prompt)
  out.rules = ai.TAILOR_OUTPUT_RULES;     // fixed: how the JSON answer is returned
  out.defaultPrompt = ai.DEFAULT_RESUME_PROMPT;
  out.prompt += ai.TAILOR_OUTPUT_RULES;
  out.schema = ai.TAILOR_SCHEMA;
  return out;
}

// Saves a prompt as Resume Builder's resume prompt (shared with the extension and web app).
async function savePrompt(file) {
  const text = fs.readFileSync(file, 'utf8').trim();
  if (!text) throw new Error('The prompt is empty.');
  const db = require(path.join(ROOT, 'lib', 'db.js'));
  await db.setSetting('prompt_resume', text);
  return { saved: text.length };
}

async function build(requestFile) {
  const req = JSON.parse(fs.readFileSync(requestFile, 'utf8'));
  const resume = ai.mergeTailored(req.base, req.tailored);
  fs.mkdirSync(req.outDir, { recursive: true });
  const pdfPath = path.join(req.outDir, `${req.fileBase}_Resume.pdf`);
  const html = await webFontsInline(templates.renderResumeHtml(resume, req.template));
  fs.writeFileSync(pdfPath, await htmlToPdf(html));
  fs.writeFileSync(path.join(req.outDir, 'resume.json'), JSON.stringify(resume, null, 2));
  return { pdf: pdfPath, resume };
}

(async () => {
  const [cmd, arg] = process.argv.slice(2);
  let result;
  if (cmd === 'settings') result = await settings();
  else if (cmd === 'build') result = await build(arg);
  else if (cmd === 'save-prompt') result = await savePrompt(arg);
  else throw new Error('usage: node resume_bridge.js settings | build <request.json> | save-prompt <file.txt>');
  process.stdout.write(JSON.stringify(result));
  process.exit(0);   // the PDF browser and Supabase client would keep Node alive
})().catch((err) => {
  process.stderr.write(String(err && err.stack || err));
  process.exit(1);
});
