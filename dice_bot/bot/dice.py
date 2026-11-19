"""Dice pages: the recommended-jobs list, a job page, and the Easy Apply wizard.
Dice changes its markup now and then, so every lookup goes by visible text and roles, with fallbacks.
On any failure the runner saves a screenshot + HTML in data/debug/ to tune these quickly."""
from __future__ import annotations

import re
import time

from .config import REPO

LIST_URL = "https://www.dice.com/recommended-jobs?page={page}"
AGENT_JS = (REPO / "extension-v2" / "content" / "agent.js").read_text(encoding="utf-8")

SUCCESS_RE = re.compile(r"(application (has been |was )?(successfully )?(submitted|sent|received)|you('ve| have) (successfully )?applied|"
                        r"thank(s| you) for (applying|your application)|applied successfully|application complete)", re.I)

# ---------- recommended jobs ----------

_LIST_JS = r"""
() => {
  const byId = new Map();
  for (const a of document.querySelectorAll('a[href*="/job-detail/"]')) {
    const m = a.href.match(/job-detail\/([0-9a-f-]{16,})/i);
    if (!m) continue;
    const id = m[1];
    // the card = the largest ancestor whose job links all point to this job
    let card = a;
    while (card.parentElement && card.parentElement !== document.body) {
      const other = [...card.parentElement.querySelectorAll('a[href*="/job-detail/"]')].some((x) => !x.href.includes(id));
      if (other) break;
      card = card.parentElement;
    }
    const rec = byId.get(id) || { id, card, titles: [] };
    const t = (a.innerText || '').trim();
    if (t && !/^apply/i.test(t)) rec.titles.push(t);
    byId.set(id, rec);
  }
  const BTN = /^(apply now|apply|easy apply|applied|save|saved|unsave)$/i;
  return [...byId.values()].map((r) => {
    const text = r.card.innerText || '';
    const lines = text.split('\n').map((s) => s.trim()).filter(Boolean);
    const title = r.titles.sort((a, b) => b.length - a.length)[0] || '';
    // tags, the company, the location and the date sit side by side (Dice draws the "•" with CSS, so it is
    // often not in the text): read each from its own element, not from the lines
    const leaves = [...r.card.querySelectorAll('*')].filter((e) => !e.children.length).map((e) => (e.textContent || '').trim()).filter((t) => t && t.length < 80);
    const AGE = /^(posted |updated )?(today|yesterday|just posted|just now|new|\d+\+?\s*(minutes?|mins?|hours?|hrs?|days?|weeks?|months?)\s*ago)$/i;
    const meta = lines.find((l) => l.includes('•')) || '';
    const parts = meta.split('•').map((s) => s.trim()).filter(Boolean);
    const posted = parts.find((p) => AGE.test(p)) || leaves.find((l) => AGE.test(l)) || '';
    const companyLink = r.card.querySelector('a[href*="company" i], [data-cy*="company" i], [data-testid*="company" i]');
    const ti = leaves.indexOf(title);
    const company = (companyLink && (companyLink.textContent || '').trim())
      || leaves.slice(0, ti > 0 ? ti : 1).filter((l) => !BTN.test(l) && l.length > 1 && !l.includes('•'))[0] || '';
    const location = (parts.length && !AGE.test(parts[0]) ? parts[0] : '')
      || leaves.find((l) => l !== title && l !== company && !AGE.test(l) && /\b(remote|hybrid|on-?site)\b|,\s*[A-Z][A-Za-z]+|,\s*[A-Z]{2}\b/.test(l)) || '';
    // workplace words anywhere on the card except the company name ("Remote", "Hybrid", "On-site")
    const cardText = leaves.filter((l) => l !== company).join(' | ');
    return {
      id: r.id, url: `https://www.dice.com/job-detail/${r.id}`, title, company, location, posted,
      remoteWord: /\bremote\b/i.test(cardText), hybridWord: /\bhybrid\b(?![\s-]*(cloud|multi-?cloud|apps?|applications?|mobile|infrastructure|architectures?|search|retrieval|systems?|environments?|data|deployments?|storage))/i.test(cardText), onsiteWord: /\b(on-?site|in[- ]office)\b/i.test(cardText),
      salary: leaves.find((l) => /^(USD|\$)/.test(l)) || '',
      type: leaves.find((l) => /^(full[- ]?time|part[- ]?time|contract|third party|c2c|w2)/i.test(l)) || '',
      applied: lines.some((l) => /^applied$/i.test(l)) || /\bApplied\b/.test(text) && !/Apply Now/i.test(text)
    };
  });
}
"""


