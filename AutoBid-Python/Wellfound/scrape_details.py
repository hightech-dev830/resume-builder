"""Stage 2: job detail pages + application questions (autoOpenApplication form)."""
import argparse
import sys
import time

from playwright.sync_api import sync_playwright

import db
import wellfound_browser as wf
from config import stop_requested, wait_while_paused

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def todo_jobs(conn, orgs=None, refresh=False):
    sql = ("SELECT id, org, company, title, job_url, apply_url FROM jobs "
           "WHERE is_active=1 AND relevant=1")
    params = []
    if not refresh:
        sql += " AND details_at IS NULL"
    if orgs:
        sql += f" AND org IN ({','.join('?' * len(orgs))})"
        params += list(orgs)
    sql += " ORDER BY published_at DESC"
    return conn.execute(sql, params).fetchall()


def scrape_one(page, row):
    page.goto(row["job_url"], wait_until="domcontentloaded", timeout=120_000)
    wf.dismiss_cookies(page)
    page.wait_for_timeout(1000)
    details = wf.extract_job_details(page)

    # Open apply form to discover questions (Picture 4/5) without submitting.
    apply = row["apply_url"] or details.get("apply_url")
    page.goto(apply, wait_until="domcontentloaded", timeout=120_000)
    page.wait_for_timeout(1200)
    questions = []
    if wf.wait_for_apply_form(page, timeout_ms=15000):
        questions = wf.extract_application_fields(page) or []
        # Close modal if possible so the next navigation is clean
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
    else:
        # Fallback: known default Wellfound fields (Picture 5)
        questions = _default_questions(row["id"])
    return details, questions


def _default_questions(job_id):
    defaults = [
        ("full_name", "Full Name", "String", True),
        ("email", "Email", "Email", True),
        ("password", "Set a Password", "Password", True),
        ("password_confirm", "Confirm Password", "Password", True),
        ("location", "Set Your Location", "String", True),
        ("open_remote", "I'm open to work remotely", "Boolean", False),
        ("years_experience", "Years of experience", "ValueSelect", True),
        ("desired_salary", "Desired Salary", "String", True),
        ("authorized_us", "Are you legally authorized to work in the United States?", "Boolean", True),
        ("sponsorship", "Do you or will you require sponsorship for a US employment visa (e.g. H-1B)?",
         "Boolean", True),
        ("phone", "Phone", "Phone", True),
        ("heard_about", "How did you hear about this company?", "String", False),
        ("linkedin", "LinkedIn URL", "String", False),
        ("github", "GitHub URL", "String", False),
    ]
    out = []
    for path, title, typ, req in defaults:
        out.append({
            "id": f"{job_id}:{path}", "path": path, "title": title, "type": typ,
            "required": req, "form": "application", "section": "", "options": (
                [{"label": "Yes", "value": "true"}, {"label": "No", "value": "false"}]
                if typ == "Boolean" else []),
            "description": "", "raw": {},
        })
    return out


def scrape_details(conn, orgs=None, refresh=False, limit=0):
    rows = todo_jobs(conn, orgs=orgs, refresh=refresh)
    if limit:
        rows = rows[:limit]
    print(f"Stage 2: details + questions for {len(rows)} relevant jobs ...")
    if not rows:
        return 0
    ok = fail = 0
    with sync_playwright() as pw:
        ctx = wf.open_ctx(pw)
        page = ctx.new_page()
        try:
            for i, row in enumerate(rows, 1):
                if wait_while_paused() or stop_requested():
                    print("Stopped.")
                    break
                try:
                    details, questions = scrape_one(page, row)
                    db.save_details(conn, row["id"], details, questions)
                    conn.commit()
                    ok += 1
                    print(f"  {i}/{len(rows)} {row['company']}: {row['title'][:50]} "
                          f"({len(questions)} questions)")
                except Exception as e:
                    fail += 1
                    print(f"  ! {row['title'][:40]}: {str(e)[:120]}")
                time.sleep(0.4)
        finally:
            ctx.close()
    print(f"Details done: {ok} ok, {fail} failed")
    return ok


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", action="append")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)
    conn = db.connect()
    scrape_details(conn, orgs=args.org, refresh=args.refresh, limit=args.limit)


if __name__ == "__main__":
    from config import COLLECT_LOCK
    from runlock import RunLock
    with RunLock(COLLECT_LOCK):
        main()
