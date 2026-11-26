"""Collect = scrape every Greenhouse board from companies.csv (public API, like Ashby).

No MyGreenhouse login and no browser for Collect:
  1. Stage 1  scrape_jobs.py      — list Remote + US jobs, relevance filter
  2. Stage 2  scrape_details.py   — description + application questions
                                    (boards-api …/jobs/{id}?questions=true)
  Apply links are saved per company board (job-boards.greenhouse.io/{token}/jobs/{id}).

    python collect.py                  # all companies in companies.csv
    python collect.py --today          # boards still listed; keep recently posted/updated
    python collect.py --org stripe     # one board token (repeatable)
    python collect.py --no-ai          # relevance by rules only
    python collect.py --limit 100      # cap jobs kept this run
    python collect.py --refresh-details  # re-fetch description+questions for relevant jobs
"""
import argparse
import sys

import db
import scrape_details
import scrape_jobs

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _sync_answer_bank(conn):
    """Push newly discovered required questions into the Greenhouse Supabase bank."""
    try:
        import answers as bank_mod
        bank = bank_mod.load_bank()
        added = bank_mod.sync_bank(conn, bank)
        bank_mod.save_bank(bank)
        if added:
            print(f"Answer bank: +{added} new question(s) from this collect")
        else:
            print("Answer bank: already up to date")
    except Exception as e:
        print(f"! Answer bank sync skipped: {str(e)[:160]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--org", action="append")
    parser.add_argument("--no-ai", action="store_true")
    parser.add_argument("--today", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--refresh-details", action="store_true",
                        help="Re-download description + questions for relevant jobs")
    args = parser.parse_args()
    conn = db.connect()

    stage1 = []
    if args.org:
        for o in args.org:
            stage1 += ["--org", o]
    if args.no_ai:
        stage1.append("--no-ai")
    if args.today:
        stage1.append("--today")
    if args.limit:
        stage1 += ["--limit", str(args.limit)]

    scrape_jobs.main(stage1)
    print()
    from config import stop_requested, wait_while_paused
    if wait_while_paused() or stop_requested():
        print("⏸ Stopped after stage 1. Jobs stay in the local DB.")
    else:
        scrape_details.scrape_details(
            conn, orgs=args.org, refresh=args.refresh_details)
        nq = conn.execute(
            "SELECT COUNT(DISTINCT job_id) FROM questions").fetchone()[0]
        print(f"Questions stored for {nq} job(s)")
        _sync_answer_bank(conn)

    row = conn.execute("""
        SELECT COUNT(*) total,
               SUM(is_active=1) active,
               SUM(is_active=1 AND relevant=1) rel,
               SUM(is_active=1 AND relevant=1 AND status='ready') ready,
               SUM(status='applied') applied,
               COUNT(DISTINCT CASE WHEN is_active=1 AND relevant=1 THEN org END) orgs
        FROM jobs""").fetchone()
    print(f"\nDB: {row['active'] or 0} active | {row['rel'] or 0} relevant at "
          f"{row['orgs'] or 0} companies | {row['ready'] or 0} ready | "
          f"{row['applied'] or 0} applied")
    print(f"Local job list DB: {db.DB_PATH}")


if __name__ == "__main__":
    from config import COLLECT_LOCK
    from runlock import RunLock
    with RunLock(COLLECT_LOCK):
        main()
