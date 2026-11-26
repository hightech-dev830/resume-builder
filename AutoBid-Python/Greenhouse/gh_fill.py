"""Greenhouse apply-form filling: autofill first, then answer only the empty fields.

    prime_autofill(page)        focus the first input once, accept a MyGreenhouse "Autofill"
                                offer if the page shows one, wait until values settle
    scan_form(page)             every live field: kind, label, current value, options,
                                attached file name (resume / cover letter)
    fill_field(page, f, value)  text / textarea / native select / radio / checkbox /
                                react-select combobox / type-ahead (address, location)
    swap_file(page, f, path)    remove an already-attached file, upload ours

Dropdown-like fields (including text inputs that pop a suggestion list, e.g. address or
"Location (City)") never get the whole answer pasted: one word is typed first, the
suggestions are read and the closest one to the full answer is clicked. More words are
typed only when nothing close enough showed up yet.
"""
from __future__ import annotations

import json
import os
import re
from difflib import SequenceMatcher

# ------------------------------------------------------------------ matching

_US_STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california",
    "CO": "colorado", "CT": "connecticut", "DE": "delaware", "FL": "florida", "GA": "georgia",
    "HI": "hawaii", "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa",
    "KS": "kansas", "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi",
    "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada", "NH": "new hampshire",
    "NJ": "new jersey", "NM": "new mexico", "NY": "new york", "NC": "north carolina",
    "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania",
    "RI": "rhode island", "SC": "south carolina", "SD": "south dakota", "TN": "tennessee",
    "TX": "texas", "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington",
    "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming", "DC": "district of columbia",
    "US": "united states", "USA": "united states",
}
_NO_OPTION = re.compile(r"^(no options?|no results?|loading|searching|type to search|"
                        r"start typing|no matches)", re.I)


def norm(s) -> str:
    """lower-case words; upper-case state codes (TX, NY, US) spelled out."""
    toks = []
    for t in re.findall(r"[A-Za-z0-9]+", str(s or "").replace("&", " and ")):
        toks.append(_US_STATES.get(t, t.lower()) if t.isupper() else t.lower())
    return " ".join(toks)


def _bool_word(v):
    if isinstance(v, bool):
        return "yes" if v else "no"
    t = str(v or "").strip().lower()
    if t in ("yes", "y", "true", "1"):
        return "yes"
    if t in ("no", "n", "false", "0"):
        return "no"
    return None


def score(answer, option) -> float:
    a, o = norm(answer), norm(option)
    if not a or not o:
        return 0.0
    if a == o:
        return 1.0
    yn = _bool_word(answer)
    if yn:
        first = o.split()[0]
        if first == yn:
            return 0.95
        if first in ("yes", "no"):
            return 0.0
    if f" {a} " in f" {o} " or f" {o} " in f" {a} ":
        short, long_ = sorted((len(a), len(o)))
        return 0.7 + 0.25 * short / long_
    ta, to = set(a.split()), set(o.split())
    s = max(len(ta & to) / len(ta | to), SequenceMatcher(None, a, o).ratio())
    if a.split()[0] == o.split()[0]:
        s = min(0.9, s + 0.15)
    return s


def best_match(answer, options):
    """(index, score) of the option closest to answer; (-1, 0) when there are none."""
    best, best_s = -1, 0.0
    for i, opt in enumerate(options):
        s = score(answer, opt)
        if s > best_s:
            best, best_s = i, s
    return best, best_s


ACCEPT = 0.6        # good enough while still typing
ACCEPT_LAST = 0.45  # good enough after the whole answer was typed


# ------------------------------------------------------------------ page scanning

