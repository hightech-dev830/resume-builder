// Word (.docx) export with real paragraphs, bullets and tab-aligned dates (not embedded HTML),
// so ATS parsers read it as text. Font, size and accent colour follow the chosen template.
// The .docx is a ZIP; it is written here without compression (store) to avoid a dependency.

// ---------- ZIP (store) ----------

const CRC = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(bytes) {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

export function zip(files) {
  const enc = new TextEncoder();
  const parts = [];
  const central = [];
  let offset = 0;
  const d = new Date();
  const time = (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1);
  const date = ((d.getFullYear() - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate();
  for (const [name, content] of Object.entries(files)) {
    const data = typeof content === 'string' ? enc.encode(content) : content;
    const nameBytes = enc.encode(name);
    const crc = crc32(data);
    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true);
    local.setUint16(4, 20, true);
    local.setUint16(6, 0x0800, true); // UTF-8 names
    local.setUint16(8, 0, true);
    local.setUint16(10, time, true);
    local.setUint16(12, date, true);
    local.setUint32(14, crc, true);
    local.setUint32(18, data.length, true);
    local.setUint32(22, data.length, true);
    local.setUint16(26, nameBytes.length, true);
    parts.push(new Uint8Array(local.buffer), nameBytes, data);
    const cen = new DataView(new ArrayBuffer(46));
    cen.setUint32(0, 0x02014b50, true);
    cen.setUint16(4, 20, true);
    cen.setUint16(6, 20, true);
    cen.setUint16(8, 0x0800, true);
    cen.setUint16(10, 0, true);
    cen.setUint16(12, time, true);
    cen.setUint16(14, date, true);
    cen.setUint32(16, crc, true);
    cen.setUint32(20, data.length, true);
    cen.setUint32(24, data.length, true);
    cen.setUint16(28, nameBytes.length, true);
    cen.setUint32(42, offset, true);
    central.push(new Uint8Array(cen.buffer), nameBytes);
    offset += 30 + nameBytes.length + data.length;
  }
  const cenSize = central.reduce((s, p) => s + p.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true);
  end.setUint16(8, Object.keys(files).length, true);
  end.setUint16(10, Object.keys(files).length, true);
  end.setUint32(12, cenSize, true);
  end.setUint32(16, offset, true);
  const all = [...parts, ...central, new Uint8Array(end.buffer)];
  const out = new Uint8Array(all.reduce((s, p) => s + p.length, 0));
  let pos = 0;
  for (const p of all) {
    out.set(p, pos);
    pos += p.length;
  }
  return out;
}

// ---------- WordprocessingML ----------

const x = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function fmtDate(d) {
  const m = /^(\d{4})(?:-(\d{1,2}))?/.exec(String(d || ''));
  if (!m) return String(d || '');
  return m[2] ? `${MONTHS[Number(m[2]) - 1]} ${m[1]}` : m[1];
}
const range = (s, e, present = 'Present') => [fmtDate(s), e ? fmtDate(e) : present].filter(Boolean).join(' – ');

// Font, size and accent colour of a rendered template (its HTML from templates.js).
export function styleFromHtml(html) {
  const body = /body\s*\{[^}]*\}/.exec(html || '')?.[0] || '';
  const fam = /font-family:\s*([^;]+)/.exec(body)?.[1] || 'Calibri';
  const font = fam.split(',')[0].replace(/['"]/g, '').trim();
  const size = Number(/font-size:\s*([\d.]+)pt/.exec(body)?.[1]) || 10.5;
  const h2 = [...String(html || '').matchAll(/h2\s*\{[^}]*\}/g)].map((m) => m[0]).join(' ');
  const color = (/color:\s*#([0-9a-f]{6})\b/i.exec(h2)?.[1] || '1F3864').toUpperCase();
  return { font: /carlito/i.test(font) ? 'Calibri' : font, size, color };
}

function run(text, { b, i, size, color, caps } = {}) {
  const pr = [b && '<w:b/>', i && '<w:i/>', caps && '<w:caps/>', color && `<w:color w:val="${color}"/>`, size && `<w:sz w:val="${Math.round(size * 2)}"/>`].filter(Boolean).join('');
  return `<w:r>${pr ? `<w:rPr>${pr}</w:rPr>` : ''}<w:t xml:space="preserve">${x(text)}</w:t></w:r>`;
}

function para(runs, { align, after = 60, before = 0, border, tab, bullet, keepNext } = {}) {
  const pr = [
    bullet && '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>',
    keepNext && '<w:keepNext/>',
    border && `<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="${border}"/></w:pBdr>`,
    tab && '<w:tabs><w:tab w:val="right" w:pos="10080"/></w:tabs>',
    `<w:spacing w:before="${before}" w:after="${after}"/>`,
    align && `<w:jc w:val="${align}"/>`
  ].filter(Boolean).join('');
  return `<w:p><w:pPr>${pr}</w:pPr>${[].concat(runs).join('')}</w:p>`;
}
const tabRun = '<w:r><w:tab/></w:r>';

function heading(title, st) {
  return para(run(title, { b: true, caps: true, size: st.size + 1, color: st.color }), { before: 200, after: 80, border: st.color, keepNext: true });
}

function resumeBody(r, st) {
  const b = r.basics || {};
  const out = [];
  out.push(para(run(b.name, { b: true, size: st.size + 11 }), { align: 'center', after: 40 }));
  if (b.label) out.push(para(run(b.label, { b: true, size: st.size + 2, color: st.color }), { align: 'center', after: 40 }));
  const contact = [b.email, b.phone, [b.location?.city, b.location?.region].filter(Boolean).join(', '),
    ...(b.profiles || []).map((p) => (p.url || '').replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, ''))].filter(Boolean).join('  |  ');
  if (contact) out.push(para(run(contact, { size: st.size - 0.5 }), { align: 'center', after: 120 }));

  if (b.summary || (b.highlights || []).length) {
    out.push(heading('Summary', st));
    for (const p of String(b.summary || '').split(/\n\s*\n/).filter(Boolean)) out.push(para(run(p.trim()), { after: 80 }));
    for (const h of b.highlights || []) out.push(para([run(`${h.name}: `, { b: true }), run(h.value)], { bullet: true, after: 20 }));
  }
  if ((r.skills || []).length) {
    out.push(heading('Skills', st));
    for (const s of r.skills) out.push(para([run(`${s.name}: `, { b: true }), run((s.keywords || []).join(', '))], { after: 30 }));
  }
  if ((r.work || []).length) {
    out.push(heading('Experience', st));
    for (const w of r.work) {
      out.push(para([run(w.position, { b: true, size: st.size + 0.5 }), tabRun, run(range(w.startDate, w.endDate), { b: true })], { tab: true, before: 100, after: 0, keepNext: true }));
      out.push(para([run(w.name, { i: true }), w.location ? run(`  |  ${w.location}`, { i: true }) : ''], { after: 40, keepNext: true }));
      if (w.description) out.push(para(run(w.description), { after: 40 }));
      for (const h of w.highlights || []) out.push(para(run(h), { bullet: true, after: 20 }));
    }
  }
  if ((r.education || []).length) {
    out.push(heading('Education', st));
    for (const e of r.education) {
      out.push(para([run([e.studyType, e.area].filter(Boolean).join(', '), { b: true }), tabRun, run(range(e.startDate, e.endDate, ''))], { tab: true, after: 0 }));
      out.push(para(run(e.institution, { i: true }), { after: 60 }));
    }
  }
  if ((r.languages || []).length) {
    out.push(heading('Languages', st));
    out.push(para(run(r.languages.map((l) => `${l.language}${l.fluency ? ` (${l.fluency})` : ''}`).join('  ·  '))));
  }
  return out.join('');
}

function coverBody(r, letter, job, st) {
  const b = r.basics || {};
  const out = [];
  out.push(para(run(b.name, { b: true, size: st.size + 9 }), { after: 20 }));
  if (b.label) out.push(para(run(b.label, { color: st.color, b: true }), { after: 20 }));
  const contact = [b.email, b.phone, [b.location?.city, b.location?.region].filter(Boolean).join(', ')].filter(Boolean).join('  |  ');
  if (contact) out.push(para(run(contact), { after: 240, border: st.color }));
  out.push(para(run(new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })), { after: 200 }));
  if (job?.company) out.push(para(run(job.company), { after: 0 }));
  if (job?.title) out.push(para(run(`Re: ${job.title}`), { after: 200 }));
  for (const p of String(letter || '').split(/\n\s*\n/).filter(Boolean)) {
    const lines = p.trim().split('\n');
    out.push(para(lines.map((l, i) => (i ? '<w:r><w:br/></w:r>' : '') + run(l)), { after: 160 }));
  }
  return out.join('');
}

