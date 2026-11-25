"""Stage 2 — download job descriptions for relevant Greenhouse jobs (public API)."""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import db
import greenhouse_api
from config import WORKERS, stop_requested, wait_while_paused

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _one(row):
    token = row["org"]
    jid = str(row["id"]).split(":")[-1]
    details = greenhouse_api.fetch_job_details(token, jid, row["company"] or token)
    return row["id"], details


def scrape_details(conn, orgs=None):
    todo = db.jobs_needing_details(conn, orgs=orgs)
    if not todo:
        print("Stage 2: every relevant job already has a description.")
        return 0
    print(f"Stage 2: fetching descriptions for {len(todo)} relevant jobs "
          f"({WORKERS} at a time) ...")
    started = time.time()
    ok = fail = 0
    pool = ThreadPoolExecutor(max_workers=WORKERS)
    stopped = False
    try:
        futures = {pool.submit(_one, r): r["id"] for r in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            jid = futures[fut]
            try:
                _, details = fut.result()
                if not details:
                    fail += 1
                else:
                    db.save_details(conn, jid, details)
                    ok += 1
            except Exception as e:
                fail += 1
                print(f"  ! {jid}: {str(e)[:100]}")
            if i % 40 == 0 or i == len(todo):
                conn.commit()
                print(f"  {i}/{len(todo)} | {time.time() - started:.0f}s")
            if wait_while_paused() or stop_requested():
                stopped = True
                print("\n⏸ Stop — descriptions saved so far.")
                break
    finally:
        pool.shutdown(wait=not stopped, cancel_futures=True)
        conn.commit()
    print(f"Stage 2 done: {ok} descriptions, {fail} failed; {time.time() - started:.0f}s")
    return ok


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", action="append")
    args = parser.parse_args(argv)
    conn = db.connect()
    scrape_details(conn, orgs=args.org)


if __name__ == "__main__":
    from config import COLLECT_LOCK
    from runlock import RunLock
    with RunLock(COLLECT_LOCK):
        main()
