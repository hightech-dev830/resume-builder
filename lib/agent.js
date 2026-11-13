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
- First classify the page (page_type). On a job posting, click the button that starts the application (Apply / Apply now / Apply for this job / I'm interested). Ignore "Apply with LinkedIn/Indeed/Google" unless nothing else exists. Some Apply buttons are dropdowns ("Apply Now ▾", marked "opens a dropdown menu"): clicking one opens a small menu with a second "Apply Now" (and "Start applying with LinkedIn"); then click the menu's plain "Apply Now" item (marked "item in an open menu"), not the dropdown button again.
- If the page shows an error summary such as "Your form needs corrections / Missing entry for required field: <question>", answer exactly those questions (find each one among the fields by its question text) before clicking Submit again. For a choice question, always pick one of its options: e.g. preferred programming language for interviews -> the candidate's interview_language (Python), countries you are seeking to work in -> United States.
- Cookie or consent dialogs: accept them (Accept / Agree / Allow all / OK / Yes) before anything else, by putting that button in next_click with no other actions.
- Fill every required field and every optional field you have a good answer for. Skip fields that already hold the correct value. Never invent employers, degrees or licenses.
- Use the candidate profile facts exactly. Values like {{password}}, {{ssn_last4}}, {{dob_mm_dd_yyyy}} are placeholders for secrets: copy them verbatim into value; the browser replaces them. Only fill SSN-related fields with {{ssn_last4}} when the field explicitly asks for the last 4 digits of the SSN; never enter a full SSN.
- Resume upload: use op "upload_resume" on the file input (or on the upload button/drop zone when there is no file input). Cover letter upload: "upload_cover" when there is a cover-letter file input. If the form has a cover letter text box instead, "fill" it with the cover letter text provided.
- If the form offers "autofill from resume" / "upload resume to prefill", upload the resume there first, then fill the remaining fields on the next turn.
- Selects, radio groups and checkbox groups: use op "select" on the field id (for a checkbox group with several right answers, join them with " | "). Single checkboxes: "check" / "uncheck". Custom dropdowns / typeaheads (react-select, comboboxes, location search): use op "choose".
- Dropdown values come from the FORM, not from the profile: when a field lists options=[...], the value MUST be one of those options, copied exactly. Map the candidate's answer to the offered option that means the same (profile "Male (Man)" -> "Male"; "Asian (Not Hispanic or Latino)" -> "Asian"; "No, I do not have a disability" -> the option that says No / does not have one). Never send an answer the form does not offer, and avoid "Other" (it usually opens an extra text box) unless nothing else fits. Only comboboxes without options (typeahead search such as location or school) take free text to type.
- If the history says "no option matches" and lists the options, pick one of those listed options this time.
- Screening questions: answer truthfully from the profile and resume. Work authorization in the US: Yes. Needs visa sponsorship now or in future: No. Willing to relocate: answer per the profile (remote roles: No). Start date: the profile's start date. Salary expectation: use the job's posted range midpoint if shown, otherwise the profile's salary expectation. For "how did you hear about us" / source / referral source: pick from the offered options: "Jobright" if offered, otherwise "Indeed", otherwise another job board or online option (LinkedIn, Glassdoor, ZipRecruiter, Job Board, Online, Internet, Company website). Never "Other", never "Referral" / "Employee referral". Years of experience with a specific skill or technology: for a choice question pick the option with the MOST years (e.g. "10+ years" over "5-10 years"); for a typed answer enter 6; for "Do you have N+ years of experience with X?" answer Yes.
- Free-text technical or motivation questions: write 2-5 plain, specific sentences in first person based on the resume and the job. No hype, no em dashes. Put the question text in "question".
- EEO / voluntary self-identification: use the profile's answers (gender, race/ethnicity, veteran, disability). Pick the offered option that means the same thing; only if none does, "Decline to self-identify".
- Agreements / consent checkboxes (privacy policy, terms, SMS consent, certify information is accurate): check them.
- Start options: when the site offers several ways to start (for example Workday's "Autofill with Resume" / "Apply Manually" / "Use My Last Application"), choose "Autofill with Resume" (or "Upload resume" / "Apply with resume"); the resume is uploaded on the next page.
- Accounts: many sites (Workday, iCIMS, Taleo, SuccessFactors) need an account first. If a page asks you to sign in or create an account, do it yourself:
  - New site (history shows no account created here yet): go to account creation (click "Create Account" / "Sign Up" / "Register" / "New user?"), fill email, {{password}}, verify/confirm password with {{password}}, check the terms/consent box, and press the create button.
  - If the site says the account already exists (or history shows you created it), sign in with the email and {{password}}.
  - After creating an account, if the site shows a sign-in form, sign in.
  - Only a wrong-password / locked-account error or an email/SMS verification code is a reason for needs_human.
- Identify-yourself steps are NOT sign-in or account pages (page_type "application_form", never "login"/"register"), even when the URL says /login:
  - iCIMS "Enter Your Information" with only an Email box: type the candidate's email, leave the "If you are a European Union (EU) or United Kingdom (UK) resident, please check this box" checkbox UNCHECKED, click "Next".
  - ADP "Tell us about yourself" (First Name, Last Name, Email, Mobile Number): fill them, click "Continue" (not the LinkedIn/Google/Facebook buttons). In the "Get your verification code" dialog ("Where would you like to receive a verification code?") choose the candidate's EMAIL address (not the phone number) and click "Send".
  - When the page then asks for the code that was sent (a verification-code / one-time-code box), set needs_human "enter the verification code sent to <email>"; the person types it and the bot continues.
- Ashby (jobs.ashbyhq.com): the posting has two tabs, "Overview" (the job description) and "Application" (the form). On the Overview tab click the "Application" tab (or "Apply for this Job"). On the form: skip the "Autofill from resume" upload box (it retypes the form) and use "upload_resume" on the Resume field; Yes/No questions are radio groups whose options are the Yes / No buttons (use "select" with "Yes" or "No"); Location is a typeahead: "choose" the candidate's city; answer every required question, then click "Submit Application".
- Multi-step wizards (progress bar with steps like My Information, My Experience, Application Questions, Voluntary Disclosures, Self Identify, Review): fill the current step completely, then press "Save and Continue" / "Continue" / "Next". On "My Experience", if the resume upload already filled work and education, just continue; otherwise add the work history and education from the resume. On the Review step press Submit.
- After filling, set next_click to the button that advances (Next / Continue / Save and continue / Review / Submit application). Prefer the final Submit on review pages.
- Make your own decisions. Never set needs_human because you cannot find the form, a field or a button: pick the most likely way forward (Apply, Autofill with Resume, Continue, Next, Create Account, Sign In). If the page looks empty or still loading, return page_type "other" with no actions and next_click "" so the browser waits and looks again.
- If the snapshot shows validation errors from the last attempt, fix exactly those fields this turn.
- Success: page_type "success" and done=true when the text confirms the application was received (thank you for applying, application submitted, we received your application, congratulations). Same for already_applied.
- Set needs_human (and no next_click) ONLY for: a visible captcha / "I'm not a robot" check, an email or SMS verification code, or a wrong password / locked account. For a required question with no answer in the profile, answer it sensibly from the resume (or "N/A" for free text) instead of stopping. If the same step keeps failing (see history), try a different approach (another option, another button) rather than giving up.
- Never click links that leave the application (company homepage, other jobs, social links, "Sign in with Google/LinkedIn/Facebook", "Apply with LinkedIn"). If the ONLY way to apply is through LinkedIn (Easy Apply, or the site requires signing in with LinkedIn), return page_type "requires_linkedin" with no actions. A plain LinkedIn profile URL field is fine to fill.
- Use only ids that appear in the snapshot.
- The browser fills the standard questions (contact details, work authorization, sponsorship, EEO, source, uploads, consent) by itself. When the snapshot says other fields were already filled automatically, answer ONLY the fields listed, and still set next_click to the button that moves forward.`;

function profileBlock(profile) {
  return Object.entries(profile || {})
    .filter(([, v]) => v !== '' && v != null)
    .map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`)
    .join('\n');
}

