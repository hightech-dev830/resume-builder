"""Make tailored resumes WITHOUT applying (to check them, or to prepare ahead).

Usage:
    python make_resume.py --login              # open the AI site once to log in
    python make_resume.py <job-id>             # resume for one job
    python make_resume.py <job-id> --ai claude # with another AI (chatgpt|claude|deepseek|claude_code|cursor)
    python make_resume.py --all                # every ready job that has no resume yet
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
from config import stop_requested
from web_ai import PROVIDERS

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("job_id", nargs="*", help="one or more job ids")
    parser.add_argument("--jobs-file", help="JSON file with a list of job ids (the app's selection)")
    parser.add_argument("--ai", help="AI platform (default: AI tab settings)")
    parser.add_argument("--model", help="AI model (default: AI tab settings)")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--login", action="store_true", help="open the AI site to log in")
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
            print(f"Log in to {PROVIDERS[args.ai]['url']} in the Chrome window, "
                  f"then close the window (the login is remembered).")
            while ctx.pages:
                time.sleep(1)
        return

    if args.all:
        # only jobs the bot can really apply to (all required answers ready), so no AI time
        # is spent on jobs that are still missing answers
        bank = answers.load_bank()
        resolved = answers.resolve_all(conn, bank)
        jobs = [j for j in conn.execute(
            "SELECT * FROM jobs WHERE is_active=1 AND relevant=1 AND status IN ('ready','needs_you') "
            "AND (resume_path IS NULL OR resume_path='') ORDER BY published_at DESC")
            if j["id"] in resolved and not answers.blocking(resolved[j["id"]])]
        print(f"{len(jobs)} jobs have all required answers and no resume yet.")
    elif args.job_id or args.jobs_file:
        ids = list(args.job_id or [])
        if args.jobs_file:
            ids += json.loads(Path(args.jobs_file).read_text(encoding="utf-8"))
        jobs = conn.execute(f"SELECT * FROM jobs WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
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
                if stop_requested():
                    print("\n⏸ Stopped after the current resume, as you asked.")
                    break
                print(f"\n- {job['title']} ({job['id']})  [{ai.label}]")
                started = time.time()
                try:
                    pdf = make_resume(ai, db.job_for_ai(job), source="ashby")
                except Exception as e:
                    print(f"  ! {e}")
                    continue
                db.set_resume(conn, job["id"], pdf)
                made += 1
                print(f"  done in {time.time() - started:.0f}s -> {pdf}")
            if made:
                notify("AutoBid: resumes ready", f"{made} of {len(jobs)} resume(s) written with {ai.label}.")
            else:
                notify("AutoBid: no resume written", "See the console for the reason.")
        finally:
            if ctx:
                ctx.close()


if __name__ == "__main__":
    main()
