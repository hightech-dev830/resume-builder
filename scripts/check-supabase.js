// Checks the Supabase setup from env: npm run check
const { env } = require('../lib/env');

const url = env('SUPABASE_URL');
const secret = env('SUPABASE_SECRET_KEY') || env('SUPABASE_SERVICE_ROLE_KEY');
const publishable = env('SUPABASE_PUBLISHABLE_KEY');
let problems = 0;

function line(ok, text) {
  if (!ok) problems++;
  console.log(`${ok ? '✓' : '✗'} ${text}`);
}

async function rest(key, pathname) {
  const res = await fetch(`${url}${pathname}`, { headers: { apikey: key, Authorization: `Bearer ${key}` } });
  return { status: res.status, body: await res.json().catch(() => null) };
}

(async () => {
  line(!!url, `SUPABASE_URL ${url || 'is missing'}`);
  if (!url) return done();

  if (publishable) {
    const r = await rest(publishable, '/rest/v1/');
    line(r.status < 500, `Project reachable with the publishable key (HTTP ${r.status})`);
  }

  line(!!secret, `SUPABASE_SECRET_KEY ${secret ? 'is set' : 'is missing — the server needs it (Project Settings → API keys → Secret keys)'}`);
  line(!!env('SESSION_SECRET'), `SESSION_SECRET ${env('SESSION_SECRET') ? 'is set' : 'is missing — set any long random string'}`);
  if (!secret) return done();

  for (const table of ['settings', 'resumes']) {
    const r = await rest(secret, `/rest/v1/${table}?select=*&limit=1`);
    line(r.status === 200, `Table ${table}: ${r.status === 200 ? 'OK' : r.body?.message || `HTTP ${r.status}`}`);
  }
  const cols = await rest(secret, '/rest/v1/resumes?select=ai_provider,ai_model,prompt_used&limit=1');
  line(cols.status === 200, `resumes has the AI columns${cols.status === 200 ? '' : ': ' + (cols.body?.message || cols.status)}`);

  const bucket = env('SUPABASE_BUCKET', 'resumes');
  const b = await rest(secret, `/storage/v1/bucket/${bucket}`);
  line(b.status === 200, `Storage bucket "${bucket}": ${b.status === 200 ? (b.body?.public ? 'exists (WARNING: public)' : 'exists, private') : b.body?.message || `HTTP ${b.status}`}`);

  if (publishable) {
    const leak = await rest(publishable, '/rest/v1/settings?select=key&limit=1');
    line(!(leak.status === 200 && leak.body?.length), 'Publishable key cannot read settings (row level security is on)');
  }
  done();
})().catch((e) => {
  console.error('✗', e.message);
  process.exit(1);
});

function done() {
  if (problems) console.log(`\n${problems} problem(s). If tables are missing, run web/supabase/schema.sql in the Supabase SQL editor.`);
  else console.log('\nSupabase is ready.');
  process.exitCode = problems ? 1 : 0;
}
