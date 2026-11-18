// Tailored resume: the prompt sent to ChatGPT / DeepSeek (or the OpenAI API), parsing its JSON answer,
// merging it onto the profile's facts, and the editable plain-text form shown in the side panel.
import { totalYears } from './profiles.js';
import { jdKeywords } from './ats.js';

// What the model writes; employers, dates, locations, contact details and education come from the profile.
export const TAILOR_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['company_industry', 'label', 'summary', 'highlights', 'skills', 'work', 'keywords_matched'],
  properties: {
    company_industry: { type: 'string' },
    label: { type: 'string' },
    summary: { type: 'string' },
    highlights: { type: 'array', items: { type: 'object', additionalProperties: false, required: ['name', 'value'], properties: { name: { type: 'string' }, value: { type: 'string' } } } },
    skills: { type: 'array', items: { type: 'object', additionalProperties: false, required: ['name', 'keywords'], properties: { name: { type: 'string' }, keywords: { type: 'array', items: { type: 'string' } } } } },
    work: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false, required: ['index', 'position', 'description', 'highlights'],
        properties: { index: { type: 'integer' }, position: { type: 'string' }, description: { type: 'string' }, highlights: { type: 'array', items: { type: 'string' } } }
      }
    },
    keywords_matched: { type: 'array', items: { type: 'string' } }
  }
};

const JSON_SHAPE = `{
  "company_industry": "the hiring company's industry and product, in a few words",
  "label": "headline under the name",
  "summary": "3-4 sentences",
  "highlights": [{ "name": "competency group", "value": "comma-separated details" }],
  "skills": [{ "name": "group", "keywords": ["skill", "..."] }],
  "work": [{ "index": 0, "position": "title", "description": "one line framing the work", "highlights": ["bullet", "..."] }],
  "keywords_matched": ["JD keyword now covered", "..."]
}`;

// The facts the model works from: each job with its dates, industry, real stack and its original bullets as evidence.
function profileBlock(profile) {
  const r = profile.resume;
  const lines = [
    `Name: ${r.basics?.name || ''}`,
    `Current headline: ${r.basics?.label || ''}`,
    `Total experience: ${totalYears(r)} years (from the earliest start date to today)`,
    '',
    'WORK HISTORY (index, employer, dates, location, industry, real stack, evidence):'
  ];
  (r.work || []).forEach((w, i) => {
    lines.push(`\n[${i}] ${w.position} at ${w.name}${w.context ? ` (${w.context})` : ''}, ${w.startDate} to ${w.endDate || 'present'}, ${w.location || ''}`);
    if (w.industry) lines.push(`    Industry: ${w.industry}`);
    if (w.stack?.length) lines.push(`    Real stack: ${[].concat(w.stack).join(', ')}`);
    if (w.description) lines.push(`    Description: ${w.description}`);
    for (const h of w.highlights || []) lines.push(`    - ${h}`);
  });
  lines.push('', 'SKILLS ON FILE:', ...(r.skills || []).map((s) => `- ${s.name}: ${(s.keywords || []).join(', ')}`));
  lines.push('', 'EDUCATION (fixed):', ...(r.education || []).map((e) => `- ${[e.studyType, e.area].filter(Boolean).join(', ')}, ${e.institution} (${e.endDate || ''})`));
  return lines.join('\n');
}

