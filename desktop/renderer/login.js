const $ = (s) => document.querySelector(s);

$('#loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  $('#loginError').textContent = '';
  $('#loginBtn').disabled = true;
  try {
    await window.api.login($('#username').value.trim(), $('#password').value);
  } catch (err) {
    $('#loginError').textContent = err.message;
    if (/database/i.test(err.message)) $('#dbDetails').open = true;
  } finally {
    $('#loginBtn').disabled = false;
  }
});

(async () => {
  const cfg = await window.api.getDbConfig();
  const f = $('#dbForm');
  for (const k of ['host', 'port', 'user', 'password', 'database']) f[k].value = cfg[k] ?? '';
})();

$('#dbForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = e.target;
  const cfg = Object.fromEntries(['host', 'port', 'user', 'password', 'database'].map((k) => [k, f[k].value]));
  $('#dbStatus').textContent = 'Connecting…';
  $('#dbStatus').className = 'hint';
  try {
    await window.api.saveDbConfig(cfg);
    $('#dbStatus').textContent = 'Connected. Tables are ready.';
    $('#dbStatus').className = 'hint ok';
  } catch (err) {
    $('#dbStatus').textContent = err.message;
    $('#dbStatus').className = 'hint err';
  }
});
