// Uses ChatGPT or DeepSeek in a normal browser tab, signed in with your own account, instead of an API:
// opens (or reuses) the chat tab, types the prompt, waits for the full answer and returns its text.
// Runs in the side panel, which stays alive for the minutes a long answer can take.
import { sleep } from './util.js';

export const CHAT_SITES = {
  deepseek: { name: 'DeepSeek', home: 'https://chat.deepseek.com/', match: ['https://chat.deepseek.com/*'] },
  chatgpt: { name: 'ChatGPT', home: 'https://chatgpt.com/', match: ['https://chatgpt.com/*', 'https://chat.openai.com/*'] }
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
export async function askChat(provider, message, { newChat = true, tabId = null, onStatus = () => {}, signal, returnTo = null, timeoutMin = 8 } = {}) {
  const site = CHAT_SITES[provider];
  if (!site) throw new Error('Unknown chat ' + provider);
  const check = () => {
    if (signal?.aborted) throw new Error('Stopped');
  };

  let tab = tabId ? await chrome.tabs.get(tabId).catch(() => null) : null;
  if (!tab) [tab] = await chrome.tabs.query({ url: site.match });
  if (!tab) tab = await chrome.tabs.create({ url: site.home, active: true });
  else if (newChat) tab = await chrome.tabs.update(tab.id, { url: site.home, active: true });
  else tab = await chrome.tabs.update(tab.id, { active: true });
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

  const before = st.count;
  await call(tab.id, 'overlay', true, `Auto Bid is generating your application content with ${site.name}…`);
  try {
    onStatus(`Sending the prompt to ${site.name}…`);
    const sent = await call(tab.id, 'send', message);
    if (!sent?.ok) throw new Error(`Could not send the prompt to ${site.name} (${sent?.note || 'no response'}).`);

    // the answer is complete once a new one exists, nothing is streaming and its length holds still
    const end = Date.now() + timeoutMin * 60000;
    let lastLen = -1;
    let still = 0;
    let started = false;
    while (Date.now() < end) {
      check();
      await sleep(1500);
      st = await call(tab.id, 'state').catch(() => null);
      if (!st) {
        await inject(tab.id).catch(() => {});
        continue;
      }
      if (st.count > before) started = true;
      if (!started) {
        onStatus(`${site.name} is thinking…`);
        continue;
      }
      onStatus(`${site.name} is writing… ${st.length.toLocaleString()} characters`);
      still = st.length === lastLen && !st.generating ? still + 1 : 0;
      lastLen = st.length;
      if (still >= (provider === 'deepseek' ? 4 : 3) && st.length > 0) break;
    }
    if (!started) throw new Error(`${site.name} did not answer in time.`);
    const ans = await call(tab.id, 'lastAnswer');
    if (!ans?.text) throw new Error(`Could not read ${site.name}'s answer.`);
    return { text: ans.text, plain: ans.plain, tabId: tab.id };
  } finally {
    await call(tab.id, 'overlay', false).catch(() => {});
    if (returnTo) await chrome.tabs.update(returnTo, { active: true }).catch(() => {});
  }
}
