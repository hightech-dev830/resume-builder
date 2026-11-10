// Extension settings, profile and server client. Shared by the service worker, popup and options page.

export const DEFAULT_SETTINGS = {
  serverUrl: 'http://localhost:3000', // Resume Builder web app (local `npm run dev` or the Vercel URL)
  token: '',
  listUrl: 'https://jobright.ai/jobs/recommend',
  filters: {
    remoteOnly: true,
    maxAgeHours: 24,       // "today's jobs": posted within the last 24 hours
    maxBids: 25,           // stop after this many submitted applications per run
    refreshMin: 5,         // reload the Jobright list this often (minutes); the bot waits for new jobs
    minMatch: 0,           // skip jobs below this Jobright match %
    titleExclude: '',      // comma-separated words; skip titles containing any
    companyExclude: ''     // comma-separated company names to skip
  },
  behavior: {
    autoSubmit: true,      // false = stop on the final review/submit step for you to check
    accounts: 'try',       // sign-up / sign-in walls: 'try' (create the account; skip the job if it cannot get past) or 'skip'
    workers: 2,            // Job links mode: jobs applied to at the same time (one window each, 1-4)
    linkDelaySec: 2,       // Job links mode: pause between links in one window
    emailWaitSec: 120,     // how long to wait for a verification email in Gmail
    template: '',          // '' = the web app's default template
    uploadCover: true,
    delaySec: 8,           // pause between bids
    stepLimit: 40,         // max pages per application (Workday-style wizards need ~15)
    captchaWaitMin: 3,     // how long to wait for you to solve a captcha
    closeTabs: true        // close the job tabs after a successful bid
  }
};

// Personal details the bot types into forms: Kevin's profile, so a new browser starts filled in.
// The street address and the secrets (password, SSN last 4, date of birth) are NOT here, because
// this repo is public: they come from profile.local.json (kept out of git) or the options page.
export const DEFAULT_PROFILE = {
  first_name: 'Kevin', last_name: 'Burgess', full_name: 'Kevin Burgess', preferred_name: 'Kevin',
  email: 'kcburgess11@gmail.com', phone: '310-933-9572', phone_country_code: '+1',
  address_line1: '', city: 'Rancho Palos Verdes', state: 'CA', state_full: 'California', zip: '90275', county: 'Los Angeles', country: 'United States',
  current_location: 'Rancho Palos Verdes, CA, United States',
  linkedin: 'https://www.linkedin.com/in/kevin-burgess-357505446/', github: 'https://github.com/hightech-dev830', website: 'https://portfolio-kg-red.vercel.app/',
  current_title: 'Senior Software Engineer', current_company: '', years_experience: '11',
  highest_degree: "Bachelor's Degree", degree_field: 'Computer Science', school: 'California State University Dominguez Hills', graduation_year: '2014',
  work_authorized_us: 'Yes, authorized to work for any employer in the US', needs_sponsorship: 'No, now or in the future', citizenship: '',
  willing_to_relocate: 'No', remote_preference: 'Remote',
  start_date: '2026-12-01 (or immediately if the form asks for availability)', notice_period: 'Available immediately', salary_expectation: '',
  gender: 'Male (Man)', pronouns: 'He/Him', sexual_orientation: 'Heterosexual', race_ethnicity: 'Asian (Not Hispanic or Latino)', hispanic_latino: 'No',
  veteran_status: 'I am not a protected veteran', disability_status: 'No, I do not have a disability',
  sms_consent: 'Yes', how_heard: 'Jobright.ai', licenses_certifications: 'None',
  other_info: "Self-rating / comfort-level questions (for example \"Rate your comfort level translating ambiguous business requirements into technical architecture\", data warehouse architecture): choose \"Comfortable\", not the highest level.\n\"Have you led or owned the design of data models / data warehouse architecture?\": Yes.\nWilling to complete a take-home assignment or interview exercise: Yes.\nDisability questions: No. Sexual orientation: Heterosexual. Gender: Man / Male. Race: Asian.\nFor any other optional question with no answer in this profile, write N/A.",
  // secrets: never sent to the AI, only typed into the page; never in git
  password: '', ssn_last4: '', dob: ''
};

export const SECRET_KEYS = ['password', 'ssn_last4', 'dob'];

