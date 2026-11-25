"""Stage 1 — list jobs from every Greenhouse board in companies.csv (public API).

No browser / MyGreenhouse login. Keeps Remote + US-eligible roles, then marks
relevant (software/hardware / eng management) via common/relevance.py.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import greenhouse_api
import db
import ai_settings
from config import (BOARD_WORKERS, COLLECT_LIMIT, COMPANIES_FILE, LOCATION_FILTER,
                    ONLY_ORGS, TODAY_HOURS, stop_requested, wait_while_paused)
from relevance import RelevanceFilter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# --- US location matcher (same idea as Ashby) ---
_US_STATE_ABBR = (
    r"AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|"
    r"NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC"
)
_US_STATE_NAME = (
    r"Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|"
    r"Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|"
    r"Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|"
    r"Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|"
    r"Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|"
    r"Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming|"
    r"District of Columbia"
)
_US_CITY = (
    r"New York|NYC|Los Angeles|San Francisco|SF Bay|Bay Area|Seattle|Austin|Denver|"
    r"Boston|Chicago|Miami|Dallas|Atlanta|Portland|Phoenix|San Diego|San Jose|"
    r"Washington,? D\.?C\.?|Remote[- ]?US|United States|USA\b|U\.S\.A?\.?"
)
_US_LOC = re.compile(
    rf"(?i)\b(US|USA|U\.S\.A?\.?|United States(?: of America)?)\b|"
    rf"\bNorth America\b|\bAmericas\b|"
    rf",\s*(?:{_US_STATE_ABBR})\b|"
    rf"\b(?:{_US_STATE_NAME})\b|"
    rf"\b(?:{_US_CITY})\b|"
    rf"\bUS[- ]"
)
_NON_US_LOC = re.compile(
    r"(?i)\b(UK|U\.K\.|United Kingdom|England|London|Europe|EMEA|APAC|"
    r"Canada|Toronto|Germany|Berlin|Ireland|Dublin|France|Paris|Netherlands|"
    r"Sweden|Poland|India|Singapore|Japan|Tokyo|Korea|Seoul|Australia|Brazil|"
    r"Mexico|Israel|China|Hong Kong|UAE|Dubai|LATAM|Romania|Nordics)\b"
)
_BARE_REMOTE = re.compile(r"(?i)^Remote( only|[- ]worldwide|[- ]global)?$")


def location_ok(locations):
    if not LOCATION_FILTER:
        return True
    if LOCATION_FILTER != "US":
        return any(re.search(LOCATION_FILTER, l) for l in locations if l)
    locs = [l.strip() for l in (locations or []) if l and str(l).strip()]
    if not locs:
        return True
    if any(_US_LOC.search(l) for l in locs):
        return True
    if all(_BARE_REMOTE.match(l) for l in locs):
        return True
    if any(_NON_US_LOC.search(l) for l in locs) and not any(_US_LOC.search(l) for l in locs):
        return False
    # Ambiguous remote (e.g. "Remote") already handled; "Chicago, Seattle, NYC, Remote" hits US
    return True


def wanted(job):
    if not job.get("is_remote"):
        return False
    return location_ok(job.get("locations") or [])


def load_companies():
    companies = {}
    with open(COMPANIES_FILE, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            url = (row.get("careers_page") or "").strip()
            token = greenhouse_api.board_token_from_url(url)
            if not token:
                continue
            companies[token] = (row.get("company") or token).strip()
    return companies


def _parse_when(iso):
    if not iso:
        return None
    try:
        text = str(iso).replace("Z", "+00:00")
        # Greenhouse: 2026-11-23T18:38:04-05:00
        return datetime.fromisoformat(text)
    except Exception:
        return None


def is_recent(job, hours=TODAY_HOURS):
    """True if first_published or updated_at is within `hours`."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    for key in ("published_at", "updated_at"):
        dt = _parse_when(job.get(key))
        if not dt:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.astimezone(timezone.utc) >= cutoff:
            return True
    return False


def fetch_board(token, company):
    jobs = greenhouse_api.fetch_job_list(token, company)
    return [j for j in jobs if wanted(j)], len(jobs)


