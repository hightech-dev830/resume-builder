// Bridge between the Python bots and the Resume Builder code, so a Python bot writes the
// SAME resume as the extension: same prompt, base profile, JSON schema, merge rules,
// HTML template and PDF renderer. Only the AI call differs (a web-browser AI instead of an API).
//
//   node resume_bridge.js settings
//       -> JSON: { prompt, base, template, outputDir, source, warning? }
//          prompt = your Settings prompt (Supabase) + the app's output rules
//   node resume_bridge.js save-prompt <file.txt>
//       -> saves the text as Resume Builder's resume prompt (Supabase setting prompt_resume)
//   node resume_bridge.js ai-complete <request.json>
//       -> one Claude / OpenAI API call with your saved key (see aiComplete below)
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

// One AI call through an API key (Claude or OpenAI), with the SAME code as the web app and the
// extension: lib/shared/ai.js complete() (official SDKs, proxy from Settings, Claude fallbacks).
// The key and default model come from Resume Builder Settings (Supabase); tokens + cost go to
// the ai_usage table like every other call.
//   request = { provider: 'anthropic' | 'openai', model, system, user, schema?, maxTokens, effort, task }
//   -> { text, model, usage }
async function aiComplete(requestFile) {
  const req = JSON.parse(fs.readFileSync(requestFile, 'utf8'));
  const provider = req.provider;
  if (!ai.KEY_SETTING[provider]) throw new Error(`unknown API provider ${provider}`);
  const db = require(path.join(ROOT, 'lib', 'db.js'));
  const s = await db.getAllSettings();
  const cfg = {
    provider,
    apiKey: s[ai.KEY_SETTING[provider]] || '',
    model: req.model || s[ai.MODEL_SETTING[provider]] || ai.PROVIDERS[provider].defaultModel,
    proxy: s.proxy_enabled === '0' ? '' : s.claude_proxy || ''
  };
  if (!cfg.apiKey) throw new Error(`${ai.PROVIDERS[provider].label} API key is not set. Add it in the AutoBid app's AI tab (or Resume Builder Settings).`);
  let usage = null;
  let usageMod = null;
  try { usageMod = require(path.join(ROOT, 'lib', 'usage.js')); } catch {}
  const saving = [];
  ai.onUsage((u) => {
    usage = u;
    if (usageMod) saving.push(Promise.resolve(usageMod.track({ task: req.task || 'autobid' }, () => usageMod.record(u))).catch(() => {}));
  });
  const text = await ai.complete(cfg, {
    system: req.system || 'You are a helpful assistant.',
    user: req.user,
    schema: req.schema || undefined,
    maxTokens: req.maxTokens || 16000,
    effort: req.effort || 'medium'
  });
  await Promise.all(saving);   // usage row saved before Node exits
  return { text, model: (usage && usage.model) || cfg.model, usage };
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
  else if (cmd === 'ai-complete') result = await aiComplete(arg);
  else throw new Error('usage: node resume_bridge.js settings | build <request.json> | save-prompt <file.txt> | ai-complete <request.json>');
  process.stdout.write(JSON.stringify(result));
  process.exit(0);   // the PDF browser and Supabase client would keep Node alive
})().catch((err) => {
  // first line = the readable message (Python shows it), then the stack for debugging
  process.stderr.write(`${(err && err.message) || err}\n${(err && err.stack) || ''}`);
  process.exit(1);
});
