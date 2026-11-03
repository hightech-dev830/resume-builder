// Supabase Storage stands in for the desktop G:/Document/Resume folders.
// Paths look like "2026-11-03/12_Acme/Kevin_Burgess_Acme_Resume.pdf".
const { env } = require('./env');
const { sb } = require('./db');

const bucket = () => env('SUPABASE_BUCKET', 'resumes');

const CONTENT_TYPES = { pdf: 'application/pdf', json: 'application/json', txt: 'text/plain; charset=utf-8' };

async function put(path, body) {
  const ext = path.split('.').pop();
  const { error } = await sb().storage.from(bucket()).upload(path, body, {
    contentType: CONTENT_TYPES[ext] || 'application/octet-stream',
    upsert: true,
    cacheControl: '0'
  });
  if (error) throw new Error(`Storage upload failed (${path}): ${error.message}`);
  return path;
}

async function signedUrl(path, { download = false, expiresIn = 600 } = {}) {
  const { data, error } = await sb().storage.from(bucket()).createSignedUrl(path, expiresIn, download ? { download: true } : undefined);
  if (error) throw new Error(`Could not open ${path}: ${error.message}`);
  return data.signedUrl;
}

// Lists a "folder" (prefix) with a short-lived link per file.
async function listFolder(prefix) {
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
  const { data, error } = await sb().storage.from(bucket()).list(prefix, { limit: 1000 });
  if (error) throw new Error(`Could not list ${prefix}: ${error.message}`);
  const paths = data.filter((f) => f.id).map((f) => `${prefix}/${f.name}`);
  if (paths.length) {
    const { error: e2 } = await sb().storage.from(bucket()).remove(paths);
    if (e2) throw new Error(`Could not delete ${prefix}: ${e2.message}`);
  }
  return paths.length;
}

async function exists(path) {
  const slash = path.lastIndexOf('/');
  const { data } = await sb().storage.from(bucket()).list(path.slice(0, slash), { search: path.slice(slash + 1) });
  return !!data?.some((f) => f.name === path.slice(slash + 1));
}

module.exports = { put, signedUrl, listFolder, removeFolder, exists, bucket };
