// HTML -> PDF with Chrome's own print engine (the same output as the desktop app's printToPDF):
// a background about:blank tab gets the template HTML through the DevTools protocol, then Page.printToPDF.
// Chrome shows a short "started debugging this browser" bar while it runs; that is this call.
import { sleep } from './util.js';

let queue = Promise.resolve();

export function htmlToPdf(html) {
  // one at a time: two debugger sessions on new tabs at once confuse focus
  const job = queue.then(() => render(html));
  queue = job.catch(() => {});
  return job;
}

async function render(html) {
  const tab = await chrome.tabs.create({ url: 'about:blank', active: false });
  const target = { tabId: tab.id };
  const send = (method, params = {}) => chrome.debugger.sendCommand(target, method, params);
  let attached = false;
  try {
    await chrome.debugger.attach(target, '1.3');
    attached = true;
    await send('Page.enable');
    const { frameTree } = await send('Page.getFrameTree');
    await send('Page.setDocumentContent', { frameId: frameTree.frame.id, html });
    await send('Runtime.evaluate', { expression: 'document.fonts.ready.then(() => true)', awaitPromise: true, timeout: 10000 }).catch(() => {});
    await sleep(150);
    const { data } = await send('Page.printToPDF', {
      printBackground: true, preferCSSPageSize: true, paperWidth: 8.5, paperHeight: 11,
      marginTop: 0, marginBottom: 0, marginLeft: 0, marginRight: 0
    });
    return data; // base64
  } catch (err) {
    if (/Another debugger is already attached/i.test(err.message)) throw new Error('Close DevTools on the PDF tab and try again.');
    throw new Error('PDF rendering failed: ' + err.message);
  } finally {
    if (attached) await chrome.debugger.detach(target).catch(() => {});
    await chrome.tabs.remove(tab.id).catch(() => {});
  }
}