_SCAN_JS = r"""() => {
  const root = document.querySelector('#application-form, form#application_form, #application_form, form[id*="application" i], #application')
            || document.querySelector('main form, form') || document.body;
  const vis = el => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const clean = s => String(s || '').replace(/\s+/g, ' ').replace(/\s*\*\s*$/, '').trim();
  const firstLine = s => String(s || '').split('\n').map(x => x.trim()).filter(Boolean)[0] || '';
  const esc = s => { try { return CSS.escape(s); } catch (e) { return s; } };
  const labelFor = el => {
    if (el.id) { const l = document.querySelector(`label[for="${esc(el.id)}"]`); if (l) return l; }
    const lb = el.getAttribute('aria-labelledby');
    if (lb) for (const id of lb.split(/\s+/)) { const l = document.getElementById(id); if (l && clean(l.innerText)) return l; }
    const own = el.closest('label'); if (own) return own;
    let p = el.parentElement;
    for (let i = 0; p && i < 6; i++, p = p.parentElement) {
      const l = p.querySelector(':scope > label, :scope > legend, :scope > .label, :scope > div > label');
      if (l && !l.contains(el)) return l;
    }
    return null;
  };
  const isReq = (el, lab) => !!(el.required || el.getAttribute('aria-required') === 'true'
                                || (lab && /\*/.test(lab.innerText || '')));
  const out = [], seen = new Set();
  const push = f => { if (!seen.has(f.key)) { seen.add(f.key); out.push(f); } };

  // radio / checkbox groups (one field per name)
  const groups = {};
  for (const el of root.querySelectorAll('input[type=radio], input[type=checkbox]')) {
    const name = el.name || el.id; if (!name) continue;
    (groups[name] = groups[name] || []).push(el);
  }
  for (const [name, els] of Object.entries(groups)) {
    const optText = e => {
      let t = '';
      if (e.id) { const l = document.querySelector(`label[for="${esc(e.id)}"]`); if (l) t = l.innerText; }
      if (!t) { const l = e.closest('label'); if (l) t = l.innerText; }
      return clean(t || e.value || e.getAttribute('aria-label') || '');
    };
    const options = els.map(optText);
    const fs = els[0].closest('fieldset');
    let lab = fs && fs.querySelector('legend');
    if (!lab) {
      let p = els[0].parentElement;
      for (let i = 0; p && i < 6; i++, p = p.parentElement) {
        if (!els.every(e => p.contains(e))) continue;
        const c = [...p.querySelectorAll('label, legend, .label')].find(
          l => !els.some(e => l.contains(e) || (l.htmlFor && l.htmlFor === e.id)));
        if (c) { lab = c; break; }
      }
    }
    if (!els.some(vis) && !els.some(e => vis(e.closest('label') || e.parentElement))) continue;
    push({ key: 'group:' + name, kind: els[0].type === 'radio' ? 'radio' : 'checkbox',
           id: els.length === 1 ? (els[0].id || '') : '', name,
           label: clean(firstLine(lab ? lab.innerText : (els.length === 1 ? options[0] : name))),
           options, value: els.map((e, i) => e.checked ? options[i] : null).filter(Boolean).join(', '),
           required: els.some(e => e.required) || !!(lab && /\*/.test(lab.innerText || '')),
           single: els.length === 1, suggest: false, attached: '' });
  }

  // job-boards file fields: <div class="file-upload" role="group" aria-labelledby="upload-label-resume">.
  // After a file is attached the <input type=file> is gone and .file-upload__filename shows the name.
  const FILE_GROUP = '.file-upload, [aria-labelledby^="upload-label-"]';
  for (const g of root.querySelectorAll(FILE_GROUP)) {
    if (g.parentElement && g.parentElement.closest(FILE_GROUP)) continue;
    const lbId = g.getAttribute('aria-labelledby') || '';
    const inp = g.querySelector('input[type=file]');
    const id = lbId.replace(/^upload-label-/, '') || (inp && inp.id) || '';
    if (!id) continue;
    const labEl = lbId ? document.getElementById(lbId) : g.querySelector('label, .label');
    const fn = g.querySelector('[class*="filename"]');
    let attached = fn ? clean(fn.innerText) : '';
    if (!attached) {
      const m = (g.innerText || '').match(/[^\s\\/:*?"<>|()]+\.(pdf|docx?|rtf|txt|odt|pages)\b/i);
      attached = m ? m[0] : '';
    }
    push({ key: 'file:' + id, kind: 'file', id, name: (inp && inp.name) || '', group: lbId,
           label: clean(firstLine(labEl ? labEl.innerText : id)), options: [], value: attached,
           required: g.getAttribute('aria-required') === 'true' || !!(labEl && /\*/.test(labEl.innerText || '')),
           single: false, suggest: false, attached });
  }

  for (const el of root.querySelectorAll('input, textarea, select')) {
    const t = (el.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'radio', 'checkbox', 'image', 'reset'].includes(t)) continue;
    if (/^security-input-/.test(el.id || '')) continue;   // email OTP boxes are not questions
    // react-select's hidden "required" twin (tabindex=-1, aria-hidden) is not a field
    if (el.getAttribute('aria-hidden') === 'true' || (el.tabIndex === -1 && !el.id && !el.name)) continue;
    const isFile = t === 'file';
    if (isFile && el.closest(FILE_GROUP)) continue;      // handled as a file group above
    if (!isFile && !vis(el)) continue;
    const lab = labelFor(el);
    const label = clean(firstLine(lab ? lab.innerText : (el.getAttribute('aria-label') || el.placeholder || '')));
    const key = el.id || el.name || label; if (!key) continue;
    let kind = el.tagName === 'TEXTAREA' ? 'textarea' : el.tagName === 'SELECT' ? 'select' : isFile ? 'file' : 'text';
    let value = '', options = [], attached = '', suggest = false;
    if (kind === 'select') {
      options = [...el.options].map(o => clean(o.text));
      const o = el.options[el.selectedIndex];
      value = (o && el.value) ? clean(o.text) : '';
      if (/^(select|choose|please select|--)/i.test(value)) value = '';
    } else if (kind === 'file') {
      let w = el.parentElement;
      for (let i = 0; w && i < 6; i++, w = w.parentElement) {
        if (w.querySelectorAll('input[type=file]').length > 1) break;   // left this field
        const m = (w.innerText || '').match(/[^\s\\/:*?"<>|()]+\.(pdf|docx?|rtf|txt|odt|pages)\b/i);
        if (m) { attached = m[0]; break; }
      }
      value = attached;
    } else {
      const combo = el.getAttribute('role') === 'combobox' || /select__input/.test(el.className || '');
      if (combo) {
        kind = 'combobox';
        const ctl = el.closest('[class*="select__control"], [class*="-control"]');
        const box = ctl ? (ctl.parentElement || ctl) : el.parentElement;
        const vals = box ? [...box.querySelectorAll('[class*="single-value"], [class*="multi-value__label"]')]
                             .map(x => clean(x.innerText)).filter(Boolean) : [];
        value = vals.join(', ');
      } else {
        value = el.value || '';
        suggest = !!(el.getAttribute('list') || el.getAttribute('aria-autocomplete')
                     || el.getAttribute('aria-haspopup') === 'listbox');
      }
    }
    push({ key: 'el:' + key, kind, id: el.id || '', name: el.name || '', label, options, group: '',
           value: clean(value), required: isReq(el, lab), single: false, suggest, attached });
  }
  return out;
}"""

