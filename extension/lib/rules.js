// Fills the usual application questions straight from the profile, without the AI.
// Works on the same page snapshot the AI gets and returns actions in the AI's format,
// so the AI is only asked about what is left (or not at all).

import { EXAMPLES, fillProfile } from './examples.js';

export const norm = (s) => String(s || '').toLowerCase().replace(/[_*✱:]/g, ' ').replace(/\s+/g, ' ').trim();

// "how did you hear about us" style answers, best first; "Other" / "Referral" are never picked
const HOW_HEARD = [/^jobright/, /jobright/, /^indeed/, /indeed/, /job ?board/, /linkedin/, /glassdoor/, /ziprecruiter/, /online|internet|web ?search|google/, /company (web)?site|career(s)? (page|site)/, /social media/];
const YES = [/^yes\b/, /^y$/, /^true$/];
const NO = [/^no\b/, /^n$/, /^false$/];

const fieldText = (f) => norm([f.question, f.label, f.name, f.placeholder].filter(Boolean).join(' | '));
const isChoice = (f) => (f.options?.length || 0) > 0 && f.kind !== 'file';
const isRequired = (f) => !!f.required || /[*✱]|\(required\)/i.test(`${f.label || ''} ${f.question || ''}`);
const TEXTY = new Set(['text', 'email', 'tel', 'url', 'number', '', 'combobox']);

export function isFilled(f) {
  if (f.kind === 'checkbox') return !!f.checked;
  if (f.kind === 'file') return !!f.value;
  const v = norm(f.value);
  return !!v && !/^(select|choose|please select|--|select\.\.\.|select one)/.test(v);
}

// First option matching the preferences, in preference order. Exact text, or null.
export function pickOption(options, prefs) {
  const opts = (options || []).filter((o) => o && !/^(select|choose|please select|--)/i.test(norm(o)));
  for (const p of prefs) {
    const hit = opts.find((o) => (p instanceof RegExp ? p.test(norm(o)) : norm(o) === norm(p)));
    if (hit) return hit;
  }
  return null;
}

const v = (k) => (p) => p[k] || '';
const yesno = (s) => (/^(yes|true)\b/i.test(String(s)) ? 'Yes' : /^(no|false)\b/i.test(String(s)) ? 'No' : '');

