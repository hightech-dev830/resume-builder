// Loads web/.env.local (then .env) into process.env for local dev. On Vercel the
// variables come from the project settings and these files don't exist.
const fs = require('fs');
const path = require('path');

let loaded = false;

function loadEnv() {
  if (loaded) return;
  loaded = true;
  for (const name of ['.env.local', '.env']) {
    const file = path.join(__dirname, '..', name);
    if (!fs.existsSync(file)) continue;
    for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
      const m = /^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/.exec(line);
      if (!m || line.trim().startsWith('#')) continue;
      let v = m[2];
      if (/^(["']).*\1$/.test(v)) v = v.slice(1, -1);
      // an empty value (e.g. "KEY=" copied from .env.example) must not hide a real one in the next file
      if (v !== '' && !process.env[m[1]]) process.env[m[1]] = v;
    }
  }
}

function env(name, fallback = '') {
  loadEnv();
  const v = process.env[name];
  return v === undefined || v === '' ? fallback : v;
}

function requireEnv(name) {
  const v = env(name);
  if (!v) throw new Error(`Missing environment variable ${name}. Set it in web/.env.local (local) or Vercel project settings.`);
  return v;
}

module.exports = { env, requireEnv, loadEnv };