export function buildPrompt({ profile, job, atsTarget = 95 }) {
  const mode = profile.prefs?.stackMode || 'adjacent';
  const kw = jdKeywords(job.jd, job.title);
  const must = kw.tech.filter((t) => t.req).map((t) => t.tech.name);
  const other = kw.tech.filter((t) => !t.req).map((t) => t.tech.name);
  const thisYear = new Date().getFullYear();
  return `You are a senior technical recruiter and resume writer who also knows how ATS parsers (Greenhouse, Lever, Workday, iCIMS) score resumes. Rewrite the candidate's resume for the job below so it scores ${atsTarget}%+ on keyword match AND reads as a real person's career that holds up in a background check and a technical interview.

=== HARD FACTS (never change) ===
Employer names, employment dates, locations, education, contact details and the number and order of jobs stay exactly as in the profile.

=== WHAT YOU REWRITE ===
Headline, summary, core competencies, skills, each job's title (at most one level above the real one, never a management title the profile does not have), each job's one-line description and every bullet.

=== ATS RULES ===
1. Headline = the JD's job title (or the closest title in the same family).
2. Use the JD's exact wording for each technology and practice, and spell out acronyms once in context, e.g. "Amazon Web Services (AWS)".
3. Every must-have JD technology appears in Skills AND in at least one bullet of a job whose stack backs it. Must-haves found in the JD: ${must.join(', ') || '(read the JD)'}. Also relevant: ${other.join(', ') || '(read the JD)'}.
4. Mirror the JD's responsibilities and domain phrases in the bullets of the two most recent jobs.
5. Skills groups follow the JD's priorities; JD technologies first in each group; drop noise.
6. Standard words only: no tables, no icons, no unusual section names.

=== REALISM RULES (these win over ATS rules) ===
7. Evidence: each job's bullets come from that job's real stack, industry and evidence bullets. ${mode === 'strict'
    ? 'Use ONLY technologies in that job\'s real stack. A JD technology no job backs goes in Skills only.'
    : 'You may use a close equivalent of a technology in that job\'s real stack when the JD asks for it (PostgreSQL -> MySQL, Jenkins -> GitHub Actions, RabbitMQ -> Kafka, React -> Next.js), if it fits the company\'s industry and the dates. Never put a technology into a job that has nothing related in its stack; list it in Skills only.'}
8. Timeline: no technology before it existed or was usable in production (examples: LLM / RAG / agents work not before 2022-2023, MCP not before 2024, Kubernetes not before 2015, GitHub Actions not before 2019). Use the versions and tools of each job's dates.
9. Years: any "N+ years" claim must fit the dates. Total experience is ${totalYears(profile.resume)} years. A skill's years cannot exceed the time spent in the jobs that used it, or the time since it was released (today is ${thisYear}).
10. Domain: frame each job's work in that employer's industry and product (a bank builds payments and risk systems, a hospital builds clinical and patient systems). Do not move a job into the hiring company's industry.
11. Bullets: action verb + what was built or changed + key technologies + result or scope. About half the bullets carry a number; numbers are modest and specific (users, requests per second, data volume, latency, cost, team size); no "100%", no "10x", no "zero downtime". 5-6 bullets for each of the two most recent jobs, 3-4 for older jobs; 1-2 lines each; vary length and opening verbs; never repeat a bullet.
12. Seniority shows through scope: design reviews, mentoring, on-call, stakeholder work, testing and CI/CD where the JD asks for them.

=== STYLE ===
13. No first person, no em dashes, American English. Avoid filler: leveraged, spearheaded, utilized, robust, seamless, cutting-edge, synergy, passionate, dynamic, innovative, pivotal, meticulous, showcase, elevate, empower, delve. Plain, specific, confident. It must not read as AI-written.
${profile.prefs?.instructions ? `\n=== EXTRA INSTRUCTIONS FROM THE CANDIDATE (override the rules above, except the hard facts) ===\n${profile.prefs.instructions}\n` : ''}
=== CANDIDATE PROFILE ===
${profileBlock(profile)}

=== JOB ===
Company: ${job.company || ''}
Title: ${job.title || ''}
Location: ${job.location || ''} ${job.workMode ? `(${job.workMode})` : ''}
${job.salary ? `Salary: ${job.salary}\n` : ''}
JOB DESCRIPTION:
${String(job.jd || '').slice(0, 14000)}

=== OUTPUT ===
First work out the company's industry, the JD's must-haves and how each one maps to a job's real stack. Then reply with ONLY one JSON object in a \`\`\`json code block, no other text, in this shape ("work" has one entry per job of the profile, same order, "index" = the job's index):
\`\`\`json
${JSON_SHAPE}
\`\`\``;
}

export function fixPrompt(issueText) {
  return `Check found these problems in your resume:

${issueText}

Fix every one of them. Keep all the other rules, the employers, dates and education. Reply with the complete corrected JSON object only, in a \`\`\`json code block, same shape as before.`;
}

