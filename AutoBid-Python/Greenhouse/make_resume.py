"""Make tailored resumes for Greenhouse jobs without applying.

    python make_resume.py --login
    python make_resume.py <job-id>
    python make_resume.py --all
    python make_resume.py --jobs-file data/selection.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

import answers
import db
from browser import open_browser
from notify import notify
from resume import make_resume, settings
import ai_settings
from config import stop_requested, wait_while_paused
from web_ai import PROVIDERS

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("job_id", nargs="*", help="one or more job ids")
    parser.add_argument("--jobs-file", help="JSON file with job ids")
    parser.add_argument("--ai", help="AI platform")
    parser.add_argument("--model", help="AI model")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--login", action="store_true")
    args = parser.parse_args()
    args.ai = args.ai or ai_settings.get("resume")[0]

    conn = db.connect()
    if args.login:
        if args.ai not in PROVIDERS:
            sys.exit(f"{args.ai} is a CLI, nothing to log in to in the browser")
        with sync_playwright() as pw:
            ctx = open_browser(pw)
            page = ctx.new_page()
            page.goto(PROVIDERS[args.ai]["url"])
            print(f"Log in to {PROVIDERS[args.ai]['url']}, then close the window.")
            while ctx.pages:
                time.sleep(1)
        return

    if args.all:
        jobs = conn.execute(
            "SELECT * FROM jobs WHERE is_active=1 AND relevant=1 "
            "AND status IN ('ready','needs_you','new') "
            "AND (resume_path IS NULL OR resume_path='') "
            "AND apply_url IS NOT NULL AND apply_url != '' "
            "ORDER BY COALESCE(published_at, first_seen_at) DESC").fetchall()
        print(f"{len(jobs)} jobs with Apply link and no resume yet.")
    elif args.job_id or args.jobs_file:
        ids = list(args.job_id or [])
        if args.jobs_file:
            ids += json.loads(Path(args.jobs_file).read_text(encoding="utf-8"))
        jobs = conn.execute(
            f"SELECT * FROM jobs WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
        print(f"{len(jobs)} selected job(s)")
    else:
        parser.print_help()
        return
    if not jobs:
        sys.exit("No matching jobs.")

    problem = ai_settings.api_key_problem(args.ai or ai_settings.get("resume")[0])
    if problem:
        sys.exit(f"Not started: {problem}.")
    s = settings()
    print(f"Prompt + base profile from: {s['source']} | template: {s['template']} | "
          f"folder: {s['outputDir']}")

    with sync_playwright() as pw:
        ctx = open_browser(pw) if args.ai in PROVIDERS else None
        ai = ai_settings.make_ai("resume", ctx, args.ai, args.model)
        made = 0
        try:
            for job in jobs:
                if wait_while_paused() or stop_requested():
                    print("\nStopped.")
                    break
                print(f"\n- {job['title']} ({job['id']})  [{ai.label}]")
                started = time.time()
                try:
                    pdf = make_resume(ai, db.job_for_ai(job), source="greenhouse")
                except Exception as e:
                    print(f"  ! {e}")
                    try:
                        ai.finish_web_chat()
                    except Exception:
                        pass
                    continue
                model = (getattr(ai, "last_model", None) or ai.model or "")
                db.set_resume(conn, job["id"], pdf, ai_provider=ai.provider, ai_model=model)
                made += 1
                print(f"  done in {time.time() - started:.0f}s -> {pdf}")
                try:
                    ai.finish_web_chat()
                except Exception:
                    pass
            if made:
                notify("AutoBid Greenhouse: resumes ready",
                       f"{made} of {len(jobs)} resume(s) written with {ai.label}.")
        finally:
            try:
                ai.finish_web_chat()
            except Exception:
                pass
            if ctx:
                ctx.close()


if __name__ == "__main__":
    main()