def age_days(label: str) -> int | None:
    """'Today' -> 0, 'Yesterday' -> 1, '3 days ago' -> 3, '5 hours ago' -> 0, '30+ days ago' -> 30; None if unknown."""
    s = (label or "").lower()
    if not s:
        return None
    if "today" in s or re.search(r"\b(just now|minutes?|hours?|hrs?)\b", s):
        return 0
    if "yesterday" in s:
        return 1
    m = re.search(r"(\d+)\+?\s*days?", s)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\+?\s*(weeks?|months?)", s)
    if m:
        return int(m.group(1)) * (7 if m.group(2).startswith("week") else 30)
    return None


def read_list(page, page_no: int) -> list[dict]:
    page.goto(LIST_URL.format(page=page_no), wait_until="domcontentloaded", timeout=60000)
    if re.search(r"/(login|sign-?in)", page.url):
        raise NotLoggedIn()
    try:
        page.wait_for_selector('a[href*="/job-detail/"]', timeout=20000)
    except Exception:
        if re.search(r"/(login|sign-?in)", page.url) or page.get_by_text(re.compile(r"^\s*(log ?in|sign ?in)\s*$", re.I)).count() > 2:
            raise NotLoggedIn()
        return []
    time.sleep(1.5)  # let the rest of the cards render
    return page.evaluate(_LIST_JS)


class NotLoggedIn(RuntimeError):
    def __init__(self):
        super().__init__("Dice is not signed in. Press 'Open browser', sign in to Dice there, then Start again.")


# ---------- job page ----------

_JOB_JS = r"""
() => {
  const txt = (el) => (el ? (el.innerText || el.textContent || '').trim() : '');
  const h1 = document.querySelector('h1');
  // the header card: climb from the title until the block holds a button (Apply / Applied)
  let head = h1;
  for (let i = 0; i < 6 && head && head.parentElement; i++) {
    head = head.parentElement;
    if (head.querySelector('button, a[role="button"]') && txt(head).length > txt(h1).length + 20) break;
  }
  const lines = txt(head).split('\n').map((s) => s.trim()).filter(Boolean);
  const headLeaves = [...head.querySelectorAll('*')].filter((e) => !e.children.length).map((e) => (e.textContent || '').trim()).filter(Boolean);
  // "Remote in San Francisco, CA, US • Posted 60+ days ago • Updated 10 hours ago": split on "•" when it is there, else use the pieces
  const bits = [...lines, ...headLeaves].flatMap((l) => l.split('•')).map((s) => s.trim()).filter(Boolean);
  const dates = [...new Set(bits.filter((b) => /^(posted|updated|reposted)\b/i.test(b) && b.length < 60))];
  const parts = [bits.find((b) => b !== txt(h1) && b.length < 90 && /^remote\b|\b(remote|hybrid|on-?site)\b.*\b(in|or)\b|,\s*[A-Z]{2}\b|,\s*US\b/i.test(b) && !/^(posted|updated)/i.test(b)) || ''];
  // tags ("Full Time" "On-site" "USD …") are side by side: one per element
  const tags = [...head.querySelectorAll('*')].filter((e) => !e.children.length).map((e) => (e.textContent || '').trim())
    .filter((l) => l.length < 45 && /^(full[- ]?time|part[- ]?time|contract|on-?site|remote|hybrid|third party|usd|\$)/i.test(l));
  const companyEl = document.querySelector('a[href*="/company-profile/"], a[href*="/companies/"], [data-cy*="company" i], [data-testid*="company" i]');
  // JSON-LD JobPosting, when the page has it
  let ld = null;
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const j = JSON.parse(s.textContent);
      for (const x of [].concat(j['@graph'] || j)) if (/JobPosting/.test(x['@type'])) ld = x;
    } catch {}
  }
  // the description: the "Job Details" section, else the longest text block in main
  const heads = [...document.querySelectorAll('h1,h2,h3,h4')];
  const det = heads.find((h) => /^(job details|job description|summary|about the (role|job))$/i.test(txt(h)));
  let jd = '';
  if (det) {
    let box = det.parentElement;
    for (let i = 0; i < 3 && box && txt(box).length < 600; i++) box = box.parentElement;
    jd = txt(box);
  }
  if (jd.length < 400 && ld?.description) {
    const d = document.createElement('div');
    d.innerHTML = ld.description;
    jd = txt(d) || d.textContent;
  }
  if (jd.length < 400) {
    const blocks = [...document.querySelectorAll('main section, main div, article')].map(txt).sort((a, b) => b.length - a.length);
    jd = blocks[0] || txt(document.body);
  }
  jd = jd.split(/\n\s*(Similar Jobs|Related Jobs|More jobs like this)\s*\n/i)[0];
  const buttons = [...document.querySelectorAll('button, a[role="button"], a')].map((b) => txt(b)).filter((t) => t && t.length < 30);
  return {
    url: location.href, title: txt(h1) || ld?.title || '',
    company: txt(companyEl) || ld?.hiringOrganization?.name || lines[0] || '',
    location: parts[0] || '', posted: dates.join(' • '), dates, tags, header: lines.join('\n').slice(0, 1500),
    salary: tags.find((t) => /^(usd|\$)/i.test(t)) || '',
    remoteLd: /TELECOMMUTE/i.test(String(ld?.jobLocationType || '')),
    jd: jd.slice(0, 20000),
    applied: buttons.some((t) => /^(applied|application submitted)$/i.test(t)),
    canApply: buttons.some((t) => /^(easy apply|apply|apply now)$/i.test(t))
  };
}
"""

