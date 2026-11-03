// The templates name Windows fonts (Calibri, Segoe UI, Georgia…). Vercel's Linux Chromium
// has none of them, so the web build swaps each for a metric-compatible open font.
// Preview and PDF then use the same fonts, and line/page breaks stay close to the desktop PDFs.
// The font files live in public/fonts/ (downloaded once by scripts/fetch-fonts.js).
const fs = require('fs');
const path = require('path');

const SUBSTITUTES = {
  calibri: 'Carlito',
  carlito: 'Carlito',
  'times new roman': 'Tinos',
  times: 'Tinos',
  georgia: 'Gelasio',
  arial: 'Arimo',
  helvetica: 'Arimo',
  'helvetica neue': 'Arimo',
  'segoe ui': "'Noto Sans'",
  'system-ui': "'Noto Sans'",
  consolas: 'Cousine',
  'courier new': 'Cousine',
  'cascadia mono': 'Cousine',
  garamond: "'EB Garamond'",
  'eb garamond': "'EB Garamond'"
};

const FAMILIES = {
  Carlito: '0,400;0,700;1,400;1,700',
  Tinos: '0,400;0,700;1,400;1,700',
  Arimo: '0,400;0,700;1,400;1,700',
  Cousine: '0,400;0,700;1,400;1,700',
  Gelasio: '0,400;0,700;1,400;1,700',
  'Noto Sans': '0,300;0,400;0,600;0,700;0,800;1,400;1,700',
  'EB Garamond': '0,400;0,700;1,400;1,700'
};

// Source for scripts/fetch-fonts.js.
const CSS_URL = `https://fonts.googleapis.com/css2?${Object.entries(FAMILIES)
  .map(([f, w]) => `family=${f.replace(/ /g, '+')}:ital,wght@${w}`)
  .join('&')}&display=block`;

const FONT_DIR = path.join(__dirname, '..', 'public', 'fonts');
const LINK = '<link rel="stylesheet" href="/fonts/fonts.css">';

function swapStack(stack) {
  const seen = new Set();
  return stack
    .split(',')
    .map((part) => SUBSTITUTES[part.trim().replace(/^['"]|['"]$/g, '').toLowerCase()] || part.trim())
    .filter((f) => (seen.has(f) ? false : seen.add(f)))
    .join(', ');
}

function swapFonts(html) {
  return html.replace(/font-family:\s*([^;}"]+)/g, (m, stack) => 'font-family: ' + swapStack(stack));
}

// For the browser preview: fonts load from this site's /fonts/.
function webFonts(html) {
  return swapFonts(html).replace('</head>', LINK + '</head>');
}

// For PDFs: the same fonts embedded as data URIs, so rendering needs no network at all.
let inlineCss = null;

function buildInlineCss() {
  const cssFile = path.join(FONT_DIR, 'fonts.css');
  if (!fs.existsSync(cssFile)) {
    console.warn('public/fonts/fonts.css missing; run scripts/fetch-fonts.js. PDFs will use fallback fonts.');
    return '';
  }
  return fs.readFileSync(cssFile, 'utf8').replace(/url\(\/fonts\/([^)]+)\)/g, (m, file) =>
    `url(data:font/woff2;base64,${fs.readFileSync(path.join(FONT_DIR, file)).toString('base64')})`);
}

function webFontsInline(html) {
  if (inlineCss === null) inlineCss = buildInlineCss();
  return swapFonts(html).replace('</head>', `<style>${inlineCss}</style></head>`);
}

module.exports = { webFonts, webFontsInline, CSS_URL };
