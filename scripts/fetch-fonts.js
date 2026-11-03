// Downloads the Latin subsets of the substitute fonts into public/fonts/ and writes
// public/fonts/fonts.css. Run once (and commit the result): node scripts/fetch-fonts.js
const fs = require('fs');
const path = require('path');
const { CSS_URL } = require('../lib/fonts');

const OUT = path.join(__dirname, '..', 'public', 'fonts');
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36';

async function get(url, asText) {
  for (let attempt = 1; ; attempt++) {
    try {
      const res = await fetch(url, { headers: { 'User-Agent': UA }, signal: AbortSignal.timeout(30000) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return asText ? res.text() : Buffer.from(await res.arrayBuffer());
    } catch (err) {
      if (attempt >= 5) throw new Error(`${url}: ${err.cause?.code || err.message}`);
      await new Promise((r) => setTimeout(r, 1000 * attempt));
    }
  }
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const css = await get(CSS_URL, true);
  const blocks = [...css.matchAll(/\/\*\s*latin\s*\*\/\s*(@font-face\s*{[^}]*})/g)].map((m) => m[1]);
  const out = [];
  for (const block of blocks) {
    const family = /font-family:\s*'([^']+)'/.exec(block)[1];
    const style = /font-style:\s*(\w+)/.exec(block)[1];
    const weight = /font-weight:\s*(\d+)/.exec(block)[1];
    const url = /url\((https:[^)]+)\)/.exec(block)[1];
    const file = `${family.replace(/\s+/g, '')}-${weight}${style === 'italic' ? 'i' : ''}.woff2`;
    if (!fs.existsSync(path.join(OUT, file))) fs.writeFileSync(path.join(OUT, file), await get(url));
    out.push(block.replace(/url\((https:[^)]+)\)/, `url(/fonts/${file})`));
    console.log('  ' + file);
  }
  fs.writeFileSync(path.join(OUT, 'fonts.css'), `/* Latin subsets of the web substitute fonts (scripts/fetch-fonts.js). */\n${out.join('\n')}\n`);
  console.log(`Saved ${out.length} font files to public/fonts/`);
})().catch((e) => {
  console.error('FAILED:', e.message);
  process.exit(1);
});
