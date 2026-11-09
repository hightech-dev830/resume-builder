// GET /api/file?path=<date>/<id>_<Company>/<file> — opens a bid file saved in the local folder
// (when the server runs on your PC). Requires the login cookie. Elsewhere it redirects to Supabase Storage.
const fs = require('fs');
const auth = require('../lib/auth');
const storage = require('../lib/storage');

module.exports = async function handler(req, res) {
  if (!auth.isAuthed(req)) {
    res.statusCode = 401;
    return res.end('Not logged in');
  }
  const rel = new URL(req.url, 'http://x').searchParams.get('path') || '';
  try {
    const full = await storage.localFile(rel);
    if (!full) {
      res.statusCode = 302;
      res.setHeader('Location', await storage.signedUrl(rel));
      return res.end();
    }
    const name = rel.split('/').pop();
    res.setHeader('Content-Type', storage.CONTENT_TYPES[name.split('.').pop()] || 'application/octet-stream');
    res.setHeader('Content-Disposition', `inline; filename="${name.replace(/"/g, '')}"`);
    res.setHeader('Cache-Control', 'no-store');
    fs.createReadStream(full).pipe(res);
  } catch (err) {
    res.statusCode = 404;
    res.end(err.message);
  }
};