# Mark the currently visible dropdown / suggestion options with data-ab-opt=<n>.
_OPTIONS_JS = r"""() => {
  document.querySelectorAll('[data-ab-opt]').forEach(e => e.removeAttribute('data-ab-opt'));
  const sel = '[role="option"], .select__option, [class*="select__option"], .pac-item, li[id*="-option-"], div[id*="-option-"]';
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    if (!el.getClientRects().length) continue;
    if (el.parentElement && el.parentElement.closest('[data-ab-opt]')) continue;
    const t = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (!t || /^(no options?|no results?|loading|searching|type to search|start typing|no matches)/i.test(t)) continue;
    el.setAttribute('data-ab-opt', String(out.length));
    out.push(t);
  }
  return out;
}"""

_FILLED_COUNT_JS = r"""() => {
  const root = document.querySelector('#application-form, form[id*="application" i], #application') || document.querySelector('form') || document.body;
  let n = 0;
  for (const el of root.querySelectorAll('input:not([type=hidden]):not([type=file]), textarea, select'))
    if ((el.type === 'checkbox' || el.type === 'radio') ? el.checked : (el.value || '').trim()) n++;
  n += root.querySelectorAll('[class*="single-value"], [class*="multi-value__label"]').length;
  return n;
}"""

# Click the remove (X) button of the file already attached next to this file input.
_REMOVE_FILE_JS = r"""(input) => {
  let w = input.parentElement;
  for (let i = 0; w && i < 6; i++, w = w.parentElement) {
    if (w.querySelectorAll('input[type=file]').length > 1) break;
    const btns = [...w.querySelectorAll('button, [role=button], a')].filter(b => {
      const t = [b.getAttribute('aria-label'), b.getAttribute('title'), b.innerText, b.className]
                  .map(x => String(x || '')).join(' ');
      return /remove|delete|clear|×|✕|close/i.test(t) || /^\s*x\s*$/i.test(b.innerText || '');
    });
    if (btns.length) { btns[0].click(); return true; }
  }
  return false;
}"""


