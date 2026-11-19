// Draws the Dice Auto Bid icon (red rounded square, white "D", small spark) and writes
// assets/icon.png (256 px) and assets/icon.ico (16-256 px). No image libraries: pixels by hand.
// Run: node tools/make_icon.mjs
import { deflateSync } from 'node:zlib';
import { writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname } from 'node:path';

const out = `${dirname(fileURLToPath(import.meta.url))}/../assets`;
mkdirSync(out, { recursive: true });

const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const mix = (a, b, t) => a + (b - a) * t;

// signed distances (negative = inside), in a 0..1 square
function roundRect(x, y, r) {
  const qx = Math.abs(x - 0.5) - (0.5 - r);
  const qy = Math.abs(y - 0.5) - (0.5 - r);
  return Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0) - r;
}
// the "D": a bar plus a half ring, as one stroke
function letterD(x, y) {
  const cx = 0.47, cy = 0.5, R = 0.235, w = 0.075;
  const left = 0.3;
  let d = Infinity;
  // vertical bar
  d = Math.min(d, Math.max(Math.abs(x - left) - w, Math.abs(y - cy) - R - w));
  // top and bottom strokes from the bar to the bowl
  for (const yy of [cy - R, cy + R]) d = Math.min(d, Math.max(Math.abs(y - yy) - w, Math.max(left - x, x - cx)));
  // the bowl (right half ring)
  if (x >= cx) d = Math.min(d, Math.abs(Math.hypot(x - cx, y - cy) - R) - w);
  return d;
}
// four-point spark
function spark(x, y) {
  const cx = 0.78, cy = 0.24, s = 0.11;
  const dx = Math.abs(x - cx) / s, dy = Math.abs(y - cy) / s;
  return (Math.pow(dx, 0.55) + Math.pow(dy, 0.55) - 1) * s * 0.5;
}

function render(size) {
  const px = Buffer.alloc(size * size * 4);
  const aa = 1.2 / size;
  for (let j = 0; j < size; j++) {
    for (let i = 0; i < size; i++) {
      const x = (i + 0.5) / size, y = (j + 0.5) / size;
      const bg = clamp(0.5 - roundRect(x, y, 0.22) / aa);
      const t = clamp((x + y) / 2);
      let r = mix(232, 160, t), g = mix(52, 18, t), b = mix(64, 32, t);
      const ink = Math.max(clamp(0.5 - letterD(x, y) / aa), clamp(0.5 - spark(x, y) / aa));
      r = mix(r, 255, ink);
      g = mix(g, 255, ink);
      b = mix(b, 255, ink);
      const k = (j * size + i) * 4;
      px[k] = r;
      px[k + 1] = g;
      px[k + 2] = b;
      px[k + 3] = Math.round(bg * 255);
    }
  }
  return px;
}

const CRC = new Uint32Array(256).map((_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
const crc32 = (buf) => {
  let c = 0xffffffff;
  for (const x of buf) c = CRC[(c ^ x) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
};
function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}
function png(size) {
  const px = render(size);
  const raw = Buffer.alloc(size * (size * 4 + 1));
  for (let j = 0; j < size; j++) {
    raw[j * (size * 4 + 1)] = 0;
    px.copy(raw, j * (size * 4 + 1) + 1, j * size * 4, (j + 1) * size * 4);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', ihdr), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}

const sizes = [16, 24, 32, 48, 64, 128, 256];
const pngs = sizes.map(png);
writeFileSync(`${out}/icon.png`, pngs[pngs.length - 1]);
// ICO with embedded PNGs
const head = Buffer.alloc(6 + 16 * sizes.length);
head.writeUInt16LE(0, 0);
head.writeUInt16LE(1, 2);
head.writeUInt16LE(sizes.length, 4);
let offset = head.length;
sizes.forEach((s, i) => {
  const e = 6 + i * 16;
  head[e] = s === 256 ? 0 : s;
  head[e + 1] = s === 256 ? 0 : s;
  head.writeUInt16LE(1, e + 4);
  head.writeUInt16LE(32, e + 6);
  head.writeUInt32LE(pngs[i].length, e + 8);
  head.writeUInt32LE(offset, e + 12);
  offset += pngs[i].length;
});
writeFileSync(`${out}/icon.ico`, Buffer.concat([head, ...pngs]));
console.log('wrote assets/icon.png and assets/icon.ico');