// Each rule: match on the field's label/question; `choose` = option preferences, `text` = value to type.
// Order matters: specific questions first (sponsorship before work authorization before address "state").
const RULES = [
  // --- screening ---
  { re: /without (the need for |requiring |needing )?(a )?(visa )?sponsor/, choose: () => YES, text: () => 'Yes' },
  { re: /sponsor|visa status|work visa|h-?1b|immigration (support|status)/, choose: (p) => (yesno(p.needs_sponsorship) === 'Yes' ? YES : NO), text: (p) => yesno(p.needs_sponsorship) || 'No' },
  { re: /authori[sz]ed to work|eligible to work|legally (able|permitted|entitled|allowed) to work|right to work|work authori[sz]ation|work permit|legally authori[sz]ed/, choose: (p) => (yesno(p.work_authorized_us) === 'No' ? NO : [...YES, /authori[sz]ed/, /citizen/]), text: () => 'Yes' },
  { re: /relocat/, choose: (p) => (yesno(p.willing_to_relocate) === 'Yes' ? YES : NO), text: (p) => yesno(p.willing_to_relocate) || 'No' },
  { re: /18 years|age of 18|over 18|at least 18|legal (working )?age/, choose: () => YES, text: () => 'Yes' },
  { re: /(previously|ever|currently|formerly) (been )?(employed|worked)|former employee|worked (here|for us|for this company) before|employee of .* (before|previously)/, choose: () => NO, text: () => 'No' },
  { re: /non-?compete|non-?solicit/, choose: () => NO, text: () => 'No' },
  { re: /(relative|family member|related to).*(work|employ)|(work|employ).*(relative|family member)/, choose: () => NO, text: () => 'No' },
  { re: /security clearance|active clearance/, choose: () => [/^no\b/, /none/, /not applicable/] },
  { re: /background (check|screen)|drug (test|screen)/, choose: () => YES, text: () => 'Yes' },
  { re: /(text|sms) (message|messages|notification|updates|alerts)|consent to (receive )?(text|sms)|opt.?in.*(text|sms)/, choose: (p) => (yesno(p.sms_consent) === 'No' ? NO : YES) },
  { re: /(willing|comfortable|able|open).{0,30}(work(ing)? )?remote/, choose: () => YES, text: () => 'Yes' },
  { re: /take-?home|coding (test|challenge|exercise|assessment)|interview exercise|skills? assessment/, choose: () => YES, text: () => 'Yes' },
  // --- voluntary self-identification ---
  { re: /transgender/, choose: () => [/^no\b/] },
  { re: /sexual orientation/, choose: () => [/heterosexual|straight/] },
  { re: /hispanic|latin[oax]/, not: /\brace\b/, choose: (p) => (yesno(p.hispanic_latino) === 'Yes' ? YES : [/^no\b/, /not hispanic/]) },
  { re: /\brace\b|ethnicit/, choose: (p) => (/asian/i.test(p.race_ethnicity) ? [/^asian\b/, /\basian\b/] : norm(p.race_ethnicity) ? [new RegExp('^' + norm(p.race_ethnicity).split(' ')[0])] : [/decline|not (wish|want) to|don'?t wish|prefer not/]) },
  { re: /gender|\bsex\b/, not: /orientation|transgender|pronoun/, choose: (p) => (/female|woman/i.test(p.gender) ? [/^female\b/, /^woman\b/, /\bfemale\b/] : [/^male\b/, /^man\b/, /\bmale\b/, /\bman\b/]) },
  { re: /veteran/, choose: () => [/not a (protected )?veteran/, /^i am not/, /^no\b/] },
  { re: /disabilit/, choose: () => [/^no\b/, /do not have a disability|don'?t have a disability/] },
  { re: /pronoun/, choose: (p) => [new RegExp(norm(p.pronouns || 'he/him').replace('/', '.?'))], text: v('pronouns') },
  // --- source ---
  { re: /how did you (hear|find|learn)|hear about|where did you (hear|find|see|first|learn)|first (hear|see|learn|find)|(job|application|candidate|referral) source|^source\b/, not: /referred by|employee('s)? name|who referred|name of/, choose: () => HOW_HEARD, text: () => 'Jobright.ai' },
  // --- identity and contact ---
  { re: /e-?mail/, not: /referr|manager|reference|supervisor/, text: v('email') },
  { re: /preferred (first )?name|nick ?name/, text: (p) => p.preferred_name || p.first_name },
  { re: /first name|given name|\bfname\b|legal first/, text: v('first_name') },
  { re: /last name|family name|surname|\blname\b|legal last/, text: v('last_name') },
  { re: /middle name|middle initial/, skip: true },
  { re: /full name|^name\b|your name|legal name|signature/, not: /company|employer|school|university|reference|referr|manager|emergency|recruiter|supervisor/, text: v('full_name') },
  { re: /country code|dial code|phone.*country/, choose: () => [/united states.*\+1|\+1.*united states/, /\(\+1\)|^\+1\b/, /^united states/], text: v('phone_country_code') },
  { re: /phone type|device type/, choose: () => [/mobile|cell/] },
  { re: /phone|mobile|cell/, not: /extension|\bext\b/, text: v('phone') },
  { re: /address line 2|address 2|apartment|suite|apt/, skip: true },
  { re: /address|street/, not: /e-?mail|web|url|ip address/, text: v('address_line1') },
  { re: /zip|postal/, text: v('zip') },
  { re: /\bcity\b/, not: /state/, text: v('city') },
  { re: /\bstate\b|province|region/, not: /united states|statement|status/, choose: (p) => [p.state_full, p.state].filter(Boolean), text: (p) => p.state_full || p.state },
  { re: /country/, not: /citizen|countries/, choose: () => [/^united states( of america)?$/, /^usa?$/, /^united states/], text: v('country') },
  { re: /location|where (are you|do you) (currently )?(located|live|reside|based)|current city|city.*state/, combo: v('city'), text: v('current_location') },
  // --- links ---
  { re: /linkedin/, text: v('linkedin') },
  { re: /github/, text: v('github') },
  { re: /website|portfolio|personal (site|url|page)|other url|\bblog\b/, not: /employer|company'?s? (web)?site|organi[sz]ation/, text: (p) => p.website || p.github },
  // --- background ---
  { re: /current (company|employer)|most recent (company|employer)/, text: v('current_company') },
  { re: /current (job )?title|current (role|position)|most recent (job )?title/, text: v('current_title') },
  { re: /graduation (year|date)|year.*graduat/, text: v('graduation_year') },
  { re: /school|university|college|institution/, not: /high school/, combo: v('school'), text: v('school') },
  { re: /\bdegree\b/, not: /year|date/, choose: () => [/^bachelor/, /bachelor/, /^bs\b|^b\.s/], text: v('highest_degree') },
  { re: /major|field of study|discipline|area of study/, choose: (p) => [new RegExp(norm(p.degree_field || 'computer science'))], text: v('degree_field') },
  { re: /total years|years of (professional |relevant |work |industry )?experience\b(?! (with|in|using))/, text: v('years_experience') },
  { re: /salary|compensation|pay expectation|desired pay|expected (pay|salary)|rate expectation/, text: v('salary_expectation') }
];

const CONSENT = /agree|consent|acknowledge|certify|confirm|accept|terms|privacy|i understand|attest|authorize|read and understood/;

// Questions whose answers depend on the job: never reused from the saved answers.
const JOB_SPECIFIC = /salary|compensation|\bpay\b|rate expectation|why|interest|this (role|company|position|team|job)|cover letter|tell us|describe|about yourself|motivat|excit|explain|example|summar/;
export const questionKey = (f) => norm(f.question || f.label).replace(/[^a-z0-9 ]/g, '').trim().slice(0, 160);

function ruleFor(f) {
  const t = fieldText(f);
  return RULES.find((r) => r.re.test(t) && !(r.not && r.not.test(t)));
}

// The example answer for a question (lib/examples.js), if there is one.
function exampleFor(f) {
  const t = norm(f.question || f.label || f.name || '');
  return t ? EXAMPLES.find((e) => e.q && e.q.test(t) && !(e.not && e.not.test(t))) : null;
}

// Today's date as forms want it: 2026-11-11 for a date picker, else 11/11/2026.
function today(iso) {
  const d = new Date();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return iso ? `${d.getFullYear()}-${mm}-${dd}` : `${mm}/${dd}/${d.getFullYear()}`;
}

// The posting's apply button, including job boards (Jobicy, Himalayas, Remote OK, We Work Remotely…)
// that send you on to the company's own application.
const APPLY_BTN = /^(apply|apply now|apply (for|to) (this )?(job|position|role|opening)|apply online|apply here|start (my |your )?application|i'?m interested|begin application|apply (now )?(on|at|via) (the )?(company|employer'?s?|hiring company)('s)?( website| site| page)?|apply (on|at) [a-z0-9 .&-]{2,40}|apply externally|apply on website|go to (the )?application|continue to (the )?application|view (and|&) apply|apply to (this )?job)$/;

// A job posting you can read without an account: the description is on the page and an Apply button is there.
// The bot clicks Apply instead of signing in / signing up on the job board.
export function postingWithApply(snap) {
  const apply = findButton(snap.buttons, [APPLY_BTN]);
  const formish = snap.fields.filter((f) => f.kind !== 'file' && f.kind !== 'checkbox').length;
  return apply && String(snap.text || '').length > 600 && formish < 3 && !snap.fields.some((f) => f.kind === 'file') ? apply : null;
}
const START_BTN = [/^autofill with resume$/, /^(upload|apply with) (resume|cv)$/, /^apply manually$/];
const NEXT_BTN = [/^(verify|verify (email|code|account|my email)|submit code|confirm code|confirm)$/,
  /^(save (and|&) continue|continue|next|next step|proceed|save and next|review( application)?|review and submit)$/,
  /^(submit|submit (your |my )?application|send( my)? application|submit (and|&) apply|apply|apply now|finish|complete( application)?)$/];
const BAD_BTN = /linkedin|indeed|google|facebook|seek|glassdoor|easy apply|sign ?in|log ?in/;

const btnText = (b) => norm(b.text).replace(/[^a-z0-9'&/ ]/g, '').trim();
function findButton(buttons, patterns) {
  const usable = buttons.filter((b) => !b.disabled && !BAD_BTN.test(norm(`${b.text} ${b.href || ''}`)));
  for (const re of patterns) {
    const hit = usable.find((b) => re.test(btnText(b)));
    if (hit) return hit;
  }
  return null;
}

/**
 * @returns {{ actions, next_click, page_type, summary, askAI, aiFields, needsDocs, handled }}
 */
// The middle of the job's posted yearly pay range, rounded to 5,000 ("$180K – $199K" -> 190000). '' when none.
export function salaryMidpoint(text) {
  const nums = [...String(text || '').matchAll(/\$?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k\b)?/gi)]
    .map((m) => parseFloat(m[1].replace(/,/g, '')) * (m[2] ? 1000 : 1))
    .filter((n) => n >= 30000 && n <= 1000000);
  if (!nums.length) return '';
  return String(Math.round((Math.min(...nums) + Math.max(...nums)) / 2 / 5000) * 5000);
}

export function rulePlan(snap, { profile, saved = {}, uploadCover = true, coverLetter = '', forceAI = false, jobSalary = '' }) {
  // salary: the posting's own range when it shows one, otherwise the profile's figure
  const p = { ...profile, salary_expectation: salaryMidpoint(jobSalary) || profile.salary_expectation || '' };
  const actions = [];
  const leftover = [];
  let wantsCoverText = false;
  const fields = snap.fields;
  const fileFields = fields.filter((f) => f.kind === 'file');

  for (const f of fields) {
    const t = fieldText(f);
    const filled = isFilled(f);
    const q = f.question || f.label || '';
    let act = null;
    let handled = false;

    if (f.kind === 'password') {
      // password and confirm-password boxes: the placeholder is swapped for the real password inside the page
      handled = true;
      if (!filled) act = { op: 'fill', value: '{{password}}' };
    } else if (f.kind === 'file') {
      handled = true;
      if (!filled) {
        if (/cover/.test(t)) act = uploadCover ? { op: 'upload_cover' } : null;
        else if (/resume|\bcv\b|curriculum/.test(t) || (fileFields.length === 1 && !/transcript|portfolio|additional|other|photo|image/.test(t))) act = { op: 'upload_resume' };
        else handled = false;
      }
    } else if (f.kind === 'checkbox') {
      // your examples first: "pick one" checkbox lists (disability, …) and boxes left unticked
      const own = norm(f.label || q);
      const ex = EXAMPLES.find((e) => (e.check && e.check.test(own)) || (e.never && e.never.test(own)));
      if (ex) {
        handled = true;
        if (ex.check && !f.checked) act = { op: 'check' };
      } else if (CONSENT.test(t)) {
        handled = true;
        if (!f.checked) act = { op: 'check' };
      }
    } else if ((f.kind === 'textarea' || f.kind === 'richtext') && /cover letter/.test(t)) {
      handled = true;
      wantsCoverText = true;
      if (!filled && coverLetter) act = { op: 'fill', value: coverLetter };
    } else {
      // your examples first (lib/examples.js)
      const ex = exampleFor(f);
      if (ex?.leave) {
        handled = true;
        if (filled) act = { op: 'fill', value: '' };
      } else if (ex?.cover && (f.kind === 'textarea' || f.kind === 'richtext' || TEXTY.has(f.kind))) {
        handled = true;
        wantsCoverText = true;
        if (!filled && coverLetter) act = { op: 'fill', value: coverLetter };
      } else if (ex?.today) {
        handled = true;
        if (!filled) act = { op: 'fill', value: today(f.kind === 'date') };
      } else if (ex?.answer) {
        const prefs = [].concat(ex.answer);
        if (isChoice(f)) {
          const opt = pickOption(f.options, prefs);
          if (opt) {
            handled = true;
            if (norm(f.value) !== norm(opt)) act = { op: f.kind === 'combobox' ? 'choose' : 'select', value: opt, question: q };
          }
        } else if ((TEXTY.has(f.kind) || f.kind === 'textarea') && ex.text) {
          const val = fillProfile(ex.text, p);
          if (val) {
            handled = true;
            if (!filled) act = { op: f.kind === 'combobox' ? 'choose' : 'fill', value: val, question: q };
          }
        }
      }
      const rule = handled ? null : ruleFor(f);
      if (rule?.skip) handled = true;
      else if (rule && isChoice(f) && rule.choose) {
        const opt = pickOption(f.options, rule.choose(p));
        if (opt) {
          handled = true;
          if (norm(f.value) !== norm(opt)) act = { op: f.kind === 'combobox' ? 'choose' : 'select', value: opt, question: q };
        }
      } else if (rule && !isChoice(f) && TEXTY.has(f.kind)) {
        const val = f.kind === 'combobox' && rule.combo ? rule.combo(p) : rule.text?.(p);
        if (val) {
          handled = true;
          // sites that read the resume prefill the wrong town (Rolling Hills): put the profile's city back
          const wrongTown = filled && /rolling hills/i.test(f.value) && !/rolling hills/i.test(val);
          if (!filled || wrongTown) act = { op: f.kind === 'combobox' ? 'choose' : 'fill', value: val };
        }
      }
      // the same question answered before (by the AI) on another application
      if (!handled && !JOB_SPECIFIC.test(norm(q)) && f.kind !== 'textarea' && f.kind !== 'richtext') {
        const hit = saved[questionKey(f)];
        if (hit) {
          if (isChoice(f)) {
            const opt = pickOption(f.options, String(hit.answer).split(' | '));
            if (opt) {
              handled = true;
              if (norm(f.value) !== norm(opt)) act = { op: f.kind === 'combobox' ? 'choose' : 'select', value: opt, question: q, saved: true };
            }
          } else if (TEXTY.has(f.kind)) {
            handled = true;
            if (!filled) act = { op: f.kind === 'combobox' ? 'choose' : 'fill', value: hit.answer, question: q, saved: true };
          }
        }
      }
    }

    if (act) actions.push({ id: f.id, ...act });
    // the AI gets required questions the rules could not answer, and anything the site flagged
    if (f.error || (!handled && !filled && isRequired(f)) || (forceAI && !filled)) leftover.push(f);
  }

  const needsDocs = actions.some((a) => a.op.startsWith('upload')) || wantsCoverText;
  const formish = fields.filter((f) => f.kind !== 'file').length;
  const summaryBits = [`${actions.length} filled by rules`, actions.some((a) => a.saved) && `${actions.filter((a) => a.saved).length} from saved answers`].filter(Boolean);

  // job posting / start page: the obvious button, no AI.
  // Start options first: they show in a dialog on top of the posting, whose Apply button is still there.
  const start = !formish && findButton(snap.buttons, START_BTN);
  if (!forceAI && start) {
    return { actions: [], next_click: start.id, page_type: 'job_posting', summary: `start option "${start.text}"`, askAI: false, aiFields: [], needsDocs: false };
  }
  const apply = findButton(snap.buttons, [APPLY_BTN]);
  if (!forceAI && apply && formish < 3 && !fileFields.length) {
    return { actions: [], next_click: apply.id, page_type: 'job_posting', summary: `clicking "${apply.text}"`, askAI: false, aiFields: [], needsDocs: false };
  }

  const next = findButton(snap.buttons, NEXT_BTN);
  const isForm = fields.length > 0;
  const askAI = forceAI || !isForm || leftover.length > 0 || !next || !!snap.errors?.length;
  return {
    actions, next_click: next?.id || '', page_type: isForm ? 'application_form' : 'other',
    summary: `${isForm ? `form with ${fields.length} fields, ` : ''}${summaryBits.join(', ')}${next ? `, then "${next.text}"` : ''}`,
    askAI, aiFields: forceAI ? fields : leftover, needsDocs, wantsCoverText
  };
}

// Answers the AI gave to reusable questions, so the next application does not ask again.
export function answersToSave(snap, aiActions) {
  const out = {};
  for (const a of aiActions || []) {
    if (!['select', 'choose', 'fill'].includes(a.op) || !a.value || /\{\{/.test(a.value) || String(a.value).length > 80) continue;
    const f = snap.fields.find((x) => x.id === a.id);
    if (!f || f.kind === 'textarea' || f.kind === 'richtext' || f.kind === 'password' || f.kind === 'file') continue;
    const q = f.question || f.label || '';
    const key = questionKey(f);
    if (key.length < 8 || JOB_SPECIFIC.test(norm(q))) continue;
    out[key] = { answer: String(a.value), q: q.slice(0, 200), t: Date.now() };
  }
  return out;
}