# phrases in the title or JD that make a "Remote" listing not really remote
#   "hybrid" as a way of working, not "hybrid cloud" / "hybrid apps" / "hybrid search"
HYBRID_WORK = re.compile(r"\bhybrid\b(?![\s-]*(cloud|multi-?cloud|apps?|applications?|mobile|infrastructure|architectures?|search|retrieval|"
                         r"vehicles?|cars?|systems?|environments?|integrations?|solutions?|data|deployments?|networks?|it\b|storage|workloads?))", re.I)
NOT_REMOTE = [
    (HYBRID_WORK, "hybrid"),
    (re.compile(r"\b(\d|one|two|three|four)\s*\+?\s*(days?|x)\s*(a|per|/|each)\s*week\b.{0,50}\b(office|on-?site|in[- ]person)", re.I), "office days each week"),
    (re.compile(r"\b(office|on-?site|in[- ]person)\b.{0,40}\b(\d|one|two|three|four)\s*\+?\s*days?\s*(a|per|/|each)\s*week", re.I), "office days each week"),
    (re.compile(r"\bup to \d\s*days?\s*(a|per|/)\s*week (of )?telework", re.I), "mostly on-site (limited telework)"),
    (re.compile(r"\b(on-?site|in[- ]office|in[- ]person)\s+(only|required|position|role|work is required)\b", re.I), "on-site required"),
    (re.compile(r"\bmust (live|reside|be located|be based) within \d+\s*(miles|mi|km)", re.I), "must live near an office"),
    (re.compile(r"\bnot (a )?remote\b|\bno remote\b|\bremote (work )?is not (available|an option)", re.I), "says not remote"),
]


def read_job(page, url: str) -> dict:
    if url and page.url.split("?")[0] != url.split("?")[0]:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_selector("h1", timeout=20000)
    except Exception:
        pass
    time.sleep(1.2)
    # "Show more" in the skills / description
    for b in page.get_by_role("button", name=re.compile(r"^\s*(show|read|see) more\s*$", re.I)).all()[:4]:
        try:
            b.click(timeout=1500)
        except Exception:
            pass
    return page.evaluate(_JOB_JS)


REMOTE_JD = re.compile(r"\b(fully|100\s*%|completely|100 percent)\s+remote\b|\bremote[- ](first|friendly|position|role|opportunity|work|job)\b|"
                       r"\bthis (is a|role is|position is) (a )?remote\b|\bwork (from|remotely from) (home|anywhere)\b|\bremote\s*\((us|usa|u\.s\.)", re.I)


