// Fills the usual application questions straight from the profile, without the AI.
// Works on the same page snapshot the AI gets and returns actions in the AI's format,
// so the AI is only asked about what is left (or not at all).

export const norm = (s) => String(s || '').toLowerCase().replace(/[_*✱:]/g, ' ').replace(/\s+/g, ' ').trim();

// "how did you hear about us" style answers, best first; "Other" / "Referral" are never picked
const HOW_HEARD = [/^jobright/, /jobright/, /^indeed/, /indeed/, /job ?board/, /linkedin/, /glassdoor/, /ziprecruiter/, /online|internet|web ?search|google/, /company (web)?site|career(s)? (page|site)/, /social media/];
// the job board the bot is on comes first ("HiringCafe" also matches "Hiring Cafe" / "hiring.cafe")
const howHeard = (p) => {
  const board = norm(p.how_heard).replace(/\.(ai|com|cafe)$/, '').replace(/[^a-z0-9]+/g, '');
  if (!board) return HOW_HEARD;
  const loose = new RegExp(board.split('').join('[^a-z0-9]?'));
  return [new RegExp('^' + loose.source), loose, ...HOW_HEARD.filter((r) => !/jobright/.test(r.source) || /jobright/.test(board))];
};
const YES = [/^yes\b/, /^y$/, /^true$/];
const NO = [/^no\b/, /^n$/, /^false$/];

// an upload that parses the resume to fill the form for you, not the resume field itself
export const isAutofillUpload = (f) => f.kind === 'file' && /auto-?fill|autocomplete|parse|import (from|your)/i.test(`${f.label || ''} ${f.question || ''} ${f.name || ''}`);
const fieldText = (f) =>norm([f.question, f.label, f.name, f.placeholder].filter(Boolean).join(' | '));
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
// the chosen language first, then the usual fallbacks (never "C++" for "C", or "Java" for "JavaScript")
const langPrefs = (lang) => {
  const want = norm(lang || 'Python').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return [new RegExp(`^${want}\\b(?![+#])`), new RegExp(`\\b${want}\\b(?![+#])`), /^python\b/, /^(javascript|typescript)\b/, /^java\b(?!script)/, /^c\+\+/];
};
// "years of experience with <skill>" choices: the option with the most years ("10+ years" over "5-10 years")
const longestYears = (options) => {
  let best = null, top = -1;
  for (const o of options || []) {
    const t = norm(o);
    const nums = (t.match(/\d+(\.\d+)?/g) || []).map(Number);
    if (!nums.length) continue;
    const score = Math.max(...nums) + (/\+|more|over|above|greater|plus|or longer/.test(t) ? 0.5 : 0);
    if (score > top) { top = score; best = o; }
  }
  return best;
};
const SKILL_YEARS_DEFAULT = '6';
const yesno = (s) => (/^(yes|true)\b/i.test(String(s)) ? 'Yes' : /^(no|false)\b/i.test(String(s)) ? 'No' : '');

