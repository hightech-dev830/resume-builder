"""Collect = stage 1 (job scraping) + stage 2 (job details scraping), one after the other.

    python collect.py                  # all companies in companies.csv
    python collect.py --org openai     # one company (repeatable)
    python collect.py --no-ai          # relevance by rules only
    python collect.py --refresh        # also re-download details of known relevant jobs

Each stage can also run on its own: scrape_jobs.py / scrape_details.py.
"""
import argparse
import sys

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
    args = parser.parse_args()

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
    scrape_details.scrape_details(conn, refresh=args.refresh, orgs=args.org)

    row = conn.execute("""SELECT COUNT(*) total, SUM(relevant=1) rel,
        COUNT(DISTINCT CASE WHEN relevant=1 THEN org END) orgs,
        SUM(relevant=1 AND status='ready') ready, SUM(status='applied') applied
        FROM jobs WHERE is_active=1""").fetchone()
    print(f"\nDB: {row['total']} remote US jobs | {row['rel'] or 0} relevant at {row['orgs']} companies | "
          f"{row['ready'] or 0} ready to bid | {row['applied'] or 0} applied")


if __name__ == "__main__":
    from config import COLLECT_LOCK
    from runlock import RunLock
    with RunLock(COLLECT_LOCK):   # only one collection at a time
        main()
