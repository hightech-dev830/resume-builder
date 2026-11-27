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

import ai_fill
import answers as bank_mod
import db
import gh_fill as gf
import greenhouse_browser as gh
from browser import open_mail_browser, profile_busy
from config import (BATCH_SIZE, BATCH_WAIT_MIN, TAILORED_RESUME,
                    stop_requested, wait_while_paused)
from mail_code import MAIL_LABEL, normalize_provider, wait_mail_login
from notify import notify
from resume import make_cover_letter, make_resume
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


def _ntitle(s):
    return gf.norm(str(s or "").strip().rstrip("?:*"))


def _live_type(f):
    """Answer-bank type for a field found only on the live form."""
    if f["kind"] in ("select", "combobox"):
        return "ValueSelect"
    if f["kind"] == "radio":
        yn = {o.strip().lower() for o in f["options"]}
        return "Boolean" if yn <= {"yes", "no"} else "ValueSelect"
    if f["kind"] == "checkbox":
        return "Boolean" if f.get("single") else "MultiValueSelect"
    return "LongText" if f["kind"] == "textarea" else "String"


def _basics(profile):
    name = (profile.get("name") or "").strip()
    first = (profile.get("first_name") or "").strip()
    last = (profile.get("last_name") or "").strip()
    if name and not (first or last):
        first, _, last = name.partition(" ")
    return {
        "first_name": first, "last_name": last, "email": profile.get("email") or "",
        "phone": profile.get("phone") or "", "candidate-location": profile.get("location") or "",
        "location": profile.get("location") or "",
        # phone country picker on job-boards forms (#country): required, not in the API questions
        "country": profile.get("country") or "United States",
    }


def plan_answers(fields, items, bank, profile):
    """Pair every EMPTY live field with an answer (fields already filled by autofill or
    MyGreenhouse are left alone).

    Returns (plan, essays, missing, live_new):
      plan     [(field, value, multi)]  ready to fill
      essays   [(field, item)]          free-text answers the AI writes for this job
      missing  [label]                  required, empty and no answer -> needs you
      live_new [question dict]          required fields the stored API questions did not list
    """
    by_key, by_label = {}, {}
    for f in fields:
        for k in (f["id"], f["name"], (f["name"] or "").removesuffix("[]")):
            if k:
                by_key.setdefault(k.lower(), f)
        if f["label"]:
            by_label.setdefault(_ntitle(f["label"]), f)

    def field_for(q):
        path = (_qget(q, "path") or "").strip().lower()
        if path and path in by_key:
            return by_key[path]
        t = _ntitle(_qget(q, "title"))
        if not t:
            return None
        if t in by_label:
            return by_label[t]
        if len(t) >= 20:   # long questions: the page may cut or extend the label text
            for lab, f in by_label.items():
                if len(lab) >= 20 and (lab.startswith(t[:40]) or t.startswith(lab[:40])):
                    return f
        return None

    plan, essays, missing, live_new, used = [], [], [], [], set()
    for it in items:
        q = it["q"]
        f = field_for(q)
        if f is None:
            continue
        used.add(f["key"])
        if f["kind"] == "file" or f["value"] or _qget(q, "type") == "File":
            continue   # files are swapped separately; filled fields stay as autofill left them
        val = it.get("value")
        if val == bank_mod.TAILORED_PLACEHOLDER:
            continue
        title = _qget(q, "title") or f["label"]
        if bank_mod._is_empty(val):
            if re.search(r"salary|compensation", title, re.I) and profile.get("salary"):
                val = profile["salary"]
            elif re.search(r"mailing address|home address|^address", title, re.I) \
                    and profile.get("address"):
                val = profile["address"]
        if not bank_mod._is_empty(val):
            plan.append((f, val, _qget(q, "type") == "MultiValueSelect"))
        elif bank_mod.ai_writable(it):
            essays.append((f, it))
        elif it.get("missing") or _qget(q, "required"):
            missing.append(title)

    basics = _basics(profile)
    with_answer = {_ntitle(e["question"]): e for e in bank["questions"]
                   if not bank_mod._is_empty(e.get("answer"))}
    for f in fields:
        if f["key"] in used or f["kind"] == "file" or f["value"]:
            continue
        val = basics.get((f["id"] or "").lower()) or None
        if val is None:
            pkey = bank_mod.profile_key({"path": f["id"] or f["name"] or "", "title": f["label"]})
            if pkey and pkey != "resume_file" and not bank_mod._is_empty(profile.get(pkey)):
                val = profile[pkey]
        if val is None and f["label"]:
            e = with_answer.get(_ntitle(f["label"]))
            if e:
                val = e["answer"]
        if val is not None:
            plan.append((f, val, f["kind"] == "checkbox" and not f.get("single")))
            continue
        if not f["required"] or not f["label"]:
            continue
        qtype = _live_type(f)
        q = {"title": f["label"], "type": qtype, "path": f["id"] or f["name"] or f["label"],
             "required": True, "description": "", "options": f["options"]}
        live_new.append(q)
        it = {"q": q, "value": None, "missing": True, "source": "bank", "error": None}
        if f["kind"] in ("text", "textarea") and bank_mod.ai_writable(it):
            essays.append((f, it))
        else:
            missing.append(f["label"])
    return plan, essays, missing, live_new


