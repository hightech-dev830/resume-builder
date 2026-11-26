// Realism and consistency checks for a tailored resume, against the profile it came from.
// The goal is a resume a recruiter, a background check and an interview all agree with:
//  - facts unchanged: employers, dates, education
//  - every technology plausible for its job: it existed then, and it is backed by the job's real stack
//    (or, in "adjacent" mode, a close equivalent of something in it)
//  - experience claims add up: "8+ years", "5 years of Kubernetes" fit the dates
//  - titles stay near the real ones (no sudden Principal), no duplicated or copy-paste bullets
//  - writing that does not read as machine-made: no em dashes, buzzwords, identical bullet shapes
// risk = 0 means nothing to fix. Each issue says where it is and how to fix it; the same text is sent
// back to the chat model for an automatic correction round.
import { findTech, techByName, adjacent } from './ats.js';
import { ym, totalYears } from './profiles.js';

const BUZZ = /\b(leverag(e|ed|ing)|spearhead(ed|ing)?|synerg(y|ies)|cutting[- ]edge|state[- ]of[- ]the[- ]art|passionate|robust|seamless(ly)?|delv(e|ed|ing)|tapestry|game[- ]chang(er|ing)|best[- ]in[- ]class|world[- ]class|revolutioniz(e|ed|ing)|paradigm|utiliz(e|ed|ing)|holistic|dynamic environment|fast[- ]paced|results[- ]driven|go[- ]getter|thought leader|ninja|rockstar|guru|orchestrated|pivotal|meticulous(ly)?|showcas(e|ed|ing)|elevat(e|ed|ing)|empower(ed|ing)?|unparalleled|myriad|plethora|navigat(e|ed|ing) the complexities)\b/gi;
const LEVELS = [
  [/\b(intern|trainee)\b/i, 0], [/\b(junior|jr\.?|associate|entry)\b/i, 1], [/\b(mid|intermediate|ii)\b/i, 2],
  [/\b(senior|sr\.?|iii)\b/i, 3], [/\b(lead|staff)\b/i, 4], [/\b(principal|architect|distinguished)\b/i, 5],
  [/\b(manager|head of|director|vp|vice president|cto|chief)\b/i, 5]
];
const level = (title) => {
  for (const [re, n] of [...LEVELS].reverse()) if (re.test(title || '')) return n;
  return 2;
};
const yearOf = (d) => (ym(d) == null ? null : Math.floor(ym(d) / 12));
const firstWord = (s) => String(s || '').trim().split(/\s+/)[0].toLowerCase().replace(/[^a-z]/g, '');

