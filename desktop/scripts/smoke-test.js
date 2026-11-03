// End-to-end UI check without AI: electron scripts/smoke-test.js [screenshotDir]
// Logs in, exercises every tab, builds bids from the base profile, exports a PDF,
// then deletes the test bids, folders and test dropdown values again.
const fs = require('fs');
const path = require('path');
const { app, BrowserWindow } = require('electron');
require('../main.js');

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const shotDir = process.argv[2];

app.whenReady().then(async () => {
  await wait(1500);
  const win = BrowserWindow.getAllWindows()[0];
  win.webContents.setBackgroundThrottling(false);
  const js =(code) => win.webContents.executeJavaScript(code);
  let failures = 0;
  const check = (label, ok) => {
    if (!ok) failures++;
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}`);
  };
  const shot = async (name) => {
    if (!shotDir) return;
    fs.mkdirSync(shotDir, { recursive: true });
    win.show();
    win.focus();
    win.webContents.invalidate();
    await wait(600);
    fs.writeFileSync(path.join(shotDir, name + '.png'), (await win.webContents.capturePage()).toPNG());
  };
  const tab = async (name) => {
    await js(`document.querySelector('.tab[data-tab="${name}"]').click()`);
    await wait(1000);
  };

  await js(`document.querySelector('#username').value='jkr';document.querySelector('#password').value='jkr';
            document.querySelector('#loginForm').requestSubmit();`);
  await wait(3000);
  check('login opens app', (await js('location.pathname.endsWith("app.html")')) === true);

  for (const t of ['cover', 'history', 'base', 'settings', 'builder']) {
    await tab(t);
    check(`tab ${t}`, (await js(`document.querySelector('.view.active')?.id`)) === `view-${t}`);
  }

  // ---- dropdowns ----
  for (const k of ['role', 'location', 'salary']) {
    check(`${k} dropdown has options`, (await js(`fieldOptions.${k}.length`)) > 5);
  }
  // The test window has no OS focus, so focus events don't fire; open the list directly.
  await js(`combos.role.open(true)`);
  await wait(300);
  check('role dropdown opens', (await js(`document.querySelectorAll('.combo-list:not(.hidden) .combo-item').length`)) > 5);
  await shot('1-dropdown-open');
  await js(`document.querySelector('#f-role').value = 'Smoke Test Role'; combos.role.open(false);`);
  await wait(300);
  const addShown = await js(`(() => { const l = document.querySelector('#f-role').closest('.combo').querySelector('.combo-list');
     return [...l.querySelectorAll('.combo-item.add')].map(x => x.textContent).join('|'); })()`);
  check(`"+ Add" item offered (${addShown})`, addShown.includes('Smoke Test Role'));
  await js(`(() => { const add = document.querySelector('#f-role').closest('.combo').querySelector('.combo-item.add');
     add.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })); })()`);
  await wait(1200);
  check('new role saved to list', (await js(`fieldOptions.role.some(o => o.value === 'Smoke Test Role' && o.custom)`)) === true);
  check('new role selected in field', (await js(`document.querySelector('#f-role').value`)) === 'Smoke Test Role');
  await js(`api.removeOption('role', 'Smoke Test Role').then(loadFieldOptions)`);
  await wait(500);
  check('custom role removable', (await js(`fieldOptions.role.some(o => o.value === 'Smoke Test Role')`)) === false);
  await js(`document.querySelector('#f-role').blur()`);

  // ---- build + export a bid ----
  await js(`for (const [k, v] of Object.entries({company: 'Smoke Test Co', role: 'Senior Backend Engineer',
              location: 'Remote (US)', salary: '$170k–$200k', jd: 'Test JD'})) {
              const el = document.querySelector('#f-' + k); el.value = v; el.dispatchEvent(new Event('input')); }
            document.querySelector('#useBaseBtn').click();`);
  await wait(1500);
  check('preview rendered', (await js(`document.querySelector('#previewFrame').contentDocument.body.innerText.includes('Kevin Burgess')`)) === true);
  await js(`document.querySelector('#exportBtn').click()`);
  await wait(4000);
  const id = await js('state.id');
  const pdf = await js('state.row && state.row.pdf_path');
  const dirs = [await js('state.row.resume_dir')];
  check(`bid saved (#${id})`, !!id);
  check('PDF written', !!pdf && fs.existsSync(pdf));
  check('result bar shows saved PDF', (await js(`!document.querySelector('#resultBar').classList.contains('hidden')`)) === true);
  check('Open folder sits next to Generate', (await js(`!!document.querySelector('.action-foot #openFolderBtn') && !!document.querySelector('.action-foot #generateBtn')`)) === true);
  await shot('0-builder-saved');
  await js(`document.querySelector('#previewMode button[data-mode="pdf"]').click()`);
  await wait(2500);
  check('exact PDF preview', (await js(`document.querySelector('#pdfFrame').src.startsWith('blob:')`)) === true);
  await js(`document.querySelector('#previewMode button[data-mode="fast"]').click()`);

  // two more bids (no export) to exercise paging
  const extra = [];
  for (const n of [2, 3]) {
    extra.push((await js(`api.createResume({ company: 'Smoke Test Co ${n}', role: 'Engineer', job_link: 'https://example.com/jobs/${n}', status: 'draft' })`)).id);
  }

  // ---- history: date filter + paging ----
  await tab('history');
  const dateOpts = await js(`[...document.querySelectorAll('#h-date option')].map(o => o.value)`);
  check('date filter has presets', ['today', 'last7', 'thisMonth', 'custom'].every((v) => dateOpts.includes(v)));
  check('date filter lists days', dateOpts.some((v) => v.startsWith('day:')));
  check('history groups rows by day', (await js(`document.querySelectorAll('#historyBody tr.group-row').length`)) >= 1);
  check('copy job link button', (await js(`!!document.querySelector('#historyBody [data-copy="https://example.com/jobs/2"]')`)) === true);
  await js(`document.querySelector('#h-date').value = 'today'; document.querySelector('#h-date').dispatchEvent(new Event('change'))`);
  await wait(800);
  check('"Today" shows test bids', (await js(`!!document.querySelector('#historyBody tr[data-id="${id}"]')`)) === true);
  await js(`document.querySelector('#h-date').value = 'lastMonth'; document.querySelector('#h-date').dispatchEvent(new Event('change'))`);
  await wait(800);
  check('"Last month" hides them', (await js(`!!document.querySelector('#historyBody tr[data-id="${id}"]')`)) === false);
  await js(`document.querySelector('#h-date').value = ''; document.querySelector('#h-date').dispatchEvent(new Event('change'))`);
  await wait(800);
  check('page size options', (await js(`[...document.querySelectorAll('#h-size option')].map(o => o.textContent).join(',')`)) === '50,100,150,200,300,500,All');
  // shrink page size to 2 to verify paging math
  await js(`hist.size = 2; hist.page = 1; loadHistory()`);
  await wait(800);
  const p1 = await js(`document.querySelector('#h-page').textContent`);
  await js(`document.querySelector('#h-next').click()`);
  await wait(800);
  const p2 = await js(`document.querySelector('#h-page').textContent`);
  check(`paging works (${p1} -> ${p2})`, p1.startsWith('Page 1 of') && p2.startsWith('Page 2 of'));
  await js(`document.querySelector('#h-size').value = '50'; document.querySelector('#h-size').dispatchEvent(new Event('change'))`);
  await wait(800);
  await shot('2-history');

  // ---- base profile editor ----
  await tab('base');
  await wait(800);
  check('base form rendered', (await js(`document.querySelectorAll('#baseForm .pe-card').length`)) >= 6);
  check('save disabled when unchanged', (await js(`document.querySelector('#baseSaveBtn').disabled`)) === true);
  await js(`const i = document.querySelector('#baseForm [data-path="basics.label"]'); i.value = 'Staff Engineer (test)'; i.dispatchEvent(new Event('input', { bubbles: true }));`);
  await wait(300);
  check('save enabled after edit', (await js(`document.querySelector('#baseSaveBtn').disabled`)) === false);
  check('tab shows unsaved dot', (await js(`document.querySelector('.tab[data-tab="base"]').classList.contains('has-changes')`)) === true);
  await wait(800);
  await shot('3-base-profile');
  await js(`window.confirm = () => true; document.querySelector('#baseDiscardBtn').click()`);
  await wait(500);
  check('discard restores saved value', (await js(`document.querySelector('#baseForm [data-path="basics.label"]').value`)) === 'Senior Software Engineer');
  check('save disabled after discard', (await js(`document.querySelector('#baseSaveBtn').disabled`)) === true);

  // ---- settings: provider, models, prompts ----
  await tab('settings');
  await wait(800);
  check('resume prompt loaded', (await js(`document.querySelector('#s-promptResume').value.length`)) > 500);
  check('cover prompt loaded', (await js(`document.querySelector('#s-promptCover').value.length`)) > 200);
  check('settings save disabled when unchanged', (await js(`document.querySelector('#s-save').disabled`)) === true);
  // switch to the provider that is NOT saved, then back
  const savedProvider = await js(`sf.provider`);
  const other = savedProvider === 'openai' ? 'anthropic' : 'openai';
  await js(`document.querySelector('#s-provider [data-provider="${other}"]').click()`);
  await wait(300);
  check(`${other} provider selectable`, (await js(`document.querySelector('#s-keyLabel').textContent`)) === (other === 'openai' ? 'OpenAI API key' : 'Claude API key'));
  check('settings save enabled after change', (await js(`document.querySelector('#s-save').disabled`)) === false);
  await js(`document.querySelector('#s-provider [data-provider="${savedProvider}"]').click()`);
  await wait(300);
  check('switching back restores saved state', (await js(`document.querySelector('#s-save').disabled`)) === true);
  check('proxy split into fields', (await js(`document.querySelector('#s-proxyHost').value !== '' && /^\\d+$/.test(document.querySelector('#s-proxyPort').value)`)) === true);
  check('proxy rebuilds to saved value', (await js(`proxyFromFields() === state.settings.claude_proxy`)) === true);
  await js(`document.querySelector('#s-proxyPort').value = '1'; document.querySelector('#s-proxyPort').dispatchEvent(new Event('input'))`);
  check('proxy edit enables save', (await js(`document.querySelector('#s-save').disabled`)) === false);
  await js(`document.querySelector('#s-proxyOn').click()`);
  check('proxy switch off -> no proxy used', (await js(`activeProxy()`)) === '');
  await js(`loadSettings()`);
  await wait(800);
  check('reload restores saved proxy', (await js(`document.querySelector('#s-save').disabled && document.querySelector('#s-proxyOn').checked`)) === true);
  await wait(2500); // model list loads in the background
  check('model dropdown filled', (await js(`document.querySelectorAll('#s-model option').length`)) >= 1);
  await shot('4-settings');

  // ---- clean up ----
  for (const x of [id, ...extra]) await js(`api.deleteResume(${x})`);
  for (const d of dirs) if (d && d.includes('Smoke_Test_Co')) fs.rmSync(d, { recursive: true, force: true });
  console.log(`cleaned up test bids ${[id, ...extra].join(', ')}`);
  console.log(failures ? `${failures} check(s) FAILED` : 'ALL CHECKS PASSED');
  app.exit(failures ? 1 : 0);
});
