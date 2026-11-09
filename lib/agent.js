// AI planner for the Chrome auto-bid extension. The extension sends a snapshot of the current
// application page (form fields, buttons, visible text); the model says what kind of page it is,
// what to type or pick in each field, and which button moves the application forward.
// Secrets (password, SSN, date of birth) never reach the model: the profile carries placeholders
// such as {{password}}, and the extension swaps in the real values when it fills the page.
const ai = require('./shared/ai');

const PAGE_TYPES = [
  'job_posting',        // job description with an Apply button
  'application_form',   // fields to fill (contact info, resume, questions)
  'login',              // sign in to an existing candidate account
  'register',           // create a candidate account
  'review',             // final review page before submitting
  'success',            // application submitted / thank-you page
  'already_applied',    // the site says this candidate already applied
  'email_verification', // needs a code or link from email
  'captcha',            // a visible captcha / human check
  'job_closed',         // posting closed or not found
  'requires_linkedin',  // the only way to apply is LinkedIn (Easy Apply / sign in with LinkedIn)
  'blocked',            // anything else the bot cannot get past
  'other'
];

const OPS = ['fill', 'select', 'check', 'uncheck', 'click', 'choose', 'upload_resume', 'upload_cover'];

const PLAN_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['page_type', 'summary', 'actions', 'next_click', 'done', 'needs_human'],
  properties: {
    page_type: { type: 'string', enum: PAGE_TYPES },
    summary: { type: 'string', description: 'One short sentence: what this page is and what you are doing.' },
    actions: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['id', 'op', 'value', 'question'],
        properties: {
          id: { type: 'string', description: 'Element id from the snapshot (e.g. "0:17").' },
          op: { type: 'string', enum: OPS },
          value: { type: 'string', description: 'Text to type, option to pick, or "" for click/check/upload.' },
          question: { type: 'string', description: 'For free-text or screening answers: the question being answered; otherwise "".' }
        }
      }
    },
    next_click: { type: 'string', description: 'Id of the button to press after the actions (Apply / Next / Continue / Submit / Sign in / Create account / Accept cookies), or "".' },
    done: { type: 'boolean', description: 'true only when the application is confirmed submitted (success or already_applied).' },
    needs_human: { type: 'string', description: 'Empty, or the reason a person must step in: only a captcha, an email/SMS verification code, or a wrong password / locked account.' }
  }
};

const SYSTEM = `You drive a browser to submit a job application for the candidate. Each turn you get a snapshot of the current page: form fields (with ids, labels, types, options, current values, required flags), clickable buttons and links, and the visible text. Decide what to fill and what to click so the application moves forward, one page at a time.

How to work:
- First classify the page (page_type). On a job posting, click the button that starts the application (Apply / Apply now / Apply for this job / I'm interested). Ignore "Apply with LinkedIn/Indeed/Google" unless nothing else exists.
- Cookie or consent dialogs: accept them (Accept / Agree / Allow all / OK / Yes) before anything else, by putting that button in next_click with no other actions.
- Fill every required field and every optional field you have a good answer for. Skip fields that already hold the correct value. Never invent employers, degrees or licenses.
- Use the candidate profile facts exactly. Values like {{password}}, {{ssn_last4}}, {{dob_mm_dd_yyyy}} are placeholders for secrets: copy them verbatim into value; the browser replaces them. Only fill SSN-related fields with {{ssn_last4}} when the field explicitly asks for the last 4 digits of the SSN; never enter a full SSN.
- Resume upload: use op "upload_resume" on the file input (or on the upload button/drop zone when there is no file input). Cover letter upload: "upload_cover" when there is a cover-letter file input. If the form has a cover letter text box instead, "fill" it with the cover letter text provided.
- If the form offers "autofill from resume" / "upload resume to prefill", upload the resume there first, then fill the remaining fields on the next turn.
- Selects, radio groups and checkbox groups: use op "select" on the field id with the option text that best matches (for a checkbox group with several right answers, join them with " | "). Single checkboxes: "check" / "uncheck". Custom dropdowns / typeaheads (react-select, comboboxes, location search): use op "choose" with the text to type; the browser picks the best suggestion.
- Screening questions: answer truthfully from the profile and resume. Work authorization in the US: Yes. Needs visa sponsorship now or in future: No. Willing to relocate: answer per the profile (remote roles: No). Start date: the profile's start date. Salary expectation: use the job's posted range midpoint if shown, otherwise the profile's salary expectation. For "how did you hear about us": "Jobright.ai" or the closest option (job board / online). Years of experience with a skill: a realistic number consistent with the resume dates.
- Free-text technical or motivation questions: write 2-5 plain, specific sentences in first person based on the resume and the job. No hype, no em dashes. Put the question text in "question".
- EEO / voluntary self-identification: use the profile's answers (gender, race/ethnicity, veteran, disability). If the exact option is missing, pick "Decline to self-identify" or the closest option.
- Agreements / consent checkboxes (privacy policy, terms, SMS consent, certify information is accurate): check them.
- Start options: when the site offers several ways to start (for example Workday's "Autofill with Resume" / "Apply Manually" / "Use My Last Application"), choose "Autofill with Resume" (or "Upload resume" / "Apply with resume"); the resume is uploaded on the next page.
- Accounts: many sites (Workday, iCIMS, Taleo, SuccessFactors) need an account first. If a page asks you to sign in or create an account, do it yourself:
  - New site (history shows no account created here yet): go to account creation (click "Create Account" / "Sign Up" / "Register" / "New user?"), fill email, {{password}}, verify/confirm password with {{password}}, check the terms/consent box, and press the create button.
  - If the site says the account already exists (or history shows you created it), sign in with the email and {{password}}.
  - After creating an account, if the site shows a sign-in form, sign in.
  - Only a wrong-password / locked-account error or an email/SMS verification code is a reason for needs_human.
- Multi-step wizards (progress bar with steps like My Information, My Experience, Application Questions, Voluntary Disclosures, Self Identify, Review): fill the current step completely, then press "Save and Continue" / "Continue" / "Next". On "My Experience", if the resume upload already filled work and education, just continue; otherwise add the work history and education from the resume. On the Review step press Submit.
- After filling, set next_click to the button that advances (Next / Continue / Save and continue / Review / Submit application). Prefer the final Submit on review pages.
- Make your own decisions. Never set needs_human because you cannot find the form, a field or a button: pick the most likely way forward (Apply, Autofill with Resume, Continue, Next, Create Account, Sign In). If the page looks empty or still loading, return page_type "other" with no actions and next_click "" so the browser waits and looks again.
- If the snapshot shows validation errors from the last attempt, fix exactly those fields this turn.
- Success: page_type "success" and done=true when the text confirms the application was received (thank you for applying, application submitted, we received your application, congratulations). Same for already_applied.
- Set needs_human (and no next_click) ONLY for: a visible captcha / "I'm not a robot" check, an email or SMS verification code, or a wrong password / locked account. For a required question with no answer in the profile, answer it sensibly from the resume (or "N/A" for free text) instead of stopping. If the same step keeps failing (see history), try a different approach (another option, another button) rather than giving up.
- Never click links that leave the application (company homepage, other jobs, social links, "Sign in with Google/LinkedIn/Facebook", "Apply with LinkedIn"). If the ONLY way to apply is through LinkedIn (Easy Apply, or the site requires signing in with LinkedIn), return page_type "requires_linkedin" with no actions. A plain LinkedIn profile URL field is fine to fill.
- Use only ids that appear in the snapshot.`;