def fill_plan(page, plan):
    """Fill the planned fields. Returns the labels that could not be filled."""
    failed = []
    for f, value, multi in plan:
        shown = (", ".join(map(str, value)) if isinstance(value, list) else str(value))
        print(f"    · {(f['label'] or f['id'])[:55]}: {shown[:50]}")
        if not gf.fill_field(page, f, value, multi=multi):
            failed.append(f["label"] or f["id"])
    return failed


def answer_empty_fields(page, conn, ai, job, items, bank):
    """Scan the live form and answer only what is still empty.
    Returns (fields, problem) - problem is None when every empty required field got an answer."""
    fields = gf.scan_form(page)
    profile = bank["profile"]
    plan, essays, missing, live_new = plan_answers(fields, items, bank, profile)
    if live_new:
        # questions only the live form shows: keep them for the Answers tab (added, never
        # replacing the API questions, whose types and options are better)
        if db.add_questions(conn, job["id"], live_new):
            conn.commit()
            try:
                new_keys = []
                bank_mod.sync_bank(conn, bank, new_keys)
                bank_mod.save_new_questions(bank, new_keys)
            except Exception:
                pass
    if missing:
        why = "; ".join(f"{m[:50]} (no answer)" for m in missing[:4])
        return fields, f"missing answers: {why}"
    if essays:
        try:
            write_essays(ai, job, [it for _, it in essays])
        except Exception as e:
            return fields, f"essays not written: {str(e).splitlines()[0][:200]}"
        plan += [(f, it["value"], False) for f, it in essays]
    print(f"    {len(plan)} empty field(s) to answer"
          + (f" ({len(essays)} written by AI)" if essays else ""))
    failed = fill_plan(page, plan)
    if failed:
        print(f"    ! could not fill: {'; '.join(x[:40] for x in failed[:5])}")
    return fields, None


def swap_files(page, fields, ai, job, pdf):
    """Resume: always upload the tailored PDF, replacing one already attached.
    Cover letter: generated and swapped in when one is already attached, or it is required.
    Returns a problem text, or None."""
    if pdf:
        res = gf.file_field(fields, "resume")
        if not res or not gf.swap_file(page, res, pdf):
            return "resume upload failed"
    cov = gf.file_field(fields, "cover_letter")
    if cov and (cov.get("attached") or cov.get("required")):
        if not pdf:
            return "cover letter needed but there is no tailored resume to base it on"
        try:
            print(f"    writing cover letter with {ai.provider} ...")
            letter = make_cover_letter(ai, db.job_for_ai(job), pdf, source="greenhouse")
        except Exception as e:
            if cov.get("required"):
                return f"cover letter not written: {str(e).splitlines()[0][:160]}"
            print(f"    ! cover letter not written ({str(e).splitlines()[0][:120]}); "
                  f"keeping the attached one")
            return None
        if not gf.swap_file(page, cov, letter) and cov.get("required"):
            return "cover letter upload failed"
    return None


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


def fill_form(page, conn, ai, job, items, bank, fill_mode="manual"):
    """manual: saved answers only (+ AI essays). ai: one AI call for every empty field."""
    if fill_mode == "ai":
        return ai_fill.answer_empty_fields_ai(page, conn, ai, job, items, bank, _basics, fill_plan)
    return answer_empty_fields(page, conn, ai, job, items, bank)


