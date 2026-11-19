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
    const meta = lines.find((l) => l.includes('•')) || '';
    const parts = meta.split('•').map((s) => s.trim());
    // tags and the company name sit next to buttons on one line: read each from its own element, not from the lines
    const leaves = [...r.card.querySelectorAll('*')].filter((e) => !e.children.length).map((e) => (e.textContent || '').trim()).filter((t) => t && t.length < 80);
    const companyLink = r.card.querySelector('a[href*="company" i], [data-cy*="company" i], [data-testid*="company" i]');
    const ti = leaves.indexOf(title);
    const company = (companyLink && (companyLink.textContent || '').trim())
      || leaves.slice(0, ti > 0 ? ti : 1).filter((l) => !BTN.test(l) && l.length > 1 && !l.includes('•'))[0] || '';
    return {
      id: r.id, url: `https://www.dice.com/job-detail/${r.id}`, title, company,
      location: parts[0] || '', posted: parts.slice(1).join(' ') || '',
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
  const meta = lines.find((l) => l.includes('•') && /(remote|posted|updated|on-?site|hybrid|,)/i.test(l)) || '';
  const parts = meta.split('•').map((s) => s.trim());
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
    location: parts[0] || '', posted: parts.slice(1).join(' • '), tags,
    salary: tags.find((t) => /^(usd|\$)/i.test(t)) || '',
    remoteLd: /TELECOMMUTE/i.test(String(ld?.jobLocationType || '')),
    jd: jd.slice(0, 20000),
    applied: buttons.some((t) => /^(applied|application submitted)$/i.test(t)),
    canApply: buttons.some((t) => /^(easy apply|apply|apply now)$/i.test(t))
  };
}
"""

# phrases in the title or JD that make a "Remote" listing not really remote
NOT_REMOTE = [
    (re.compile(r"\bhybrid\b", re.I), "hybrid"),
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


def check_remote(job: dict, strict: bool = False) -> tuple[bool, str]:
    loc = (job.get("location") or "").lower()
    tags = [t.lower() for t in job.get("tags", [])]
    remote_listed = loc.startswith("remote") or any(t == "remote" for t in tags) or job.get("remoteLd")
    if not remote_listed:
        return False, f"location is '{job.get('location') or 'not shown'}'"
    if strict and any(t in ("on-site", "onsite", "hybrid") for t in tags):
        return False, "tagged On-site / Hybrid"
    text = f"{job.get('title', '')}\n{job.get('jd', '')}"
    for rx, why in NOT_REMOTE:
        m = rx.search(text)
        if m:
            return False, f"{why} (\"{m.group(0)[:60]}\")"
    return True, "remote"


# ---------- Easy Apply ----------

APPLY_NAME = re.compile(r"^\s*(easy\s+)?apply(\s+now)?\s*$", re.I)


def apply_button(page):
    for role in ("button", "link"):
        loc = page.get_by_role(role, name=APPLY_NAME)
        if loc.count():
            return loc.first
    return None


def open_wizard(page, ctx, timeout: float = 20):
    """Clicks Apply on a job page. Returns the page showing the Dice wizard, or raises External / ApplyError."""
    btn = apply_button(page)
    if not btn:
        raise ApplyError("no Apply button on the job page")
    before = set(ctx.pages)
    btn.scroll_into_view_if_needed()
    btn.click()
    end = time.time() + timeout
    while time.time() < end:
        for p in ctx.pages:
            if p in before and p is not page:
                continue
            try:
                u = p.url
            except Exception:
                continue
            if "/job-applications/" in u or "/apply" in u and "dice.com" in u:
                p.wait_for_load_state("domcontentloaded")
                return p
            if p not in before and p.url not in ("about:blank", "") and "dice.com" not in u:
                raise External(u, p)
        time.sleep(0.4)
    raise ApplyError("the Dice application wizard did not open")


class ApplyError(RuntimeError):
    pass


class External(RuntimeError):
    def __init__(self, url, page):
        super().__init__(f"apply goes to the company site ({url.split('/')[2] if '//' in url else url})")
        self.page = page


_CARD_JS = r"""
([label, mark]) => {
  const rx = new RegExp('^\\s*' + label + '\\s*\\*?\\s*$', 'i');
  const head = [...document.querySelectorAll('body *')].find((el) => {
    const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('').trim();
    return (rx.test(own) || (el.children.length === 0 && rx.test(el.textContent))) && el.offsetParent !== null;
  });
  if (!head) return null;
  let box = head;
  for (let i = 0; i < 6 && box.parentElement; i++) {
    box = box.parentElement;
    if (box.querySelector('button, [role="button"], input[type="file"]') || /upload/i.test(box.innerText)) break;
  }
  document.querySelectorAll(`[data-dab="${mark}"]`).forEach((e) => e.removeAttribute('data-dab'));
  box.setAttribute('data-dab', mark);
  const t = box.innerText || '';
  const file = (t.match(/[^\s\\/]+\.(docx?|pdf|txt|rtf)\b/i) || [])[0] || '';
  return { file, text: t.slice(0, 400) };
}
"""


def _upload_into(page, label: str, mark: str, path: str, log) -> None:
    from pathlib import Path
    name = Path(path).name
    info = page.evaluate(_CARD_JS, [label, mark])
    if info is None:
        raise ApplyError(f"no '{label}' section in the wizard")
    card = page.locator(f'[data-dab="{mark}"]')
    if info["file"] and info["file"] == name:
        log(f"  {label}: {name} is already there")
        return
    done = False
    try:
        if info["file"]:
            # an earlier file is preloaded: "…" menu -> Replace
            menu = card.locator('button, [role="button"]').last
            menu.click()
            with page.expect_file_chooser(timeout=8000) as fc:
                page.get_by_text(re.compile(r"^\s*Replace\s*$", re.I)).first.click()
            fc.value.set_files(path)
            log(f"  {label}: replaced {info['file']} with {name}")
        else:
            with page.expect_file_chooser(timeout=8000) as fc:
                card.get_by_text(re.compile(r"upload", re.I)).first.click()
            fc.value.set_files(path)
            log(f"  {label}: uploaded {name}")
        done = True
    except Exception as e:
        log(f"  {label}: file dialog route failed ({str(e)[:80]}); setting the file input directly", "warn")
    if not done:
        inp = card.locator('input[type="file"]')
        if not inp.count():
            raise ApplyError(f"could not upload the {label.lower()}")
        inp.first.set_input_files(path)
    # wait for the new file name to show (anywhere: the page may redraw the section after an upload)
    try:
        page.get_by_text(name).first.wait_for(timeout=25000)
    except Exception:
        if name not in (page.evaluate(_CARD_JS, [label, mark]) or {}).get("text", ""):
            raise ApplyError(f"the {label.lower()} upload did not show {name}")


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
