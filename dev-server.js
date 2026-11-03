// Local stand-in for Vercel: serves public/ and routes POST /api/rpc to api/rpc.js.
//   npm run dev   ->  http://localhost:3000
const http = require('http');
const fs = require('fs');
const path = require('path');
const { loadEnv } = require('./lib/env');

loadEnv();
const rpc = require('./api/rpc');

const PUBLIC = path.join(__dirname, 'public');
const PORT = Number(process.env.PORT) || 3000;
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.png': 'image/png', '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.woff2': 'font/woff2' };

http
  .createServer(async (req, res) => {
    const url = new URL(req.url, `http://${req.headers.host}`);
    if (url.pathname === '/api/rpc') return rpc(req, res);
    const rel = url.pathname === '/' ? '/login.html' : decodeURIComponent(url.pathname);
    const file = path.join(PUBLIC, path.normalize(rel));
    if (!file.startsWith(PUBLIC) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.statusCode = 404;
      return res.end('Not found');
    }
    res.setHeader('Content-Type', TYPES[path.extname(file)] || 'application/octet-stream');
    fs.createReadStream(file).pipe(res);
  })
  .listen(PORT, () => console.log(`Resume Builder web running at http://localhost:${PORT}`));