function profileBlock(profile) {
  return Object.entries(profile || {})
    .filter(([, v]) => v !== '' && v != null)
    .map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`)
    .join('\n');
}

function resumeText(r) {
  if (!r) return '';
  const b = r.basics || {};
  return [
    `${b.name} | ${b.label}`,
    b.summary,
    'Skills: ' + (r.skills || []).map((s) => `${s.name}: ${(s.keywords || []).join(', ')}`).join(' | '),
    ...(r.work || []).map((w) => `${w.position} at ${w.name} (${w.startDate} to ${w.endDate || 'present'}, ${w.location || ''}): ${w.description || ''}\n- ${(w.highlights || []).join('\n- ')}`),
    'Education: ' + (r.education || []).map((e) => `${e.studyType} in ${e.area}, ${e.institution} (${e.startDate} to ${e.endDate})`).join('; ')
  ].filter(Boolean).join('\n\n');
}

function snapshotBlock(snap) {
  const fields = (snap.fields || []).map((f) => {
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
    if (f.hidden) bits.push('(hidden input)');
    return bits.join(' ');
  });
  const buttons = (snap.buttons || []).map((b) => `[${b.id}] ${b.kind} "${b.text}"${b.href ? ` -> ${b.href}` : ''}${b.disabled ? ' (disabled)' : ''}`);
  return [
    `URL: ${snap.url}`,
    `Title: ${snap.title}`,
    snap.captcha ? `Captcha detected: ${snap.captcha}` : '',
    snap.steps ? `Wizard steps: ${snap.steps}` : '',
    snap.errors?.length ? `Validation messages on page:\n- ${snap.errors.join('\n- ')}` : '',
    `\nFIELDS (${fields.length}):\n${fields.join('\n') || '(none)'}`,
    `\nBUTTONS / LINKS (${buttons.length}):\n${buttons.join('\n') || '(none)'}`,
    `\nVISIBLE TEXT:\n${String(snap.text || '').slice(0, 7000)}`
  ].filter(Boolean).join('\n');
}

async function planStep({ cfg, snapshot, job, profile, resume, coverLetter, history, files }) {
  const user = [
    '<candidate_profile>', profileBlock(profile), '</candidate_profile>',
    '', '<resume>', resumeText(resume), '</resume>',
    coverLetter ? `\n<cover_letter>\n${coverLetter}\n</cover_letter>` : '',
    '', '<job>', `Company: ${job?.company || ''}`, `Role: ${job?.role || ''}`, `Location: ${job?.location || ''}`, `Salary: ${job?.salary || ''}`, '</job>',
    '', `<files>Resume PDF ready: ${files?.resume ? 'yes' : 'no'}. Cover letter PDF ready: ${files?.cover ? 'yes' : 'no'}.</files>`,
    history?.length ? `\n<history>\n${history.map((h, i) => `${i + 1}. ${h}`).join('\n')}\n</history>` : '',
    '', '<page_snapshot>', snapshotBlock(snapshot), '</page_snapshot>',
    '', 'Return the plan for this page as JSON.'
  ].join('\n');
  const text = await ai.complete(cfg, { system: SYSTEM, user, schema: PLAN_SCHEMA, maxTokens: 12000, effort: 'low' });
  try {
    return JSON.parse(text);
  } catch {
    throw new Error('The model did not return a valid page plan. Try again or pick another model.');
  }
}

module.exports = { planStep, PAGE_TYPES };
