"""Step 3: apply to jobs in BATCH MODE.

  1. Open up to BATCH_SIZE jobs, one tab each, fill the REQUIRED fields from
     answer bank (Supabase), click "Submit Application".
       - no bot check  -> submitted, tab closed, status 'applied'
       - bot check     -> tab left open, status 'needs_you'
  2. One notification: "N applications are waiting for you". Solve the bot checks
     tab after tab; each one finishes and closes by itself when you solve it.
  3. Next batch.

Usage:
    python bid.py                 # all jobs whose required answers are complete
    python bid.py --dry-run       # fill forms but do NOT submit (check the result)
    python bid.py --job <job-id>  # only this job
    python bid.py --batch 3       # override BATCH_SIZE

Requires:  pip install playwright   (uses your installed Google Chrome)
"""
import argparse
import json
import re
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit("Playwright is missing. Run:  pip install playwright")

import answers as bank_mod
import db
from config import APPLY_LIMITS, BATCH_SIZE, BATCH_WAIT_MIN, TAILORED_RESUME, stop_requested
# shared modules (AutoBid-Python/common, put on sys.path by config)
from browser import open_browser
from captcha import detect_captcha, captcha_solved
from notify import notify
from resume import make_resume
import ai_settings

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FORM_READY = "#_systemfield_name, .ashby-application-form-field-entry"
SUBMIT_BTN = "button[class*='submitButton'], button.ashby-application-form-submit-button"

_OUTCOME_JS = r"""() => {
  const text = document.body.innerText || '';
  const form = document.querySelector('#_systemfield_name, .ashby-application-form-field-entry');
  if (document.querySelector('.ashby-application-form-success-container') ||
      /submitted successfully|thank(s| you)( so much)? for (applying|your (application|interest))|application (was |has been )?(successfully )?(submitted|received|sent)|we('ve| have) received your application|application submitted/i.test(text) ||
      (!form && !/\/application/.test(location.pathname) && /thank|submitted|success/i.test(text)))
    return {state: 'success'};
  // Ashby's information box "This job has application limits ..." is shown on many forms
  // all the time - it is NOT an error. Only a message that a limit was REACHED counts.
  const notice = t => /^this job has application limits/i.test(t) || /candidates may not (apply|re-apply)/i.test(t);
  const visible = e => e.offsetParent !== null || getComputedStyle(e).position === 'fixed';
  const msgs = [...document.querySelectorAll('[class*="error" i], [role="alert"], [class*="toast" i]')]
    .filter(e => visible(e) && e.id !== 'autobid-banner')
    .map(e => e.innerText.trim()).filter(t => t && t.length < 400 && !notice(t));
  // (not "may not apply more than 3 times" - that sentence is part of Ashby's notice)
  const reached = /(reached|exceeded|hit) (the|your)? ?(maximum|application limit|limit)|you (have|'ve) already applied|you (can|may)not apply (again|to this job)|too many applications/i;
  const bad = msgs.filter(t => /error|invalid|required|missing|spam|failed|unable|could not|try again|not eligible|went wrong/i.test(t) || reached.test(t));
  // Ashby's red box "We couldn't submit your application" + the reason (application limit,
  // cooling time, spam flag, ...): shown only after a failed Submit.
  const cant = text.match(/couldn.t submit your application[\s\S]{0,300}/i);
  if (/flagged as (possible )?spam|could not be submitted|something went wrong|problem verifying this submission/i.test(text) ||
      cant || reached.test(text) || bad.length)
    return {state: 'error', message: ([...new Set(bad)].join(' | ') ||
      (cant ? cant[0].replace(/\s+/g, ' ').trim() : '') ||
      text.match(/[^.\n]*(spam|could not be submitted|something went wrong|already applied|reached|exceeded|too many applications)[^.\n]*/i)?.[0] || '').slice(0, 400)};
  return {state: 'pending'};
}"""

# What the page shows when the outcome is unclear (saved with the job, shown in a banner)
_DIAG_JS = r"""() => {
  const visible = e => e.offsetParent !== null;
  const btn = [...document.querySelectorAll('button')].find(b => /submit/i.test(b.innerText));
  const msgs = [...document.querySelectorAll('[role="alert"], [aria-live], [class*="toast" i], [class*="error" i], [class*="message" i]')]
    .filter(visible).map(e => e.innerText.trim()).filter(t => t && t.length < 300);
  return {
    url: location.href,
    submit: btn ? (btn.innerText.trim() + (btn.disabled ? ' (disabled)' : '') + (btn.getAttribute('aria-busy') === 'true' ? ' (busy)' : '')) : 'no submit button any more',
    messages: [...new Set(msgs)].slice(0, 6),
    frames: [...document.querySelectorAll('iframe')].filter(f => f.offsetWidth > 0).map(f => (f.src || '').split('?')[0].slice(0, 70) + ' ' + f.offsetWidth + 'x' + f.offsetHeight),
    end: document.body.innerText.slice(-400).replace(/\s+/g, ' ')
  };
}"""