def scan_form(page):
    try:
        return page.evaluate(_SCAN_JS) or []
    except Exception as e:
        print(f"    ! form scan failed: {str(e).splitlines()[0][:120]}")
        return []


def _locate(page, f):
    if f.get("id"):
        return page.locator(f"[id={json.dumps(f['id'])}]").first
    if f.get("name"):
        return page.locator(f"[name={json.dumps(f['name'])}]").first
    return None


def read_options(page, wait_ms=1200):
    """Visible dropdown/suggestion texts (indexes match data-ab-opt), waiting up to wait_ms."""
    waited, last = 0, None
    while True:
        try:
            opts = page.evaluate(_OPTIONS_JS) or []
        except Exception:
            opts = []
        if opts and opts == last:          # same list twice in a row = settled
            return opts
        if waited >= wait_ms:
            return opts
        last = opts
        page.wait_for_timeout(150)
        waited += 150


def _click_option(page, i):
    page.locator(f'[data-ab-opt="{i}"]').first.click(timeout=3000)


# ------------------------------------------------------------------ autofill priming

def prime_autofill(page, settle_max_ms=6000):
    """Focus the first input once, accept a MyGreenhouse autofill offer when shown, and
    wait until the autofilled values stop changing. Returns True if anything changed."""
    first = page.locator(
        "#first_name, input[name='job_application[first_name]'], "
        "input[autocomplete='given-name']").first
    try:
        if not first.count():
            first = page.locator(
                "form input[type=text]:visible, form input:not([type]):visible").first
        before = page.evaluate(_FILLED_COUNT_JS)
        first.click(timeout=3000)
    except Exception:
        return False
    page.wait_for_timeout(1200)
    ctx_pages = len(page.context.pages)
    for role in ("button", "link"):
        try:
            b = page.get_by_role(role, name=re.compile(r"auto-?fill", re.I)).first
            if b.count() and b.is_visible():
                b.click(timeout=3000)
                print("    “Autofill my application” (MyGreenhouse) clicked")
                break
        except Exception:
            continue
    # Signed out, the button opens my.greenhouse.io/users/sign_in in a new tab: close it, the
    # bot fills the form itself. Signed in, give its popup a moment to finish, then close it.
    for _ in range(20):
        extra = [p for p in page.context.pages[ctx_pages:] if not p.is_closed()]
        if not extra:
            break
        if any("sign_in" in (p.url or "") for p in extra):
            print("    MyGreenhouse is not signed in in this Chrome — filling without autofill")
            break
        page.wait_for_timeout(400)
    for extra in page.context.pages[ctx_pages:]:
        try:
            extra.close()
        except Exception:
            pass
    try:
        page.bring_to_front()
    except Exception:
        pass
    waited, last, stable = 0, None, 0
    while waited < settle_max_ms:
        try:
            n = page.evaluate(_FILLED_COUNT_JS)
        except Exception:
            break
        stable = stable + 1 if n == last else 0
        if stable >= 2:
            break
        last = n
        page.wait_for_timeout(400)
        waited += 400
    changed = last is not None and last != before
    if changed:
        print(f"    autofill: {last - before:+d} field(s) filled by the page")
    return changed


# ------------------------------------------------------------------ filling

_SUGGEST_LABEL = re.compile(
    r"address|street|city|location|town|zip|postal|state|province|country|school|"
    r"university|college|degree|discipline|major|employer|company", re.I)


def _as_values(value, multi):
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            v = json.loads(value)
            if isinstance(v, list):
                return [str(x) for x in v if str(x).strip()]
        except Exception:
            pass
    if multi and isinstance(value, str) and "," in value:
        return [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, bool):
        return ["Yes" if value else "No"]
    return [str(value)]


