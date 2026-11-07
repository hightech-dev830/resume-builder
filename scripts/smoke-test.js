// Browser check of the web app against a running server (no AI calls):
//   npm run dev            (in another terminal)
//   node scripts/smoke-test.js [baseUrl] [screenshotDir]
// Builds a test bid from the base profile, saves its PDF to Storage, checks history and
// settings, then deletes the test bid again.
const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer-core');
const { env } = require('../lib/env');

const BASE = process.argv[2] || 'http://localhost:3000';
const SHOTS = process.argv[3];
const BROWSERS = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe'
];
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await puppeteer.launch({
    executablePath: env('CHROME_PATH') || BROWSERS.find((p) => fs.existsSync(p)),
    headless: true,
    args: ['--no-proxy-server'],
    defaultViewport: { width: 1480, height: 900 }
  });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  let failures = 0;
  const check = (label, ok) => {
    if (!ok) failures++;
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}`);
  };
  const js = (code) => page.evaluate(code);
  const shot = async (name) => SHOTS && (fs.mkdirSync(SHOTS, { recursive: true }), page.screenshot({ path: path.join(SHOTS, name + '.png') }));
  const tab = async (name) => {
    await page.click(`.tab[data-tab="${name}"]`);
    await wait(1200);
  };
  const created = [];

  try {
    await page.goto(BASE + '/', { waitUntil: 'load' });
    check('login page loads', !!(await page.$('#loginForm')));
    await page.type('#username', 'jkr');
    await page.type('#password', 'jkr');
    await Promise.all([page.waitForNavigation({ timeout: 30000 }).catch(() => {}), page.click('#loginBtn')]);
    await wait(2500);
    check('login opens app', page.url().endsWith('/app.html'));
    if (!page.url().endsWith('/app.html')) console.log('   login error:', await js(`document.querySelector('#loginError')?.textContent`));
    check('web mode on', await js(`document.body.classList.contains('web')`));

    for (const t of ['cover', 'history', 'base', 'settings', 'builder']) {
      await tab(t);
      check(`tab ${t}`, (await js(`document.querySelector('.view.active')?.id`)) === `view-${t}`);
    }
    check('dropdown options loaded', (await js(`fieldOptions.role.length`)) > 5);

    await js(`for (const [k, v] of Object.entries({ company: 'Web Smoke Co', role: 'Senior Backend Engineer', location: 'Remote (US)',
      job_link: 'https://example.com/jobs/web', jd: 'Test JD' })) { const el = document.querySelector('#f-' + k); el.value = v; el.dispatchEvent(new Event('input')); }`);
    await page.click('#useBaseBtn');
    await page.waitForFunction(`document.querySelector('#previewFrame').contentDocument?.body?.innerText.includes('Kevin Burgess')`, { timeout: 20000 }).catch(() => {});
    await wait(500);
    check('preview rendered with web fonts', await js(`(() => { const d = document.querySelector('#previewFrame').contentDocument;
      return d.body.innerText.includes('Kevin Burgess') && !!d.querySelector('link[href="/fonts/fonts.css"]'); })()`));

    await page.click('#exportBtn');
    await page.waitForFunction(`state.row && state.row.pdf_path`, { timeout: 60000 }).catch(() => {});
    const row = await js(`state.row`);
    if (row?.id) created.push(row.id);
    check(`bid saved (#${row?.id})`, !!row?.id);
    check(`PDF stored: ${row?.pdf_path}`, /^\d{4}-\d{2}-\d{2}\/\d+_Web_Smoke_Co\/.+_Resume\.pdf$/.test(row?.pdf_path || ''));
    const pdf = await js(`api.openPath ? fetch('/api/rpc', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ method: 'files:url', args: [state.row.pdf_path] }) }).then(r => r.json()) : null`);
    // download the signed URL from Node: the page's CSP only allows connections to itself
    const pdfHead = pdf?.ok ? Buffer.from(await (await fetch(pdf.data)).arrayBuffer()).subarray(0, 5).toString() : '';
    check('stored PDF downloads', pdfHead === '%PDF-');
    check('result bar shown', await js(`!document.querySelector('#resultBar').classList.contains('hidden')`));
    await shot('web-builder');

    await page.click('#previewMode button[data-mode="pdf"]');
    await wait(3000);
    check('exact PDF preview', await js(`document.querySelector('#pdfFrame').src.startsWith('blob:')`));
    await page.click('#previewMode button[data-mode="fast"]');

    const folder = await js(`fetch('/api/rpc', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ method: 'files:list', args: [state.row.resume_dir] }) }).then(r => r.json())`);
    check(`folder lists files (${(folder.data || []).map((f) => f.name).join(', ')})`,
      folder.ok && ['resume.json', 'job.txt'].every((n) => folder.data.some((f) => f.name === n)));

    await tab('history');
    await page.waitForSelector(`#historyBody tr[data-id="${row?.id}"]`, { timeout: 20000 }).catch(() => {});
    check('history lists bid', await js(`!!document.querySelector('#historyBody tr[data-id="${row?.id}"]')`));
    check('copy link button', await js(`!!document.querySelector('#historyBody [data-copy="https://example.com/jobs/web"]')`));
    check('date filter has days', await js(`[...document.querySelectorAll('#h-date option')].some(o => o.value.startsWith('day:'))`));
    await shot('web-history');

    await tab('base');
    check('base form rendered', (await js(`document.querySelectorAll('#baseForm .pe-card').length`)) >= 6);
    check('base save disabled when unchanged', await js(`document.querySelector('#baseSaveBtn').disabled`));

    await tab('settings');
    await wait(1000);
    check('prompts loaded', (await js(`document.querySelector('#s-promptResume').value.length`)) > 500);
    check('output folder hidden on web', await js(`getComputedStyle(document.querySelector('#s-out').closest('.desktop-only')).display === 'none'`));
    check('db label shows Supabase', (await js(`document.querySelector('#s-db').textContent`)).includes('Supabase'));
    await page.click('#s-provider [data-provider="openai"]');
    check('OpenAI selectable', (await js(`document.querySelector('#s-keyLabel').textContent`)) === 'OpenAI API key');
    await page.click('#s-provider [data-provider="anthropic"]');
    await shot('web-settings');

    check(`no page errors${errors.length ? ': ' + errors.join(' | ') : ''}`, errors.length === 0);
  } catch (err) {
    failures++;
    console.log('FAIL  ' + err.message);
  } finally {
    for (const id of created) {
      await js(`(async () => { const r = await api.getResume(${id}); if (r?.resume_dir) await fetch('/api/rpc', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ method: 'files:removeFolder', args: [r.resume_dir] }) }); await api.deleteResume(${id}); })()`).catch(() => {});
    }
    if (created.length) console.log(`cleaned up test bid(s) ${created.join(', ')} and their stored files`);
    await browser.close();
    console.log(failures ? `${failures} check(s) FAILED` : 'ALL CHECKS PASSED');
    process.exit(failures ? 1 : 0);
  }
})();