# Bottom of the tab (not top): a top bar covers Ashby's reCAPTCHA challenge text
# ("Select all images with …"), so you cannot solve the bot check.
_BANNER_JS = r"""([title, text, color]) => {
  let b = document.getElementById('autobid-banner');
  if (!b) { b = document.createElement('div'); b.id = 'autobid-banner'; document.body.appendChild(b); }
  b.style.cssText = `position:fixed;bottom:0;left:0;right:0;z-index:2147483647;padding:10px 16px;
    max-height:28vh;overflow:auto;background:${color};color:#fff;
    font:14px/1.4 Segoe UI,Arial,sans-serif;box-shadow:0 -2px 8px #0005;white-space:pre-wrap`;
  b.textContent = 'AutoBid: ' + title + '\n' + text;
}"""


# ---------------------------------------------------------------- filling

def _labels_for(q, value):
    options = json.loads(q["options"] or "[]")
    by_value = {str(o["value"]): o["label"] for o in options}
    values = value if isinstance(value, list) else [value]
    return [by_value.get(str(v), str(v)) for v in values]


_OPTION_JS = """([path, label]) => {
  // radio / checkbox options of a choice question: their ids contain the question's path
  const want = label.trim().toLowerCase();
  for (const input of document.querySelectorAll(`input[id*="${path}-labeled-"]`)) {
    const lab = document.querySelector(`label[for="${CSS.escape(input.id)}"]`);
    if (lab && lab.innerText.trim().toLowerCase() === want) return input.id;
  }
  return null;
}"""


def _click_option(page, q, label):
    """Tick one choice of a ValueSelect / MultiValueSelect. Returns False if not found as radio/checkbox."""
    input_id = page.evaluate(_OPTION_JS, [q["path"], label])
    if not input_id:
        return False
    lab = page.locator(f'label[for="{input_id}"]')
    lab.scroll_into_view_if_needed(timeout=5000)
    if not page.locator(f'[id="{input_id}"]').is_checked():
        lab.click()
    return True


def fill_field(page, q, value):
    """Fill one Ashby field. Raises on failure."""
    entry = page.locator(f'[data-field-path="{q["path"]}"]').first
    try:
        # some wrappers are invisible (display: contents), e.g. around radio groups
        entry.scroll_into_view_if_needed(timeout=1500)
    except Exception:
        pass
    ftype = q["type"]

    if ftype == "File":
        path = Path(str(value)).expanduser()
        if not path.is_file():
            raise RuntimeError(f"resume file not found: {path}")
        # NOT the "Autofill from resume" input at the top - it would overwrite answers.
        entry.locator("input[type=file]").set_input_files(str(path))
        wait_upload_done(page, entry, path.name)
    elif ftype == "Boolean":
        entry.get_by_role("button", name="Yes" if value else "No", exact=True).click()
    elif ftype == "Location":
        box = entry.locator("input[role=combobox]").first
        box.click()
        box.fill("")
        box.type(str(value).split(",")[0].strip() if value else "", delay=40)
        option = page.locator("[role=option]").first
        option.wait_for(timeout=8000)
        # prefer the suggestion that matches the full text, else the first one
        full = page.locator("[role=option]", has_text=str(value)).first
        (full if full.count() else option).click()
    elif ftype in ("ValueSelect", "MultiValueSelect"):
        for label in _labels_for(q, value):
            if _click_option(page, q, label):
                continue
            # long lists are a dropdown (combobox) instead of radios
            box = entry.locator("input[role=combobox], input").first
            box.click()
            box.fill(label)
            page.locator("[role=option]", has_text=label).first.click(timeout=5000)
    else:   # String, Email, Phone, LongText, Number, Date, ...
        box = entry.locator("input:not([type=file]):not([type=checkbox]), textarea").first
        text = str(value)
        if ftype == "Phone" or (box.get_attribute("type") or "") == "tel":
            text = phone_format(text)
        box.fill(text)


_UPLOADING = re.compile(r"uploading|updating your application|processing|%", re.I)


