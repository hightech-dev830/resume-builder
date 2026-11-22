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
    conn = db.connect()

    # Always: scrape -> local DB -> (job list reads that DB) -> sync Supabase.
    # Sync runs even if you press Stop after stage 1, so scraped jobs are not left only local.
    try:
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
            print("⏸ Stopped after stage 1, as you asked. "
                  "Scraped jobs stay in the local DB and will be synced to Supabase now.")
        else:
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
        print(f"Local job list DB: {db.DB_PATH}")
    finally:
        sync_supabase(conn)


def print_today(conn, started):
    today = date.today().isoformat()
    new = conn.execute("SELECT COUNT(*) n, SUM(relevant=1) rel FROM jobs WHERE first_seen_at >= ?",
                       (started,)).fetchone()
    # "found today" = first_seen today (what this scrape added), not only Ashby's published_at
    rows = conn.execute(
        "SELECT company, org, title, published_at, first_seen_at FROM jobs "
        "WHERE is_active=1 AND relevant=1 AND substr(first_seen_at, 1, 10) = ? "
        "ORDER BY company", (today,)).fetchall()
    print(f"\nThis run: {new['n']} jobs new on the boards since it started "
          f"({new['rel'] or 0} relevant).")
    print(f"Relevant jobs first seen today: {len(rows)}  (saved in {db.DB_PATH})")
    for r in rows[:40]:
        pub = (r["published_at"] or "")[:10]
        print(f"  {r['company'] or r['org']}: {r['title']}"
              + (f"  [Ashby posted {pub}]" if pub and pub != today else ""))


def sync_supabase(conn):
    """Keep Supabase up to date after every collection (a failure only warns)."""
    print()
    try:
        import sync_db
        sync_db.sync(conn)
    except Exception as e:
        print(f"! Supabase sync skipped: {str(e)[:200]}  (click 'Sync to DB' later)")


if __name__ == "__main__":
    from config import COLLECT_LOCK
    from runlock import RunLock
    with RunLock(COLLECT_LOCK):   # only one collection at a time
        main()
