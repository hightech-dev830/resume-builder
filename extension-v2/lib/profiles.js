// A profile is one candidate you bid for:
//  - personal: what forms ask (contact, address, work authorization, EEO, links). Secrets (password,
//    SSN last 4, date of birth) stay on this PC and never reach the AI: it sees {{placeholders}}.
//  - resume: the factual base (JSON Resume shape). Employers, dates, locations and education are fixed;
//    each job can list `industry` and `stack` (the technologies really used there), which the resume
//    writer and the validator use as evidence.
//  - prefs: default template, how freely the stack may be adapted, extra instructions for the writer.

export const PERSONAL_FIELDS = [
  ['Identity', [
    ['first_name', 'First name'], ['last_name', 'Last name'], ['full_name', 'Full name'], ['preferred_name', 'Preferred name'],
    ['email', 'Email'], ['phone', 'Phone'], ['phone_country_code', 'Phone country code']
  ]],
  ['Address', [
    ['address_line1', 'Street address'], ['city', 'City'], ['state', 'State (code)'], ['state_full', 'State (full)'],
    ['zip', 'ZIP'], ['county', 'County'], ['country', 'Country'], ['current_location', 'Current location']
  ]],
  ['Links', [['linkedin', 'LinkedIn'], ['github', 'GitHub'], ['website', 'Website / portfolio']]],
  ['Background', [
    ['current_title', 'Current title'], ['current_company', 'Current company'], ['years_experience', 'Years of experience'],
    ['highest_degree', 'Highest degree'], ['degree_field', 'Field of study'], ['school', 'School'], ['graduation_year', 'Graduation year'],
    ['licenses_certifications', 'Licenses / certifications']
  ]],
  ['Work eligibility', [
    ['work_authorized_us', 'Authorized to work in the US'], ['needs_sponsorship', 'Needs sponsorship'], ['citizenship', 'Citizenship'],
    ['willing_to_relocate', 'Willing to relocate'], ['remote_preference', 'Remote preference'], ['start_date', 'Start date'],
    ['notice_period', 'Notice period'], ['salary_expectation', 'Salary expectation (yearly)'], ['how_heard', 'How did you hear about us']
  ]],
  ['Voluntary self-identification', [
    ['gender', 'Gender'], ['pronouns', 'Pronouns'], ['race_ethnicity', 'Race / ethnicity'], ['hispanic_latino', 'Hispanic or Latino'],
    ['veteran_status', 'Veteran status'], ['disability_status', 'Disability status'], ['sexual_orientation', 'Sexual orientation'], ['sms_consent', 'SMS consent']
  ]],
  ['Secrets (this PC only, never sent to AI or Supabase)', [
    ['password', 'Account password for job sites', 'password'], ['ssn_last4', 'SSN last 4', 'password'], ['dob', 'Date of birth (YYYY-MM-DD)', 'password']
  ]]
];

export const DEFAULT_PERSONAL = {
  phone_country_code: '+1', country: 'United States',
  work_authorized_us: 'Yes, authorized to work for any employer in the US', needs_sponsorship: 'No, now or in the future',
  willing_to_relocate: 'No', remote_preference: 'Remote', notice_period: 'Available in 2 weeks', start_date: '2 weeks from offer',
  veteran_status: 'I am not a protected veteran', disability_status: 'No, I do not have a disability', hispanic_latino: 'No',
  sms_consent: 'Yes', how_heard: 'LinkedIn', licenses_certifications: 'None', other_info: ''
};

export const STACK_MODES = {
  strict: 'Strict: each job keeps to the technologies listed in its stack',
  adjacent: 'Adjacent: close equivalents of the listed stack are allowed when the JD asks for them (default)'
};

export function emptyProfile() {
  return {
    name: 'New profile',
    personal: { ...DEFAULT_PERSONAL },
    resume: {
      basics: { name: '', label: '', email: '', phone: '', location: { city: '', region: '', countryCode: 'US' }, profiles: [], summary: '', highlights: [] },
      work: [],
      skills: [],
      education: [],
      languages: [{ language: 'English', fluency: 'Native' }]
    },
    prefs: { template: 'professional', stackMode: 'adjacent', instructions: '' }
  };
}

