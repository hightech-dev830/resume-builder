const $ = (s) => document.querySelector(s);

async function rpc(method, ...args) {
  const res = await fetch('/api/rpc', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify({ method, args })
  });
  const body = await res.json().catch(() => ({ ok: false, error: `Server error (HTTP ${res.status})` }));
  if (!body.ok) throw new Error(body.error);
  return body.data;
}

// Already signed in? Go straight to the app.
rpc('auth:check').then((ok) => ok && (location.href = '/app.html')).catch(() => {});

$('#loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  $('#loginError').textContent = '';
  $('#loginBtn').disabled = true;
  try {
    await rpc('auth:login', { username: $('#username').value.trim(), password: $('#password').value });
    location.href = '/app.html';
  } catch (err) {
    $('#loginError').textContent = err.message;
  } finally {
    $('#loginBtn').disabled = false;
  }
});
