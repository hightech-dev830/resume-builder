"""Stage 1: list jobs on every Wellfound company page (companies.csv)."""
import argparse
import csv
import re
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from config import (COMPANIES_FILE, LOCATION_FILTER, ONLY_ORGS, REMOTE_ONLY,
                    STALE_BOARD_DAYS, stop_requested, wait_while_paused)  # sets common/ on path
import ai_settings
import db
import wellfound_browser as wf
from relevance import RelevanceFilter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_companies():
    companies = {}
    with open(COMPANIES_FILE, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            url = (row.get("careers_page") or "").strip().rstrip("/")
            if "wellfound.com/company/" not in url:
                continue
            slug = wf.org_from_careers_url(url)
            if slug:
                companies[slug] = (row.get("company") or slug).strip()
    return companies


def wanted(job):
    if REMOTE_ONLY and not job.get("is_remote"):
        # still keep if location text screams remote/US
        locs = " ".join(job.get("locations") or []) + " " + (job.get("location") or "")
        if not re.search(r"\bremote\b", locs, re.I):
            return False
    if LOCATION_FILTER:
        locs = " ".join(job.get("locations") or []) + " " + (job.get("location") or "")
        if locs.strip() and not re.search(LOCATION_FILTER, locs, re.I):
            # Wellfound remote often has empty locs + remote=true → keep
            if not (job.get("is_remote") and not locs.strip()):
                return False
    return True


def scrape_company(page, slug, company, conn, seen_at):
    url = wf.company_jobs_url(slug)
    page.goto(url, wait_until="domcontentloaded", timeout=120_000)
    wf.dismiss_cookies(page)
    page.wait_for_timeout(1200)
    jobs = wf.scroll_load_jobs(page)
    kept = [j for j in jobs if wanted(j)]
    n_new = sum(db.upsert_listing(conn, slug, company, j, seen_at) for j in kept)
    db.mark_inactive(conn, slug, [j["id"] for j in kept])
    conn.commit()
    return len(jobs), len(kept), n_new


def classify(conn, use_ai=True):
    todo = db.jobs_needing_relevance(conn)
    ai = None
    provider, _ = ai_settings.get("relevance")
    if use_ai and provider and todo and provider in ai_settings.NO_BROWSER:
        if not ai_settings.api_key_problem(provider):
            ai = ai_settings.make_ai("relevance")
    rf = RelevanceFilter(ai=ai, log=print)
    if todo:
        print(f"Relevance: sorting {len(todo)} jobs ...")
        decisions = rf.classify([(r["id"], r["title"], r["dept"]) for r in todo])
        for jid, (relevant, method) in decisions.items():
            db.set_relevance(conn, jid, relevant, method)
        conn.commit()
        yes = sum(1 for r, _ in decisions.values() if r)
        print(f"Relevance: {yes} relevant / {len(decisions) - yes} not")
    try:
        rf.reopen_false_negatives(conn)
    except Exception:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", action="append")
    parser.add_argument("--no-ai", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="max companies this run (0=all)")
    args = parser.parse_args(argv)

    companies = load_companies()
    only = args.org or ONLY_ORGS
    if only:
        companies = {s: companies.get(s, s) for s in only}
    conn = db.connect()
    if not args.org:
        stale = [s for s in list(companies) if db.is_stale_board(conn, s, STALE_BOARD_DAYS)]
        if stale:
            print(f"Skipping {len(stale)} companies with no posting in {STALE_BOARD_DAYS}+ days")
            companies = {s: n for s, n in companies.items() if s not in stale}
    if args.limit:
        companies = dict(list(companies.items())[: args.limit])

    print(f"Stage 1: Wellfound jobs at {len(companies)} companies ...")
    started, seen_at = time.time(), db.now()
    listed = kept = new = failed = 0
    with sync_playwright() as pw:
        ctx = wf.open_ctx(pw)
        page = ctx.new_page()
        try:
            for i, (slug, name) in enumerate(companies.items(), 1):
                if wait_while_paused() or stop_requested():
                    print("Stopped.")
                    break
                try:
                    tot, k, n = scrape_company(page, slug, name, conn, seen_at)
                    listed += tot
                    kept += k
                    new += n
                    print(f"  {i}/{len(companies)} {name}: {k}/{tot} kept (+{n} new)")
                except Exception as e:
                    failed += 1
                    print(f"  ! {name} ({slug}): {str(e)[:120]}")
                if i % 10 == 0:
                    print(f"  … {kept} remote jobs so far | {time.time() - started:.0f}s")
        finally:
            ctx.close()
    print(f"Listed {listed}; kept {kept} ({new} new); {failed} failed; "
          f"{time.time() - started:.0f}s")
    if not stop_requested():
        classify(conn, use_ai=not args.no_ai)
    return list(companies)


if __name__ == "__main__":
    from config import COLLECT_LOCK
    from runlock import RunLock
    with RunLock(COLLECT_LOCK):
        main()