def apply_one(ctx, conn, ai, job, items, dry_run=False, mail_page=None, mail_provider="gmail",
              fill_mode="manual"):
    print(f"\n- {job['title']}  ({job['company'] or job['org']})  [{job['id']}]"
          + ("  [AI fill]" if fill_mode == "ai" else ""))
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

    def needs_you(why):
        print(f"    ! {why} — tab left open")
        if dry_run:
            return "dry_run"
        db.set_status(conn, job["id"], "needs_you", why[:500])
        return "needs_you"

    page = ctx.new_page()
    page.set_default_timeout(10_000)   # a missing element fails fast instead of waiting 30 s
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

        # 1) Focus the first input once so MyGreenhouse / Chrome autofill fills what it knows
        gf.prime_autofill(page)
        # 2) Answer only the fields that are still empty
        bank = bank_mod.load_bank()
        fields, problem = fill_form(page, conn, ai, job, items, bank, fill_mode)
        if problem:
            return needs_you(problem)
        # 3) Resume / cover letter: replace what autofill attached with the generated files
        problem = swap_files(page, fields, ai, job, pdf)
        if problem:
            return needs_you(problem)

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

        # Validation left us on the same URL (a field still empty): answer the empties, retry once
        if outcome == "unchanged" and _has_required_field_errors(page):
            print("    form still has required-field errors — filling empty fields + Submit again …")
            _, problem = fill_form(page, conn, ai, job, items, bank, fill_mode)
            if problem:
                return needs_you(problem)
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
                page, mail_page, company=company, wait_sec=90, provider=mail_provider)
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
    parser.add_argument("--mail", default="gmail", choices=["gmail", "outlook"],
                        help="mailbox that receives the Greenhouse security codes")
    parser.add_argument("--fill", default="manual", choices=["manual", "ai"],
                        help="manual: saved answers only; ai: the AI answers every empty field "
                             "(using your saved answers, profile, resume and the job)")
    args = parser.parse_args(argv)
    ai_mode = args.fill == "ai"

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
    need_ai = bool(TAILORED_RESUME) or ai_mode
    for job in jobs:
        items = bank_mod.resolve_job(conn, job["id"], bank)
        if any(bank_mod.ai_writable(i) for i in items):
            need_ai = True
            break
    ai = None
    problem = ai_settings.api_key_problem(args.ai or ai_settings.get("resume")[0]) if need_ai else None
    if problem and ai_mode:
        sys.exit(f"AI fill mode needs an AI: {problem} (AI tab), or switch Fill to Manual.")
    if problem and need_ai:
        print(f"Note: {problem} — will reuse existing resume_path when present; "
              f"text answers still need AI or Answers/profile.")

    applied = failed = needs = skipped = 0
    mail_provider = normalize_provider(args.mail)
    mail_name = MAIL_LABEL[mail_provider]
    need_mail = not args.dry_run
    if need_mail and profile_busy("mail"):
        sys.exit(
            "Mail Chrome (.browser-profile-mail) is already open.\n"
            "Close “Open mail browser” first — Apply needs that profile to read "
            f"Greenhouse security codes from {mail_name}.")
    with sync_playwright() as pw:
        mail_ctx = None
        mail_page = None
        if need_mail:
            print(f"Opening Greenhouse Chrome + mail Chrome ({mail_name} for security codes) …")
        else:
            print("Opening Greenhouse Chrome (dry-run — mail Chrome skipped) …")
        ctx = gh.open_ctx(pw)
        if need_mail:
            mail_ctx = open_mail_browser(pw)
            mail_page = mail_ctx.new_page()
            try:
                print(f"Checking {mail_name} in mail Chrome …")
                if not wait_mail_login(mail_page, mail_provider, timeout_sec=300):
                    raise RuntimeError(
                        f"{mail_name} not signed in on mail Chrome.\n"
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
        # One application per company: skip companies already applied to (earlier runs),
        # and after one successful application in this run, skip that company's other jobs.
        done_orgs = db.applied_orgs(conn)
        tried_orgs = set()   # companies already attempted in this run
        try:
            for n, job in enumerate(jobs, 1):
                if wait_while_paused() or stop_requested():
                    print("Stopped.")
                    keep_open = True
                    break
                org = (job["org"] or "").strip()
                if org in done_orgs:
                    print(f"  skip {job['title']} ({job['company'] or org}): "
                          f"already applied at this company")
                    skipped += 1
                    continue
                if org in tried_orgs and not args.dry_run:
                    print(f"  skip {job['title']} ({job['company'] or org}): "
                          f"another job at this company was already tried in this run")
                    skipped += 1
                    continue
                items = bank_mod.resolve_job(conn, job["id"], bank)
                # profile name/email required for Greenhouse forms
                profile = bank["profile"]
                if bank_mod._is_empty(profile.get("email")) or (
                        bank_mod._is_empty(profile.get("name"))
                        and bank_mod._is_empty(profile.get("first_name"))):
                    print(f"  ! fill Answers / My profile (name + email) before Apply")
                    keep_open = True
                    break
                blocked = [] if ai_mode else _blocking_non_ai(items)   # AI mode: AI answers
                if blocked:
                    why = "; ".join(
                        f"{_qget(i['q'], 'title')[:50]}" for i in blocked[:4])
                    print(f"  skip {job['title']}: missing answers — {why}")
                    skipped += 1
                    continue
                state = apply_one(
                    ctx, conn, ai, job, items, dry_run=args.dry_run, mail_page=mail_page,
                    mail_provider=mail_provider, fill_mode=args.fill)
                if state in ("applied", "needs_you"):
                    # needs_you = submitted or left open for you to finish: still one per company
                    tried_orgs.add(org)
                if state == "applied":
                    done_orgs.add(org)
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
