"""Stage 1 - JOB SCRAPING: list the jobs of every company and keep the relevant ones.

For each company board (companies.csv) it downloads only the job LIST (title, department,
locations, pay summary - no descriptions), keeps Remote/Hybrid + US jobs (city/state
names count — not only the literal words "United States"), then marks each job
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
                    STALE_BOARD_DAYS, STALE_BOARD_RECHECK_DAYS, WORKPLACE_TYPES)
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


# US state abbreviations — only match after a comma ("Chicago, IL") so "in" / "or" / "me"
# inside normal English do not false-positive.
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
    r"Washington,? D\.?C\.?|Salt Lake|Raleigh|Durham|Nashville|Minneapolis|Detroit|"
    r"Philadelphia|Pittsburgh|Charlotte|Tampa|Orlando|Las Vegas|Remote[- ]?US|"
    r"United States|USA\b|U\.S\.A?\.?"
)

# Positive: looks like a US / North-America posting
_US_LOC = re.compile(
    rf"(?i)\b(US|USA|U\.S\.A?\.?|United States(?: of America)?)\b|"
    rf"\bNorth America\b|\bAmericas\b|"
    rf",\s*(?:{_US_STATE_ABBR})\b|"
    rf"\b(?:{_US_STATE_NAME})\b|"
    rf"\b(?:{_US_CITY})\b"
)

# Negative: clearly abroad (used when nothing US-looking matched)
_NON_US_LOC = re.compile(
    r"(?i)\b(UK|U\.K\.|United Kingdom|England|Scotland|Wales|London|Europe|"
    r"European Union|\bEU\b|EMEA|APAC|Asia[- ]?Pacific|Australia|Sydney|Melbourne|"
    r"Canada|Toronto|Vancouver|Montreal|Germany|Berlin|Munich|Ireland|Dublin|"
    r"France|Paris|Netherlands|Amsterdam|Spain|Madrid|Barcelona|Italy|Milan|"
    r"Sweden|Stockholm|India|Bangalore|Bengaluru|Hyderabad|Singapore|Japan|Tokyo|"
    r"Korea|Seoul|Brazil|Mexico|Israel|Tel Aviv|Switzerland|Zurich|Poland|Warsaw|"
    r"Portugal|Lisbon|Belgium|Denmark|Norway|Finland|New Zealand|Philippines|"
    r"Indonesia|Malaysia|Thailand|Vietnam|Taiwan|Hong Kong|China|Shanghai|Beijing|"
    r"UAE|Dubai|Saudi|Africa|LATAM|Latin America|South America)\b"
)

_BARE_REMOTE = re.compile(r"(?i)^Remote( only|[- ]worldwide|[- ]global)?$")


def location_ok(locations):
    """True if this posting should count as US-eligible.

    Ashby often writes "Chicago, IL (Remote)" or "Atlanta, Georgia" instead of
    "United States" — the old US|USA regex dropped most of those.
    Bare "Remote" / empty location on a Remote/Hybrid role is kept (country omitted).
    """
    if not LOCATION_FILTER:
        return True
    # Custom regex override (legacy): any non-"US" string is treated as a regex
    if LOCATION_FILTER != "US":
        return any(re.search(LOCATION_FILTER, l) for l in locations if l)

    locs = [l.strip() for l in (locations or []) if l and str(l).strip()]
    if not locs:
        return True
    if any(_US_LOC.search(l) for l in locs):
        return True
    # Every line is just "Remote" / "Remote only" → country not specified → keep
    if all(_BARE_REMOTE.match(l) for l in locs):
        return True
    # Clearly abroad and nothing US-looking → drop
    if any(_NON_US_LOC.search(l) for l in locs):
        return False
    # Ambiguous (e.g. "Worldwide", "Multiple locations") → keep; relevance filters later
    return True


def wanted(job):
    if job["workplace_type"] not in WORKPLACE_TYPES:
        return False
    return location_ok(job.get("locations") or [])


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


def filter_stale_boards(conn, companies):
    """Drop boards whose newest known job is older than STALE_BOARD_DAYS (~4 months).

    Ashby's stage-1 GraphQL list has no publish date, so this uses the local DB
    (published_at from stage 2, else first_seen_at). Never-scraped boards stay.
    """
    keep, stale = {}, []
    for slug, name in companies.items():
        if db.is_stale_board(conn, slug, STALE_BOARD_DAYS, STALE_BOARD_RECHECK_DAYS):
            newest, _ = db.board_newest_posting(conn, slug)
            stale.append((slug, name, newest))
        else:
            keep[slug] = name
    if stale:
        print(f"Skipping {len(stale)} companies whose last posting is older than "
              f"{STALE_BOARD_DAYS} days"
              f" (re-check every {STALE_BOARD_RECHECK_DAYS}d): "
              + ", ".join(f"{n} ({s})" for s, n, _ in stale[:8])
              + (" ..." if len(stale) > 8 else ""))
    return keep


def scrape_boards(conn, companies):
    print(f"Stage 1: listing jobs at {len(companies)} companies ({BOARD_WORKERS} at a time) ...")
    started, seen_at = time.time(), db.now()
    kept = listed = new = failed = rate_limited = 0
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
                if skip_orgs.is_rate_limit_error(e):
                    # Do NOT permanent-skip on 429 — that filled skipped_orgs and hid
                    # hundreds of boards. Retry on the next collect instead.
                    rate_limited += 1
                    print(f"  ! {companies[slug]} ({slug}): {str(e)[:80]}  "
                          f"-> this run only (will retry next collect)")
                else:
                    skip_orgs.mark_failed(slug, companies[slug], e)
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
                print(f"  {i}/{len(companies)} companies | {kept} Remote/Hybrid US jobs so far | "
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
    extra = f"; {rate_limited} hit 429 this run" if rate_limited else ""
    print(f"Listed {listed} jobs; {kept} are Remote/Hybrid US ({new} new); {failed} boards failed"
          f"{extra}; {time.time() - started:.0f}s")


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
    # Old runs permanent-skipped every 429; clear those so collect can try again.
    cleared_429 = skip_orgs.clear_rate_limits()
    if cleared_429:
        print(f"Cleared {cleared_429} old HTTP 429 skip(s) — those boards will be tried again.")
    if not args.org:
        # automatic run: companies whose collection failed before are skipped (skip_orgs.py)
        skipped = [s for s in companies if skip_orgs.is_skipped(s)]
        if skipped:
            print(f"Skipping {len(skipped)} companies whose collection failed before "
                  f"(python skip_orgs.py to see them): {', '.join(skipped[:8])}"
                  + (" ..." if len(skipped) > 8 else ""))
            companies = {s: n for s, n in companies.items() if s not in skipped}
    conn = db.connect()
    if not args.org:
        companies = filter_stale_boards(conn, companies)
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
