// Single JSON-RPC endpoint: POST /api/rpc { method, args } -> { ok, data } | { ok: false, error }.
// Buffers (PDF bytes) are sent as { __b64: "…" } and turned back into bytes by public/api-web.js.
const { methods, init } = require('../lib/methods');
const auth = require('../lib/auth');

let initPromise;

function send(res, status, body, headers = {}) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json');
  res.setHeader('Cache-Control', 'no-store');
  for (const [k, v] of Object.entries(headers)) res.setHeader(k, v);
  res.end(JSON.stringify(body, (_k, v) => (v && v.type === 'Buffer' && Array.isArray(v.data) ? { __b64: Buffer.from(v.data).toString('base64') } : v)));
}

async function readBody(req) {
  if (req.body && typeof req.body === 'object') return req.body;
  if (typeof req.body === 'string') return JSON.parse(req.body || '{}');
  const chunks = [];
  for await (const c of req) chunks.push(c);
  return JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
}

module.exports = async function handler(req, res) {
  if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'POST only' });
  let body;
  try {
    body = await readBody(req);
  } catch {
    return send(res, 400, { ok: false, error: 'Invalid JSON' });
  }
  const { method, args = [] } = body;

  try {
    if (method === 'auth:login') {
      const [{ username, password } = {}] = args;
      if (!auth.checkCredentials(username, password)) return send(res, 200, { ok: false, error: 'Invalid username or password' });
      initPromise = initPromise || init();
      await initPromise.catch((e) => {
        initPromise = null;
        throw new Error(`Database connection failed: ${e.message}`);
      });
      return send(res, 200, { ok: true, data: true }, { 'Set-Cookie': auth.sessionCookie(req) });
    }
    if (method === 'auth:logout') return send(res, 200, { ok: true, data: true }, { 'Set-Cookie': auth.clearCookie() });
    if (method === 'auth:check') return send(res, 200, { ok: true, data: auth.isAuthed(req) });

    if (!auth.isAuthed(req)) return send(res, 401, { ok: false, error: 'Not logged in', code: 'AUTH' });
    const fn = methods[method];
    if (!fn) return send(res, 404, { ok: false, error: `Unknown method ${method}` });
    const data = await fn(...args);
    return send(res, 200, { ok: true, data });
  } catch (err) {
    console.error(`[${method}]`, err);
    return send(res, 200, { ok: false, error: err.message || String(err) });
  }
};
