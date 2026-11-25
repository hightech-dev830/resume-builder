"""Collect = stage 1 (company job lists) + stage 2 (details + questions).

    python collect.py
    python collect.py --org bitwarden
    python collect.py --limit 20
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
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    argv = []
    if args.org:
        for o in args.org:
            argv += ["--org", o]
    if args.no_ai:
        argv.append("--no-ai")
    if args.limit:
        argv += ["--limit", str(args.limit)]
    scrape_jobs.main(argv)
    from config import stop_requested, wait_while_paused
    if wait_while_paused() or stop_requested():
        print("Stopped after stage 1.")
        return
    dargv = []
    if args.org:
        for o in args.org:
            dargv += ["--org", o]
    if args.refresh:
        dargv.append("--refresh")
    if args.limit:
        dargv += ["--limit", str(args.limit)]
    scrape_details.main(dargv)
    conn = db.connect()
    row = conn.execute("""SELECT COUNT(*) total, SUM(relevant=1) rel,
        SUM(relevant=1 AND status='ready') ready FROM jobs WHERE is_active=1""").fetchone()
    print(f"\nDB: {row['total']} jobs | {row['rel'] or 0} relevant | {row['ready'] or 0} ready")
    print(f"Local DB: {db.DB_PATH}")


if __name__ == "__main__":
    from config import COLLECT_LOCK
    from runlock import RunLock
    with RunLock(COLLECT_LOCK):
        main()
