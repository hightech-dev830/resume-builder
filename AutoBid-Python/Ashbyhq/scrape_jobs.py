"""Stage 1 - JOB SCRAPING: list the jobs of every company and keep the relevant ones.

For each company board (companies.csv) it downloads only the job LIST (title, department,
locations, pay summary - no descriptions), keeps Remote + US jobs, then marks each job
relevant / not relevant: software/hardware development or engineering management
(common/relevance.py: rules first, the AI only for unclear titles).

Usage:
    python scrape_jobs.py                  # all companies
    python scrape_jobs.py --org openai     # one company (repeatable)
    python scrape_jobs.py --no-ai          # rules only (unclear titles are kept)
"""
import argparse
import csv
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import ashby_api
import db
import ai_settings
import skip_orgs
from config import stop_requested, wait_while_paused
from config import (BOARD_WORKERS, COMPANIES_FILE, LOCATION_FILTER, ONLY_ORGS,
                    WORKPLACE_TYPES)
from relevance import RelevanceFilter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_companies():
    """{board slug: company name} from companies.csv (careers_page = jobs.ashbyhq.com/<slug>)."""
    companies = {}
    with open(COMPANIES_FILE, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            url = (row.get("careers_page") or "").strip().rstrip("/")
            if "ashbyhq.com/" not in url:
                continue
            slug = urllib.parse.unquote(url.rsplit("/", 1)[-1])
            companies[slug] = (row.get("company") or slug).strip()
    return companies


def wanted(job):
    if job["workplace_type"] not in WORKPLACE_TYPES:
        return False
    return not LOCATION_FILTER or any(re.search(LOCATION_FILTER, l) for l in job["locations"] if l)


def fetch_board(slug):
    """(matching jobs, total listed). One retry on a network error."""
    for attempt in (1, 2):
        try:
            jobs = ashby_api.fetch_job_list(slug)
            return [j for j in jobs if wanted(j)], len(jobs)
        except Exception as e:
            if attempt == 2 or "no job board" in str(e):
                raise
            time.sleep(2)


def scrape_boards(conn, companies):
    print(f"Stage 1: listing jobs at {len(companies)} companies ({BOARD_WORKERS} at a time) ...")
    started, seen_at = time.time(), db.now()
    kept = listed = new = failed = 0
    pool = ThreadPoolExecutor(max_workers=BOARD_WORKERS)
    stopped = False
    try:
        futures = {pool.submit(fetch_board, slug): slug for slug in companies}
        for i, fut in enumerate(as_completed(futures), 1):
            slug = futures[fut]
            try:
                jobs, total = fut.result()
            except Exception as e:
                failed += 1
                skip_orgs.mark_failed(slug, companies[slug], e)   # skipped from now on
                print(f"  ! {companies[slug]} ({slug}): {str(e)[:80]}  -> skipped from now on")
            else:
                if skip_orgs.is_skipped(slug):      # it works again (explicitly asked for)
                    skip_orgs.clear(slug)
                n_new = sum(db.upsert_listing(conn, slug, companies[slug], j, seen_at) for j in jobs)
                db.mark_inactive(conn, slug, [j["id"] for j in jobs])   # closed / filtered out
                kept, listed, new = kept + len(jobs), listed + total, new + n_new
                # saved company by company: whatever is scraped when you press Stop is kept
                # (and the write lock is short, so the app stays usable)
                conn.commit()
            if i % 10 == 0 or i == len(companies):
                print(f"  {i}/{len(companies)} companies | {kept} remote US jobs so far | "
                      f"{time.time() - started:.0f}s")
            if wait_while_paused():
                stopped = True
                print(f"\n⏸ Stop requested while paused - {kept} jobs scraped so far are saved.")
                break
            if stop_requested():
                stopped = True
                print(f"\n⏸ Stop requested - {kept} jobs scraped so far are saved.")
                break
    finally:
        # on stop do not wait for the requests still running (a 429 backoff can take minutes)
        pool.shutdown(wait=not stopped, cancel_futures=True)
    conn.commit()
    print(f"Listed {listed} jobs; {kept} are remote US ({new} new); {failed} boards failed; "
          f"{time.time() - started:.0f}s")


def classify(conn, use_ai):
    todo = db.jobs_needing_relevance(conn)
    ai = None
    provider, _ = ai_settings.get("relevance")
    if use_ai and provider and todo:
        problem = ai_settings.api_key_problem(provider)
        if provider not in ai_settings.NO_BROWSER:
            print(f"  ({provider} is a web chat page; job sorting uses rules only - pick a CLI "
                  f"or an API in the AI tab)")
        elif problem:
            print(f"  ({provider}: {problem}; job sorting uses rules only, unclear titles are kept)")
        else:
            ai = ai_settings.make_ai("relevance")
            print(f"  unclear titles are sorted by {ai.label}")
    rf = RelevanceFilter(ai=ai, log=print)
    if not todo:
        print("Relevance: nothing new to sort.")
    else:
        print(f"Relevance: sorting {len(todo)} jobs (software/hardware development + eng management) ...")
        decisions = rf.classify([(r["id"], r["title"], r["dept"]) for r in todo])
        counts = {}
        for jid, (relevant, method) in decisions.items():
            db.set_relevance(conn, jid, relevant, method)
            counts[(relevant, method)] = counts.get((relevant, method), 0) + 1
        conn.commit()
        yes = sum(n for (r, _), n in counts.items() if r)
        print(f"Relevance: {yes} relevant, {len(decisions) - yes} not  "
              f"({', '.join(f'{m}:{n}' for (r, m), n in sorted(counts.items(), key=str) if r)} relevant)")
    # Re-open Data Analyst / IT Specialist / Solutions Engineer etc. wrongly marked NO before
    rf.reopen_false_negatives(conn)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--org", action="append", help="only this board slug (repeatable)")
    parser.add_argument("--no-ai", action="store_true", help="relevance by rules only")
    args = parser.parse_args(argv)

    companies = load_companies()
    only = args.org or ONLY_ORGS
    if only:
        companies = {s: companies.get(s) or ashby_api.fetch_company_name(s) for s in only}
    if not args.org:
        # automatic run: companies whose collection failed before are skipped (skip_orgs.py)
        skipped = [s for s in companies if skip_orgs.is_skipped(s)]
        if skipped:
            print(f"Skipping {len(skipped)} companies whose collection failed before "
                  f"(python skip_orgs.py to see them): {', '.join(skipped[:8])}"
                  + (" ..." if len(skipped) > 8 else ""))
            companies = {s: n for s, n in companies.items() if s not in skipped}
    conn = db.connect()
    scrape_boards(conn, companies)
    if stop_requested():
        # sort what was scraped by rules now (fast, no AI), so the jobs show up in the app;
        # unclear titles are kept and get sorted by the AI on the next collection
        print("Stopped: sorting the scraped jobs by rules so you can see them ...")
        classify(conn, use_ai=False)
        return list(companies)
    classify(conn, use_ai=not args.no_ai)
    return list(companies)


if __name__ == "__main__":
    from config import COLLECT_LOCK
    from runlock import RunLock
    with RunLock(COLLECT_LOCK):   # only one collection at a time
        main()
