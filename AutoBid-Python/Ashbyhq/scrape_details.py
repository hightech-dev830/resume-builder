"""Stage 2 - JOB DETAILS SCRAPING: only for relevant jobs from stage 1.

One request per job: description (Overview tab), pay per region, publish date and the
application + survey questions (Application tab). The job then becomes 'ready' to bid.

Usage:
    python scrape_details.py               # relevant jobs without details yet
    python scrape_details.py --refresh     # re-download details of all relevant jobs
    python scrape_details.py --org openai  # one company (repeatable)
"""
import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import ashby_api
import db
from config import WORKERS, stop_requested

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def scrape_details(conn, refresh=False, orgs=None):
    todo = db.jobs_needing_details(conn, refresh=refresh, orgs=orgs)
    if not todo:
        print("Stage 2: all relevant jobs already have their details.")
        return
    print(f"Stage 2: downloading details + questions for {len(todo)} relevant jobs ({WORKERS} at a time) ...")
    started, failed, gone = time.time(), 0, 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(ashby_api.fetch_job_details, org, jid): jid for org, jid in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            if stop_requested():
                for f in futures:
                    f.cancel()                     # not started yet -> dropped
                print("\n⏸ Stop requested - finishing the requests already running, then stopping.")
                break
            jid = futures[fut]
            try:
                details, questions = fut.result()
                if details is None:            # removed from the board meanwhile
                    conn.execute("UPDATE jobs SET is_active=0 WHERE id=?", (jid,))
                    gone += 1
                else:
                    db.save_details(conn, jid, details, questions)
            except Exception as e:             # stays without details; retried next run
                failed += 1
                print(f"  ! {jid}: {str(e)[:100]}")
            if i % 10 == 0 or i == len(todo):   # short write locks: the app stays usable
                conn.commit()
                print(f"  {i}/{len(todo)} | {time.time() - started:.0f}s")
    conn.commit()
    print(f"Stage 2 done: {len(todo) - failed - gone} jobs ready, {gone} closed, {failed} failed"
          + (" (run again to retry)" if failed else ""))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--org", action="append")
    args = parser.parse_args(argv)
    scrape_details(db.connect(), refresh=args.refresh, orgs=args.org)


if __name__ == "__main__":
    from config import COLLECT_LOCK
    from runlock import RunLock
    with RunLock(COLLECT_LOCK):   # only one collection at a time
        main()