def wait_upload_done(page, entry, filename, timeout=60):
    """Submit only after Ashby finished uploading the file: it then shows the file name
    (e.g. "KevinBurgess_DevOps Engineer_Resume.pdf  Replace") and no progress/spinner.
    Clicking Submit earlier gives "We're updating your application (e.g. uploading files),
    please try again when they're finished."""
    stem = Path(filename).stem[:25].lower()
    end = time.time() + timeout
    while time.time() < end:
        try:
            text = entry.inner_text(timeout=2000)
            busy = entry.locator("[aria-busy='true'], [role='progressbar'], [class*='spinner' i], "
                                 "[class*='loading' i], [class*='progress' i]").count()
        except Exception:
            text, busy = "", 1
        if stem in text.lower() and not busy and not _UPLOADING.search(text):
            page.wait_for_timeout(1000)        # let Ashby store the upload id
            return True
        page.wait_for_timeout(500)
    raise RuntimeError(f"the resume upload did not finish within {timeout} s")


def wait_page_idle(page, timeout=60):
    """No upload still running anywhere on the form (before clicking Submit)."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            # spinners count only while they really spin (Ashby keeps a still one on the page)
            busy = page.evaluate("""() => !![...document.querySelectorAll(
                '[aria-busy="true"], [role="progressbar"], [class*="spinner" i]')].find(e =>
                    e.offsetParent && (!/spinner/i.test(e.className) ||
                      (getComputedStyle(e).animationName !== 'none' && !/speed-no/i.test(e.className))))
                || /uploading/i.test(document.body.innerText)""")
        except Exception:
            busy = False
        if not busy:
            return
        page.wait_for_timeout(500)


def phone_format(raw):
    """US numbers in the full international form (Ashby's example is "1-415-555-1234"):
    "3109813618" / "(310) 981-3618" -> "+1 310-981-3618". Other numbers are left as they are."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10 and not raw.strip().startswith("+") or (raw.strip().startswith("+1") and len(digits) == 10):
        return f"+1 {digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return raw


def fill_form(page, items):
    problems = []
    for item in items:
        q = item["q"]
        try:
            fill_field(page, q, item["value"])
        except Exception as e:
            problems.append(f"{q['title'][:60]}: {str(e).splitlines()[0][:120]}")
    return problems


# ---------------------------------------------------------------- outcome

def outcome(page):
    try:
        if detect_captcha(page):
            return {"state": "captcha"}
        return page.evaluate(_OUTCOME_JS)
    except Exception:
        return {"state": "pending"}   # page navigating


def wait_outcome(page, seconds=60):
    """After Submit: success, error, or a bot check showed up (Ashby can take a while:
    resume upload + invisible reCAPTCHA scoring)."""
    end = time.time() + seconds
    while time.time() < end:
        res = outcome(page)
        if res["state"] == "error":
            # give a success message a moment to appear before believing an error
            for _ in range(8):
                page.wait_for_timeout(500)
                again = outcome(page)
                if again["state"] == "success":
                    return again
            return outcome(page) if outcome(page)["state"] != "pending" else res
        if res["state"] != "pending":
            return res
        page.wait_for_timeout(700)
    return {"state": "pending"}


_TRY_AGAIN = re.compile(r"updating your application|uploading files|try again when (they('| a)re|it'?s) finished", re.I)


# Errors that say nothing about the job: reCAPTCHA scored the submit as a bot, or Ashby's
# spam filter hit. Waiting a little and submitting again often works ("please submit again").
_TRANSIENT = re.compile(r"problem verifying this submission|verif\w+ .{0,30}captcha|flagged as (possible )?spam|"
                        r"submit your application again", re.I)
# The company limits how often one person may apply: retrying never helps.
_LIMIT = re.compile(r"limited to (\d+) applications? within a rolling (\d+) day|cooling time|"
                    r"already applied to some of our roles|reached (the|your) .{0,30}limit|"
                    r"too many applications|may not apply more than", re.I)


def limit_days(message):
    """How many days the company keeps us out, from its message ('rolling 30 day period',
    '90 days cooling time'); None when the message is not about a limit."""
    if not _LIMIT.search(message or ""):
        return None
    m = (re.search(r"rolling (\d+) day", message, re.I) or re.search(r"(\d+) days? cooling", message, re.I)
         or re.search(r"(\d+) days?", message, re.I))
    return int(m.group(1)) if m else 30


def human_pause(page, seconds):
    """Wait like a person would (a little mouse movement and scrolling): reCAPTCHA Enterprise
    scores the behaviour before Submit, and an instant fill-and-click looks like a script."""
    import random
    end = time.time() + seconds
    while time.time() < end:
        try:
            page.mouse.move(random.randint(200, 900), random.randint(150, 600), steps=random.randint(8, 20))
            if random.random() < 0.4:
                page.mouse.wheel(0, random.choice([-120, 120, 200]))
        except Exception:
            pass
        page.wait_for_timeout(random.randint(500, 1100))


def submit(page, tries=4, transient_tries=2):
    """Click Submit and wait for the result. If Ashby answers "We're updating your application
    (e.g. uploading files), please try again when they're finished", wait and submit again.
    A reCAPTCHA / spam-filter error is retried up to `transient_tries` times."""
    import random
    transient = 0
    for attempt in range(1, tries + transient_tries + 1):
        wait_page_idle(page)
        if attempt == 1:
            human_pause(page, random.uniform(2.5, 5))
        page.locator(SUBMIT_BTN).first.click()
        print(f"    submitted{' (again, try ' + str(attempt) + ')' if attempt > 1 else ''}, "
              f"waiting for the answer (up to 60 s) ...")
        res = wait_outcome(page)
        msg = res.get("message") or ""
        if res["state"] == "error" and _TRY_AGAIN.search(msg):
            print("    Ashby: still uploading - waiting 8 s and submitting again")
            page.wait_for_timeout(8000)
            continue
        if res["state"] == "error" and _TRANSIENT.search(msg) and transient < transient_tries:
            transient += 1
            wait = 12 * transient
            print(f"    Ashby: bot check / spam filter said no ({msg[:70]!r}) - "
                  f"waiting {wait} s, then submitting again ({transient}/{transient_tries})")
            human_pause(page, wait)
            if not page.locator(SUBMIT_BTN).count():
                return res
            continue
        return res
    return res


FAIL_DIR = Path(__file__).resolve().parent / "data" / "failures"


def diagnose(page, job):
    """Unclear outcome: what does the page show? Saves a screenshot. Returns a short reason."""
    try:
        d = page.evaluate(_DIAG_JS)
    except Exception as e:
        return f"page could not be read ({str(e)[:80]})"
    parts = [f"submit button: {d['submit']}"]
    if d["messages"]:
        parts.append("page says: " + " | ".join(d["messages"]))
    if d["frames"]:
        parts.append("frames: " + ", ".join(d["frames"]))
    if not d["messages"]:
        parts.append(f"end of page: {d['end'][-160:]}")
    try:
        FAIL_DIR.mkdir(parents=True, exist_ok=True)
        shot = FAIL_DIR / f"{job['id']}.png"
        page.screenshot(path=str(shot), full_page=True)
        parts.append(f"screenshot: {shot}")
    except Exception:
        pass
    return " ; ".join(parts)


def banner(page, title, text, color="#c62828"):
    """A red (or orange) bar on top of the tab, so you see at once why it is still open."""
    try:
        page.evaluate(_BANNER_JS, [title, text, color])
        page.bring_to_front()
    except Exception:
        pass


# ---------------------------------------------------------------- jobs

def selected_companies():
    """Companies picked in the app's Jobs tab (data/app_settings.json); [] = all."""
    try:
        path = Path(__file__).resolve().parent / "data" / "app_settings.json"
        return json.loads(path.read_text(encoding="utf-8")).get("companies") or []
    except Exception:
        return []


def pick_jobs(conn, bank, only_jobs=None, companies=None):
    """Jobs whose required questions can all be answered right now.
    only_jobs: list of job ids (the jobs selected in the app) - each skipped one is explained."""
    sql = "SELECT * FROM jobs WHERE is_active=1 AND relevant=1 AND status IN ('ready','needs_you')"
    params = []
    if only_jobs:
        sql += f" AND id IN ({','.join('?' * len(only_jobs))})"
        params += only_jobs
        found = {r["id"] for r in conn.execute(sql.replace("SELECT *", "SELECT id"), params)}
        for jid in only_jobs:
            if jid not in found:
                row = conn.execute("SELECT title, status, relevant FROM jobs WHERE id=?", (jid,)).fetchone()
                why = "unknown job" if not row else (
                    "not relevant" if not row["relevant"] else f"status is '{row['status']}'")
                print(f"  skip {row['title'] if row else jid}: {why}")
    if companies:
        sql += f" AND company IN ({','.join('?' * len(companies))})"
        params += companies
    rows = conn.execute(sql + " ORDER BY published_at DESC", params).fetchall()

    ready, waiting_ai, blocked = [], 0, 0
    for job in rows:
        items = bank_mod.resolve_job(conn, job["id"], bank)
        missing = bank_mod.blocking(items)
        if not missing:
            ready.append((job, items))
            continue
        # only text/essay questions missing -> the AI writes them in prepare(); still bid
        if all(bank_mod.ai_writable(i) for i in missing):
            waiting_ai += 1
            ready.append((job, items))
            continue
        blocked += 1
        if only_jobs:
            print(f"  skip {job['title']}: " + "; ".join(
                f"{i['q']['title'][:50]} ({i['error'] or 'no answer'})" for i in missing[:4]
                if not bank_mod.ai_writable(i)))
    return ready, waiting_ai, blocked


BLOCKS_FILE = Path(__file__).resolve().parent / "data" / "org_blocks.json"


def load_blocks():
    """{org: {"until": ISO date, "why": text}} - companies that told us their limit is reached."""
    try:
        data = json.loads(BLOCKS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    now = datetime.now().isoformat()
    return {o: b for o, b in data.items() if b.get("until", "") > now}


def block_org(org, days, why):
    blocks = load_blocks()
    blocks[org] = {"until": (datetime.now() + timedelta(days=days)).isoformat(timespec="minutes"),
                   "why": " ".join(why.split())[:200]}
    BLOCKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    BLOCKS_FILE.write_text(json.dumps(blocks, indent=2), encoding="utf-8")


def within_limit(conn, org, planned):
    if org in load_blocks():
        return False
    limit = APPLY_LIMITS.get(org)
    if not limit:
        return True
    max_n, days = limit
    return db.applied_recently(conn, org, days) + planned.get(org, 0) < max_n


# ---------------------------------------------------------------- main loop

def tailored_resume(conn, ai, job):
    """The job's tailored resume PDF: made once, reused on a retry."""
    if job["resume_path"] and Path(job["resume_path"]).is_file():
        print(f"    resume: reusing {job['resume_path']}")
        return job["resume_path"]
    print(f"    writing tailored resume with {ai.provider} ...")
    started = time.time()
    pdf = make_resume(ai, db.job_for_ai(job), source="ashby")
    model = (getattr(ai, "last_model", None) or ai.model or "")
    db.set_resume(conn, job["id"], pdf, ai_provider=ai.provider, ai_model=model)
    print(f"    resume ready in {time.time() - started:.0f}s: {pdf}")
    return pdf


def write_essays(ai, job, items):
    """Fill missing String / LongText answers with one AI call per job. Mutates items in place.
    Answers are per job (not saved to the answer bank). Raises on AI failure."""
    todo = [i for i in items if bank_mod.ai_writable(i)]
    if not todo:
        return
    if ai is None:
        raise RuntimeError("AI is not set up, but this job needs essays written")
    print(f"    writing {len(todo)} text/essay answer(s) with {ai.provider} ...")
    started = time.time()
    job_info = db.job_for_ai(job)
    try:
        from resume import settings as resume_settings, job_block
        profile = json.dumps(resume_settings()["base"], indent=2, ensure_ascii=False)
        job_txt = job_block(job_info)
    except Exception:
        profile = json.dumps({k: v for k, v in (job_info or {}).items() if k != "jd"},
                             ensure_ascii=False)
        job_txt = f"Role: {job_info.get('role')}\nCompany: {job_info.get('company')}\n" \
                  f"<job_description>\n{(job_info.get('jd') or '')[:6000]}\n</job_description>"

    q_lines = []
    for n, item in enumerate(todo):
        q = item["q"]                       # sqlite3.Row (no .get)
        tip = "1-2 short sentences or a phrase" if q["type"] == "String" else \
              "a short essay, about 80-180 words, first person"
        desc = (q["description"] or "").strip()
        note = f"\n   hint under the question: {desc}" if desc else ""
        q_lines.append(f"{n}. [{q['type']}] {q['title']}{note}\n   write: {tip}")

    prompt = (
        "You are filling a job application form for the candidate below. Write truthful, "
        "specific answers that fit THIS job. Do not invent employers, degrees, dates, URLs or "
        "contact details that are not in the profile; for LinkedIn/GitHub/website/phone/email "
        "questions use the profile values (or say you prefer not to share if they are empty). "
        "Do not refuse; answer every question.\n\n"
        f"<candidate_profile>\n{profile}\n</candidate_profile>\n\n"
        f"{job_txt}\n\n"
        "<questions>\n" + "\n".join(q_lines) + "\n</questions>\n\n"
        'Reply with ONLY one JSON object inside a ```json code block, shaped like:\n'
        '{"0": "answer for question 0", "1": "answer for question 1", ...}\n'
        "Keys are the question numbers as strings. No text before or after the JSON."
    )
    data = ai.ask_json(prompt, required_keys=[str(n) for n in range(len(todo))])
    for n, item in enumerate(todo):
        text = data.get(str(n))
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError(f"AI returned no answer for: {item['q']['title'][:60]}")
        item["value"] = text.strip()
        item["missing"] = False
        item["source"] = f"AI ({ai.provider})"
        print(f"      · {item['q']['title'][:55]}: {text.strip()[:70].replace(chr(10), ' ')}"
              + ("…" if len(text.strip()) > 70 else ""))
    print(f"    essays ready in {time.time() - started:.0f}s")


def handle_error(conn, job, page, msg):
    """The page shows an error after Submit. Returns (page or None, state):
      company limit  -> job back to 'ready', company blocked for its period, tab closed (nothing to do)
      bot check/spam -> 'needs_you' (retried next run), tab kept: press Submit yourself
      other          -> 'failed', tab kept with the reason"""
    days = limit_days(msg)
    if days:
        block_org(job["org"], days, msg)
        until = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        db.set_status(conn, job["id"], "ready", f"company limit - not before {until}: {' '.join(msg.split())[:200]}")
        print(f"    COMPANY LIMIT - {job['org']} takes no more applications until {until}. "
              f"Its other jobs are skipped.  ({' '.join(msg.split())[:120]})")
        try:
            page.close()
        except Exception:
            pass
        return None, "limit"
    if _TRANSIENT.search(msg):
        db.set_status(conn, job["id"], "needs_you", f"bot check / spam filter: {msg}"[:500])
        print(f"    NOT SUBMITTED - the site refused it twice ({msg[:100]!r}). Tab kept: press Submit yourself.")
        banner(page, "the site's bot check refused the submit - press Submit yourself",
               msg + "\nIf it goes through, the bot marks it applied and closes the tab.", "#b35900")
        return page, "retry"
    db.set_status(conn, job["id"], "failed", msg)
    print(f"    FAILED - the page says: {msg}")
    banner(page, "NOT applied - the page shows an error", msg)
    return page, "failed"


def prepare(ctx, conn, ai, job, items, dry_run):
    """(Essays) + (Resume) + open + fill + submit one job. Returns (page, state)."""
    print(f"\n- {job['title']}  ({job['id']})")
    try:
        write_essays(ai, job, items)
    except Exception as e:
        # an AI problem is not the job's fault: it stays 'ready' and is tried again
        msg = f"essays not written: {str(e).splitlines()[0][:200]}"
        print(f"    ! {msg} (job stays ready)")
        conn.execute("UPDATE jobs SET note=? WHERE id=?", (msg, job["id"]))
        conn.commit()
        return None, "skipped"
    for item in items:
        if item["value"] == bank_mod.TAILORED_PLACEHOLDER:
            try:
                item["value"] = tailored_resume(conn, ai, job)
            except Exception as e:
                # an AI problem is not the job's fault: it stays 'ready' and is tried again
                msg = f"resume not written: {str(e).splitlines()[0][:200]}"
                print(f"    ! {msg} (job stays ready)")
                conn.execute("UPDATE jobs SET note=? WHERE id=?", (msg, job["id"]))
                conn.commit()
                return None, "skipped"
    page = ctx.new_page()
    page.goto(job["apply_url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector(FORM_READY, timeout=45000)
    page.wait_for_timeout(800)

    problems = fill_form(page, items)
    if problems:
        for p in problems:
            print(f"    ! {p}")
        banner(page, "some fields could not be filled - NOT submitted", "\n".join(problems)[:600])
        if dry_run:   # a test run never changes the job's status
            print("    dry run: some fields could not be filled (tab left open)")
            return page, "dry"
        db.set_status(conn, job["id"], "failed", "fill: " + " | ".join(problems)[:500])
        return page, "failed"
    for i in items:
        db.save_answer(conn, i["q"]["id"], i["value"])
    conn.commit()
    print(f"    filled {len(items)} required fields")

    if dry_run:
        print("    dry run: not submitted (tab left open)")
        banner(page, "dry run - filled, NOT submitted", "Check the answers. Close this tab when done.", "#1f4fbf")
        return page, "dry"

    res = submit(page)
    if res["state"] == "success":
        db.set_status(conn, job["id"], "applied")
        print("    APPLIED")
        page.close()
        return None, "applied"
    if res["state"] == "captcha":
        db.set_status(conn, job["id"], "needs_you", "bot check after submit")
        print("    bot check -> waiting for you (tab kept open)")
        return page, "needs_you"
    if res["state"] == "error":
        return handle_error(conn, job, page, res.get("message") or "error after submit")
    # no success, no error, no bot check: say what the page shows, keep the tab for you
    why = diagnose(page, job)
    db.set_status(conn, job["id"], "needs_you", f"no confirmation after submit: {why}"[:900])
    print(f"    UNCLEAR - no confirmation within 60 s. {why}")
    banner(page, "no confirmation after Submit - please check this tab",
           f"{why[:500]}\nIf it was submitted, close the tab. If not, fix it and press Submit yourself.",
           "#b35900")
    return page, "unclear"


def check_watch(conn, watch):
    """Tabs you were asked to finish yourself (press Submit): when one goes through,
    mark it applied and close it."""
    for job_id, (job, page) in list(watch.items()):
        if page.is_closed():
            del watch[job_id]
            continue
        if outcome(page)["state"] == "success":
            db.set_status(conn, job_id, "applied")
            print(f"  APPLIED (by you): {job['title']}")
            page.close()
            del watch[job_id]


def wait_for_you(conn, pending, watch=None):
    """pending: {job_id: (job, page)}. Finishes each tab as you solve its bot check."""
    if watch:
        check_watch(conn, watch)
    if not pending:
        return
    notify("AutoBid: bot checks waiting",
           f"{len(pending)} application(s) are filled and waiting for you. "
           f"Solve the bot check in each tab - they submit by themselves.")
    first = next(iter(pending.values()))[1]
    try:
        first.bring_to_front()
    except Exception:
        pass

    deadline = time.time() + BATCH_WAIT_MIN * 60
    resubmitted = set()
    while pending and time.time() < deadline:
        time.sleep(1.5)
        if stop_requested():
            print("  stop requested - not waiting for the bot checks any longer")
            break
        if watch:
            check_watch(conn, watch)
        for job_id, (job, page) in list(pending.items()):
            if page.is_closed():            # you closed it yourself
                print(f"  closed by you: {job['title']} (stays 'needs_you')")
                del pending[job_id]
                continue
            res = outcome(page)
            if res["state"] == "success":
                db.set_status(conn, job_id, "applied")
                print(f"  APPLIED: {job['title']}")
                page.close()
                del pending[job_id]
                if pending:   # show the next one
                    next(iter(pending.values()))[1].bring_to_front()
            elif res["state"] == "error":
                print(f"  {job['title']}:")
                kept, state = handle_error(conn, job, page, res.get("message") or "error after submit")
                if state == "retry" and watch is not None:
                    watch[job_id] = (job, page)
                del pending[job_id]          # the tab stays open (kept by main)
            elif res["state"] == "pending" and captcha_solved(page) and job_id not in resubmitted:
                # solved, but the form did not go through by itself -> submit again once
                resubmitted.add(job_id)
                try:
                    page.locator(SUBMIT_BTN).first.click(timeout=3000)
                except Exception:
                    pass
    if pending:
        notify("AutoBid: moving on",
               f"{len(pending)} bot check(s) not solved in {BATCH_WAIT_MIN} min. "
               f"They stay 'needs_you' and are retried next run.")


def _has_banner(page):
    try:
        return not page.is_closed() and page.evaluate("() => !!document.getElementById('autobid-banner')")
    except Exception:
        return False


def wait_until_closed(ctx, pages, dry_run, conn=None, watch=None):
    """Never close the browser while a tab needs you: wait until you closed those tabs
    (or the whole browser window). The app's Stop button ends it at any time."""
    pages = [p for p in dict.fromkeys(pages) if p and not p.is_closed()]
    if not pages:
        return
    what = "Dry run done: forms are filled, nothing was submitted." if dry_run else \
        f"{len(pages)} tab(s) need you - each has a coloured bar with the reason."
    notify("AutoBid: check the browser", what + " The browser stays open until you close them.")
    print(f"\n{what}\nThe browser stays open until you close these tabs (or the browser window):")
    for p in pages:
        try:
            print(f"   - {p.title()[:70]}  {p.url}")
        except Exception:
            pass
    pages[0].bring_to_front()
    while True:
        try:
            if all(p.is_closed() for p in pages) or not ctx.pages:
                break
            if stop_requested():
                print("Stop requested - closing the browser.")
                break
            ctx.pages[0].wait_for_timeout(1000)   # keeps the browser responsive
            if watch:                             # you pressed Submit yourself and it went through
                check_watch(conn, watch)
        except Exception:                         # browser window closed
            break
    print("All tabs closed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="fill forms, do not submit")
    parser.add_argument("--job", action="append", help="only this job id (repeatable)")
    parser.add_argument("--jobs-file", help="JSON file with a list of job ids (the app's selection)")
    parser.add_argument("--batch", type=int, default=BATCH_SIZE)
    parser.add_argument("--ai", help="AI platform for the tailored resume (default: AI tab settings)")
    parser.add_argument("--model", help="AI model (default: AI tab settings)")
    parser.add_argument("--selected-companies", action="store_true",
                        help="only the companies selected in the app's Jobs tab")
    args = parser.parse_args()

    conn = db.connect()
    bank = bank_mod.load_bank()
    bank_mod.sync_bank(conn, bank)
    bank_mod.save_bank(bank)

    companies = selected_companies() if args.selected_companies else None
    if companies:
        print(f"Only {len(companies)} selected companies: {', '.join(companies[:8])}"
              + (" ..." if len(companies) > 8 else ""))
    only_jobs = list(args.job or [])
    if args.jobs_file:
        only_jobs += json.loads(Path(args.jobs_file).read_text(encoding="utf-8"))
    if only_jobs:
        print(f"{len(only_jobs)} selected job(s)")
    ready, waiting_ai, blocked = pick_jobs(conn, bank, only_jobs or None, companies)
    print(f"{len(ready)} jobs ready ({waiting_ai} with AI essays to write) | "
          f"{blocked} need answers in the answer bank")

    planned, queue = {}, []
    for job, items in ready:
        if args.dry_run or within_limit(conn, job["org"], planned):
            planned[job["org"]] = planned.get(job["org"], 0) + 1
            queue.append((job, items))
    if len(queue) < len(ready):
        print(f"{len(ready) - len(queue)} skipped because of company application limits {APPLY_LIMITS}")
    if not queue:
        return

    # AI is needed for tailored resumes and/or per-job essay answers
    need_ai = TAILORED_RESUME or waiting_ai > 0
    provider = args.ai or ai_settings.get("resume")[0]
    # setup check BEFORE touching any job: a missing AI tool must not fail jobs
    if need_ai and provider in ai_settings.CLI_PROVIDERS:
        from web_ai import CLI, find_cli
        if not find_cli(CLI[provider][0]):
            print(f"\nSTOPPED: resumes/essays are set to be written by '{provider}', but its program "
                  f"'{CLI[provider][0]}' is not installed on this PC.\n"
                  f"Choose another platform in the app's AI tab (Claude Code works here). "
                  f"No job was touched.")
            notify("AutoBid: not started", f"'{provider}' is not installed - change it in the AI tab.")
            return
    problem = ai_settings.api_key_problem(provider) if need_ai else None
    if problem:
        print(f"\nSTOPPED: resumes/essays are set to be written by '{provider}', but {problem}.\n"
              f"No job was touched.")
        notify("AutoBid: not started", f"{provider}: {problem}")
        return

    with sync_playwright() as pw:
        ctx = open_browser(pw)
        ai = ai_settings.make_ai("resume", ctx, args.ai, args.model) if need_ai else None
        if ai:
            print(f"Resumes and essays are written by {ai.label}")
        keep_open = []      # tabs you should look at: errors, unclear, dry runs, unsolved bot checks
        watch = {}          # tabs you finish by hand: when one goes through it is marked applied
        try:
            try:
                for start in range(0, len(queue), args.batch):
                    if stop_requested():
                        break
                    batch = queue[start:start + args.batch]
                    print(f"\n=== Batch {start // args.batch + 1}: {len(batch)} jobs ===")
                    pending = {}
                    for job, items in batch:
                        if stop_requested():
                            print("\n⏸ Stopped after the current job, as you asked. "
                                  "Jobs not started yet stay 'ready'.")
                            break
                        if not args.dry_run and job["org"] in load_blocks():
                            print(f"\n- {job['title']}: skipped, {job['org']} reached its application limit")
                            continue
                        page, state = None, None
                        tabs_before = list(ctx.pages)
                        try:
                            page, state = prepare(ctx, conn, ai, job, items, args.dry_run)
                        except Exception as e:
                            # whatever went wrong with this job, never close the browser
                            # over it: the tab stays open for you
                            if not ctx.pages:
                                raise            # the browser window was closed by hand
                            msg = ("form did not load" if isinstance(e, PWTimeout)
                                   else f"bot error: {str(e).splitlines()[0][:200]}")
                            db.set_status(conn, job["id"], "failed", msg)
                            print(f"    failed: {msg}")
                            if not isinstance(e, PWTimeout):
                                traceback.print_exc()
                            for p in ctx.pages:
                                if p not in tabs_before:
                                    banner(p, "NOT applied - the bot hit an error", msg)
                                    keep_open.append(p)
                            continue
                        if state == "needs_you":
                            pending[job["id"]] = (job, page)
                        elif page is not None and state in ("failed", "unclear", "dry", "retry"):
                            keep_open.append(page)
                            if state in ("unclear", "retry"):
                                watch[job["id"]] = (job, page)
                    wait_for_you(conn, pending, watch)
                    keep_open += [p for _, p in pending.values()]
                    keep_open += [p for p in ctx.pages if p not in keep_open and _has_banner(p)]
            except Exception as e:
                print(f"\n! The run ended early: {e}")
                traceback.print_exc()
            keep_open += [p for p in ctx.pages if p not in keep_open and _has_banner(p)]
            wait_until_closed(ctx, keep_open, args.dry_run, conn, watch)
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    row = conn.execute("SELECT SUM(status='applied') a, SUM(status='needs_you') n, "
                       "SUM(status='failed') f FROM jobs").fetchone()
    print(f"\nTotal: {row['a'] or 0} applied | {row['n'] or 0} waiting for bot check | "
          f"{row['f'] or 0} failed")
    try:                                   # statuses to Supabase, so every computer sees them
        import sync_db
        sync_db.sync(conn, quiet=True)
        print("Statuses synced with Supabase.")
    except Exception as e:
        print(f"! Supabase sync skipped: {str(e)[:200]}  (click 'Sync to DB' later)")


if __name__ == "__main__":
    main()
