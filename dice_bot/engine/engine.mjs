// Bridge from the Python bot to the resume engine of extension-v2 (one source of truth):
// the ATS-first prompt, merging the model's answer onto the profile's fixed facts, the ATS score,
// the consistency (fraud-risk) check, the fix-round prompt, the cover letter prompt, Word files,
// and the rule answers + example knowledge base for extra application questions.
//
// Usage: node engine.mjs <command>   with a JSON request on stdin; prints { ok, data } | { ok: false, error }.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { importProfile, totalYears, personalForAI } from '../../extension-v2/lib/profiles.js';
import { buildPrompt, fixPrompt, parseJson, mergeTailored, toText, coverPrompt, cleanLetter, TAILOR_SCHEMA } from '../../extension-v2/lib/resume.js';
import { atsScore } from '../../extension-v2/lib/ats.js';
import { validate, issuesForModel } from '../../extension-v2/lib/validate.js';
import { resumeDocx, coverDocx, styleFromHtml } from '../../extension-v2/lib/docx.js';
import { renderResumeHtml, listTemplates } from '../../extension-v2/lib/templates.js';
import { rulePlan } from '../../extension-v2/lib/rules.js';
import { setKB, examplesForAI } from '../../extension-v2/lib/examples.js';

const here = dirname(fileURLToPath(import.meta.url));
const V2 = `${here}/../../extension-v2`;

function analyze(profile, resume, job) {
  const ats = atsScore(resume, job.jd || '', job.title || '', totalYears(profile.resume));
  const val = validate(resume, profile.resume, { stackMode: profile.prefs?.stackMode || 'adjacent' });
  return { ats: { score: ats.score, parts: ats.parts, missing: ats.missing, skillsOnly: ats.skillsOnly, jdYears: ats.jdYears }, val };
}

function write(path, bytes) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, bytes);
  return path;
}

const commands = {
  // a profile JSON file (full profile or a bare JSON Resume) -> normalized profile
  profile: ({ path }) => {
    const p = importProfile(readFileSync(path, 'utf8'));
    return { ...p, totalYears: totalYears(p.resume) };
  },

  templates: () => listTemplates(),

  schema: () => TAILOR_SCHEMA,

  // the resume prompt (same as extension-v2)
  prompt: ({ profile, job, atsTarget = 95 }) => ({ prompt: buildPrompt({ profile, job, atsTarget }) }),

  // the model's answer (text) -> merged resume, its editable text, ATS score and consistency check
  merge: ({ profile, answer, job }) => {
    const tailored = typeof answer === 'string' ? parseJson(answer) : answer;
    const resume = mergeTailored(profile.resume, tailored);
    return { tailored, resume, text: toText(resume), ...analyze(profile, resume, job) };
  },

  // the correction-round prompt for the issues found
  fix: ({ val, ats }) => {
    const missing = (ats?.missing || []);
    return { prompt: fixPrompt(issuesForModel(val, { missing, skillsOnly: ats?.skillsOnly || [] })), needed: val.issues.some((x) => x.severity !== 'low') || missing.length > 0 };
  },

  cover_prompt: ({ profile, resume, job }) => ({ prompt: coverPrompt({ profile, resume, job }) }),

  clean_letter: ({ text }) => ({ text: cleanLetter(text) }),

  // Word files; the template only sets font, size and accent colour
  docx: ({ resume, letter, job, template = 'professional', resumePath, coverPath }) => {
    const style = styleFromHtml(renderResumeHtml(resume, template));
    const out = { resume: write(resumePath, resumeDocx(resume, style)) };
    if (letter && coverPath) out.cover = write(coverPath, coverDocx(resume, letter, { company: job?.company, title: job?.title }, style));
    return out;
  },

  // extra application questions: the rule answers + example knowledge base (no AI)
  rules: ({ snapshot, personal, coverLetter = '', salary = '' }) => {
    setKB(JSON.parse(readFileSync(`${V2}/data/examples.json`, 'utf8')));
    const plan = rulePlan(snapshot, { profile: personal, coverLetter, jobSalary: salary, uploadCover: true });
    return { actions: plan.actions, left: plan.aiFields.map((f) => f.question || f.label || f.name || f.id) };
  },

  // the example answers closest to some questions (for an AI prompt, if one is used later)
  examples: ({ fields, personal, url }) => {
    setKB(JSON.parse(readFileSync(`${V2}/data/examples.json`, 'utf8')));
    return { text: examplesForAI(fields, { url, profile: personal }) };
  },

  personal_for_ai: ({ personal }) => personalForAI(personal)
};

const cmd = process.argv[2];
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (c) => { input += c; });
process.stdin.on('end', () => {
  try {
    if (!commands[cmd]) throw new Error(`Unknown command ${cmd}`);
    const data = commands[cmd](input.trim() ? JSON.parse(input) : {});
    process.stdout.write(JSON.stringify({ ok: true, data }));
  } catch (err) {
    process.stdout.write(JSON.stringify({ ok: false, error: err.message }));
  }
});
