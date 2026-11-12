// The example knowledge base (data/examples.json): answers from applications Kevin filled in correctly.
//  - EXAMPLES: its "rules", answered without the AI before the built-in rules (an example always wins)
//  - examplesForAI(): the worked examples, notes, ATS flow and learned answers closest to the questions
//    the AI is about to answer, as a text block for its prompt
//  - learnFrom(): new questions the AI answered, kept so later forms (and the AI) can reuse them
// Loaded with fetch (loadKB), not a JSON import: Chrome service workers cannot import .json modules everywhere.
let KB = { rules: [], applications: [], notes: [], ats: [] };
let ALL_QA = [];
let loaded = null;

const rx = (s) => (s ? new RegExp(s) : null);
const pref = (s) => (/^\/.+\/$/.test(s) ? new RegExp(s.slice(1, -1)) : s);

//   q / not:  the question matches q and not `not`
//   answer:   option preferences in order (exact text or RegExp); the first plain text is also what is typed
//   text:     what to type in a text box (may use {profile.key})
//   leave:    keep the field empty (cleared if something else filled it)
//   today:    today's date
//   cover:    the tailored cover letter text (e.g. "Message to the Hiring Team")
//   check / never: a checkbox whose own text matches is ticked / never ticked
const compile = (r) => ({
  q: rx(r.q), not: rx(r.not), check: rx(r.check), never: rx(r.never),
  answer: r.pick ? r.pick.map(pref) : r.text ? [r.text] : null,
  text: r.text || (r.pick || []).find((p) => !/^\/.+\/$/.test(p)) || '',
  leave: !!r.leave, today: !!r.today, cover: !!r.cover, from: r.from
});
export const EXAMPLES = []; // filled in place by setKB, so rules.js always sees the current list

export const fillProfile = (s, p = {}) => String(s || '').replace(/\{profile\.(\w+)\}/g, (_, k) => p[k] || '');

const STOP = new Set('a an the and or of to in on for with you your do did does have has are is be been was were this that what which how any at by as from will would can our we us it its if not please'.split(' '));
const words = (s) => new Set(String(s || '').toLowerCase().replace(/[^a-z0-9+#]+/g, ' ').split(' ').filter((w) => w.length > 1 && !STOP.has(w)));
function similarity(a, b) {
  if (!a.size || !b.size) return 0;
  let n = 0;
  for (const w of a) if (b.has(w)) n++;
  return n / Math.min(a.size, b.size) * (n / Math.max(a.size, b.size)) ** 0.25;
}

export function setKB(kb) {
  KB = { rules: [], applications: [], notes: [], ats: [], ...kb };
  EXAMPLES.length = 0;
  EXAMPLES.push(...KB.rules.map(compile));
  ALL_QA = KB.applications.flatMap((app) => app.qa.map((x) => ({ ...x, from: `${app.company} (${app.ats})`, w: words(x.q) })));
}

// Reads data/examples.json once per service-worker start. A broken file is logged and the bot carries on without it.
export function loadKB() {
  loaded ||= fetch(chrome.runtime.getURL('data/examples.json'))
    .then((r) => r.json())
    .then(setKB)
    .catch((e) => { console.warn('data/examples.json could not be read:', e); loaded = null; });
  return loaded;
}

// Text block for the AI: general notes, how this ATS works, then the closest example questions.
export function examplesForAI(fields, { url = '', profile = {}, learned = [], max = 18 } = {}) {
  const qs = (fields || []).map((f) => words(`${f.question || ''} ${f.label || ''}`)).filter((w) => w.size);
  const pool = [
    ...ALL_QA,
    ...learned.map((l) => ({ q: l.q, a: l.a, type: l.kind, from: `learned on ${l.host || 'an earlier application'}`, w: words(l.q) }))
  ];
  const scored = pool
    .map((x) => ({ x, s: Math.max(0, ...qs.map((w) => similarity(w, x.w))) }))
    .filter((r) => r.s >= 0.3)
    .sort((a, b) => b.s - a.s)
    .slice(0, max)
    .map(({ x }) => `- Q: ${x.q}${x.options ? ` [options: ${x.options.join(' | ')}]` : ''}\n  A: ${fillProfile(x.a, profile)}${x.how ? `  (${x.how})` : ''}  [${x.from}]`);
  const ats = KB.ats.find((a) => new RegExp(a.url, 'i').test(url));
  return [
    'How Kevin wants forms answered:', ...KB.notes.map((n) => `- ${n}`),
    ats ? `\nThis is ${ats.name}. Usual flow: ${ats.flow}` : '',
    scored.length ? `\nAnswers Kevin gave to the closest questions on earlier applications (use them when the question means the same; match the form's own options):\n${scored.join('\n')}` : ''
  ].filter(Boolean).join('\n');
}

// What the AI answered on this page, kept as learned examples (newest first, one per question).
// Secrets, placeholders, uploads and buttons are never kept.
export function learnFrom(snap, aiActions, { host = '', company = '' } = {}) {
  const out = [];
  for (const a of aiActions || []) {
    if (!['select', 'choose', 'fill'].includes(a.op) || !a.value || /\{\{/.test(a.value)) continue;
    const f = (snap.fields || []).find((x) => x.id === a.id);
    if (!f || ['password', 'file', 'hidden'].includes(f.kind) || f.hidden) continue;
    const q = String(f.question || f.label || '').trim();
    if (q.length < 12 || /password|ssn|social security|birth|e-?mail|phone|address|zip|postal/i.test(q)) continue;
    out.push({ q: q.slice(0, 300), a: String(a.value).slice(0, 900), kind: f.kind, options: f.options?.slice(0, 20), host, company, t: Date.now() });
  }
  return out;
}

export function mergeLearned(old = [], add = [], cap = 400) {
  const key = (x) => x.q.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  const seen = new Set();
  return [...add, ...old].filter((x) => !seen.has(key(x)) && seen.add(key(x))).slice(0, cap);
}
