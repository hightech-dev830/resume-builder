// Single-user login (APP_USERNAME / APP_PASSWORD) kept in an HMAC-signed, HttpOnly cookie.
const crypto = require('crypto');
const { env, requireEnv } = require('./env');

const COOKIE = 'rb_session';
const MAX_AGE = 60 * 60 * 24 * 30; // 30 days

const sign = (payload) => crypto.createHmac('sha256', requireEnv('SESSION_SECRET')).update(payload).digest('base64url');

function safeEqual(a, b) {
  const x = Buffer.from(String(a));
  const y = Buffer.from(String(b));
  return x.length === y.length && crypto.timingSafeEqual(x, y);
}

function checkCredentials(username, password) {
  return safeEqual(username, env('APP_USERNAME', 'jkr')) && safeEqual(password, env('APP_PASSWORD', 'jkr'));
}

function sessionCookie(req) {
  const payload = `${env('APP_USERNAME', 'jkr')}.${Math.floor(Date.now() / 1000) + MAX_AGE}`;
  const secure = req.headers['x-forwarded-proto'] === 'https' || process.env.VERCEL ? '; Secure' : '';
  return `${COOKIE}=${payload}.${sign(payload)}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${MAX_AGE}${secure}`;
}

function clearCookie() {
  return `${COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0`;
}

function isAuthed(req) {
  const raw = String(req.headers.cookie || '').split(/;\s*/).find((c) => c.startsWith(COOKIE + '='));
  if (!raw) return false;
  const value = raw.slice(COOKIE.length + 1);
  const dot = value.lastIndexOf('.');
  const payload = value.slice(0, dot);
  if (!safeEqual(value.slice(dot + 1), sign(payload))) return false;
  const exp = Number(payload.split('.').pop());
  return exp > Date.now() / 1000;
}

module.exports = { checkCredentials, sessionCookie, clearCookie, isAuthed };