// The model's reply -> object. Takes the last ```json block, else the outermost { … }.
export function parseJson(text) {
  const s = String(text || '');
  const blocks = [...s.matchAll(/```(?:json)?\s*([\s\S]*?)```/gi)].map((m) => m[1]);
  const candidates = [...blocks.reverse(), s];
  for (const c of candidates) {
    const a = c.indexOf('{');
    const b = c.lastIndexOf('}');
    if (a < 0 || b <= a) continue;
    const raw = c.slice(a, b + 1);
    for (const attempt of [raw, raw.replace(/,\s*([}\]])/g, '$1').replace(/[“”]/g, '"')]) {
      try {
        return JSON.parse(attempt);
      } catch { /* try the next */ }
    }
  }
  throw new Error('The answer did not contain valid JSON. Press Generate again, or paste a corrected answer into the text box.');
}

export function mergeTailored(base, t) {
  const r = JSON.parse(JSON.stringify(base));
  r.basics = r.basics || {};
  if (t.label) r.basics.label = t.label;
  if (t.summary) r.basics.summary = t.summary;
  if (Array.isArray(t.highlights)) r.basics.highlights = t.highlights.filter((h) => h && h.name);
  if (Array.isArray(t.skills) && t.skills.length) r.skills = t.skills.map((s) => ({ name: s.name, keywords: [].concat(s.keywords || []) }));
  (t.work || []).forEach((w, i) => {
    const job = r.work[Number.isInteger(w.index) ? w.index : i];
    if (!job) return;
    if (w.position) job.position = w.position;
    if (typeof w.description === 'string') job.description = w.description;
    if (Array.isArray(w.highlights) && w.highlights.length) job.highlights = w.highlights.map(String);
  });
  // evidence fields are for the writer and validator, not for the printed resume
  for (const w of r.work || []) {
    delete w.stack;
    delete w.industry;
  }
  return r;
}

// ---------- editable text form ----------
// # Name
// Headline
// == SUMMARY ==  /  == CORE COMPETENCIES ==  /  == SKILLS ==  /  == EXPERIENCE ==  /  == EDUCATION ==
// ### Position | Company | Start - End | Location
// > one-line description
// - bullet

const contactLine = (b) => [b.email, b.phone, [b.location?.city, b.location?.region].filter(Boolean).join(', '),
  ...(b.profiles || []).map((p) => (p.url || '').replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, ''))].filter(Boolean).join(' | ');

export function toText(r) {
  const b = r.basics || {};
  const out = [`# ${b.name || ''}`, b.label || '', contactLine(b), '', '== SUMMARY ==', b.summary || ''];
  if ((b.highlights || []).length) out.push('', '== CORE COMPETENCIES ==', ...b.highlights.map((h) => `${h.name}: ${h.value}`));
  out.push('', '== SKILLS ==', ...(r.skills || []).map((s) => `${s.name}: ${(s.keywords || []).join(', ')}`));
  out.push('', '== EXPERIENCE ==');
  for (const w of r.work || []) {
    out.push('', `### ${w.position} | ${w.name} | ${w.startDate || ''} - ${w.endDate || 'Present'}${w.location ? ` | ${w.location}` : ''}`);
    if (w.description) out.push(`> ${w.description}`);
    for (const h of w.highlights || []) out.push(`- ${h}`);
  }
  if ((r.education || []).length) {
    out.push('', '== EDUCATION ==', ...(r.education || []).map((e) => `${[e.studyType, e.area].filter(Boolean).join(', ')} | ${e.institution} | ${e.endDate || ''}`));
  }
  return out.join('\n').trim() + '\n';
}