def check_remote(job: dict, strict: bool = False) -> tuple[bool, str]:
    """The job page decides: Remote in the location, tags, header, title or Dice's data, or a clearly remote
    description; Hybrid anywhere in the header / title, or on-site phrases in the description, reject it."""
    loc = (job.get("location") or "").lower()
    tags = [t.lower() for t in job.get("tags", [])]
    head = f"{job.get('title', '')}\n{job.get('header', '')}"
    if HYBRID_WORK.search(f"{head}\n{' '.join(tags)}"):
        return False, "hybrid"
    signals = []
    if loc.startswith("remote") or re.search(r"\bremote\b", loc):
        signals.append("location")
    if "remote" in tags:
        signals.append("tag")
    if job.get("remoteLd"):
        signals.append("Dice data")
    if re.search(r"\bremote\b|\btelework\b|\bwork from home\b|\bwfh\b", head, re.I):
        signals.append("job header")
    onsite_tag = any(t in ("on-site", "onsite") for t in tags)
    # the description counts only when the page itself does not say On-site (a "remote team" line in the
    # company blurb must not turn an on-site job remote)
    if not signals and not onsite_tag and REMOTE_JD.search(job.get("jd", "")):
        signals.append("description")
    if not signals:
        return False, "on-site" if onsite_tag else f"no sign of remote work (location '{job.get('location') or 'not shown'}')"
    if strict and onsite_tag:
        return False, "tagged On-site"
    for rx, why in NOT_REMOTE[1:]:  # hybrid was handled above (header/title); here the description's on-site phrases
        m = rx.search(f"{job.get('title', '')}\n{job.get('jd', '')}")
        if m:
            return False, f"{why} (\"{m.group(0)[:60]}\")"
    m = NOT_REMOTE[0][0].search(job.get("jd", ""))
    if m and not re.search(r"\b(not|no)\s+hybrid\b|hybrid (or|/) remote|remote (or|/) hybrid", job.get("jd", ""), re.I):
        return False, f"hybrid (\"{job['jd'][max(0, m.start() - 30):m.end() + 30].strip()}\")"
    return True, "remote (" + ", ".join(signals) + ")"


def job_age(job: dict) -> int | None:
    """Days since the job page's newest date ('Posted 60+ days ago • Updated 10 hours ago' -> 0, as Dice's list shows it)."""
    ages = [a for a in (age_days(d) for d in job.get("dates") or [job.get("posted", "")]) if a is not None]
    return min(ages) if ages else None


# ---------- Easy Apply ----------

APPLY_NAME = re.compile(r"^\s*(easy\s+)?apply(\s+now)?\s*$", re.I)


def apply_button(page):
    for role in ("button", "link"):
        loc = page.get_by_role(role, name=APPLY_NAME)
        if loc.count():
            return loc.first
    return None


def wizard_url(job_id: str) -> str:
    # Dice's Easy Apply wizard lives at /job-applications/<job id>/wizard (the same id as /job-detail/<id>)
    return f"https://www.dice.com/job-applications/{job_id}/wizard"


def _wizard_tab(ctx, job_id: str):
    for p in ctx.pages:
        try:
            if "/job-applications/" in p.url and (not job_id or job_id in p.url):
                return p
        except Exception:
            continue
    return None


def _is_wizard(page) -> bool:
    try:
        return "/job-applications/" in page.url and page.get_by_text(re.compile(r"(you'?re applying for|resume)", re.I)).count() > 0
    except Exception:
        return False


def open_wizard(page, ctx, job_id: str = "", timeout: float = 15):
    """Clicks Apply on a job page and returns the tab with the Dice wizard (same tab or a new one).
    If the click opens nothing Dice-like, opens the wizard's address directly. Raises External / ApplyError."""
    btn = apply_button(page)
    if not btn:
        raise ApplyError("no Apply button on the job page")
    before = {id(p) for p in ctx.pages}
    try:
        btn.scroll_into_view_if_needed(timeout=5000)
        btn.click(timeout=8000)
    except Exception:
        pass  # covered or hidden button: the direct address below still works
    end = time.time() + timeout
    while time.time() < end:
        w = _wizard_tab(ctx, job_id)
        if w:
            w.wait_for_load_state("domcontentloaded")
            w.bring_to_front()
            return w
        for p in ctx.pages:
            if id(p) in before:
                continue
            try:
                u = p.url
            except Exception:
                continue
            if u and u != "about:blank" and "dice.com" not in u:
                raise External(u, p)
        time.sleep(0.4)
    if not job_id:
        raise ApplyError("the Dice application wizard did not open")
    # the click did nothing we could see: go to the wizard's address
    page.goto(wizard_url(job_id), wait_until="domcontentloaded", timeout=60000)
    for _ in range(30):
        if _is_wizard(page):
            return page
        if "/job-applications/" not in page.url and "dice.com" in page.url and time.time() > end + 5:
            break
        time.sleep(0.5)
    raise ApplyError(f"the Dice application wizard did not open (ended on {page.url})")