function docxFiles(bodyXml, st, title) {
  const W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"';
  return {
    '[Content_Types].xml': `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>`,
    '_rels/.rels': `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>`,
    'word/_rels/document.xml.rels': `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/></Relationships>`,
    'docProps/core.xml': `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>${x(title)}</dc:title><dc:creator>${x(title.split(' - ')[0])}</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">${new Date().toISOString().slice(0, 19)}Z</dcterms:created></cp:coreProperties>`,
    'word/styles.xml': `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles ${W}><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="${x(st.font)}" w:hAnsi="${x(st.font)}" w:cs="${x(st.font)}" w:eastAsia="${x(st.font)}"/><w:sz w:val="${Math.round(st.size * 2)}"/><w:szCs w:val="${Math.round(st.size * 2)}"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="60" w:line="259" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style></w:styles>`,
    'word/numbering.xml': `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering ${W}><w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="360" w:hanging="230"/></w:pPr><w:rPr><w:rFonts w:ascii="${x(st.font)}" w:hAnsi="${x(st.font)}"/></w:rPr></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>`,
    'word/document.xml': `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document ${W}><w:body>${bodyXml}<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="720" w:right="1080" w:bottom="720" w:left="1080" w:header="360" w:footer="360" w:gutter="0"/></w:sectPr></w:body></w:document>`
  };
}

export function resumeDocx(resume, style) {
  const st = { font: 'Calibri', size: 10.5, color: '1F3864', ...(style || {}) };
  return zip(docxFiles(resumeBody(resume, st), st, `${resume.basics?.name || 'Resume'} - Resume`));
}

export function coverDocx(resume, letter, job, style) {
  const st = { font: 'Calibri', size: 11, color: '1F3864', ...(style || {}) };
  return zip(docxFiles(coverBody(resume, letter, job, st), st, `${resume.basics?.name || 'Cover letter'} - Cover Letter`));
}

export const DOCX_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
