// "Auto fill this page": one pass over whatever the current tab shows. Not a bot: it never presses
// Next / Submit / Apply and never moves to another page; you check the page and press those yourself,
// then press the button again on the next step.
//   1. snapshot every frame (fields, buttons, text) with content/agent.js
//   2. rules + saved answers + the example knowledge base fill the usual questions with no AI (lib/rules.js)
//   3. the rest (screening and free-text questions) goes to OpenAI with the profile, the tailored resume and the JD
//   4. the actions are typed into the page; the resume (and cover letter, if made) are uploaded
import { rulePlan, answersToSave } from './rules.js';
import { loadKB, examplesForAI, learnFrom, mergeLearned } from './examples.js';
import * as store from './store.js';
import { personalForAI, secretsFor, totalYears } from './profiles.js';
import { openaiJson } from './openai.js';
import { renderResumeHtml } from './templates.js';
import { htmlToPdf } from './pdf.js';
import { resumeDocx, styleFromHtml, DOCX_TYPE } from './docx.js';
import { bytesToB64, slug, hostOf } from './util.js';

const OPS = ['fill', 'select', 'check', 'uncheck', 'click', 'choose', 'upload_resume', 'upload_cover'];
// buttons the fill must never press: they move the application on, which is your call
const NAV = /\b(next|continue|submit|apply|save|review|finish|send|sign ?(in|up)|log ?in|register|create (an )?account|back|previous|cancel|delete|remove|withdraw|close|done|confirm)\b/i;

const PLAN_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['page_type', 'summary', 'actions', 'needs_human'],
  properties: {
    page_type: { type: 'string', enum: ['job_posting', 'application_form', 'login', 'register', 'review', 'success', 'already_applied', 'captcha', 'other'] },
    summary: { type: 'string' },
    actions: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false, required: ['id', 'op', 'value', 'question'],
        properties: { id: { type: 'string' }, op: { type: 'string', enum: OPS }, value: { type: 'string' }, question: { type: 'string' } }
      }
    },
    needs_human: { type: 'string' }
  }
};

const SYSTEM = `You fill in a job application form for the candidate, one page at a time. You get a snapshot of the current page: the form fields still to answer (with ids, labels, types, options, current values, required flags), the buttons, and some page text. Return what to type, pick or check in each field.

Rules:
- Fill every listed field you can answer. Skip fields that already hold the correct value. Never invent employers, degrees or licenses.
- Do NOT press Next, Continue, Submit, Apply, Save, Sign in or similar: the candidate presses them. Op "click" is only for in-form controls such as a Yes/No button pair or "Add another" when the form needs it.
- Use the candidate profile facts exactly. Values like {{password}}, {{ssn_last4}}, {{dob_mm_dd_yyyy}} are placeholders for secrets: copy them verbatim; the browser replaces them. Only fill SSN fields with {{ssn_last4}} when the field explicitly asks for the last 4 digits.
- Resume upload: op "upload_resume" on the file input or its upload button. Cover letter upload: "upload_cover" only when a cover letter file is ready. A cover letter text box: "fill" it with the cover letter text if one is provided, otherwise leave it.
- Selects, radio groups and checkbox groups: op "select" on the field id (several answers in a checkbox group: join with " | "). Single checkboxes: "check" / "uncheck". Custom dropdowns and typeaheads: "choose".
- When a field lists options=[...], the value MUST be one of those options, copied exactly. Map the candidate's answer to the offered option that means the same. Avoid "Other" unless nothing else fits.
- Screening questions: answer truthfully from the profile and resume. Work authorization, sponsorship, relocation, start date and salary come from the profile. Salary: the job's posted range midpoint if shown, else the profile's expectation. "How did you hear about us": the profile's answer mapped to an offered option (never "Referral").
- Years of experience with a skill: a realistic number that matches the resume dates for the jobs that used it.
- Free-text questions (why this company, describe a project, motivation): 2-5 plain, specific first-person sentences based on THIS candidate's resume and the job. No hype, no em dashes, no clichés. Put the question in "question".
- EEO / voluntary self-identification: use the profile's answers mapped to the offered options; only if nothing fits, "Decline to self-identify".
- Agreements and consent boxes (privacy policy, terms, certify accurate): check them.
- <examples> holds answers to similar questions on earlier applications; choice answers carry over, free-text answers show style only.
- If the page is a job posting, a login, a captcha or a thank-you page, say so in page_type and return no actions; needs_human explains what the candidate should do ("" otherwise).
- Use only ids from the snapshot.`;