class ApplyError(RuntimeError):
    pass


class External(RuntimeError):
    def __init__(self, url, page):
        super().__init__(f"apply goes to the company site ({url.split('/')[2] if '//' in url else url})")
        self.page = page


_CARD_JS = r"""
([label, mark]) => {
  const rx = new RegExp('^\\s*' + label + '\\s*\\*?\\s*$', 'i');
  // every element, also inside web components (open shadow roots)
  const all = [];
  (function walk(root) {
    for (const el of root.querySelectorAll('*')) {
      all.push(el);
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  })(document);
  const head = all.find((el) => {
    const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('').trim();
    return (rx.test(own) || (el.children.length === 0 && rx.test(el.textContent))) && el.offsetParent !== null;
  });
  if (!head) return null;
  let box = head;
  for (let i = 0; i < 6 && box.parentElement; i++) {
    box = box.parentElement;
    if (box.querySelector('button, [role="button"], input[type="file"]') || /upload/i.test(box.innerText)) break;
  }
  all.filter((e) => e.getAttribute && e.getAttribute('data-dab') === mark).forEach((e) => e.removeAttribute('data-dab'));
  box.setAttribute('data-dab', mark);
  const t = box.innerText || '';
  const file = (t.match(/[^\s\\/]+\.(docx?|pdf|txt|rtf)\b/i) || [])[0] || '';
  return { file, text: t.slice(0, 400) };
}
"""


_MENU_JS = r"""
(mark) => {
  // the "…" opener inside the section: a button, or any small clickable icon (Dice draws it as an SVG),
  // marked rightmost first so the bot can try them in turn
  let card = null;
  (function walk(root) {
    for (const el of root.querySelectorAll('*')) {
      if (el.getAttribute('data-dab') === mark) card = el;
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  })(document);
  if (!card) return 0;
  const cands = [...card.querySelectorAll('button, [role="button"], [aria-haspopup], [tabindex], a, span, div, svg')].filter((el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 6 || r.height < 6 || r.width > 90 || r.height > 90) return false;
    const t = (el.innerText || el.textContent || '').trim();
    const cls = typeof el.className === 'string' ? el.className : (el.className && el.className.baseVal) || '';
    const lab = `${el.getAttribute('aria-label') || ''} ${el.getAttribute('title') || ''} ${el.getAttribute('data-testid') || ''} ${cls}`;
    if (/^(\.\.\.|…|•••|⋯|⋮|···)$/.test(t)) return true;
    if (/\b(more|options?|menu|actions?|kebab|ellipsis|dots|overflow)\b/i.test(lab)) return true;
    return !t && (el.tagName === 'svg' || el.querySelector('svg')) && (el.matches('button, [role="button"], [tabindex], [aria-haspopup]') || el.tagName === 'svg');
  });
  // outermost clickable first among overlapping ones, rightmost first overall
  const uniq = cands.filter((el) => !cands.some((o) => o !== el && o.contains(el) && o.matches('button, [role="button"], [tabindex], [aria-haspopup]')));
  uniq.sort((a, b) => b.getBoundingClientRect().right - a.getBoundingClientRect().right);
  document.querySelectorAll('[data-dab-menu]').forEach((e) => e.removeAttribute('data-dab-menu'));
  uniq.forEach((el, i) => el.setAttribute('data-dab-menu', String(i)));
  return uniq.length;
}
"""

REPLACE_RX = re.compile(r"^\s*(replace|replace (resume|file|cover letter)|upload (a )?new( resume| file)?|change)\s*$", re.I)


def _shows(page, name: str, timeout: float) -> bool:
    try:
        page.get_by_text(name).first.wait_for(timeout=timeout * 1000)
        return True
    except Exception:
        return False


def _set_via_chooser(page, click, path) -> bool:
    try:
        with page.expect_file_chooser(timeout=6000) as fc:
            click()
        fc.value.set_files(path)
        return True
    except Exception:
        return False


