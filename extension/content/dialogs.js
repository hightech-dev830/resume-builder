// Runs in the page itself (MAIN world) while the bot works: the page's own alert / confirm / prompt
// boxes ("careers.cisco.com says: You have Uploaded Resume Successfully. [OK]") freeze the tab until
// someone clicks OK, so the bot answers them at once, as if OK was clicked.
(() => {
  if (window.__abDialogs) return;
  window.__abDialogs = true;
  const note = (kind, msg) => {
    try {
      document.documentElement.setAttribute('data-ab-dialog', `${kind}: ${String(msg ?? '').slice(0, 200)}`);
    } catch {}
    console.info('[auto-bid] answered a page dialog with OK:', kind, msg);
  };
  window.alert = function (msg) { note('alert', msg); };
  window.confirm = function (msg) { note('confirm', msg); return true; };
  window.prompt = function (msg, def) { note('prompt', msg); return def ?? ''; };
})();
