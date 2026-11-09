// Single-user login (APP_USERNAME / APP_PASSWORD) kept in an HMAC-signed, HttpOnly cookie.
// The Chrome extension sends the same kind of signed value as "Authorization: Bearer <token>".
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

function issueToken(maxAge = MAX_AGE) {
  const payload = `${env('APP_USERNAME', 'jkr')}.${Math.floor(Date.now() / 1000) + maxAge}`;
  return `${payload}.${sign(payload)}`;
}

function sessionCookie(req) {
  const secure = req.headers['x-forwarded-proto'] === 'https' || process.env.VERCEL ? '; Secure' : '';
  return `${COOKIE}=${issueToken()}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${MAX_AGE}${secure}`;
}

function clearCookie() {
  return `${COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0`;
}

function validToken(value) {
  if (!value) return false;
  const dot = value.lastIndexOf('.');
  const payload = value.slice(0, dot);
  if (!safeEqual(value.slice(dot + 1), sign(payload))) return false;
  const exp = Number(payload.split('.').pop());
  return exp > Date.now() / 1000;
}

function isAuthed(req) {
  const bearer = /^Bearer\s+(.+)$/i.exec(String(req.headers.authorization || ''));
  if (bearer) return validToken(bearer[1].trim());
  const raw = String(req.headers.cookie || '').split(/;\s*/).find((c) => c.startsWith(COOKIE + '='));
  return raw ? validToken(raw.slice(COOKIE.length + 1)) : false;
}

module.exports = { checkCredentials, issueToken, sessionCookie, clearCookie, isAuthed };
