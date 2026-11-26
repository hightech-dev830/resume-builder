// Writes a ready-to-use prompt (.md) for one profile, for a chat where the ONLY user message is a job
// description: paste the .md as the system prompt / project instructions, then send each JD.
// The rules come straight from lib/resume.js (the extension's own prompt), plus the self-check round the
// extension runs with its checker, so the .md never drifts from the extension.
//
//   node prompts/make-prompt.mjs <profile.json> [out.md]
//   <profile.json>: a profile exported from the extension (Profiles → Export), or a plain JSON Resume
//   default output: prompts/out/<Name>.md (prompts/out is git-ignored: it holds personal details)
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { importProfile, totalYears } from '../lib/profiles.js';
import { buildPrompt } from '../lib/resume.js';
import { findTech } from '../lib/ats.js';

const [src, outArg] = process.argv.slice(2);
if (!src) {
  console.error('usage: node prompts/make-prompt.mjs <profile.json> [out.md]');
  process.exit(1);
}
const raw = readFileSync(src, 'utf8');
// a CommonJS resume file (module.exports = {...}) is accepted too, e.g. desktop/src/baseResume.js
const json = /module\.exports\s*=/.test(raw) ? JSON.stringify(new Function('module', `${raw}; return module.exports;`)({ exports: {} })) : raw;
const profile = importProfile(json);

// jobs without a real stack: take the technologies their own bullets name (an exported profile is better)
const derived = [];
for (const w of profile.resume.work || []) {
  if (!w.industry && w.context) w.industry = w.context;
  if (!(w.stack || []).length) {
    w.stack = [...new Set(findTech([w.description, ...(w.highlights || [])].join('\n')).filter((t) => !t.generic).map((t) => t.name))];
    derived.push(w.name);
  }
}

const b = profile.resume.basics || {};
const contact = [b.email, b.phone, [b.location?.city, b.location?.region].filter(Boolean).join(', '),
  ...(b.profiles || []).map((p) => (p.url || '').replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, ''))].filter(Boolean).join(' | ');

// the extension's prompt, without its per-job tail (JOB + OUTPUT): here the job comes in the user's message
let core = buildPrompt({ profile, job: { title: '', company: '', jd: '' }, atsTarget: 95 });
core = core.slice(0, core.indexOf('\n=== JOB ==='));
const swaps = [
  ["Rewrite the candidate's resume for the job below", "Rewrite the candidate's resume for the job description in the user's message"],
  ['Must-haves found in the JD: (read the JD). Also relevant: (read the JD).', 'Must-haves: the technologies and practices the JD lists as required; also cover the preferred ones the profile supports.']
];
for (const [a, c] of swaps) {
  if (!core.includes(a)) throw new Error(`lib/resume.js changed: cannot find "${a.slice(0, 40)}…"; update make-prompt.mjs`);
  core = core.replace(a, c);
}

const md = `# Resume writer: ${profile.name}

Paste everything below the line into your chat as the system prompt (or project instructions). Then send only a job description as the message. Generated from extension-v2 \`lib/resume.js\` on ${new Date().toISOString().slice(0, 10)}${derived.length ? `; the real stack of ${derived.join(', ')} was taken from their bullets (export the profile from the extension's Experience tab for exact stacks)` : ''}.

---

${core}

CONTACT (print exactly): ${b.name || ''} | ${contact}

=== THE USER'S MESSAGE ===
Each user message is one job description (it may include the title, company and location). Do not ask questions: if the company, title or location are missing, take them from the JD; if they are not there, leave them out.

=== HOW TO WORK (all in one reply) ===
Step 1. Analyze the JD: company, title, the company's industry and product, the must-have and preferred technologies and practices, the required years, and the responsibilities. Map each must-have to the job(s) whose real stack backs it, or mark it "Skills only".
Step 2. Write the resume by the rules above.
Step 3. Audit your draft and fix every problem before answering:
  1. Facts: employers, dates, locations, education and the number and order of jobs exactly as in the profile.
  2. Titles: at most one level above the real title; no management title the profile does not have; titles progress over time.
  3. Timeline: no technology in a job that ended before it existed (LLM / RAG / agents not before 2022-2023, MCP not before 2024, Kubernetes not before 2015, GitHub Actions not before 2019).
  4. Evidence: every technology in a job's bullets is in that job's real stack or a close equivalent of something in it; otherwise move it to Skills only.
  5. Years: every "N+ years" claim fits the dates, overall (${totalYears(profile.resume)} years in total) and per technology (only the jobs that used it count).
  6. ATS: every must-have JD keyword is in Skills AND in at least one bullet of a job that backs it, using the JD's exact wording.
  7. Writing: no repeated bullets; numbers in about half the bullets; no "100%", "10x" or "zero downtime"; varied opening verbs and bullet lengths; no em dashes; none of the filler words.
Step 4. Answer in the format below.

=== OUTPUT ===
First a short "Match notes" section (at most 8 lines): company and title; the company's industry; must-haves covered in bullets; must-haves in Skills only (and why: no job backs them); what the audit fixed.

Then the final resume in exactly this plain-text layout (it reads as a clean resume, and it pastes straight into the Auto Bid extension's resume box for Analyze / PDF / Word):

\`\`\`
# ${b.name || 'Full Name'}
<headline>
${contact || '<email> | <phone> | <city, state> | <linkedin>'}

== SUMMARY ==
<3-4 sentences>

== CORE COMPETENCIES ==
<Group>: <comma-separated details>

== SKILLS ==
<Group>: <skill>, <skill>, ...

== EXPERIENCE ==

### <Title> | <Employer> | <YYYY-MM> - <YYYY-MM or Present> | <Location>
> <one-line description of the work>
- <bullet>
- <bullet>

== EDUCATION ==
<Degree, Field> | <School> | <YYYY-MM>
\`\`\`

If the user asks for JSON instead, reply with only this object in a \`\`\`json block ("work" has one entry per job of the profile, same order, "index" = the job's index):
\`\`\`json
{
  "company_industry": "the hiring company's industry and product, in a few words",
  "label": "headline under the name",
  "summary": "3-4 sentences",
  "highlights": [{ "name": "competency group", "value": "comma-separated details" }],
  "skills": [{ "name": "group", "keywords": ["skill", "..."] }],
  "work": [{ "index": 0, "position": "title", "description": "one line framing the work", "highlights": ["bullet", "..."] }],
  "keywords_matched": ["JD keyword now covered", "..."]
}
\`\`\`

If the user sends a follow-up such as "fix", "improve ATS" or a list of problems, run Step 3 again on your last resume, fix what was asked, and reply with the complete corrected resume in the same layout.
`;

const here = dirname(fileURLToPath(import.meta.url));
const out = outArg || join(here, 'out', `${(profile.resume.basics?.name || profile.name).replace(/[^\w]+/g, '_')}.md`);
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, md);
console.log(`wrote ${out}`);
if (derived.length) console.log(`note: real stack taken from the bullets for: ${derived.join(', ')}`);