function resumeBrief(r) {
  if (!r) return '';
  const b = r.basics || {};
  return [
    `${b.name} | ${b.label}`, b.summary,
    'Skills: ' + (r.skills || []).map((s) => `${s.name}: ${(s.keywords || []).join(', ')}`).join(' | '),
    ...(r.work || []).map((w) => `${w.position} at ${w.name} (${w.startDate} to ${w.endDate || 'present'})\n- ${(w.highlights || []).slice(0, 4).join('\n- ')}`),
    'Education: ' + (r.education || []).map((e) => `${e.studyType} in ${e.area}, ${e.institution} (${e.endDate || ''})`).join('; ')
  ].filter(Boolean).join('\n').slice(0, 6000);
}

function snapshotBlock(snap, prefilled) {
  const fields = snap.fields.map((f) => {
    const bits = [`[${f.id}] ${f.kind}`];
    if (f.label) bits.push(`label="${f.label}"`);
    if (f.question && f.question !== f.label) bits.push(`question="${f.question}"`);
    if (f.name) bits.push(`name=${f.name}`);
    if (f.placeholder) bits.push(`placeholder="${f.placeholder}"`);
    if (f.required) bits.push('REQUIRED');
    if (f.value) bits.push(`value="${f.value}"`);
    if (f.checked) bits.push('CHECKED');
    if (f.options?.length) bits.push(`options=${JSON.stringify(f.options.slice(0, 60))}`);
    if (f.error) bits.push(`ERROR="${f.error}"`);
    return bits.join(' ');
  });
  const buttons = snap.buttons.filter((b) => !NAV.test(b.text)).slice(0, 40).map((b) => `[${b.id}] ${b.kind} "${b.text}"`);
  return [
    `URL: ${snap.url}`, `Title: ${snap.title}`,
    snap.captcha ? `Captcha detected: ${snap.captcha}` : '',
    snap.errors?.length ? `Validation messages:\n- ${snap.errors.join('\n- ')}` : '',
    prefilled ? `${prefilled} other fields were already filled by rules; they are not listed.` : '',
    `\nFIELDS TO ANSWER (${fields.length}):\n${fields.join('\n') || '(none)'}`,
    `\nIN-FORM BUTTONS:\n${buttons.join('\n') || '(none)'}`,
    `\nPAGE TEXT:\n${String(snap.text || '').slice(0, fields.length ? 2000 : 5000)}`
  ].filter(Boolean).join('\n');
}

// ---------- page access (content/agent.js in every frame) ----------

async function inject(tabId) {
  await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, files: ['content/agent.js'] }).catch(() =>
    chrome.scripting.executeScript({ target: { tabId }, files: ['content/agent.js'] }));
}

async function call(tabId, method, args = [], { allFrames = false, frameId } = {}) {
  const target = frameId != null ? { tabId, frameIds: [frameId] } : { tabId, allFrames };
  const res = await chrome.scripting.executeScript({ target, func: (m, a) => (window.__abAgent?.[m] ? window.__abAgent[m](...a) : null), args: [method, args] });
  return allFrames ? res : res[0]?.result;
}

export async function snapshotTab(tabId) {
  await inject(tabId);
  const frames = await call(tabId, 'snapshot', [], { allFrames: true });
  const parts = frames.filter((f) => f.result && (f.result.top || (f.result.area > 20000 && (f.result.fields.length || f.result.buttons.length || f.result.text.length > 30))));
  const m = { url: '', title: '', fields: [], buttons: [], errors: [], text: '', captcha: '', success: false, already: false };
  for (const { frameId, result: r } of parts) {
    if (r.top) Object.assign(m, { url: r.url, title: r.title });
    m.fields.push(...r.fields.map((f) => ({ ...f, id: `${frameId}:${f.id}` })));
    m.buttons.push(...r.buttons.map((b) => ({ ...b, id: `${frameId}:${b.id}` })));
    m.errors.push(...r.errors);
    m.text += (r.top ? '' : `\n[frame ${hostOf(r.url)}]\n`) + r.text;
    m.captcha ||= r.captcha;
    m.success ||= r.success;
    m.already ||= r.already;
  }
  m.text = m.text.slice(0, 12000);
  return m;
}

const splitId = (id) => {
  const i = String(id).indexOf(':');
  return { frameId: Number(String(id).slice(0, i)), local: String(id).slice(i + 1) };
};

// Job title / company / description of the posting in a tab (for "read from current tab").
export async function readJob(tabId) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ['content/agent.js'] });
  const info = (await call(tabId, 'jobInfo')) || {};
  const page = (await call(tabId, 'pageText')) || {};
  const tab = await chrome.tabs.get(tabId);
  return { ...info, url: tab.url, text: page.text || '' };
}

