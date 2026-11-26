"""Apply to MyGreenhouse / boards.greenhouse.io jobs.

    python bid.py --dry-run --jobs-file ids.json
    python bid.py --jobs-file ids.json

Flow (from your screenshots):
  1. Open remembered Apply link (Picture 3)
  2. Fill First/Last/Email/Phone + other fields from Answers / My profile
  3. Delete the pre-uploaded base resume (X) and upload the AI-tailored PDF (Picture 4)
  4. Click Submit application
  5. If Greenhouse shows the 8-character email security code step, read it from
     Gmail in the separate mail Chrome (.browser-profile-mail) and submit again

Apply always opens TWO Playwright Chromes: Greenhouse bid + mail (Gmail).
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

import answers as bank_mod
import db
import greenhouse_browser as gh
from browser import open_mail_browser, profile_busy
from config import (BATCH_SIZE, BATCH_WAIT_MIN, TAILORED_RESUME,
                    stop_requested, wait_while_paused)
from mail_code import wait_gmail_login
from notify import notify
from resume import make_resume
import ai_settings

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_jobs(conn, ids):
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    return conn.execute(
        f"SELECT * FROM jobs WHERE id IN ({ph}) AND is_active=1 "
        f"ORDER BY COALESCE(published_at, first_seen_at) DESC",
        ids).fetchall()


def tailored_resume(conn, ai, job):
    if job["resume_path"] and Path(job["resume_path"]).is_file():
        print(f"    resume: reusing {job['resume_path']}")
        return job["resume_path"]
    print(f"    writing tailored resume with {ai.provider} ...")
    started = time.time()
    pdf = make_resume(ai, db.job_for_ai(job), source="greenhouse")
    model = (getattr(ai, "last_model", None) or ai.model or "")
    db.set_resume(conn, job["id"], pdf, ai_provider=ai.provider, ai_model=model)
    print(f"    resume ready in {time.time() - started:.0f}s: {pdf}")
    return pdf


def write_essays(ai, job, items):
    """Fill missing String / LongText answers with one AI call per job. Mutates items.

    Same idea as Ashby: Answers filter “✓ all (ready to apply)” includes jobs whose
    only gaps are AI-writable text. Without this, salary / mailing / essays stay blank
    and Greenhouse validation fails after Submit.
    """
    todo = [i for i in items if bank_mod.ai_writable(i)]
    if not todo:
        return
    if ai is None:
        raise RuntimeError("AI is not set up, but this job needs text answers written")
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
        job_txt = (
            f"Role: {job_info.get('role')}\nCompany: {job_info.get('company')}\n"
            f"<job_description>\n{(job_info.get('jd') or '')[:6000]}\n</job_description>"
        )
    try:
        from web_ai import PROVIDERS
        if ai.provider in PROVIDERS and len(profile) > 12000:
            profile = profile[:12000] + "\n…[profile truncated]"
        if ai.provider in PROVIDERS and len(job_txt) > 7000:
            job_txt = job_txt[:7000] + "\n…[job truncated]"
    except Exception:
        pass

    q_lines = []
    keys = [str(n) for n in range(len(todo))]
    for n, item in enumerate(todo):
        q = item["q"]
        qtype = q["type"] if not hasattr(q, "get") else (q.get("type") or "String")
        title = q["title"] if not hasattr(q, "get") else (q.get("title") or "")
        desc = ""
        try:
            desc = (q["description"] or "").strip()
        except Exception:
            desc = (q.get("description") or "").strip() if hasattr(q, "get") else ""
        tip = ("1-2 short sentences or a phrase" if qtype == "String"
               else "a short essay, about 80-180 words, first person")
        if re.search(r"salary|compensation|pay", title or "", re.I):
            tip = "a concrete salary figure or range with currency (e.g. 120000 USD or $120k)"
        if re.search(r"address|mailing", title or "", re.I):
            tip = "a full mailing address from the profile if present; otherwise a plausible US address format"
        note = f"\n   hint under the question: {desc}" if desc else ""
        q_lines.append(
            f'{n}. key="{n}" [{qtype}] {title}{note}\n   write: {tip}')

    example = "{" + ", ".join(f'"{k}": "your answer for question {k}"' for k in keys) + "}"
    prompt = (
        "You are filling a Greenhouse job application form for the candidate below. Write "
        "truthful, specific answers that fit THIS job. Do not invent employers, degrees, dates, "
        "URLs or contact details that are not in the profile; for LinkedIn/GitHub/website/phone/"
        "email/address/salary questions use the profile values when present. "
        "Do not refuse; answer every question.\n\n"
        f"<candidate_profile>\n{profile}\n</candidate_profile>\n\n"
        f"{job_txt}\n\n"
        "<questions>\n" + "\n".join(q_lines) + "\n</questions>\n\n"
        "Reply with ONLY one JSON object inside a ```json code block.\n"
        f"Required keys (strings): {', '.join(keys)}\n"
        f"Exact shape:\n{example}\n"
        "Do not use an array. Do not nest objects. Do not use question titles as keys. "
        "No text before or after the JSON."
    )
    data = ai.ask_json(prompt, required_keys=keys)
    for n, item in enumerate(todo):
        text = data.get(str(n))
        q = item["q"]
        title = (q["title"] if isinstance(q, dict) else q["title"]) or ""
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError(f"AI returned no answer for: {title[:60]}")
        item["value"] = text.strip()
        item["missing"] = False
        item["source"] = f"AI ({ai.provider})"
        print(f"      · {title[:55]}: {text.strip()[:70].replace(chr(10), ' ')}"
              + ("…" if len(text.strip()) > 70 else ""))
    print(f"    essays ready in {time.time() - started:.0f}s")


def _blocking_non_ai(items):
    return [i for i in bank_mod.blocking(items) if not bank_mod.ai_writable(i)]


def _qget(q, key, default=""):
    try:
        if isinstance(q, dict):
            return q.get(key, default)
        v = q[key]
        return default if v is None else v
    except Exception:
        return default


def _truthy(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "y")


_LOCATION_TITLES = re.compile(
    r"^(location(\s*\(city\))?|city|current (city|location))$", re.I)


def _fill_select_or_radio(page, title: str, value) -> bool:
    """Native <select>, radio Yes/No, or react-select for a labelled question."""
    choice = str(value).strip()
    if not choice:
        return False
    # Yes/No radios / checkboxes
    if choice.lower() in ("yes", "no", "true", "false", "1", "0"):
        yn = "Yes" if _truthy(choice) else "No"
        try:
            group = page.locator(f"text=/{re.escape(title)}/i").locator(
                "xpath=ancestor::*[self::div or self::fieldset or self::label][1]")
            group.get_by_label(yn, exact=True).check(timeout=1500)
            return True
        except Exception:
            pass
    # Native <select>
    try:
        lab = page.get_by_label(re.compile(re.escape(title), re.I)).first
        tag = (lab.evaluate("el => el.tagName") or "").lower()
        if tag == "select":
            try:
                lab.select_option(label=choice, timeout=2000)
                return True
            except Exception:
                lab.select_option(value=choice, timeout=1500)
                return True
    except Exception:
        pass
    # React-select / combobox near the label
    try:
        root = page.locator(f"text=/{re.escape(title)}/i").locator(
            "xpath=ancestor::*[self::div or self::fieldset][1]")
        box = root.locator(
            "input[role=combobox], .select__control, [class*='select__control']").first
        if box.count():
            box.click(timeout=2000)
            page.wait_for_timeout(250)
            try:
                root.locator("input").first.type(choice, delay=25)
            except Exception:
                page.keyboard.type(choice, delay=25)
            page.wait_for_timeout(400)
            opt = page.locator(
                f'[role=option]:has-text("{choice}"), .select__option:has-text("{choice}")'
            ).first
            if opt.count():
                opt.click(timeout=2500)
                return True
            page.keyboard.press("Enter")
            return True
    except Exception:
        pass
    return False


def _fill_labelled_text(page, title: str, value) -> bool:
    """Fill input/textarea for a Greenhouse question label (long titles OK)."""
    text = str(value)
    if not text or not title:
        return False
    # 1) Accessible label
    try:
        page.get_by_label(re.compile(re.escape(title), re.I)).fill(text, timeout=2500)
        return True
    except Exception:
        pass
    # 2) Partial label match (first ~48 chars) — salary/address questions are long
    needle = title[:48]
    try:
        lab = page.locator("label").filter(has_text=re.compile(re.escape(needle), re.I)).first
        if lab.count():
            el = None
            try:
                fid = lab.get_attribute("for")
                if fid:
                    el = page.locator(f"#{fid}, [id={json.dumps(fid)}]").first
            except Exception:
                el = None
            if not el or not el.count():
                wrap = lab.locator(
                    "xpath=ancestor::*[self::div or self::fieldset or self::li][1]")
                el = wrap.locator(
                    "textarea, input:not([type=hidden]):not([type=file]):not([type=checkbox]):not([type=radio])"
                ).first
            if el and el.count():
                el.click(timeout=2000)
                el.fill("")
                el.fill(text, timeout=2500)
                return True
    except Exception:
        pass
    # 3) JS: find label containing title, set nearest control
    try:
        ok = page.evaluate(
            """([title, value]) => {
              const labs = Array.from(document.querySelectorAll('label'));
              const t = title.toLowerCase();
              const lab = labs.find(l => (l.innerText || '').toLowerCase().includes(t.slice(0, 40)));
              if (!lab) return false;
              let el = null;
              if (lab.htmlFor) el = document.getElementById(lab.htmlFor);
              if (!el) el = lab.querySelector('textarea,input:not([type=hidden]):not([type=file])');
              if (!el) {
                const wrap = lab.closest('div,fieldset,li') || lab.parentElement;
                el = wrap && wrap.querySelector(
                  'textarea,input:not([type=hidden]):not([type=file]):not([type=checkbox]):not([type=radio])');
              }
              if (!el) return false;
              el.focus();
              el.value = value;
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
              return true;
            }""",
            [title, text],
        )
        return bool(ok)
    except Exception:
        return False


def fill_from_profile_and_bank(page, items, profile):
    """Fill Greenhouse apply form fields (classic boards + MyGreenhouse modal)."""
    # Build lookup
    by_title = {}
    for item in items:
        if bank_mod._is_empty(item.get("value")) or item["value"] == bank_mod.TAILORED_PLACEHOLDER:
            continue
        t = (item["q"].get("title") if isinstance(item["q"], dict) else item["q"]["title"] or "")
        by_title[t.strip().lower().rstrip("?:*").strip()] = item["value"]

    # Name / email / phone / Location (City) typeahead from profile
    gh.fill_basic_fields(page, profile)
    # Profile mailing address / salary when present (also in items when mapped)
    if profile.get("address") and "please provide your mailing address" not in by_title:
        by_title["please provide your mailing address"] = profile["address"]
    if profile.get("salary"):
        # Any salary-ish title without a value yet gets the profile salary
        for item in items:
            title = (_qget(item["q"], "title") or "").strip().lower().rstrip("?:*").strip()
            if title and re.search(r"salary|compensation", title) and title not in by_title:
                by_title[title] = profile["salary"]

    filled_n = 0
    # Extra text / select / radio from bank
    for title, value in by_title.items():
        if title in ("first name", "last name", "email", "phone", "resume/cv", "resume"):
            continue
        # Location (City) is a geocode combobox — never plain fill()
        if _LOCATION_TITLES.match(title):
            if gh.fill_location_city(page, str(value)):
                filled_n += 1
            continue
        if _fill_select_or_radio(page, title, value):
            filled_n += 1
            continue
        if _fill_labelled_text(page, title, value):
            filled_n += 1
            print(f"    · filled: {title[:60]}")
            continue
        print(f"    ! could not fill: {title[:60]}")

    # Common authorization / sponsorship from profile when present
    for label, key in (
        (r"legally authorized to work", "authorized_us"),
        (r"require sponsorship", "sponsorship"),
    ):
        if key not in profile:
            continue
        choice = "Yes" if _truthy(profile.get(key)) else "No"
        try:
            group = page.locator(f"text=/{label}/i").locator(
                "xpath=ancestor::*[self::div or self::fieldset][1]")
            group.get_by_label(choice, exact=True).check(timeout=2000)
        except Exception:
            pass
    # Ensure location again after other fills (some boards re-render the form)
    if profile.get("location"):
        gh.fill_location_city(page, profile["location"])
    if filled_n:
        print(f"    filled {filled_n} custom field(s)")


def _has_required_field_errors(page) -> bool:
    try:
        return bool(page.locator("text=/This field is required/i").count())
    except Exception:
        return False


def board_apply_url(job) -> str:
    """Direct Greenhouse job page: job-boards.greenhouse.io/{token}/jobs/{id}[#app].

    Prefer scraped apply_url; rebuild from org + gh id when needed. Never use the
    company careers listing alone.
    """
    raw = (job["apply_url"] or job["job_url"] or "").strip()
    org = (job["org"] or "").strip()
    # id is stored as "token:12345"
    jid = ""
    sid = str(job["id"] or "")
    if ":" in sid:
        token, jid = sid.split(":", 1)
        org = org or token
    if org and jid and jid.isdigit():
        return f"https://job-boards.greenhouse.io/{org}/jobs/{jid}#app"
    if "/jobs/" in raw:
        # Ensure we land on the application form section when possible
        if "#app" not in raw and "job-boards.greenhouse.io" in raw:
            return raw.split("#")[0] + "#app"
        return raw
    return raw


def apply_one(ctx, conn, ai, job, items, dry_run=False, mail_page=None):
    print(f"\n- {job['title']}  ({job['company'] or job['org']})  [{job['id']}]")
    apply_url = board_apply_url(job)
    if not apply_url:
        print("    ! no Apply link remembered — skip")
        db.set_status(conn, job["id"], "failed", "no apply_url")
        return "failed"
    print(f"    apply link: {apply_url}")

    # Resolve tailored resume path into File items
    pdf = None
    for item in items:
        if item.get("value") == bank_mod.TAILORED_PLACEHOLDER:
            try:
                pdf = tailored_resume(conn, ai, job)
                item["value"] = pdf
            except Exception as e:
                msg = f"resume not written: {str(e).splitlines()[0][:200]}"
                print(f"    ! {msg} (job stays ready)")
                conn.execute("UPDATE jobs SET note=? WHERE id=?", (msg, job["id"]))
                conn.commit()
                return "skipped"
    if TAILORED_RESUME and not pdf:
        # still make one even if questions table had no File row
        try:
            pdf = tailored_resume(conn, ai, job)
        except Exception as e:
            print(f"    ! resume: {e}")
            return "skipped"

    page = ctx.new_page()
    try:
        # Go straight to the scraped job-board apply page (not MyGreenhouse browse).
        page.goto(apply_url, wait_until="domcontentloaded", timeout=120_000)
        gh.dismiss_cookies(page)
        page.wait_for_timeout(1200)
        # Some boards hide the form until "Apply" is clicked
        if not page.locator(
                "form#application-form, form[id*='application'], "
                "#first_name, input[name='job_application[first_name]'], "
                "#security-input-0").count():
            for text in ("Apply", "Apply for this job", "Submit application"):
                try:
                    btn = page.locator(f"a:has-text('{text}'), button:has-text('{text}')").first
                    if btn.count() and btn.is_visible():
                        btn.click(timeout=2500)
                        page.wait_for_timeout(1000)
                        break
                except Exception:
                    continue

        # Discover live form fields (salary / mailing / custom) → save → re-resolve
        fields = gh.extract_apply_form_fields(page) or []
        if fields:
            db.save_questions(conn, job["id"], fields)
            conn.commit()
            try:
                bank_mod.sync_bank(conn, bank_mod.load_bank())
            except Exception:
                pass
            bank = bank_mod.load_bank()
            items = bank_mod.resolve_job(conn, job["id"], bank)
            # Re-apply tailored resume placeholder → concrete PDF path
            for item in items:
                if item.get("value") == bank_mod.TAILORED_PLACEHOLDER and pdf:
                    item["value"] = pdf

        blocked = _blocking_non_ai(items)
        if blocked:
            why = "; ".join(
                f"{_qget(i['q'], 'title')[:50]} (no answer)" for i in blocked[:4])
            print(f"    ! missing answers (not AI-writable): {why} — tab left open")
            if not dry_run:
                db.set_status(conn, job["id"], "needs_you", f"missing answers: {why}"[:500])
            return "needs_you" if not dry_run else "dry_run"

        try:
            write_essays(ai, job, items)
        except Exception as e:
            msg = f"essays not written: {str(e).splitlines()[0][:200]}"
            print(f"    ! {msg} (tab left open)")
            if not dry_run:
                db.set_status(conn, job["id"], "needs_you", msg)
            return "needs_you" if not dry_run else "dry_run"

        profile = bank_mod.load_bank()["profile"]
        fill_from_profile_and_bank(page, items, profile)

        # Picture 4: remove base resume, upload tailored PDF
        if pdf:
            ok = gh.upload_resume(page, str(pdf))
            if not ok:
                print("    ! resume upload failed — tab left open")
                if not dry_run:
                    db.set_status(conn, job["id"], "needs_you", "resume upload failed")
                    return "needs_you"
                return "dry_run"

        if dry_run:
            print("    dry-run: filled, not submitting (tab left open)")
            return "dry_run"

        # Submit click alone is NOT success — success = URL changes afterward.
        url_before = page.url or ""
        if not gh.click_submit(page):
            print("    ! Submit application button not found — tab left open")
            db.set_status(conn, job["id"], "needs_you", "submit button not found")
            return "needs_you"

        outcome = gh.wait_after_submit(page, url_before, timeout_ms=20_000)

        # Validation left us on the same URL (empty salary/address etc.) — refill + one retry
        if outcome == "unchanged" and _has_required_field_errors(page):
            print("    form still has required-field errors — refill + Submit again …")
            fill_from_profile_and_bank(page, items, profile)
            page.wait_for_timeout(600)
            url_before = page.url or ""
            if gh.click_submit(page):
                outcome = gh.wait_after_submit(page, url_before, timeout_ms=20_000)

        if outcome == "confirmed":
            db.set_status(conn, job["id"], "applied", "url changed after submit")
            print(f"    ✓ applied (url changed → {page.url})")
            page.close()
            return "applied"

        if outcome == "email_verify" or gh.has_email_verification(page):
            company = (job["company"] or job["org"] or "").strip()
            ok = gh.handle_email_verification(
                page, mail_page, company=company, wait_sec=90)
            if ok:
                db.set_status(conn, job["id"], "applied",
                              "url changed after email code")
                print(f"    ✓ applied (email code + url changed → {page.url})")
                page.close()
                return "applied"
            db.set_status(conn, job["id"], "needs_you", "email security code")
            print("    ? email security code — finish in Greenhouse Chrome (tab left open)")
            return "needs_you"

        # Still on the same URL → do not mark applied; keep tab for you
        why = "submit clicked but url did not change"
        if _has_required_field_errors(page):
            why = "submit blocked — required fields still empty"
        db.set_status(conn, job["id"], "needs_you", why)
        print(f"    ? {why} — tab left open (still {page.url})")
        return "needs_you"
    except Exception as e:
        print(f"    ! {e} — tab left open")
        db.set_status(conn, job["id"], "needs_you", str(e)[:300])
        # Do not close the page — you finish / inspect in Greenhouse Chrome
        return "needs_you"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jobs-file", help="JSON list of job ids")
    parser.add_argument("--job", action="append", default=[], help="job id (repeatable)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch", type=int, default=BATCH_SIZE)
    parser.add_argument("--ai", help="AI provider override")
    parser.add_argument("--model", help="AI model override")
    args = parser.parse_args(argv)

    ids = list(args.job or [])
    if args.jobs_file:
        ids += json.loads(Path(args.jobs_file).read_text(encoding="utf-8"))
    ids = list(dict.fromkeys(ids))
    if not ids:
        sys.exit("Pass --job <id> or --jobs-file selection.json")

    conn = db.connect()
    jobs = load_jobs(conn, ids)
    if not jobs:
        sys.exit("No matching jobs in DB.")

    bank = bank_mod.load_bank()
    bank_mod.sync_bank(conn, bank)

    # AI needed for tailored resumes and/or missing String/LongText answers
    need_ai = bool(TAILORED_RESUME)
    for job in jobs:
        items = bank_mod.resolve_job(conn, job["id"], bank)
        if any(bank_mod.ai_writable(i) for i in items):
            need_ai = True
            break
    ai = None
    problem = ai_settings.api_key_problem(args.ai or ai_settings.get("resume")[0]) if need_ai else None
    if problem and need_ai:
        print(f"Note: {problem} — will reuse existing resume_path when present; "
              f"text answers still need AI or Answers/profile.")

    applied = failed = needs = skipped = 0
    need_mail = not args.dry_run
    if need_mail and profile_busy("mail"):
        sys.exit(
            "Mail Chrome (.browser-profile-mail) is already open.\n"
            "Close “Open mail browser” first — Apply needs that profile to read "
            "Greenhouse security codes from Gmail.")
    with sync_playwright() as pw:
        mail_ctx = None
        mail_page = None
        if need_mail:
            print("Opening Greenhouse Chrome + mail Chrome (Gmail for security codes) …")
        else:
            print("Opening Greenhouse Chrome (dry-run — mail Chrome skipped) …")
        ctx = gh.open_ctx(pw)
        if need_mail:
            mail_ctx = open_mail_browser(pw)
            mail_page = mail_ctx.new_page()
            try:
                print("Checking Gmail in mail Chrome …")
                if not wait_gmail_login(mail_page, timeout_sec=300):
                    raise RuntimeError(
                        "Gmail not signed in on mail Chrome.\n"
                        "Use “Open mail browser”, sign in with the address you put on "
                        "applications, close that window, then Apply again.")
            except Exception as e:
                try:
                    mail_ctx.close()
                except Exception:
                    pass
                try:
                    ctx.close()
                except Exception:
                    pass
                sys.exit(str(e))

        # Apply uses scraped job-boards …/jobs/<id> URLs — no MyGreenhouse browse/login.
        ai = None
        if need_ai:
            from web_ai import PROVIDERS
            provider = args.ai or ai_settings.get("resume")[0]
            # browser AI needs the Greenhouse context
            ai = ai_settings.make_ai("resume", ctx if provider in PROVIDERS else None,
                                     args.ai, args.model)

        batch = max(1, args.batch)
        keep_open = False  # True once any bid did not fully finish in-browser
        try:
            for n, job in enumerate(jobs, 1):
                if wait_while_paused() or stop_requested():
                    print("Stopped.")
                    keep_open = True
                    break
                items = bank_mod.resolve_job(conn, job["id"], bank)
                # profile name/email required for Greenhouse forms
                profile = bank["profile"]
                if bank_mod._is_empty(profile.get("email")) or (
                        bank_mod._is_empty(profile.get("name"))
                        and bank_mod._is_empty(profile.get("first_name"))):
                    print(f"  ! fill Answers / My profile (name + email) before Apply")
                    keep_open = True
                    break
                blocked = _blocking_non_ai(items)
                if blocked:
                    why = "; ".join(
                        f"{_qget(i['q'], 'title')[:50]}" for i in blocked[:4])
                    print(f"  skip {job['title']}: missing answers — {why}")
                    skipped += 1
                    continue
                state = apply_one(
                    ctx, conn, ai, job, items, dry_run=args.dry_run, mail_page=mail_page)
                if state == "applied":
                    applied += 1
                elif state == "needs_you":
                    needs += 1
                    keep_open = True
                elif state == "dry_run":
                    skipped += 1
                    keep_open = True  # dry-run tabs stay for review
                elif state == "skipped":
                    skipped += 1
                else:
                    failed += 1
                    keep_open = True
                # Optional cool-down only when BATCH_WAIT_MIN > 0 (default is 0 = no pause)
                wait_sec = max(0, int(BATCH_WAIT_MIN * 60))
                if wait_sec > 0 and n % batch == 0 and n < len(jobs):
                    print(f"  batch pause {BATCH_WAIT_MIN} min "
                          f"(finished {n}/{len(jobs)}; next job after cool-down) …")
                    end = time.time() + wait_sec
                    last_left = None
                    while time.time() < end:
                        if stop_requested() or wait_while_paused():
                            keep_open = True
                            break
                        left = int(end - time.time())
                        if last_left is None or left // 15 != last_left // 15:
                            print(f"  batch pause … {max(0, left)}s left "
                                  f"({n}/{len(jobs)} done)")
                            last_left = left
                        time.sleep(1)
                elif n < len(jobs):
                    print(f"  → next job ({n}/{len(jobs)} done, no pause)")
        finally:
            try:
                if ai:
                    ai.finish_web_chat()
            except Exception:
                pass
            open_pages = [p for p in ctx.pages if not p.is_closed()]
            # Also keep browsers if any apply tabs are still open
            if open_pages and (needs or args.dry_run or failed):
                keep_open = True
            if keep_open and open_pages:
                why = []
                if needs:
                    why.append(f"{needs} need you")
                if args.dry_run:
                    why.append(f"{skipped} dry-run")
                if failed:
                    why.append(f"{failed} failed")
                label = ", ".join(why) or "unfinished bid(s)"
                notify("AutoBid Greenhouse", f"Chrome kept open — {label}")
                print(f"\nChrome kept open ({label}).")
                print("All selected jobs are done — this is not a batch pause.")
                print("Finish or confirm in Greenhouse Chrome, then close that window "
                      "(or press Stop now).")
                if mail_ctx is not None:
                    print("Mail Chrome stays open too (security codes).")
                # Wait until you close Greenhouse Chrome (or Stop now)
                while True:
                    if stop_requested():
                        print("Stop requested — closing browsers.")
                        break
                    try:
                        alive = [p for p in ctx.pages if not p.is_closed()]
                    except Exception:
                        alive = []
                    if not alive:
                        print("Greenhouse Chrome closed — wrapping up.")
                        break
                    time.sleep(2)
            elif keep_open and not open_pages:
                print("\nUnfinished bids, but no tabs left — closing browsers.")
            # Close only after wait (or when every bid fully applied)
            try:
                ctx.close()
            except Exception:
                pass
            if mail_ctx is not None:
                try:
                    mail_ctx.close()
                except Exception:
                    pass

    print(f"\nDone — applied {applied}, needs_you {needs}, failed {failed}, skipped/dry {skipped}")


if __name__ == "__main__":
    main()
