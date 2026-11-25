"""Apply to MyGreenhouse / boards.greenhouse.io jobs.

    python bid.py --dry-run --jobs-file ids.json
    python bid.py --jobs-file ids.json

Flow (from your screenshots):
  1. Open remembered Apply link (Picture 3)
  2. Fill First/Last/Email/Phone + other fields from Answers / My profile
  3. Delete the pre-uploaded base resume (X) and upload the AI-tailored PDF (Picture 4)
  4. Click Submit application
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
from config import (BATCH_SIZE, BATCH_WAIT_MIN, TAILORED_RESUME,
                    stop_requested, wait_while_paused)
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


def _truthy(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "y")


def fill_from_profile_and_bank(page, items, profile):
    """Fill Greenhouse apply form fields (classic boards + MyGreenhouse modal)."""
    # Build lookup
    by_title = {}
    for item in items:
        if bank_mod._is_empty(item.get("value")) or item["value"] == bank_mod.TAILORED_PLACEHOLDER:
            continue
        t = (item["q"].get("title") if isinstance(item["q"], dict) else item["q"]["title"] or "")
        by_title[t.strip().lower().rstrip("?:*").strip()] = item["value"]

    gh.fill_basic_fields(page, profile)

    # Extra text / select / radio from bank
    for title, value in by_title.items():
        if title in ("first name", "last name", "email", "phone", "resume/cv", "resume"):
            continue
        try:
            page.get_by_label(re.compile(re.escape(title), re.I)).fill(str(value), timeout=2000)
            continue
        except Exception:
            pass
        # Yes/No style
        if str(value).strip().lower() in ("yes", "no", "true", "false"):
            choice = "Yes" if _truthy(value) else "No"
            try:
                group = page.locator(f"text=/{re.escape(title)}/i").locator(
                    "xpath=ancestor::*[self::div or self::fieldset][1]")
                group.get_by_label(choice, exact=True).check(timeout=1500)
            except Exception:
                pass

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


def apply_one(ctx, conn, ai, job, items, dry_run=False):
    print(f"\n- {job['title']}  ({job['company'] or job['org']})  [{job['id']}]")
    apply_url = (job["apply_url"] or job["job_url"] or "").strip()
    if not apply_url:
        print("    ! no Apply link remembered — skip")
        db.set_status(conn, job["id"], "failed", "no apply_url")
        return "failed"

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
        page.goto(apply_url, wait_until="domcontentloaded", timeout=120_000)
        gh.dismiss_cookies(page)
        page.wait_for_timeout(1200)
        if gh.is_login_page(page):
            print("    login required — waiting …")
            gh.wait_until_logged_in(page)
            page.goto(apply_url, wait_until="domcontentloaded", timeout=120_000)
            page.wait_for_timeout(1000)

        # Discover + save fields for the answer bank next time
        fields = gh.extract_apply_form_fields(page) or []
        if fields:
            db.save_questions(conn, job["id"], fields)
            conn.commit()

        profile = bank_mod.load_bank()["profile"]
        fill_from_profile_and_bank(page, items, profile)

        # Picture 4: remove base resume, upload tailored PDF
        if pdf:
            ok = gh.upload_resume(page, str(pdf))
            if not ok:
                print("    ! resume upload failed")
                if not dry_run:
                    db.set_status(conn, job["id"], "failed", "resume upload failed")
                    return "failed"

        if dry_run:
            print("    dry-run: filled, not submitting (tab left open)")
            return "dry_run"

        if not gh.click_submit(page):
            print("    ! Submit application button not found")
            db.set_status(conn, job["id"], "needs_you", "submit button not found")
            return "needs_you"

        page.wait_for_timeout(2500)
        body = ""
        try:
            body = page.locator("body").inner_text(timeout=3000)
        except Exception:
            pass
        if re.search(r"thank|submitted|application (has been )?received|success", body or "", re.I):
            db.set_status(conn, job["id"], "applied", "submitted")
            print("    ✓ submitted")
            page.close()
            return "applied"
        # leave tab open if unclear
        db.set_status(conn, job["id"], "needs_you", "submit clicked — confirm in browser")
        print("    ? submit clicked — confirm outcome in Chrome (tab left open)")
        return "needs_you"
    except Exception as e:
        print(f"    ! {e}")
        db.set_status(conn, job["id"], "failed", str(e)[:300])
        try:
            page.close()
        except Exception:
            pass
        return "failed"


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

    need_ai = TAILORED_RESUME
    ai = None
    problem = ai_settings.api_key_problem(args.ai or ai_settings.get("resume")[0]) if need_ai else None
    if problem and need_ai:
        print(f"Note: {problem} — will reuse existing resume_path when present.")

    applied = failed = needs = skipped = 0
    with sync_playwright() as pw:
        ctx = gh.open_ctx(pw)
        # ensure MyGreenhouse session once
        page0 = ctx.new_page()
        try:
            print("Checking MyGreenhouse login …")
            gh.ensure_logged_in(page0)
        finally:
            page0.close()

        ai = None
        if need_ai:
            from web_ai import PROVIDERS
            provider = args.ai or ai_settings.get("resume")[0]
            # browser AI needs the shared context
            ai = ai_settings.make_ai("resume", ctx if provider in PROVIDERS else None,
                                     args.ai, args.model)

        batch = max(1, args.batch)
        for n, job in enumerate(jobs, 1):
            if wait_while_paused() or stop_requested():
                print("Stopped.")
                break
            items = bank_mod.resolve_job(conn, job["id"], bank)
            missing = [i for i in bank_mod.blocking(items) if not bank_mod.ai_writable(i)]
            # profile name/email required for Greenhouse forms
            profile = bank["profile"]
            if bank_mod._is_empty(profile.get("email")) or (
                    bank_mod._is_empty(profile.get("name"))
                    and bank_mod._is_empty(profile.get("first_name"))):
                print(f"  ! fill Answers / My profile (name + email) before Apply")
                break
            state = apply_one(ctx, conn, ai, job, items, dry_run=args.dry_run)
            if state == "applied":
                applied += 1
            elif state == "needs_you":
                needs += 1
            elif state == "skipped" or state == "dry_run":
                skipped += 1
            else:
                failed += 1
            if n % batch == 0 and n < len(jobs):
                print(f"  batch pause {BATCH_WAIT_MIN} min …")
                end = time.time() + BATCH_WAIT_MIN * 60
                while time.time() < end:
                    if stop_requested() or wait_while_paused():
                        break
                    time.sleep(1)
        try:
            if ai:
                ai.finish_web_chat()
        except Exception:
            pass
        # leave context open if needs_you tabs remain
        open_pages = [p for p in ctx.pages if not p.is_closed()]
        if needs and open_pages:
            notify("AutoBid Greenhouse", f"{needs} application(s) need you in Chrome")
            print(f"\n{needs} tab(s) left open — finish / confirm, then close Chrome.")
            while any(not p.is_closed() for p in ctx.pages):
                if stop_requested():
                    break
                time.sleep(2)
        else:
            ctx.close()

    print(f"\nDone — applied {applied}, needs_you {needs}, failed {failed}, skipped/dry {skipped}")


if __name__ == "__main__":
    main()
