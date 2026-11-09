// Extension settings, profile and server client. Shared by the service worker, popup and options page.

export const DEFAULT_SETTINGS = {
  serverUrl: 'http://localhost:3000', // Resume Builder web app (local `npm run dev` or the Vercel URL)
  token: '',
  listUrl: 'https://jobright.ai/jobs/recommend',
  filters: {
    remoteOnly: true,
    maxAgeHours: 24,       // "today's jobs": posted within the last 24 hours
    maxBids: 25,           // stop after this many submitted applications per run
    minMatch: 0,           // skip jobs below this Jobright match %
    titleExclude: '',      // comma-separated words; skip titles containing any
    companyExclude: ''     // comma-separated company names to skip
  },
  behavior: {
    autoSubmit: true,      // false = stop on the final review/submit step for you to check
    template: '',          // '' = the web app's default template
    uploadCover: true,
    delaySec: 8,           // pause between bids
    stepLimit: 40,         // max pages per application (Workday-style wizards need ~15)
    captchaWaitMin: 3,     // how long to wait for you to solve a captcha
    closeTabs: true        // close the job tabs after a successful bid
  }
};

// Personal details the bot types into forms. Filled from profile.local.json on install
// (kept out of git) and editable on the options page.
export const DEFAULT_PROFILE = {
  first_name: '', last_name: '', full_name: '', preferred_name: '',
  email: '', phone: '', phone_country_code: '+1',
  address_line1: '', city: '', state: '', state_full: '', zip: '', county: '', country: 'United States',
  current_location: '',
  linkedin: '', github: '', website: '',
  current_title: '', current_company: '', years_experience: '',
  highest_degree: '', degree_field: '', school: '', graduation_year: '',
  work_authorized_us: 'Yes', needs_sponsorship: 'No', citizenship: '',
  willing_to_relocate: 'No', remote_preference: 'Remote',
  start_date: '', notice_period: '', salary_expectation: '',
  gender: '', pronouns: '', race_ethnicity: '', hispanic_latino: '', veteran_status: '', disability_status: '',
  sms_consent: 'Yes', how_heard: 'Jobright.ai', licenses_certifications: 'None',
  other_info: '',
  // secrets: never sent to the AI, only typed into the page
  password: '', ssn_last4: '', dob: ''
};

export const SECRET_KEYS = ['password', 'ssn_last4', 'dob'];

export async function getSettings() {
  const { settings } = await chrome.storage.local.get('settings');
  const s = settings || {};
  // 25 was the old default page limit; multi-step sites (Workday) need more
  if (s.behavior?.stepLimit === 25) s.behavior.stepLimit = DEFAULT_SETTINGS.behavior.stepLimit;
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
  await chrome.storage.local.set({ profile: { ...DEFAULT_PROFILE, ...profile } });
}

// Loads profile.local.json (if present) the first time, so the profile need not be typed in.
export async function seedProfile() {
  const { profile } = await chrome.storage.local.get('profile');
  if (profile && profile.email) return false;
  try {
    const res = await fetch(chrome.runtime.getURL('profile.local.json'));
    if (!res.ok) return false;
    await saveProfile(await res.json());
    return true;
  } catch {
    return false;
  }
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
  for (const [k, v] of Object.entries(p)) if (!SECRET_KEYS.includes(k) && v !== '') out[k] = v;
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