def scrape_boards(conn, companies, today_only=False, limit=0):
    print(f"Stage 1: listing jobs at {len(companies)} Greenhouse boards "
          f"({BOARD_WORKERS} at a time, public API) ...")
    started, seen_at = time.time(), db.now()
    kept = listed = new = failed = rate_limited = 0
    pool = ThreadPoolExecutor(max_workers=BOARD_WORKERS)
    stopped = False
    try:
        futures = {pool.submit(fetch_board, t, n): t for t, n in companies.items()}
        for i, fut in enumerate(as_completed(futures), 1):
            token = futures[fut]
            try:
                jobs, total = fut.result()
            except Exception as e:
                failed += 1
                msg = str(e)
                if "429" in msg or "Too Many" in msg:
                    rate_limited += 1
                    print(f"  ! {companies[token]} ({token}): {msg[:80]}  -> retry next collect")
                elif "no job board" in msg:
                    print(f"  ! {companies[token]} ({token}): no job board")
                else:
                    print(f"  ! {companies[token]} ({token}): {msg[:80]}")
            else:
                board_ids = [j["id"] for j in jobs]
                if today_only:
                    jobs = [j for j in jobs if is_recent(j)]
                if limit and kept + len(jobs) > limit:
                    jobs = jobs[: max(0, limit - kept)]
                n_new = sum(db.upsert_job(conn, j, seen_at) for j in jobs)
                # Inactive = gone from the full remote-US board list (not the --today slice)
                if not today_only:
                    db.mark_inactive(conn, token, board_ids)
                kept, listed, new = kept + len(jobs), listed + total, new + n_new
                conn.commit()
            if i % 25 == 0 or i == len(companies):
                print(f"  {i}/{len(companies)} boards | {kept} remote US kept | "
                      f"{time.time() - started:.0f}s")
            if wait_while_paused() or stop_requested():
                stopped = True
                print(f"\n⏸ Stop — {kept} jobs saved so far.")
                break
            if limit and kept >= limit:
                print(f"  Reached --limit {limit}")
                break
    finally:
        pool.shutdown(wait=not stopped, cancel_futures=True)
    conn.commit()
    extra = f"; {rate_limited} hit 429" if rate_limited else ""
    print(f"Listed {listed} jobs; kept {kept} remote US ({new} new); "
          f"{failed} boards failed{extra}; {time.time() - started:.0f}s")
    return kept


def classify(conn, use_ai=True):
    todo = db.jobs_needing_relevance(conn)
    ai = None
    provider, _ = ai_settings.get("relevance")
    if use_ai and provider and todo:
        problem = ai_settings.api_key_problem(provider)
        if provider not in ai_settings.NO_BROWSER:
            print(f"  ({provider}: web chat — relevance uses rules only)")
        elif problem:
            print(f"  ({provider}: {problem}; rules only, unclear titles kept)")
        else:
            try:
                ai = ai_settings.make_ai("relevance")
                print(f"  unclear titles sorted by {ai.label}")
            except Exception as e:
                print(f"  (relevance AI skipped: {e})")
    rf = RelevanceFilter(ai=ai, log=print)
    if not todo:
        print("Relevance: nothing new to sort.")
        return
    print(f"Relevance: sorting {len(todo)} jobs ...")
    decisions = rf.classify([(r["id"], r["title"], r["dept"]) for r in todo])
    yes = 0
    for jid, (relevant, method) in decisions.items():
        db.set_relevance(conn, jid, relevant, method)
        yes += int(relevant)
    conn.commit()
    print(f"Relevance: {yes} relevant, {len(decisions) - yes} not")
    try:
        rf.reopen_false_negatives(conn)
    except Exception:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", action="append", help="board token only (repeatable)")
    parser.add_argument("--today", action="store_true",
                        help=f"only jobs published/updated in the last {TODAY_HOURS}h")
    parser.add_argument("--no-ai", action="store_true")
    parser.add_argument("--limit", type=int, default=COLLECT_LIMIT,
                        help="max jobs to keep this run (0 = no cap)")
    args = parser.parse_args(argv)

    companies = load_companies()
    only = args.org or ONLY_ORGS
    if only:
        companies = {t: companies.get(t) or greenhouse_api.fetch_company_name(t)
                     for t in only}
    conn = db.connect()
    scrape_boards(conn, companies, today_only=args.today, limit=args.limit or 0)
    if stop_requested():
        print("Stopped: sorting scraped jobs by rules ...")
        classify(conn, use_ai=False)
        return
    classify(conn, use_ai=not args.no_ai)


if __name__ == "__main__":
    from config import COLLECT_LOCK
    from runlock import RunLock
    with RunLock(COLLECT_LOCK):
        main()