// Keeps the resume header and the form answers in step (name, email, phone, city, links).
export function syncBasics(p) {
  const pe = p.personal || {};
  const b = (p.resume.basics ||= {});
  b.name = b.name || pe.full_name || [pe.first_name, pe.last_name].filter(Boolean).join(' ');
  b.email = b.email || pe.email || '';
  b.phone = b.phone || pe.phone || '';
  b.location = b.location || {};
  b.location.city = b.location.city || pe.city || '';
  b.location.region = b.location.region || pe.state || '';
  if (!(b.profiles || []).length && pe.linkedin) b.profiles = [{ network: 'LinkedIn', url: pe.linkedin }];
  if (!pe.full_name && b.name) pe.full_name = b.name;
  if (!pe.current_company && p.resume.work?.[0]) pe.current_company = p.resume.work[0].endDate ? '' : p.resume.work[0].name;
  return p;
}

// Earliest start date to today, in whole years.
export function totalYears(resume) {
  const starts = (resume?.work || []).map((w) => ym(w.startDate)).filter(Boolean);
  if (!starts.length) return 0;
  return Math.floor((ym(null) - Math.min(...starts)) / 12);
}

// "2021-03" -> months since year 0; null/'' end date = now
export function ym(d) {
  if (!d) {
    const n = new Date();
    return n.getFullYear() * 12 + n.getMonth();
  }
  const m = /^(\d{4})(?:-(\d{1,2}))?/.exec(String(d));
  return m ? Number(m[1]) * 12 + (m[2] ? Number(m[2]) - 1 : 0) : null;
}

// What the AI sees of the personal info: secrets become placeholders.
export function personalForAI(pe = {}) {
  const out = {};
  for (const [k, v] of Object.entries(pe)) if (!['password', 'ssn_last4', 'dob'].includes(k) && v) out[k] = v;
  if (pe.password) out.password = '{{password}}';
  if (pe.ssn_last4) out.ssn_last4 = '{{ssn_last4}} (only for a field that explicitly asks for the last 4 digits of the SSN)';
  if (dobParts(pe.dob)) out.date_of_birth = '{{dob_mm_dd_yyyy}} (use {{dob_iso}} for yyyy-mm-dd date inputs; only when asked)';
  out.today = new Date().toISOString().slice(0, 10);
  return out;
}

function dobParts(dob) {
  const m = /^(\d{4})[-./](\d{1,2})[-./](\d{1,2})$/.exec(String(dob || '').trim());
  if (!m) return null;
  const [y, mo, d] = [m[1], m[2].padStart(2, '0'), m[3].padStart(2, '0')];
  return { iso: `${y}-${mo}-${d}`, us: `${mo}/${d}/${y}` };
}

export function secretsFor(pe = {}) {
  const d = dobParts(pe.dob);
  return { password: pe.password || '', ssn_last4: pe.ssn_last4 || '', dob_mm_dd_yyyy: d?.us || '', dob_iso: d?.iso || '' };
}

// Imports a profile from JSON: either a full profile ({ name, personal, resume, prefs }) or a bare JSON Resume.
export function importProfile(json) {
  const data = typeof json === 'string' ? JSON.parse(json) : json;
  const p = emptyProfile();
  if (data.resume || data.personal) {
    Object.assign(p, data, { personal: { ...p.personal, ...(data.personal || {}) }, prefs: { ...p.prefs, ...(data.prefs || {}) } });
    p.resume = { ...p.resume, ...(data.resume || {}) };
  } else if (data.basics || data.work) {
    p.resume = { ...p.resume, ...data };
  } else throw new Error('Not a profile: expected { personal, resume } or a JSON Resume with basics and work');
  const b = p.resume.basics || {};
  p.name = data.name && typeof data.name === 'string' ? data.name : b.name || 'Imported profile';
  if (b.name && !p.personal.first_name) {
    const [first, ...rest] = b.name.split(/\s+/);
    Object.assign(p.personal, { first_name: first, last_name: rest.join(' '), full_name: b.name });
  }
  for (const k of ['email', 'phone']) if (b[k] && !p.personal[k]) p.personal[k] = b[k];
  if (b.location?.city && !p.personal.city) Object.assign(p.personal, { city: b.location.city, state: b.location.region || '' });
  delete p.id;
  return syncBasics(p);
}
