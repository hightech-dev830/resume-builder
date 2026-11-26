// Uses ChatGPT or DeepSeek in a normal browser tab, signed in with your own account, instead of an API:
// opens (or reuses) the chat tab, types the prompt, waits for the full answer and returns its text.
// Runs in the side panel, which stays alive for the minutes a long answer can take.
import { sleep } from './util.js';

export const CHAT_SITES = {
  // host: which tabs belong to the site; thread: a URL that is an open conversation (not the home page)
  deepseek: { name: 'DeepSeek', home: 'https://chat.deepseek.com/', match: ['https://chat.deepseek.com/*'], host: /^chat\.deepseek\.com$/, thread: /\/a\/chat\/s\/|\/chat\/s\// },
  chatgpt: { name: 'ChatGPT', home: 'https://chatgpt.com/', match: ['https://chatgpt.com/*', 'https://chat.openai.com/*'], host: /^(chatgpt\.com|chat\.openai\.com)$/, thread: /\/c\/[0-9a-f-]{8,}|\/g\/[^/]+\/c\// }
};

async function waitComplete(tabId, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const t = await chrome.tabs.get(tabId).catch(() => null);
    if (!t) throw new Error('The chat tab was closed.');
    if (t.status === 'complete') return t;
    await sleep(300);
  }
  return chrome.tabs.get(tabId);
}

const call = async (tabId, method, ...args) => {
  const [res] = await chrome.scripting.executeScript({
    target: { tabId },
    func: (m, a) => (window.__abChat ? window.__abChat[m](...a) : null),
    args: [method, args]
  });
  return res?.result;
};

const inject = (tabId) => chrome.scripting.executeScript({ target: { tabId }, files: ['content/chat.js'] });

/**
 * Sends `message` to the chat and returns the answer text.
 * @param {'deepseek'|'chatgpt'} provider
 * @param {object} o  newChat: start a new conversation (false = continue the one in the tab, for fix rounds)
 *                    tabId: a chat tab from an earlier call; onStatus(text); signal: AbortSignal; returnTo: tab id
 */
export async function askChat(provider, message, { newChat = true, tabId = null, onStatus = () => {}, signal, returnTo = null, timeoutMin = 8, expectJson = true } = {}) {
  const site = CHAT_SITES[provider];
  if (!site) throw new Error('Unknown chat ' + provider);
  const check = () => {
    if (signal?.aborted) throw new Error('Stopped');
  };

  // the chat tab to use: the one from the last call (still on this site), else your pinned one,
  // else one already showing a conversation, else any tab of the site; a new tab only when there is none
  let tab = tabId ? await chrome.tabs.get(tabId).catch(() => null) : null;
  if (tab && !site.host.test(new URL(tab.url || tab.pendingUrl || 'about:blank').host)) tab = null;
  if (!tab) {
    const tabs = await chrome.tabs.query({ url: site.match });
    tab = tabs.find((t) => t.pinned) || tabs.find((t) => site.thread.test(t.url || '')) || tabs.find((t) => t.active) || tabs[0] || null;
  }
  if (!tab) tab = await chrome.tabs.create({ url: site.home, active: true });
  else if (newChat) tab = await chrome.tabs.update(tab.id, { url: site.home, active: true });
  else tab = await chrome.tabs.update(tab.id, { active: true }); // keep the open conversation: no reload
  await chrome.windows.update(tab.windowId, { focused: true }).catch(() => {});
  onStatus(`Opening ${site.name}…`);
  await waitComplete(tab.id);

  // wait for the chat box (or a sign-in page)
  let st = null;
  for (let i = 0; i < 60; i++) {
    check();
    await inject(tab.id).catch(() => {});
    st = await call(tab.id, 'state').catch(() => null);
    if (st?.ready) break;
    if (st?.loggedOut && i > 3) throw new Error(`Sign in to ${site.name} in the opened tab, then press Generate again.`);
    await sleep(700);
  }
  if (!st?.ready) throw new Error(`${site.name} did not show its chat box. Check the tab (sign in, close pop-ups) and try again.`);

  // mark the answers already in the thread: the new answer is the one without the mark
  await call(tab.id, 'markOld');
  await call(tab.id, 'overlay', true, `Auto Bid is generating your application content with ${site.name}…`);
  try {
    onStatus(`Sending the prompt to ${site.name}…`);
    const sent = await call(tab.id, 'send', message);
    if (!sent?.ok) throw new Error(`Could not send the prompt to ${site.name} (${sent?.note || 'no response'}).`);

    // done when the new answer's JSON is complete (at once), or, for plain text, when nothing is
    // streaming and its length has held still for ~2 s
    const end = Date.now() + timeoutMin * 60000;
    let lastLen = -1;
    let still = 0;
    let frozen = 0; // polls with an unchanged length, even if the page still looks busy
    let started = false;
    while (Date.now() < end) {
      check();
      await sleep(700);
      st = await call(tab.id, 'state').catch(() => null);
      if (!st) {
        await inject(tab.id).catch(() => {});
        continue;
      }
      if (st.apiDone) break; // ChatGPT's own record says the reply is finished
      if (st.fresh > 0 && st.length > 0) started = true;
      if (!started) {
        const secs = Math.round((Date.now() - end) / 1000 + timeoutMin * 60);
        onStatus(`${site.name} is thinking… ${secs > 20 ? `(${secs} s; replies on the page: ${st.count}, new: ${st.fresh})` : ''}`);
        continue;
      }
      onStatus(`${site.name} is writing… ${st.length.toLocaleString()} characters`);
      frozen = st.length === lastLen ? frozen + 1 : 0;
      const steady = frozen > 0 && !st.generating;
      still = steady ? still + 1 : 0;
      lastLen = st.length;
      if (st.done && frozen >= 1) break;                         // ChatGPT shows the reply's copy buttons: finished
      if (expectJson && st.jsonReady && still >= 1) break;      // the JSON block closed and parses
      if (still >= (expectJson ? 6 : 3) && st.length > 0) break; // no parsable JSON: wait a little longer
      if (st.jsonReady && frozen >= 3) break;                    // complete JSON, unchanged for ~2 s, whatever the page shows
    }
    if (!started && !st?.apiDone) throw new Error(`${site.name} did not answer in time.`);
    const ans = await call(tab.id, 'lastAnswer');
    if (!ans?.text) throw new Error(`Could not read ${site.name}'s answer.`);
    return { text: ans.text, plain: ans.plain, tabId: tab.id };
  } finally {
    await call(tab.id, 'overlay', false).catch(() => {});
    if (returnTo) await chrome.tabs.update(returnTo, { active: true }).catch(() => {});
  }
}
