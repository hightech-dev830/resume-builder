// Where each bid's files go: resume PDF, cover letter PDF, resume.json, job.txt, cover_letter.txt.
// Paths look like "2026-11-03/12_Acme/Kevin_Burgess_Acme_Resume.pdf".
//  - Running on your PC (npm run dev): a local folder, like the desktop app. The folder is the
//    `local_output_dir` setting (default G:/Document/Resume_bot); set it empty to use Supabase.
//  - Running on Vercel: the Supabase Storage bucket.
// Reads fall back to Supabase Storage, so bids saved there earlier still open.
const fs = require('fs');
const path = require('path');
const { env } = require('./env');
const { sb, getSetting } = require('./db');

const bucket = () => env('SUPABASE_BUCKET', 'resumes');
const DEFAULT_LOCAL_DIR = 'G:/Document/Resume_bot';

const CONTENT_TYPES = { pdf: 'application/pdf', json: 'application/json', txt: 'text/plain; charset=utf-8' };

let dirCache = { t: 0, dir: '' };

// The local folder in use, or '' when files go to Supabase Storage.
async function localDir() {
  if (process.env.VERCEL) return '';
  if (Date.now() - dirCache.t > 10000) dirCache = { t: Date.now(), dir: String(await getSetting('local_output_dir', DEFAULT_LOCAL_DIR)).trim() };
  return dirCache.dir;
}

function resetCache() {
  dirCache = { t: 0, dir: '' };
}

// Absolute path inside the local folder; refuses anything that would step outside it.
function localPath(dir, rel) {
  const root = path.resolve(dir);
  const full = path.resolve(root, rel);
  if (full !== root && !full.startsWith(root + path.sep)) throw new Error('Invalid path: ' + rel);
  return full;
}

const fileLink = (rel) => `/api/file?path=${encodeURIComponent(rel)}`;

async function put(rel, body) {
  const dir = await localDir();
  if (dir) {
    const full = localPath(dir, rel);
    await fs.promises.mkdir(path.dirname(full), { recursive: true });
    await fs.promises.writeFile(full, body);
    return rel;
  }
  const { error } = await sb().storage.from(bucket()).upload(rel, body, {
    contentType: CONTENT_TYPES[rel.split('.').pop()] || 'application/octet-stream',
    upsert: true,
    cacheControl: '0'
  });
  if (error) throw new Error(`Storage upload failed (${rel}): ${error.message}`);
  return rel;
}

async function get(rel) {
  const dir = await localDir();
  if (dir && fs.existsSync(localPath(dir, rel))) return fs.promises.readFile(localPath(dir, rel));
  const { data, error } = await sb().storage.from(bucket()).download(rel);
  if (error) throw new Error(`Could not read ${rel}${dir ? ` (not in ${dir} or Supabase Storage)` : ''}: ${error.message}`);
  return Buffer.from(await data.arrayBuffer());
}

async function signedUrl(rel, { download = false, expiresIn = 600 } = {}) {
  const dir = await localDir();
  if (dir && fs.existsSync(localPath(dir, rel))) return fileLink(rel);
  const { data, error } = await sb().storage.from(bucket()).createSignedUrl(rel, expiresIn, download ? { download: true } : undefined);
  if (error) throw new Error(`Could not open ${rel}: ${error.message}`);
  return data.signedUrl;
}

// Lists a bid folder with a link per file.
async function listFolder(prefix) {
  const dir = await localDir();
  if (dir && fs.existsSync(localPath(dir, prefix))) {
    const full = localPath(dir, prefix);
    const names = (await fs.promises.readdir(full, { withFileTypes: true })).filter((d) => d.isFile()).map((d) => d.name).sort();
    return Promise.all(names.map(async (name) => ({ name, size: (await fs.promises.stat(path.join(full, name))).size, url: fileLink(`${prefix}/${name}`) })));
  }
  const { data, error } = await sb().storage.from(bucket()).list(prefix, { limit: 100, sortBy: { column: 'name', order: 'asc' } });
  if (error) throw new Error(`Could not list ${prefix}: ${error.message}`);
  const files = data.filter((f) => f.id); // folders have no id
  return Promise.all(files.map(async (f) => ({
    name: f.name,
    size: f.metadata?.size || 0,
    url: await signedUrl(`${prefix}/${f.name}`)
  })));
}

async function removeFolder(prefix) {
  let count = 0;
  const dir = await localDir();
  if (dir && fs.existsSync(localPath(dir, prefix))) {
    count += (await fs.promises.readdir(localPath(dir, prefix))).length;
    await fs.promises.rm(localPath(dir, prefix), { recursive: true, force: true });
  }
  const { data, error } = await sb().storage.from(bucket()).list(prefix, { limit: 1000 });
  if (error && !count) throw new Error(`Could not list ${prefix}: ${error.message}`);
  const paths = (data || []).filter((f) => f.id).map((f) => `${prefix}/${f.name}`);
  if (paths.length) {
    const { error: e2 } = await sb().storage.from(bucket()).remove(paths);
    if (e2) throw new Error(`Could not delete ${prefix}: ${e2.message}`);
  }
  return count + paths.length;
}

async function exists(rel) {
  const dir = await localDir();
  if (dir && fs.existsSync(localPath(dir, rel))) return true;
  const slash = rel.lastIndexOf('/');
  const { data } = await sb().storage.from(bucket()).list(rel.slice(0, slash), { search: rel.slice(slash + 1) });
  return !!data?.some((f) => f.name === rel.slice(slash + 1));
}

// Absolute local path of a stored file (for the /api/file endpoint), or null.
async function localFile(rel) {
  const dir = await localDir();
  if (!dir) return null;
  const full = localPath(dir, rel);
  return fs.existsSync(full) ? full : null;
}

module.exports = { put, get, signedUrl, listFolder, removeFolder, exists, bucket, localDir, localFile, resetCache, DEFAULT_LOCAL_DIR, CONTENT_TYPES };