export function validate(resume, base, { stackMode = 'adjacent' } = {}) {
  const issues = [];
  const add = (severity, where, msg, fix) => issues.push({ severity, where, msg, fix });
  const work = resume?.work || [];
  const baseWork = base?.work || [];

  // 1. facts that must not change
  if (work.length !== baseWork.length) add('high', 'Experience', `The resume has ${work.length} jobs; the profile has ${baseWork.length}.`, 'Keep exactly the profile\'s jobs, in the same order.');
  work.forEach((w, i) => {
    const b = baseWork[i];
    if (!b) return;
    if ((w.name || '').trim() !== (b.name || '').trim()) add('high', `Job ${i + 1}`, `Employer changed from "${b.name}" to "${w.name}".`, 'Use the employer name exactly as in the profile.');
    if (String(w.startDate || '') !== String(b.startDate || '') || String(w.endDate || '') !== String(b.endDate || '')) {
      add('high', `Job ${i + 1}`, `Dates at ${b.name} changed.`, `Use ${b.startDate} to ${b.endDate || 'present'}.`);
    }
    // titles: at most one level above the real title, never a management title the profile does not have
    const lb = level(b.position);
    const lw = level(w.position);
    if (lw > lb + 1 || (lw >= 5 && lb < 5)) add('high', `${b.name}`, `Title "${w.position}" is well above the real title "${b.position}".`, `Keep the title within one level of "${b.position}".`);
    else if (lw === lb + 1) add('low', `${b.name}`, `Title "${w.position}" is one level above "${b.position}".`, 'Fine if the work matches; keep it consistent with the bullets.');
  });
  // titles over time: a later job should not be two levels below an earlier one
  for (let i = 0; i < work.length - 1; i++) {
    const newer = level(work[i].position);
    const older = level(work[i + 1].position);
    if (older - newer >= 2) add('med', work[i].name, `"${work[i].position}" comes after the more senior "${work[i + 1].position}".`, 'Make the titles progress over time.');
  }
  if ((resume?.education || []).length !== (base?.education || []).length) add('high', 'Education', 'Education entries differ from the profile.', 'Keep education exactly as in the profile.');

  // 2. technologies per job: existed at the time, and backed by the job's stack
  const techYears = new Map(); // tech -> months used (union over jobs)
  work.forEach((w, i) => {
    const b = baseWork[i] || w;
    const text = [w.position, w.description, ...(w.highlights || [])].join('\n');
    const used = findTech(text);
    const endY = yearOf(b.endDate) ?? new Date().getFullYear();
    const startY = yearOf(b.startDate) ?? endY;
    const evidence = [...(b.stack || []), ...(b.highlights || []), b.description || ''].join('\n');
    const real = findTech(evidence);
    for (const t of used) {
      if (t.year && endY < t.year) add('high', b.name, `${t.name} appears at ${b.name} (${startY}-${endY}) but was not available until ${t.year}.`, `Remove ${t.name} from this job or use what existed then.`);
      else if (t.year && startY < t.year && endY - t.year < 1 && !t.generic) add('low', b.name, `${t.name} (from ${t.year}) is used at the very end of ${b.name}.`, 'Mention it as a late, small part of the work.');
      if (t.generic || !real.length) continue;
      const backed = real.some((r) => r.name === t.name) || (stackMode !== 'strict' && real.some((r) => adjacent(r, t)));
      if (!backed) {
        add(stackMode === 'strict' ? 'high' : 'med', b.name, `${t.name} is not in ${b.name}'s stack${stackMode !== 'strict' ? ' or a close equivalent of it' : ''}.`,
          `Move ${t.name} to the Skills section only, or replace it with what ${b.name} really used.`);
      }
    }
    // years with a technology = time in the jobs that used it: named in the resume, or in the job's real stack
    const months = (ym(b.endDate) ?? ym(null)) - (ym(b.startDate) ?? 0);
    const counted = new Set();
    for (const t of [...used, ...real]) {
      if (t.generic || counted.has(t.name)) continue;
      counted.add(t.name);
      techYears.set(t.name, (techYears.get(t.name) || 0) + Math.max(months, 0));
    }
  });

  // 3. experience claims ("8+ years", "5 years of Go")
  const total = totalYears(base || resume);
  const claimsText = [resume?.basics?.summary, resume?.basics?.label, ...(resume?.basics?.highlights || []).map((h) => h.value)].join('\n');
  // "N years [of] [experience] [with|in|of|using] <up to 3 words>": those words name the technology, if any
  for (const m of claimsText.matchAll(/(\d{1,2})\s*\+?\s*years?(?:\s+of)?(?:\s+(?:experience|professional|hands-on))?(?:\s+(?:with|in|of|using|building|working with))?((?:\s+[A-Za-z.#+/-]+){0,3})/gi)) {
    const n = Number(m[1]);
    const about = m[2] ? findTech(m[2]).find((t) => !t.generic) : null;
    if (about) {
      const yrs = Math.floor((techYears.get(about.name) || 0) / 12);
      const sinceRelease = about.year ? new Date().getFullYear() - about.year : 99;
      if (n > sinceRelease) add('high', 'Summary', `"${m[0].trim()}": ${about.name} has only existed for ${sinceRelease} years.`, `Lower it to at most ${Math.max(1, sinceRelease - 1)} years.`);
      else if (n > yrs + 1) add('med', 'Summary', `"${m[0].trim()}" but the jobs that used ${about.name} add up to about ${yrs} years.`, `Use ${Math.max(1, yrs)}+ years, or show ${about.name} in more of the matching jobs.`);
    } else if (n > total + 1) {
      add('high', 'Summary', `"${m[0].trim()}" but the work history covers about ${total} years.`, `Use ${total}+ years.`);
    }
  }

  // 4. bullets: duplicates, identical shapes, numbers everywhere, repeated openings
  const all = work.flatMap((w) => (w.highlights || []).map((h) => ({ h, job: w.name })));
  const seen = new Map();
  for (const { h, job } of all) {
    const key = h.toLowerCase().replace(/[^a-z ]/g, '').split(' ').slice(0, 8).join(' ');
    if (seen.has(key) && key.length > 20) add('med', job, `Bullet repeats one at ${seen.get(key)}: "${h.slice(0, 70)}…"`, 'Rewrite it with different work.');
    else seen.set(key, job);
  }
  const withNumbers = all.filter(({ h }) => /\d/.test(h)).length;
  if (all.length >= 8 && withNumbers / all.length > 0.75) add('med', 'Experience', `${withNumbers} of ${all.length} bullets carry numbers; real resumes mix in plain bullets.`, 'Keep numbers in about half of the bullets.');
  for (const { h, job } of all) {
    if (/\b100\s*%|\b(10|5|3)x\b|\bzero downtime\b|\b99\.999/i.test(h)) add('low', job, `Absolute claim: "${h.match(/\b100\s*%|\b(10|5|3)x\b|\bzero downtime\b|\b99\.999%?/i)[0]}".`, 'Use a modest, specific figure.');
  }
  for (const w of work) {
    const hs = w.highlights || [];
    const openers = hs.map(firstWord);
    const dup = openers.find((o, i) => o && openers.indexOf(o) !== i);
    if (dup) add('low', w.name, `Several bullets start with "${dup}".`, 'Vary the opening verbs.');
    const lens = hs.map((h) => h.length);
    if (hs.length >= 4 && Math.max(...lens) - Math.min(...lens) < 25) add('low', w.name, 'All bullets are almost the same length.', 'Mix shorter and longer bullets.');
  }

  // 5. wording that reads as AI-written
  const text = JSON.stringify({ s: resume?.basics?.summary, w: work.map((w) => [w.description, w.highlights]) });
  const dashes = (text.match(/—/g) || []).length;
  if (dashes) add('low', 'Wording', `${dashes} em dash${dashes > 1 ? 'es' : ''}.`, 'Use commas or periods instead.');
  const buzz = [...new Set((text.match(BUZZ) || []).map((x) => x.toLowerCase()))];
  if (buzz.length) add(buzz.length > 3 ? 'med' : 'low', 'Wording', `Filler words: ${buzz.slice(0, 8).join(', ')}.`, 'Replace them with plain verbs (built, ran, cut, moved).');

  // 6. keyword stuffing
  const counts = new Map();
  for (const t of findTech(text)) {
    t.re.lastIndex = 0;
    counts.set(t.name, (text.match(t.re) || []).length);
  }
  for (const [n, c] of counts) if (c > 7) add('low', 'Wording', `${n} appears ${c} times.`, 'Keep it to where it was really used; ATS counts one mention in skills plus a few in context.');

  const weight = { high: 25, med: 10, low: 3 };
  const risk = Math.min(100, issues.reduce((s, x) => s + weight[x.severity], 0));
  const order = { high: 0, med: 1, low: 2 };
  issues.sort((a, b) => order[a.severity] - order[b.severity]);
  const techSpan = [...techYears.entries()].map(([name, months]) => ({ name, years: Math.round(months / 12 * 10) / 10 })).sort((a, b) => b.years - a.years);
  return { risk, issues, techSpan, totalYears: total };
}

// Issue list as instructions for the model's correction round.
export function issuesForModel(v, ats) {
  const lines = v.issues.filter((x) => x.severity !== 'low' || v.issues.length < 12)
    .map((x, i) => `${i + 1}. [${x.severity}] ${x.where}: ${x.msg} Fix: ${x.fix}`);
  if (ats?.missing?.length) lines.push(`${lines.length + 1}. [ats] JD keywords still missing: ${ats.missing.map((m) => m.name).join(', ')}. Add each one where the profile supports it (a bullet in a job whose stack backs it); otherwise list it in Skills only.`);
  if (ats?.skillsOnly?.length) lines.push(`${lines.length + 1}. [ats] Only in Skills, never in a bullet: ${ats.skillsOnly.join(', ')}. Use them in context in a job that backs them, if any does.`);
  return lines.join('\n');
}

export { techByName };