// Short resume for page planning: summary, skills, and each role with its top bullets.
// The free-text answers need the facts, not every bullet.
function resumeBrief(r) {
  if (!r) return '';
  const b = r.basics || {};
  return [
    `${b.name} | ${b.label}`,
    b.summary,
    'Skills: ' + (r.skills || []).map((s) => `${s.name}: ${(s.keywords || []).join(', ')}`).join(' | '),
    ...(r.work || []).map((w) => `${w.position} at ${w.name} (${w.startDate} to ${w.endDate || 'present'})\n- ${(w.highlights || []).slice(0, 3).join('\n- ')}`),
    'Education: ' + (r.education || []).map((e) => `${e.studyType} in ${e.area}, ${e.institution} (${e.endDate || ''})`).join('; ')
  ].filter(Boolean).join('\n').slice(0, 4500);
}

function snapshotBlock(snap, prefilled = 0) {
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
  const buttons = (snap.buttons || []).map((b) => `[${b.id}] ${b.kind} "${b.text}"${b.href ? ` -> ${b.href}` : ''}${b.disabled ? ' (disabled)' : ''}${b.menu ? ' (opens a dropdown menu)' : ''}${b.inMenu ? ' (item in an open menu)' : ''}`);
  return [
    `URL: ${snap.url}`,
    `Title: ${snap.title}`,
    snap.captcha ? `Captcha detected: ${snap.captcha}` : '',
    snap.steps ? `Wizard steps: ${snap.steps}` : '',
    snap.errors?.length ? `Validation messages on page:\n- ${snap.errors.join('\n- ')}` : '',
    prefilled ? `\n${prefilled} other fields on this page were already filled automatically; they are not listed.` : '',
    `\nFIELDS TO ANSWER (${fields.length}):\n${fields.join('\n') || '(none)'}`,
    `\nBUTTONS / LINKS (${buttons.length}):\n${buttons.join('\n') || '(none)'}`,
    // a form needs little page text (its fields carry the questions); other pages need more to be understood
    `\nVISIBLE TEXT:\n${String(snap.text || '').slice(0, fields.length || prefilled ? 1500 : 5000)}`
  ].filter(Boolean).join('\n');
}

async function planStep({ cfg, snapshot, job, profile, resume, coverLetter, history, files, prefilled = 0 }) {
  // parts that stay the same on every page of a bid come first, so the provider's prompt cache covers them
  const user = [
    '<candidate_profile>', profileBlock(profile), '</candidate_profile>',
    '', '<resume>', resumeBrief(resume), '</resume>',
    '', '<job>', `Company: ${job?.company || ''}`, `Role: ${job?.role || ''}`, `Location: ${job?.location || ''}`, `Salary: ${job?.salary || ''}`, '</job>',
    coverLetter ? `\n<cover_letter>\n${coverLetter}\n</cover_letter>` : '',
    '', `<files>Resume PDF ready: ${files?.resume ? 'yes' : 'no'}. Cover letter PDF ready: ${files?.cover ? 'yes' : 'no'}.</files>`,
    history?.length ? `\n<history>\n${history.slice(-5).map((h, i) => `${i + 1}. ${h}`).join('\n')}\n</history>` : '',
    '', '<page_snapshot>', snapshotBlock(snapshot, prefilled), '</page_snapshot>',
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