// Text -> resume. Employers, dates, contact details and education always come from `base`
// (edits to those lines are ignored); everything else is taken from the text.
export function fromText(text, base) {
  const r = JSON.parse(JSON.stringify(base));
  r.basics = r.basics || {};
  const lines = String(text || '').replace(/\r/g, '').split('\n');
  let sec = 'head';
  let headLine = 0;
  let job = -1;
  const summary = [];
  const highlights = [];
  const skills = [];
  const work = (r.work || []).map((w) => ({ ...w, description: '', highlights: [] }));
  for (const raw of lines) {
    const line = raw.trim();
    const head = /^==\s*(.+?)\s*==$/.exec(line);
    if (head) {
      const h = head[1].toLowerCase();
      sec = /summary|profile|overview/.test(h) ? 'summary' : /competenc/.test(h) ? 'comp' : /skill/.test(h) ? 'skills' : /experience|work|employment/.test(h) ? 'work' : /education/.test(h) ? 'edu' : 'other';
      continue;
    }
    if (sec === 'head') {
      if (line.startsWith('#')) continue;
      if (line && headLine++ === 0) r.basics.label = line;
      continue;
    }
    if (sec === 'summary') summary.push(raw);
    else if (sec === 'comp' && line) {
      const i = line.indexOf(':');
      if (i > 0) highlights.push({ name: line.slice(0, i).trim(), value: line.slice(i + 1).trim() });
    } else if (sec === 'skills' && line) {
      const i = line.indexOf(':');
      if (i > 0) skills.push({ name: line.slice(0, i).trim(), keywords: line.slice(i + 1).split(/,(?![^()]*\))/).map((s) => s.trim()).filter(Boolean) });
    } else if (sec === 'work' && line) {
      if (line.startsWith('###')) {
        job++;
        const position = line.replace(/^###\s*/, '').split('|')[0].trim();
        if (work[job] && position) work[job].position = position;
      } else if (work[job] && line.startsWith('>')) work[job].description = line.replace(/^>\s*/, '');
      else if (work[job] && /^[-•*]\s*/.test(line)) work[job].highlights.push(line.replace(/^[-•*]\s*/, ''));
      else if (work[job]) {
        const hs = work[job].highlights;
        if (hs.length) hs[hs.length - 1] += ' ' + line;
        else work[job].description = (work[job].description ? work[job].description + ' ' : '') + line;
      }
    }
  }
  const s = summary.join('\n').trim();
  if (s) r.basics.summary = s.replace(/\n{3,}/g, '\n\n');
  r.basics.highlights = highlights;
  if (skills.length) r.skills = skills;
  work.forEach((w, i) => {
    if (!w.highlights.length) w.highlights = (base.work[i]?.highlights || []).slice();
    delete w.stack;
    delete w.industry;
  });
  r.work = work;
  return r;
}

// ---------- cover letter ----------

export function coverPrompt({ profile, resume, job }) {
  const r = resume || profile.resume;
  const recent = (r.work || []).slice(0, 3).map((w) => `${w.position} at ${w.name} (${w.startDate} to ${w.endDate || 'present'}):\n- ${(w.highlights || []).slice(0, 4).join('\n- ')}`).join('\n\n');
  return `Write a short, realistic cover letter for this job application.

- 150-230 words, 3 short paragraphs. Body text only: no letterhead, date, address block or subject line.
- Start with "Dear ${job.company ? `${job.company} team` : 'Hiring Manager'}," and end with "Best regards," and the candidate's full name (${r.basics?.name || ''}) on the next line.
- Paragraph 1: the role (${job.title || 'the role'}) and one specific reason it fits, taken from the job description (product, domain or stack).
- Paragraph 2: two concrete examples from the resume below that match the job's main requirements. Name the technologies and what was built. Use only numbers that appear in the resume.
- Paragraph 3: one or two sentences offering to talk further.
- Plain words, short sentences, no hype. No clichés ("I am writing to express", "passionate", "perfect fit", "thrilled"), no em dashes, no bullet points. It must not read as AI-written.
- Output only the letter text.

CANDIDATE: ${r.basics?.name || ''}, ${r.basics?.label || ''}
Summary: ${r.basics?.summary || ''}

RECENT WORK:
${recent}

JOB: ${job.title || ''} at ${job.company || ''}
JOB DESCRIPTION:
${String(job.jd || '').slice(0, 8000)}`;
}

// Strips chat chrome from a plain-text answer (code fences, "Here is your letter:" lines).
export function cleanLetter(text) {
  let s = String(text || '').replace(/```[a-z]*\n?|```/g, '').trim();
  s = s.replace(/^(here('| i)s|sure|certainly)[^\n]*\n+/i, '');
  return s.replace(/—/g, ', ').trim();
}