// Each rule: match on the field's label/question; `choose` = option preferences, `text` = value to type.
// Order matters: specific questions first (sponsorship before work authorization before address "state").
const RULES = [
  // --- identity check (ADP "Get your verification code": the phone number or the email) ---
  { re: /verification code|receive (a |the |your )?code|send (the |a )?code to|where (should|would) (we|you like (us )?to) send/, choose: (p) => [new RegExp(`^${norm(p.email).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`), /@/, /e-?mail/] },
  // iCIMS "If you are a European Union (EU) or United Kingdom (UK) resident, please check this box": you are not
  { re: /(european union|\beu\b|united kingdom|\buk\b|gdpr).{0,40}resident|resident.{0,40}(european union|\beu\b|united kingdom|\buk\b)/, choose: () => NO, text: () => 'No' },
  // --- screening ---
  // "In which of the following employment eligible countries are you seeking to work, if hired?" (Quora/Ashby)
  { re: /countr(y|ies).*(seeking to work|want to work|work from|working from|be (based|working)|employ|eligible|if hired)|(seeking to work|work from|based in|employ(ment|ed) in|eligible).*countr(y|ies)/, not: /citizen/, choose: () => [/^united states( of america)?$/, /^usa?$/, /^united states/], text: () => 'United States' },
  // "What is your preferred programming language for your interviews?"
  { re: /(preferred|favou?rite|primary|strongest|choose a|which) (programming |coding )?language|programming language.*interview|(coding|programming) language/, not: /spoken|speak|fluen|english/, choose: (p) => langPrefs(p.interview_language), text: (p) => p.interview_language || 'Python' },
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
  { re: /\brace\b|ethnicit/, choose: (p) => (/asian/i.test(p.race_ethnicity) ? [/^asian\b/, /\basian\b/] : [new RegExp('^' + norm(p.race_ethnicity).split(' ')[0])]) },
  { re: /gender|\bsex\b/, not: /orientation|transgender|pronoun/, choose: (p) => (/female|woman/i.test(p.gender) ? [/^female\b/, /^woman\b/, /\bfemale\b/] : [/^male\b/, /^man\b/, /\bmale\b/, /\bman\b/]) },
  { re: /veteran/, choose: () => [/not a (protected )?veteran/, /^i am not/, /^no\b/] },
  { re: /disabilit/, choose: () => [/^no\b/, /do not have a disability|don'?t have a disability/] },
  { re: /pronoun/, choose: (p) => [new RegExp(norm(p.pronouns || 'he/him').replace('/', '.?'))], text: v('pronouns') },
  // --- source ---
  { re: /how did you (hear|find|learn)|hear about|where did you (hear|find|see|first|learn)|first (hear|see|learn|find)|(job|application|candidate|referral) source|^source\b/, not: /referred by|employee('s)? name|who referred|name of/, choose: (p) => howHeard(p), text: (p) => p.how_heard || 'Jobright.ai' },
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
  { re: /website|portfolio|personal (site|url|page)|other url|\bblog\b/, text: (p) => p.website || p.github },
  // --- background ---
  { re: /current (company|employer)|most recent (company|employer)/, text: v('current_company') },
  { re: /current (job )?title|current (role|position)|most recent (job )?title/, text: v('current_title') },
  { re: /graduation (year|date)|year.*graduat/, text: v('graduation_year') },
  { re: /school|university|college|institution/, not: /high school/, combo: v('school'), text: v('school') },
  { re: /\bdegree\b/, not: /year|date/, choose: () => [/^bachelor/, /bachelor/, /^bs\b|^b\.s/], text: v('highest_degree') },
  { re: /major|field of study|discipline|area of study/, choose: (p) => [new RegExp(norm(p.degree_field || 'computer science'))], text: v('degree_field') },
  // years with a particular skill ("How many years of experience do you have with React?", "Years of Python experience"):
  // pick the longest option; typed in, 6. "Do you have 5+ years of experience with X?" as yes/no: Yes
  { re: /(years?|yrs)\b.{0,60}\bexperience\b.{0,30}\b(with|in|using|on)\b|(years?|yrs)( of)? (?!(of|professional|relevant|work|industry|total|overall|related|prior|previous)\b)[a-z0-9.+#/-]+( [a-z0-9.+#/-]+){0,3} experience|experience (with|in|using|working with) .{0,60}\b(years?|yrs)\b|how long have you (worked with|used|been using|been working with)/,
    not: /total|overall|(with|in) (software|the industry|this field|your field|a professional|professional)\b/,
    choose: (p, f) => [longestYears(f.options), ...YES].filter(Boolean),
    text: (p, f) => (/^(do|have|are) you\b/.test(norm(f.question || f.label)) ? 'Yes' : SKILL_YEARS_DEFAULT) },
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

const APPLY_BTN = /^(apply|apply now|apply (for|to) (this )?(job|position|role)|apply online|apply here|start (my |your )?application|i'?m interested|begin application)$/;
const START_BTN = [/^autofill with resume$/, /^(upload|apply with) (resume|cv)$/, /^apply manually$/];
// the button of a "where should we send your verification code" dialog (it covers the form's Continue)
const SEND_CODE_BTN = /^(send|send( the)?( verification)? code|get( verification)? code|send me (a |the )?code|email me( the code)?|request code)$/;
const NEXT_BTN = [/^(save (and|&) continue|continue|next|next step|proceed|save and next|review( application)?|review and submit)$/,
  /^(submit|submit (your |my )?application|send( my)? application|submit (and|&) apply|apply|apply now|finish|complete( application)?)$/];
const BAD_BTN = /linkedin|indeed|google|facebook|seek|glassdoor|easy apply|sign ?in|log ?in/;

const btnText = (b) => norm(b.text).replace(/[^a-z0-9'&/ ]/g, '').trim();
const usableButtons = (buttons) => buttons.filter((b) => !b.disabled && !BAD_BTN.test(norm(`${b.text} ${b.href || ''}`)));
function findButton(buttons, patterns) {
  const usable = usableButtons(buttons);
  for (const re of patterns) {
    const hit = usable.find((b) => re.test(btnText(b)));
    if (hit) return hit;
  }
  return null;
}
const findButtons = (buttons, re) => usableButtons(buttons).filter((b) => re.test(btnText(b)));

/**
 * @returns {{ actions, next_click, page_type, summary, askAI, aiFields, needsDocs, handled }}
 */
export function rulePlan(snap, { profile: p, saved = {}, uploadCover = true, coverLetter = '', forceAI = false, lastClick = '' }) {
  const actions = [];
  const leftover = [];
  let wantsCoverText = false;
  const fields = snap.fields.filter((f) => f.kind !== 'password');
  const fileFields = fields.filter((f) => f.kind === 'file');
  // text of the form's error messages, to find the questions they name
  const plain = (x) => norm(x).replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim();
  const errLines = (snap.errors || []).map(plain);
  const qKey = (q) => plain(q).slice(0, 60).trim();
  const namedInErrors = (q) => {
    const k = qKey(q);
    return k.length >= 8 && errLines.some((e) => e.includes(k));
  };
  // error lines that name none of the questions (or only the "needs corrections" heading)
  const unexplainedErrors = () => errLines.filter((e) => !/^(your )?form (needs|has) (corrections|errors)$|^please (fix|correct)/.test(e)
    && !fields.some((f) => { const k = qKey(f.question || f.label); return k.length >= 8 && e.includes(k); }));
  const realResume = fileFields.find((f) => !isAutofillUpload(f) && /resume|\bcv\b|curriculum/.test(fieldText(f)))
    || (fileFields.some(isAutofillUpload) ? fileFields.find((f) => !isAutofillUpload(f) && !/cover|transcript|portfolio|additional|other|photo|image/.test(fieldText(f))) : null);

  for (const f of fields) {
    const t = fieldText(f);
    // the page's error summary names this question ("Missing entry for required field: <question>"):
    // whatever the field shows, the site did not take the answer, so answer it again
    const q = f.question || f.label || '';
    const named = namedInErrors(q);
    const filled = isFilled(f) && !named;
    let act = null;
    let handled = false;

    if (f.kind === 'file') {
      handled = true;
      if (!filled) {
        // "Autofill from resume" (Ashby and others) re-types the form from the file, over what was filled;
        // skip it when the form has its own resume field (Workday-style pages with only an autofill upload keep it)
        if (realResume && realResume !== f && isAutofillUpload(f)) act = null;
        else if (/cover/.test(t)) act = uploadCover ? { op: 'upload_cover' } : null;
        else if (f === realResume || /resume|\bcv\b|curriculum/.test(t) || (fileFields.length === 1 && !/transcript|portfolio|additional|other|photo|image/.test(t))) act = { op: 'upload_resume' };
        else handled = false;
      }
    } else if (f.kind === 'checkbox') {
      if (/(european union|eu|united kingdom|uk).{0,40}resident|resident.{0,40}(european union|eu|united kingdom|uk)/.test(t)) {
        handled = true; // iCIMS: "if you are an EU or UK resident, check this box": not you, leave it empty
        if (f.checked) act = { op: 'uncheck' };
      } else if (CONSENT.test(t)) {
        handled = true;
        if (!f.checked) act = { op: 'check' };
      }
    } else if ((f.kind === 'textarea' || f.kind === 'richtext') && /cover letter/.test(t)) {
      handled = true;
      wantsCoverText = true;
      if (!filled && coverLetter) act = { op: 'fill', value: coverLetter };
    } else {
      const rule = ruleFor(f);
      if (rule?.skip) handled = true;
      else if (rule && isChoice(f) && rule.choose) {
        const opt = pickOption(f.options, rule.choose(p, f));
        if (opt) {
          handled = true;
          if (named || norm(f.value) !== norm(opt)) act = { op: f.kind === 'combobox' ? 'choose' : 'select', value: opt, question: q };
        }
      } else if (rule && !isChoice(f) && TEXTY.has(f.kind)) {
        const val = f.kind === 'combobox' && rule.combo ? rule.combo(p) : rule.text?.(p, f);
        if (val) {
          handled = true;
          if (!filled) act = { op: f.kind === 'combobox' ? 'choose' : 'fill', value: val };
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
              if (named || norm(f.value) !== norm(opt)) act = { op: f.kind === 'combobox' ? 'choose' : 'select', value: opt, question: q, saved: true };
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
    // a box for the code that was emailed/texted: only you have it (the AI then hands the page to you)
    const codeBox = !isChoice(f) && TEXTY.has(f.kind) && /verification code|one[- ]time (pass)?code|security code|passcode|enter (the |your )?code|otp/.test(t);
    if (f.error || (!handled && !filled && (isRequired(f) || named || codeBox)) || (named && !act) || (forceAI && !filled)) leftover.push(named && !f.error ? { ...f, required: true, error: 'the form says this required answer is missing' } : f);
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
  // "Apply Now ▾" can be a dropdown whose menu has another "Apply Now": the first click opens the menu,
  // the second (on the menu entry) starts the application. Prefer the open menu's entry, and never
  // click the same Apply button twice in a row (that would just close the menu again).
  const applies = findButtons(snap.buttons, APPLY_BTN);
  const fresh = applies.filter((b) => b.id !== lastClick);
  const apply = fresh.find((b) => b.inMenu) || fresh.find((b) => !b.menu) || fresh[0] || null;
  if (!forceAI && apply && formish < 3 && !fileFields.length) {
    return { actions: [], next_click: apply.id, page_type: 'job_posting', summary: `clicking "${apply.text}"`, askAI: false, aiFields: [], needsDocs: false };
  }

  const codeDialog = fields.some((f) => isChoice(f) && /verification code|receive (a |the |your )?code|send (the |a )?code to/.test(fieldText(f)));
  const next = findButton(snap.buttons, codeDialog ? [SEND_CODE_BTN, ...NEXT_BTN] : NEXT_BTN);
  const isForm = fields.length > 0;
  // errors that name questions the rules just answered need no AI; any other error does
  const askAI = forceAI || !isForm || leftover.length > 0 || !next || unexplainedErrors().length > 0;
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
