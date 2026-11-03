// HTML -> PDF with headless Chromium. On Vercel: @sparticuz/chromium. Locally: an
// installed Chrome or Edge (CHROME_PATH, or auto-detected on Windows/macOS/Linux).
const fs = require('fs');
const puppeteer = require('puppeteer-core');
const { env } = require('./env');

const LOCAL_BROWSERS = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium-browser',
  '/usr/bin/chromium'
];

let browserPromise;

async function launch() {
  if (process.env.VERCEL || process.env.AWS_LAMBDA_FUNCTION_NAME) {
    const chromium = require('@sparticuz/chromium');
    return puppeteer.launch({
      args: chromium.args,
      executablePath: await chromium.executablePath(),
      headless: true,
      defaultViewport: { width: 816, height: 1056 }
    });
  }
  const executablePath = env('CHROME_PATH') || LOCAL_BROWSERS.find((p) => fs.existsSync(p));
  if (!executablePath) throw new Error('No Chrome/Edge found for PDF rendering. Set CHROME_PATH in web/.env.local.');
  return puppeteer.launch({ executablePath, headless: true, args: ['--no-sandbox'] });
}

// One browser per warm function instance; relaunched if it died.
async function getBrowser() {
  if (browserPromise) {
    const b = await browserPromise.catch(() => null);
    if (b && b.connected) return b;
  }
  browserPromise = launch();
  return browserPromise;
}

async function htmlToPdf(html) {
  const browser = await getBrowser();
  const page = await browser.newPage();
  try {
    // 'load' waits for the Google Fonts stylesheet; fonts.ready then waits for the font files in use.
    await page.setContent(html, { waitUntil: 'load', timeout: 30000 });
    await page.evaluate(() => document.fonts.ready);
    return Buffer.from(await page.pdf({ printBackground: true, preferCSSPageSize: true, format: 'letter' }));
  } finally {
    await page.close().catch(() => {});
  }
}

module.exports = { htmlToPdf };
