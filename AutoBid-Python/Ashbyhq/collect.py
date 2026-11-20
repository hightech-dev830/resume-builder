"""Collect = stage 1 (job scraping) + stage 2 (job details scraping), one after the other.

    python collect.py                  # all companies in companies.csv
    python collect.py --org openai     # one company (repeatable)
    python collect.py --no-ai          # relevance by rules only
    python collect.py --refresh        # also re-download details of known relevant jobs
    python collect.py --today          # only jobs new on the boards since the last collection:
                                       # details for those only, then lists the ones posted today

Ashby's job lists carry no posted date, so every board is still listed in both modes (that is
how new jobs are found); --today skips everything else and reports today's postings.
At the end the jobs are synced with Supabase (sync_db.py).

Each stage can also run on its own: scrape_jobs.py / scrape_details.py.
"""
import argparse
import sys
from datetime import date

import db
import scrape_details
import scrape_jobs

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--org", action="append")
    parser.add_argument("--no-ai", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--today", action="store_true",
                        help="details only for jobs new since the last collection; list today's postings")
    args = parser.parse_args()
    started = db.now()

    stage1 = []
    if args.org:
        for o in args.org:
            stage1 += ["--org", o]
    if args.no_ai:
        stage1.append("--no-ai")
    scrape_jobs.main(stage1)
    print()
    from config import stop_requested
    if stop_requested():
        print("⏸ Stopped after stage 1, as you asked.")
        return
    conn = db.connect()
    scrape_details.scrape_details(conn, refresh=args.refresh, orgs=args.org,
                                  since=started if args.today else None)
    if args.today:
        print_today(conn, started)

    row = conn.execute("""SELECT COUNT(*) total, SUM(relevant=1) rel,
        COUNT(DISTINCT CASE WHEN relevant=1 THEN org END) orgs,
        SUM(relevant=1 AND status='ready') ready, SUM(status='applied') applied
        FROM jobs WHERE is_active=1""").fetchone()
    print(f"\nDB: {row['total']} remote US jobs | {row['rel'] or 0} relevant at {row['orgs']} companies | "
          f"{row['ready'] or 0} ready to bid | {row['applied'] or 0} applied")
    sync_supabase(conn)


def print_today(conn, started):
    today = date.today().isoformat()
    new = conn.execute("SELECT COUNT(*) n, SUM(relevant=1) rel FROM jobs WHERE first_seen_at >= ?",
                       (started,)).fetchone()
    rows = conn.execute("SELECT company, org, title FROM jobs WHERE is_active=1 AND relevant=1 "
                        "AND substr(published_at, 1, 10) = ? ORDER BY company", (today,)).fetchall()
    print(f"\nToday: {new['n']} jobs new on the boards since the last collection "
          f"({new['rel'] or 0} relevant). Relevant jobs posted today: {len(rows)}")
    for r in rows[:40]:
        print(f"  {r['company'] or r['org']}: {r['title']}")


def sync_supabase(conn):
    """Keep Supabase up to date after every collection (a failure only warns)."""
    print()
    try:
        import sync_db
        sync_db.sync(conn)
    except Exception as e:
        print(f"! Supabase sync skipped: {str(e)[:200]}  (click 'Sync DB' later)")


if __name__ == "__main__":
    from config import COLLECT_LOCK
    from runlock import RunLock
    with RunLock(COLLECT_LOCK):   # only one collection at a time
        main()
