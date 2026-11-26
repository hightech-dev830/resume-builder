// Supabase over REST (no SDK, so no build step and no remote code in the extension):
//  - Auth: e-mail + password sign-in and token refresh (/auth/v1)
//  - Data: tables bid_profiles and bid_applications (/rest/v1), see supabase/migrations/004_extension_v2.sql
// Requests use the project's publishable key plus the signed-in user's token, so row level security
// limits every user to their own rows. The login name "jkrjkr" is the Auth user "jkrjkr@autobid.app"
// (anything with an @ is used as is).

export const configured = (sb) => !!(sb && sb.enabled && sb.url && sb.key);
export const loginEmail = (username) => (String(username).includes('@') ? String(username).trim() : `${String(username).trim()}@autobid.app`);
const base = (sb) => String(sb.url).replace(/\/+$/, '');
const TABLES = { profiles: 'bid_profiles', applications: 'bid_applications' };

async function call(sb, path, { method = 'GET', token, body, headers = {} } = {}) {
  let res;
  try {
    res = await fetch(base(sb) + path, {
      method,
      headers: { apikey: sb.key, Authorization: `Bearer ${token || sb.key}`, 'Content-Type': 'application/json', ...headers },
      body: body === undefined ? undefined : JSON.stringify(body)
    });
  } catch (err) {
    throw new Error(`Could not reach Supabase (${base(sb)}): ${err.message}`);
  }
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const msg = data?.msg || data?.message || data?.error_description || data?.error || `HTTP ${res.status}`;
    const code = data?.error_code || data?.code || '';
    throw Object.assign(new Error(friendly(code, msg, res.status)), { code, status: res.status });
  }
  return data;
}

function friendly(code, msg, status) {
  if (code === 'invalid_credentials' || /invalid login credentials/i.test(msg)) {
    return 'Invalid username or password. (First time with Supabase? Create the user in Supabase → Authentication → Users → Add user, with "Auto Confirm User".)';
  }
  if (code === 'email_not_confirmed') return 'This Supabase user is not confirmed: confirm it in Supabase → Authentication → Users.';
  if (code === 'PGRST205' || /could not find the table/i.test(msg)) return 'The extension tables are missing: run supabase/migrations/004_extension_v2.sql in the Supabase SQL Editor.';
  if (status === 401 && /api key|apikey/i.test(msg)) return 'The Supabase publishable key is not valid.';
  if (code === '42501' || /row-level security/i.test(msg)) return 'Supabase refused the write (row level security): run supabase/migrations/004_extension_v2.sql again.';
  return 'Supabase: ' + msg;
}

const session = (d) => ({
  uid: d.user?.id,
  email: d.user?.email || '',
  accessToken: d.access_token,
  refreshToken: d.refresh_token,
  expiresAt: Date.now() + (Number(d.expires_in || 3600) - 60) * 1000
});

export async function signIn(sb, username, password) {
  return session(await call(sb, '/auth/v1/token?grant_type=password', { method: 'POST', body: { email: loginEmail(username), password } }));
}

export async function refresh(sb, s) {
  return session(await call(sb, '/auth/v1/token?grant_type=refresh_token', { method: 'POST', body: { refresh_token: s.refreshToken } }));
}

// ---------- rows <-> extension documents ----------

const iso = (t) => (t ? new Date(t).toISOString() : null);
const num = (v) => (Number.isFinite(Number(v)) && v !== '' && v != null ? Math.round(Number(v)) : null);

function toRow(col, uid, doc) {
  const row = { user_id: uid, id: doc.id, data: doc, created_at: iso(doc.createdAt) || iso(Date.now()), updated_at: iso(doc.updatedAt) || iso(Date.now()) };
  if (col === 'profiles') return { ...row, name: doc.name || '' };
  const { jd = '', ...rest } = doc;
  return {
    ...row,
    data: rest,
    profile_id: doc.profileId || '', profile_name: doc.profileName || '', company: doc.company || '', title: doc.title || '',
    url: doc.url || '', apply_url: doc.applyUrl || '', status: doc.status || 'draft', source: doc.source || '', platform: doc.platform || '',
    ats_score: num(doc.atsScore), risk: num(doc.risk), jd, applied_at: iso(doc.appliedAt)
  };
}

function fromRow(col, row) {
  const doc = { ...(row.data || {}), id: row.id };
  if (col === 'applications') {
    doc.jd = row.jd || doc.jd || '';
    doc.status = row.status || doc.status;
  }
  return doc;
}

export async function listDocs(sb, token, uid, col) {
  const out = [];
  for (let from = 0; ; from += 1000) {
    const rows = await call(sb, `/rest/v1/${TABLES[col]}?select=*&order=updated_at.desc&offset=${from}&limit=1000`, { token });
    out.push(...rows.map((r) => fromRow(col, r)));
    if (rows.length < 1000) return out;
  }
}

export async function setDoc(sb, token, uid, col, id, doc) {
  await call(sb, `/rest/v1/${TABLES[col]}?on_conflict=user_id,id`, {
    method: 'POST', token, body: toRow(col, uid, { ...doc, id }),
    headers: { Prefer: 'resolution=merge-duplicates,return=minimal' }
  });
}

export async function deleteDoc(sb, token, uid, col, id) {
  await call(sb, `/rest/v1/${TABLES[col]}?id=eq.${encodeURIComponent(id)}`, { method: 'DELETE', token, headers: { Prefer: 'return=minimal' } });
}
