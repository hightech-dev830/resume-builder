"""Apply to Wellfound jobs: fill the autoOpenApplication form, submit, wait for email verify.

    python bid.py --dry-run --jobs-file ids.json
    python bid.py --jobs-file ids.json

Flow (from your screenshots):
  1. Open job apply URL (?autoOpenApplication=true)
  2. Fill Picture-5 fields from Answers / My profile
  3. Click Submit application
  4. If email verification appears (Picture 6), pause for you to enter the 6-digit code
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
import wellfound_browser as wf
from config import (BATCH_SIZE, BATCH_WAIT_MIN, STOP_FLAG, VERIFY_FLAG,
                    stop_requested, wait_while_paused)
from notify import notify

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_jobs(conn, ids):
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    return conn.execute(
        f"SELECT * FROM jobs WHERE id IN ({ph}) AND is_active=1 ORDER BY published_at DESC",
        ids).fetchall()


def _truthy(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "y")


def fill_application(page, resolved, profile, dry_run=False):
    """Fill the open application form from profile + bank answers."""
    # Map title/path → value
    values = {}
    for item in resolved:
        q = item["q"]
        val = item.get("value")
        if bank_mod._is_empty(val):
            continue
        values[(q.get("path") or "").lower()] = val
        values[(q.get("title") or "").strip().lower().rstrip("?:*").strip()] = val

    def val_for(*names):
        for n in names:
            if n.lower() in values:
                return values[n.lower()]
        return None

    # Direct profile fills (Picture 5)
    mapping = [
        (("Full Name", "full name", "full_name", "name"), profile.get("name"), "textbox"),
        (("Email", "email"), profile.get("email"), "textbox"),
        (("Set a Password", "password", "set a password"), profile.get("password"), "password"),
        (("Confirm Password", "confirm password"), profile.get("password"), "password"),
        (("Set Your Location", "location", "set your location"), profile.get("location"), "textbox"),
        (("Desired Salary", "desired salary"), profile.get("desired_salary"), "textbox"),
        (("Phone", "phone"), profile.get("phone"), "textbox"),
        (("LinkedIn URL", "linkedin"), profile.get("linkedin"), "textbox"),
        (("GitHub URL", "github"), profile.get("github"), "textbox"),
    ]
    for names, value, kind in mapping:
        if bank_mod._is_empty(value):
            continue
        filled = False
        for name in names:
            try:
                loc = page.get_by_label(re_label(name), exact=False)
                if kind == "password":
                    loc.fill(str(value), timeout=2000)
                else:
                    loc.fill(str(value), timeout=2000)
                filled = True
                break
            except Exception:
                try:
                    page.locator(f'input[placeholder*="{name}" i], textarea[placeholder*="{name}" i]').first.fill(
                        str(value), timeout=1500)
                    filled = True
                    break
                except Exception:
                    pass
        if not filled:
            print(f"    ! could not fill: {names[0]}")

    # Remote checkbox
    if _truthy(profile.get("open_remote", "true")):
        try:
            page.get_by_label(re.compile(r"open to work remotely", re.I)).check(timeout=2000)
        except Exception:
            try:
                page.locator('text=/open to work remotely/i').locator("..").locator(
                    'input[type=checkbox]').check(timeout=1500)
            except Exception:
                pass

    # Years of experience select
    yoe = profile.get("years_experience")
    if not bank_mod._is_empty(yoe):
        try:
            page.get_by_label(re.compile(r"years of experience", re.I)).select_option(
                label=str(yoe), timeout=2000)
        except Exception:
            try:
                page.get_by_label(re.compile(r"years of experience", re.I)).select_option(
                    value=str(yoe), timeout=1500)
            except Exception:
                try:
                    page.get_by_label(re.compile(r"years of experience", re.I)).click()
                    page.get_by_role("option", name=re.compile(re.escape(str(yoe)))).click()
                except Exception:
                    print("    ! could not set years of experience")

    # Yes/No radios
    for label, key in (
        (r"legally authorized to work in the United States", "authorized_us"),
        (r"require sponsorship for a US employment visa", "sponsorship"),
    ):
        ans = _truthy(profile.get(key))
        choice = "Yes" if ans else "No"
        try:
            group = page.locator(f"text=/{label}/i").locator("xpath=ancestor::*[self::div or self::fieldset][1]")
            group.get_by_label(choice, exact=True).check(timeout=2000)
        except Exception:
            try:
                page.get_by_role("radio", name=choice).nth(0 if "authorized" in label else 1).check(timeout=1500)
            except Exception:
                print(f"    ! could not set {key} → {choice}")

    if dry_run:
        print("    dry-run: form filled, not submitting")
        return "dry_run"

    # Submit
    for sel in ('button:has-text("Submit Application")',
                'button:has-text("Submit application")',
                'button:has-text("Submit")',
                'button:has-text("Apply")'):
        try:
            page.locator(sel).first.click(timeout=3000)
            break
        except Exception:
            continue
    else:
        return "no_submit"

    page.wait_for_timeout(2500)
    if wf.is_email_verify_page(page):
        return "email_verify"
    # success heuristics
    if page.locator("text=/application (has been )?submitted|thanks for applying/i").count():
        return "applied"
    return "submitted"


def re_label(name):
    import re as _re
    return _re.compile(_re.escape(name), _re.I)


def wait_email_verify(page, job):
    """Picture 6 — you enter the 6-digit code; bot waits."""
    VERIFY_FLAG.parent.mkdir(parents=True, exist_ok=True)
    VERIFY_FLAG.write_text(job["id"], encoding="utf-8")
    notify("AutoBid Wellfound: verify email",
           f"Enter the 6-digit code for {job['title']} ({job['company'] or job['org']})")
    print("    >>> Email verification required — enter the 6-digit code in the Chrome window.")
    print("        Click Continue in the app when done (or wait for the page to advance).")
    deadline = time.time() + BATCH_WAIT_MIN * 60
    while time.time() < deadline:
        if stop_requested():
            VERIFY_FLAG.unlink(missing_ok=True)
            return "stopped"
        if wait_while_paused():
            VERIFY_FLAG.unlink(missing_ok=True)
            return "stopped"
        if not wf.is_email_verify_page(page):
            VERIFY_FLAG.unlink(missing_ok=True)
            return "verified"
        # code typed + page moved
        try:
            if page.locator("text=/application (has been )?submitted|thanks for applying|Profile|Preferences/i").count():
                if "email_verification" not in (page.url or ""):
                    VERIFY_FLAG.unlink(missing_ok=True)
                    return "verified"
        except Exception:
            pass
        time.sleep(1.5)
    VERIFY_FLAG.unlink(missing_ok=True)
    return "timeout"


def apply_job(page, conn, job, bank, dry_run=False):
    resolved = bank_mod.resolve_job(conn, job["id"], bank)
    missing = bank_mod.blocking(resolved)
    if missing:
        titles = ", ".join(i["q"]["title"] for i in missing[:5])
        print(f"  skip {job['title']}: missing answers ({titles})")
        return "skipped"

    url = job["apply_url"] or wf.apply_url(job["id"])
    print(f"  → {job['company'] or job['org']}: {job['title']}")
    page.goto(url, wait_until="domcontentloaded", timeout=120_000)
    wf.dismiss_cookies(page)
    if not wf.wait_for_apply_form(page):
        print("    ! apply form did not open")
        db.set_status(conn, job["id"], "failed", "apply form missing")
        return "failed"

    result = fill_application(page, resolved, bank["profile"], dry_run=dry_run)
    if result == "dry_run":
        return "dry_run"
    if result == "no_submit":
        db.set_status(conn, job["id"], "failed", "submit button missing")
        return "failed"
    if result == "email_verify":
        db.set_status(conn, job["id"], "needs_you", "email verification")
        vr = wait_email_verify(page, job)
        if vr in ("verified",):
            db.set_status(conn, job["id"], "applied")
            print("    APPLIED (after email verify)")
            return "applied"
        print(f"    needs you ({vr})")
        return "needs_you"
    db.set_status(conn, job["id"], "applied")
    print("    APPLIED")
    return "applied"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--jobs-file")
    parser.add_argument("--batch", type=int, default=BATCH_SIZE)
    args = parser.parse_args()
    ids = []
    if args.jobs_file:
        ids = json.loads(Path(args.jobs_file).read_text(encoding="utf-8"))
    conn = db.connect()
    bank = bank_mod.load_bank()
    jobs = load_jobs(conn, ids)
    print(f"{len(jobs)} job(s)" + (" [DRY RUN]" if args.dry_run else ""))
    if not jobs:
        return

    STOP_FLAG.unlink(missing_ok=True)
    VERIFY_FLAG.unlink(missing_ok=True)
    stats = {"applied": 0, "dry_run": 0, "needs_you": 0, "failed": 0, "skipped": 0}
    with sync_playwright() as pw:
        ctx = wf.open_ctx(pw)
        page = ctx.new_page()
        try:
            for job in jobs:
                if wait_while_paused() or stop_requested():
                    print("Stopped.")
                    break
                try:
                    r = apply_job(page, conn, job, bank, dry_run=args.dry_run)
                    stats[r] = stats.get(r, 0) + 1
                except Exception as e:
                    stats["failed"] += 1
                    db.set_status(conn, job["id"], "failed", str(e)[:160])
                    print(f"    FAILED: {e}")
        finally:
            ctx.close()
    print("Done:", stats)


if __name__ == "__main__":
    main()