def _upload_into(page, label: str, mark: str, path: str, log) -> None:
    """Puts our file into the wizard's Resume / Cover letter section, whatever is preloaded there.
    Tries, in order: the section's own file input; the "…" menu -> Replace; the upload area; any file input."""
    from pathlib import Path
    name = Path(path).name
    info = page.evaluate(_CARD_JS, [label, mark])
    if info is None:
        raise ApplyError(f"no '{label}' section in the wizard")
    card = page.locator(f'[data-dab="{mark}"]')
    if info["file"] == name:
        log(f"  {label}: {name} is already there")
        return
    old = info["file"]
    how = ""

    # 1. a file input inside the section (often hidden behind the Replace / Upload buttons)
    try:
        inp = card.locator('input[type="file"]')
        if inp.count():
            inp.first.set_input_files(path)
            if _shows(page, name, 8):
                how = "file input"
    except Exception:
        pass

    # 2. a preloaded file: open the "…" menu, then Replace
    if not how and old:
        n = page.evaluate(_MENU_JS, mark)
        for i in range(min(n, 8)):
            try:
                page.locator(f'[data-dab-menu="{i}"]').first.click(timeout=3000)
            except Exception:
                continue
            page.wait_for_timeout(600)
            rep = page.get_by_text(REPLACE_RX)
            if not rep.count() or not rep.first.is_visible():
                page.keyboard.press("Escape")
                continue
            if _set_via_chooser(page, lambda: rep.first.click(), path) and _shows(page, name, 20):
                how = "… menu → Replace"
                break
            # Replace may only reveal a file input
            inp = page.locator('input[type="file"]')
            if inp.count():
                inp.last.set_input_files(path)
                if _shows(page, name, 15):
                    how = "… menu → Replace (input)"
                    break
        if not how:
            log(f"  {label}: tried {n} possible '…' buttons, none opened a Replace option", "warn")

    # 3. an empty section: the upload area
    if not how:
        area = card.get_by_text(re.compile(r"upload|browse|choose (a )?file|drag", re.I))
        if area.count() and _set_via_chooser(page, lambda: area.first.click(), path) and _shows(page, name, 20):
            how = "upload area"

    # 4. last resort: the page's file inputs, the resume first and the cover letter second
    if not how:
        inputs = page.locator('input[type="file"]')
        k = 0 if label.lower().startswith("resume") else inputs.count() - 1
        if inputs.count():
            inputs.nth(max(k, 0)).set_input_files(path)
            if _shows(page, name, 15):
                how = "page file input"
    if not how:
        raise ApplyError(f"could not put {name} into the {label.lower()} section (see the debug screenshot)")
    log(f"  {label}: {'replaced ' + old + ' with ' if old else 'uploaded '}{name} ({how})")


def click_named(page, name_rx, timeout=8000):
    btn = page.get_by_role("button", name=name_rx)
    btn.first.wait_for(state="visible", timeout=timeout)
    btn.first.scroll_into_view_if_needed()
    btn.first.click()


def is_review(page) -> bool:
    return page.get_by_role("button", name=re.compile(r"^\s*submit( application)?\s*$", re.I)).count() > 0 \
        or page.get_by_text(re.compile(r"review your application", re.I)).count() > 0


def page_text(page) -> str:
    try:
        return page.evaluate("document.body.innerText")
    except Exception:
        return ""


def succeeded(page) -> bool:
    try:
        u = page.url
    except Exception:
        return False
    return bool(SUCCESS_RE.search(page_text(page))) or bool(re.search(r"/(success|submitted|confirmation)", u))


def answer_extra(page, engine_call, personal: dict, letter: str, salary: str, log) -> list[str]:
    """Extra questions in the wizard: rule answers + the example knowledge base. Returns the questions left empty."""
    page.evaluate(AGENT_JS)
    snap = page.evaluate("window.__abAgent.snapshot()")
    snap["fields"] = [f for f in snap.get("fields", []) if f.get("kind") != "file"]
    if not snap["fields"]:
        return []
    plan = engine_call("rules", {"snapshot": snap, "personal": personal, "coverLetter": letter, "salary": salary})
    acts = [a for a in plan["actions"] if not a["op"].startswith("upload") and a["op"] != "click"]
    if acts:
        res = page.evaluate("([a]) => window.__abAgent.act(a, null, {})", [acts])
        ok = sum(1 for r in res if r.get("ok"))
        log(f"  extra questions: answered {ok} of {len(acts)} from the profile and saved knowledge")
    snap = page.evaluate("window.__abAgent.snapshot()")
    return [f.get("question") or f.get("label") or f.get("name") or "?" for f in snap.get("fields", [])
            if f.get("required") and not f.get("value") and not f.get("checked") and f.get("kind") != "file"]