def _type_steps(text):
    """'123 Main St, Austin, TX' -> ['123 Main', '123 Main St, Austin,', '123 Main St, Austin, TX']"""
    words = text.split()
    if not words:
        return []
    n0 = 2 if re.fullmatch(r"[\d#.,-]+", words[0]) and len(words) > 1 else 1
    steps = [" ".join(words[:n0])]
    if len(words) > n0 + 1:
        steps.append(" ".join(words[:max(n0 + 1, (len(words) + 1) // 2)]))
    # intermediate queries without trailing commas ("Austin," -> "Austin"); still prefixes
    steps = [s.rstrip(",;") for s in steps] + [" ".join(words)]
    return list(dict.fromkeys(s for s in steps if s))


def _type_and_pick(page, loc, text, free_text_ok):
    """Type one word, pick the closest suggestion; type more only if needed.
    Returns the chosen option text, the typed text (free_text_ok, no suggestions), or None."""
    try:
        loc.click(timeout=3000)
    except Exception:
        loc.focus()
    try:
        loc.fill("")
    except Exception:
        pass
    typed, seen_opts = "", False
    steps = _type_steps(text)
    for n, q in enumerate(steps):
        loc.type(q[len(typed):], delay=30)   # real keystrokes: type-ahead listens to keys
        typed = q
        opts = read_options(page, 1600)
        if not opts:
            continue
        seen_opts = True
        i, s = best_match(text, opts)
        if s >= ACCEPT or (n == len(steps) - 1 and s >= ACCEPT_LAST):
            _click_option(page, i)
            page.wait_for_timeout(250)
            return opts[i]
    if free_text_ok and not seen_opts:
        return typed                         # plain text box after all: keep the full answer
    if free_text_ok:
        # suggestions exist but none is close: keep what was typed, close the list
        page.keyboard.press("Escape")
        return typed
    try:
        page.keyboard.press("Escape")
        loc.fill("")
    except Exception:
        pass
    return None


def _fill_combobox(page, loc, values):
    ok = True
    for v in values:
        try:
            loc.click(timeout=3000)
        except Exception:
            loc.focus()
        opts = read_options(page, 600)
        if not opts:
            page.keyboard.press("ArrowDown")      # react-select opens its menu on ArrowDown
            opts = read_options(page, 600)
        if opts:
            i, s = best_match(v, opts)
            if s >= ACCEPT_LAST:
                _click_option(page, i)
                page.wait_for_timeout(200)
                print(f"      ↳ picked “{opts[i][:60]}” for “{v[:40]}”")
                continue
            page.keyboard.press("Escape")
        picked = _type_and_pick(page, loc, v, free_text_ok=False)
        if picked:
            print(f"      ↳ picked “{picked[:60]}” for “{v[:40]}”")
        else:
            print(f"      ! no option close to “{v[:60]}”")
            ok = False
    return ok


def _fill_select(loc, values):
    texts = loc.evaluate("el => [...el.options].map(o => o.text.trim())") or []
    cand = [(i, t) for i, t in enumerate(texts)
            if t and not re.match(r"^(select|choose|please select|--)", t, re.I)]
    i, s = best_match(values[0], [t for _, t in cand])
    if i < 0 or s < ACCEPT_LAST:
        print(f"      ! no option close to “{values[0][:60]}”")
        return False
    loc.select_option(index=cand[i][0], timeout=3000)
    return True


def _fill_group(page, f, values):
    inputs = page.locator(f"input[name={json.dumps(f['name'])}]")
    if not inputs.count() and f.get("id"):
        inputs = page.locator(f"[id={json.dumps(f['id'])}]")
    if f.get("single"):
        want = _bool_word(values[0])
        if want is None:   # free text answer for a one-checkbox consent → tick it
            want = "yes"
        el = inputs.first
        if (want == "yes") != el.is_checked():
            try:
                el.check(force=True, timeout=2000) if want == "yes" else el.uncheck(
                    force=True, timeout=2000)
            except Exception:
                el.evaluate("e => e.click()")
        return True
    ok = True
    picks = values if f["kind"] == "checkbox" else values[:1]
    for v in picks:
        i, s = best_match(v, f["options"])
        if i < 0 or s < ACCEPT_LAST:
            print(f"      ! no choice close to “{v[:60]}”")
            ok = False
            continue
        el = inputs.nth(i)
        try:
            el.check(force=True, timeout=2000)
        except Exception:
            el.evaluate("e => e.click()")
    return ok


def fill_field(page, f, value, multi=False):
    """Answer one live field. Returns True when something was set."""
    values = _as_values(value, multi or f["kind"] == "checkbox")
    if not values:
        return False
    try:
        if f["kind"] in ("radio", "checkbox"):
            return _fill_group(page, f, values)
        loc = _locate(page, f)
        if loc is None or not loc.count():
            return False
        if f["kind"] == "select":
            return _fill_select(loc, values)
        if f["kind"] == "combobox":
            return _fill_combobox(page, loc, values)
        text = ", ".join(values)
        if f["kind"] == "text" and (f.get("suggest") or _SUGGEST_LABEL.search(f["label"] or "")):
            picked = _type_and_pick(page, loc, text, free_text_ok=True)
            if picked and picked != text:
                print(f"      ↳ picked suggestion “{picked[:60]}”")
            return bool(picked)
        loc.fill(text, timeout=4000)
        if f["kind"] == "text":
            # a plain-looking text box that still pops suggestions: choose from them
            opts = read_options(page, 350)
            if opts:
                picked = _type_and_pick(page, loc, text, free_text_ok=True)
                if picked and picked != text:
                    print(f"      ↳ picked suggestion “{picked[:60]}”")
        return True
    except Exception as e:
        print(f"      ! {f['label'][:50]}: {str(e).splitlines()[0][:100]}")
        return False


# ------------------------------------------------------------------ files

RESUME_LABEL = re.compile(r"resume|\bcv\b", re.I)
COVER_LABEL = re.compile(r"cover\s*letter", re.I)


def file_field(fields, which):
    """The live resume / cover-letter file field (which = 'resume' | 'cover_letter')."""
    files = [f for f in fields if f["kind"] == "file"]
    for f in files:
        if f["id"] == which or f["name"] in (which, f"job_application[{which}]"):
            return f
    pat = COVER_LABEL if which == "cover_letter" else RESUME_LABEL
    for f in files:
        if pat.search(f["label"] or "") and not (
                which == "resume" and COVER_LABEL.search(f["label"] or "")):
            return f
    return None


def _file_input(page, f):
    """The <input type=file> of field f (job-boards removes it while a file is attached)."""
    if f.get("group"):
        loc = page.locator(f"[aria-labelledby={json.dumps(f['group'])}] input[type=file]").first
        if loc.count():
            return loc
    loc = _locate(page, f)
    return loc if loc is not None and loc.count() else None


def _attached_now(page, f):
    now = next((x for x in scan_form(page) if x["key"] == f["key"]), None)
    return (now or {}).get("attached") or ""


def swap_file(page, f, path):
    """Upload path into file field f; an already attached file is removed first."""
    label = f["label"] or f["id"]
    if f.get("attached"):
        removed = False
        if f.get("group"):
            btn = page.locator(
                f"[aria-labelledby={json.dumps(f['group'])}] "
                "button[aria-label*='remove' i], "
                f"[aria-labelledby={json.dumps(f['group'])}] button[aria-label*='delete' i]").first
            try:
                if btn.count():
                    btn.click(timeout=3000)
                    removed = True
            except Exception:
                pass
        if not removed:
            loc = _file_input(page, f)
            try:
                removed = bool(loc and loc.evaluate(_REMOVE_FILE_JS))
            except Exception:
                removed = False
        print(f"    {label}: {'removed' if removed else 'replacing'} already attached "
              f"“{f['attached']}”")
        for _ in range(12):                   # wait for the Attach input to come back
            page.wait_for_timeout(250)
            if _file_input(page, f) is not None:
                break
    loc = _file_input(page, f)
    if loc is None:
        print(f"    ! {label}: file input not found")
        return False
    try:
        loc.set_input_files(str(path), timeout=8000)
    except Exception as e:
        print(f"    ! upload into {label} failed: {str(e).splitlines()[0][:100]}")
        return False
    name = os.path.basename(str(path))
    shown = ""
    for _ in range(20):                       # wait until the page shows our file
        page.wait_for_timeout(250)
        shown = _attached_now(page, f)
        if shown == name:
            break
    if shown and shown != name:
        print(f"    ! {label}: page shows “{shown}”, expected “{name}”")
        return False
    print(f"    uploaded {label}: {path}")
    return True