export async function getSettings() {
  const { settings } = await chrome.storage.local.get('settings');
  const s = settings || {};
  // 25 was the old default page limit; multi-step sites (Workday) need more
  if (s.behavior?.stepLimit === 25) s.behavior.stepLimit = DEFAULT_SETTINGS.behavior.stepLimit;
  // the old "skip account sites" checkbox became the accounts setting; sign-up is now tried first
  if (s.behavior && !s.behavior.accounts) {
    s.behavior.accounts = 'try';
    delete s.behavior.skipAccounts;
  }
  return {
    ...DEFAULT_SETTINGS, ...s,
    filters: { ...DEFAULT_SETTINGS.filters, ...(s.filters || {}) },
    behavior: { ...DEFAULT_SETTINGS.behavior, ...(s.behavior || {}) }
  };
}

export async function saveSettings(patch) {
  const cur = await getSettings();
  const next = { ...cur, ...patch, filters: { ...cur.filters, ...(patch.filters || {}) }, behavior: { ...cur.behavior, ...(patch.behavior || {}) } };
  await chrome.storage.local.set({ settings: next });
  return next;
}

export async function getProfile() {
  const { profile } = await chrome.storage.local.get('profile');
  return { ...DEFAULT_PROFILE, ...(profile || {}) };
}

export async function saveProfile(profile) {
  const { profile: cur } = await chrome.storage.local.get('profile');
  // keep the file version so edits made on the options page are not overwritten by the same file again
  await chrome.storage.local.set({ profile: { ...DEFAULT_PROFILE, _version: cur?._version || 0, ...profile } });
}

// Loads profile.local.json (if present) the first time, so the profile need not be typed in.
// Also reloads it when the file's "_version" is newer than the saved profile's.
export async function seedProfile() {
  const { profile } = await chrome.storage.local.get('profile');
  let file = null;
  try {
    const res = await fetch(chrome.runtime.getURL('profile.local.json'));
    if (res.ok) file = await res.json();
  } catch {
    // no profile.local.json in this copy (e.g. a fresh clone): the built-in profile is used
  }
  const newer = file && (Number(file._version) || 0) > (Number(profile?._version) || 0);
  if (profile?.email && !newer) return false;
  // a new browser, or a profile saved empty: start from the built-in profile (+ the local file, if any)
  await chrome.storage.local.set({ profile: { ...DEFAULT_PROFILE, ...(file || {}), _version: file?._version || profile?._version || 0 } });
  return true;
}

function dobParts(dob) {
  const m = /^(\d{4})[-./](\d{1,2})[-./](\d{1,2})$/.exec(String(dob || '').trim());
  if (!m) return null;
  const [y, mo, d] = [m[1], m[2].padStart(2, '0'), m[3].padStart(2, '0')];
  return { iso: `${y}-${mo}-${d}`, us: `${mo}/${d}/${y}` };
}

// What the AI sees: real values, except secrets become {{placeholders}}.
export function profileForAI(p) {
  const out = {};
  for (const [k, v] of Object.entries(p)) if (!SECRET_KEYS.includes(k) && !k.startsWith('_') && v !== '') out[k] = v;
  if (p.password) out.password = '{{password}}';
  if (p.ssn_last4) out.ssn_last4 = '{{ssn_last4}} (only for a field that explicitly asks for the last 4 digits of the SSN)';
  if (dobParts(p.dob)) out.date_of_birth = '{{dob_mm_dd_yyyy}} (use {{dob_iso}} for yyyy-mm-dd date inputs; only when asked)';
  out.today = new Date().toISOString().slice(0, 10);
  return out;
}

// Real values for the placeholders, used only inside the page.
export function secretsFor(p) {
  const d = dobParts(p.dob);
  return { password: p.password, ssn_last4: p.ssn_last4, dob_mm_dd_yyyy: d?.us || '', dob_iso: d?.iso || '' };
}

// ---------- Resume Builder server (POST /api/rpc) ----------

export async function rpc(method, ...args) {
  const s = await getSettings();
  if (!s.serverUrl) throw new Error('Set the Resume Builder server URL in the extension settings.');
  let res;
  try {
    res = await fetch(`${s.serverUrl.replace(/\/+$/, '')}/api/rpc`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(s.token ? { Authorization: `Bearer ${s.token}` } : {}) },
      body: JSON.stringify({ method, args })
    });
  } catch (err) {
    throw new Error(`Cannot reach ${s.serverUrl} (${err.message}). Is the Resume Builder server running?`);
  }
  const body = await res.json().catch(() => ({ ok: false, error: `HTTP ${res.status}` }));
  if (res.status === 401 || body.code === 'AUTH') throw new Error('Not signed in to Resume Builder. Open the extension settings and sign in.');
  if (!body.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body.data;
}

export async function login(serverUrl, username, password) {
  await saveSettings({ serverUrl: serverUrl.replace(/\/+$/, ''), token: '' });
  const { token } = await rpc('auth:token', { username, password });
  await saveSettings({ token });
  return token;
}
