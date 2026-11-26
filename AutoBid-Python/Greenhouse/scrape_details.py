"""Stage 2 — download job descriptions + application questions (public API).

Uses boards-api …/jobs/{id}?questions=true — same fields as the job-boards
apply page (Resume, LinkedIn, visa dropdowns, etc.). No browser.

    python scrape_details.py
    python scrape_details.py --refresh
    python scrape_details.py --org stripe
    python scrape_details.py --jobs-file data/selection.json --refresh
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import db
import greenhouse_api
from config import WORKERS, stop_requested, wait_while_paused

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _one(row):
    token = row["org"]
    jid = str(row["id"]).split(":")[-1]
    details, questions = greenhouse_api.fetch_job_details(
        token, jid, row["company"] or token)
    return row["id"], details, questions


def _todo_rows(conn, orgs=None, refresh=False, job_ids=None):
    if job_ids:
        ph = ",".join("?" * len(job_ids))
        rows = conn.execute(
            f"SELECT id, org, company FROM jobs WHERE id IN ({ph})", job_ids
        ).fetchall()
        return list(rows)
    return db.jobs_needing_details(conn, orgs=orgs, refresh=refresh)


def scrape_details(conn, orgs=None, refresh=False, job_ids=None):
    todo = _todo_rows(conn, orgs=orgs, refresh=refresh, job_ids=job_ids)
    if not todo:
        print("Stage 2: every relevant job already has description + questions.")
        return 0
    print(f"Stage 2: fetching descriptions + questions for {len(todo)} jobs "
          f"({WORKERS} at a time) ...")
    started = time.time()
    ok = fail = gone = 0
    pool = ThreadPoolExecutor(max_workers=WORKERS)
    stopped = False
    try:
        futures = {pool.submit(_one, r): r["id"] for r in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            jid = futures[fut]
            try:
                _, details, questions = fut.result()
                if not details:
                    conn.execute("UPDATE jobs SET is_active=0 WHERE id=?", (jid,))
                    gone += 1
                else:
                    db.save_details(conn, jid, details, questions)
                    ok += 1
            except Exception as e:
                fail += 1
                print(f"  ! {jid}: {str(e)[:100]}")
            if i % 40 == 0 or i == len(todo):
                conn.commit()
                print(f"  {i}/{len(todo)} | {ok} ok · {gone} gone · {fail} fail | "
                      f"{time.time() - started:.0f}s")
            if wait_while_paused() or stop_requested():
                stopped = True
                print("\n⏸ Stop — details + questions saved so far.")
                break
    finally:
        pool.shutdown(wait=not stopped, cancel_futures=True)
        conn.commit()
    print(f"Stage 2 done: {ok} with details+questions, {gone} closed, {fail} failed; "
          f"{time.time() - started:.0f}s")
    return ok


def _sync_answer_bank(conn):
    try:
        import answers as bank_mod
        bank = bank_mod.load_bank()
        added = bank_mod.sync_bank(conn, bank)
        bank_mod.save_bank(bank)
        if added:
            print(f"Answer bank: +{added} new question(s)")
        else:
            print("Answer bank: up to date")
    except Exception as e:
        print(f"! Answer bank sync skipped: {str(e)[:160]}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", action="append")
    parser.add_argument("--refresh", action="store_true",
                        help="Re-download details+questions for all relevant jobs")
    parser.add_argument("--jobs-file",
                        help="JSON list of job ids (Fetch selected)")
    parser.add_argument("--job", action="append", default=[], help="job id (repeatable)")
    args = parser.parse_args(argv)
    conn = db.connect()
    job_ids = list(args.job or [])
    if args.jobs_file:
        job_ids += json.loads(Path(args.jobs_file).read_text(encoding="utf-8"))
    job_ids = list(dict.fromkeys(job_ids)) or None
    scrape_details(conn, orgs=args.org, refresh=args.refresh, job_ids=job_ids)
    nq = conn.execute("SELECT COUNT(DISTINCT job_id) FROM questions").fetchone()[0]
    print(f"Questions stored for {nq} job(s)")
    _sync_answer_bank(conn)


if __name__ == "__main__":
    from config import COLLECT_LOCK
    from runlock import RunLock
    with RunLock(COLLECT_LOCK):
        main()