// ---------- files to upload ----------

const fileBase = (resume, app) => `${slug(resume?.basics?.name || 'Resume')}${app?.company ? `_${slug(app.company)}` : ''}`;

export async function makeResumeFile(resume, template, app, type = 'pdf') {
  const html = renderResumeHtml(resume, template);
  if (type === 'docx') return { b64: bytesToB64(resumeDocx(resume, styleFromHtml(html))), name: `${fileBase(resume, app)}_Resume.docx`, type: DOCX_TYPE };
  return { b64: await htmlToPdf(html), name: `${fileBase(resume, app)}_Resume.pdf`, type: 'application/pdf' };
}

async function filesFor(app, profile, resume, settings) {
  const key = app?.id || `base_${profile.id}`;
  const files = await store.getFiles(key);
  const want = settings.fill.fileType === 'docx' ? 'docx' : 'pdf';
  const have = files.resume && (want === 'docx') === /\.docx$/i.test(files.resume.name);
  if (!have) {
    files.resume = await makeResumeFile(resume, app?.template || profile.prefs?.template, app, want);
    await store.saveFile(key, 'resume', files.resume);
  }
  return files;
}

// ---------- the fill ----------

export async function fillTab(tabId, { onLog = () => {} } = {}) {
  const settings = await store.getSettings();
  const active = await store.getActive();
  const profiles = await store.list('profiles');
  const profile = profiles.find((p) => p.id === active.profileId) || profiles[0];
  if (!profile) throw new Error('Create a profile first (side panel → Profiles).');
  const app = active.appId ? await store.getDoc('applications', active.appId) : null;
  const useApp = app && app.profileId === profile.id ? app : null;
  const tailored = useApp ? (await store.getResume(useApp.id))?.json : null;
  const resume = tailored || profile.resume;
  const tab = await chrome.tabs.get(tabId);
  if (/^(chrome|edge|about|chrome-extension):/.test(tab.url || '')) throw new Error('This page cannot be filled.');

  onLog(`Reading the page as ${profile.name}${useApp ? ` (${useApp.title} at ${useApp.company})` : ' (no application selected: base resume)'}`);
  let snap = await snapshotTab(tabId);
  if (snap.success || snap.already) return { status: 'success', note: 'This page says the application was submitted.', profile, app: useApp };
  if (snap.fields.some((f) => f.kind === 'combobox' && !f.options)) {
    await call(tabId, 'probeOptions', [], { allFrames: true }).catch(() => {});
    snap = await snapshotTab(tabId);
  }
  if (!snap.fields.length) {
    return { status: 'none', note: snap.captcha ? `Solve the ${snap.captcha} first, then press autofill again.` : 'No form fields on this page. If it is the job posting, press its Apply button first.', profile, app: useApp };
  }

  const personal = {
    ...profile.personal,
    years_experience: profile.personal.years_experience || String(totalYears(profile.resume)),
    current_title: profile.personal.current_title || resume.work?.[0]?.position || '',
    current_company: profile.personal.current_company || (resume.work?.[0] && !resume.work[0].endDate ? resume.work[0].name : '')
  };
  await loadKB();
  const saved = await store.getSaved(profile.id);
  const coverLetter = useApp?.coverLetter || '';
  const needFiles = snap.fields.some((f) => f.kind === 'file');
  let files = null;
  if (needFiles) {
    onLog('Preparing the resume file for upload');
    files = await filesFor(useApp, profile, resume, settings);
  }
  const rp = rulePlan(snap, { profile: personal, saved, uploadCover: settings.fill.uploadCover && !!files?.cover, coverLetter, jobSalary: useApp?.salary || '' });
  let actions = rp.page_type === 'job_posting' ? [] : rp.actions.slice();
  onLog(`${snap.fields.length} fields; ${actions.length} answered from the profile`);

  // the AI: required questions the rules could not answer, plus empty optional free-text and choice questions
  const ruleIds = new Set(actions.map((a) => a.id));
  const aiFields = snap.fields.filter((f) => !ruleIds.has(f.id) && (rp.aiFields.some((x) => x.id === f.id)
    || (!f.value && !f.checked && ['textarea', 'richtext', 'select', 'radio group', 'checkbox group', 'combobox'].includes(f.kind) && !/cover letter/i.test(`${f.label} ${f.question}`))));
  let aiNote = '';
  if (aiFields.length && settings.ai.openaiKey) {
    onLog(`Asking ${settings.ai.openaiModel} about ${aiFields.length} question${aiFields.length > 1 ? 's' : ''}`);
    const learned = await store.getLearned(profile.id);
    const examples = examplesForAI(aiFields, { url: snap.url, profile: personal, learned });
    const user = [
      '<candidate_profile>', Object.entries(personalForAI(personal)).map(([k, v]) => `${k}: ${v}`).join('\n'), '</candidate_profile>',
      '', '<resume>', resumeBrief(resume), '</resume>',
      '', '<job>', `Company: ${useApp?.company || ''}`, `Role: ${useApp?.title || ''}`, `Location: ${useApp?.location || ''}`, `Salary: ${useApp?.salary || ''}`,
      useApp?.jd ? `Description:\n${String(useApp.jd).slice(0, 5000)}` : '', '</job>',
      coverLetter ? `\n<cover_letter>\n${coverLetter}\n</cover_letter>` : '',
      `\n<files>Resume file ready: ${files ? 'yes' : 'not needed on this page'}. Cover letter file ready: ${files?.cover ? 'yes' : 'no'}.</files>`,
      examples ? `\n<examples>\n${examples.slice(0, 9000)}\n</examples>` : '',
      '', '<page_snapshot>', snapshotBlock({ ...snap, fields: aiFields }, snap.fields.length - aiFields.length), '</page_snapshot>',
      '', 'Return the actions for this page as JSON.'
    ].join('\n');
    try {
      const plan = await openaiJson({ apiKey: settings.ai.openaiKey, model: settings.ai.openaiModel, system: SYSTEM, user, schema: PLAN_SCHEMA, maxTokens: 12000 });
      const ids = new Set(snap.fields.map((f) => f.id).concat(snap.buttons.map((b) => b.id)));
      const ai = plan.actions.filter((a) => ids.has(a.id) && !ruleIds.has(a.id));
      actions.push(...ai);
      aiNote = plan.needs_human || '';
      await store.addSaved(profile.id, answersToSave(snap, ai));
      await store.setLearned(profile.id, mergeLearned(await store.getLearned(profile.id), learnFrom(snap, ai, { host: hostOf(snap.url), company: useApp?.company || '' })));
    } catch (err) {
      onLog('AI step failed: ' + err.message, 'warn');
      aiNote = err.message;
    }
  } else if (aiFields.length) {
    aiNote = `${aiFields.length} question${aiFields.length > 1 ? 's' : ''} need AI: add an OpenAI key in Settings → AI.`;
  }

  // never press navigation buttons
  actions = actions.filter((a) => {
    if (a.op !== 'click') return true;
    const b = snap.buttons.find((x) => x.id === a.id);
    return b && !NAV.test(b.text);
  });
  if (actions.some((a) => a.op === 'upload_resume' || a.op === 'upload_cover') && !files) files = await filesFor(useApp, profile, resume, settings);

  const secrets = secretsFor(profile.personal);
  const results = [];
  for (const a of actions) {
    const { frameId, local } = splitId(a.id);
    const r = await call(tabId, 'act', [[{ ...a, id: local }], files, secrets], { frameId }).catch((err) => [{ ok: false, note: err.message }]);
    // keep the frame-qualified id (the page reports its local id)
    results.push({ ...a, ok: !!r?.[0]?.ok, note: r?.[0]?.note || '' });
  }
  const ok = results.filter((r) => r.ok).length;
  const failed = results.filter((r) => !r.ok);
  const answers = results.filter((r) => r.ok && r.question && r.value && !/\{\{/.test(r.value)).map((r) => ({ question: r.question, answer: r.value }));
  if (useApp && answers.length) {
    const prev = useApp.answers || [];
    const keep = prev.filter((p) => !answers.some((a) => a.question === p.question));
    await store.put('applications', { ...useApp, answers: [...keep, ...answers].slice(-80), applyUrl: useApp.applyUrl || snap.url, status: useApp.status === 'applied' ? 'applied' : 'filling' });
  }
  const left = snap.fields.filter((f) => f.required && !f.value && !results.some((r) => r.ok && r.id === f.id)).length;
  return {
    status: 'filled', filled: ok, failed: failed.map((f) => `${f.op} ${f.question || f.id}: ${f.note || 'failed'}`).slice(0, 6),
    left, note: aiNote, profile, app: useApp, uploaded: results.some((r) => r.ok && r.op.startsWith('upload'))
  };
}

// Whether the tab now shows a thank-you / submitted page.
export async function tabSucceeded(tabId) {
  try {
    const s = await snapshotTab(tabId);
    return s.success || s.already;
  } catch {
    return false;
  }
}
