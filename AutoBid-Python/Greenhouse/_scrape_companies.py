"""Build Greenhouse/companies.csv (Ashby-shaped): company name + job board URL.

Sources:
  1. Live public directory of Greenhouse boards (tokens confirmed against the API)
     https://kalebconfer-sys.github.io/job-board-directory/greenhouse.csv
  2. Enrich each token with the official board name + open job count:
     GET https://boards-api.greenhouse.io/v1/boards/{token}
     GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs

Output columns (same as Ashbyhq/companies.csv):
  company,careers_page,website,main_country,open_jobs,ats

    python _scrape_companies.py
    python _scrape_companies.py --fast          # skip per-board name lookup (use token)
    python _scrape_companies.py --min-jobs 1    # drop empty boards (default 1)
    python _scrape_companies.py --limit 50      # smoke test
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

OUT = Path(__file__).resolve().parent / "companies.csv"
DIR_CSV = (
    "https://kalebconfer-sys.github.io/job-board-directory/greenhouse.csv"
)
BOARD_API = "https://boards-api.greenhouse.io/v1/boards/{token}"
JOBS_API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
UA = "AutoBid-Greenhouse/1.0 (companies.csv builder)"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def http_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def http_text(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def title_from_token(token: str) -> str:
    """backblaze → Backblaze; discord → Discord (fallback when API name missing)."""
    t = (token or "").strip()
    if not t:
        return ""
    # keep all-digit tokens as-is
    if t.isdigit():
        return t
    parts = re.split(r"[-_]+", t)
    return " ".join(p[:1].upper() + p[1:] if p else "" for p in parts).strip() or t


def clean_company_name(name: str, token: str) -> str:
    n = (name or "").strip()
    # Greenhouse often returns "Backblaze External Website" / "Company Careers"
    for junk in (
        " External Website", " external website", " Careers Page", " Careers",
        " Career Site", " Job Board", " Jobs", " - Careers",
    ):
        if n.lower().endswith(junk.lower()):
            n = n[: -len(junk)].strip()
    if not n or n.lower() in ("careers", "jobs", "greenhouse"):
        return title_from_token(token)
    return n


def load_directory():
    print(f"Downloading board directory …\n  {DIR_CSV}")
    text = http_text(DIR_CSV)
    rows = list(csv.DictReader(text.splitlines()))
    print(f"  {len(rows)} boards in directory")
    return rows


def enrich_one(token: str, open_hint: int | None):
    """Return (company_name, open_jobs) via Greenhouse public API."""
    name = title_from_token(token)
    jobs = open_hint if open_hint is not None else 0
    try:
        meta = http_json(BOARD_API.format(token=token))
        name = clean_company_name(meta.get("name") or "", token)
    except Exception:
        pass
    try:
        data = http_json(JOBS_API.format(token=token))
        jobs = len(data.get("jobs") or [])
    except Exception:
        pass
    return name, jobs


def write_csv(rows: list[dict]):
    rows = sorted(
        rows,
        key=lambda r: (-int(r.get("open_jobs") or 0), (r.get("company") or "").lower()),
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["company", "careers_page", "website", "main_country", "open_jobs", "ats"])
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} companies → {OUT}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fast", action="store_true",
                    help="do not call boards-api for names (use directory counts + token titles)")
    ap.add_argument("--min-jobs", type=int, default=1,
                    help="keep boards with at least this many open roles (0 = keep empty too)")
    ap.add_argument("--limit", type=int, default=0, help="only process first N directory rows")
    ap.add_argument("--workers", type=int, default=16, help="parallel API lookups")
    args = ap.parse_args(argv)

    directory = load_directory()
    if args.limit:
        directory = directory[: args.limit]

    # Dedupe by token
    by_token = {}
    for r in directory:
        token = (r.get("token") or "").strip()
        if not token:
            continue
        try:
            open_hint = int(r.get("open_roles") or 0)
        except Exception:
            open_hint = 0
        board = (r.get("board_url") or "").strip()
        if not board:
            board = f"https://job-boards.greenhouse.io/{token}"
        # normalize host to job-boards.greenhouse.io
        board = re.sub(
            r"^https?://(boards|job-boards)\.greenhouse\.io/",
            "https://job-boards.greenhouse.io/",
            board, flags=re.I)
        by_token[token.lower()] = {
            "token": token,
            "board": board,
            "open_hint": open_hint,
            "company_hint": (r.get("company") or token).strip(),
        }

    tokens = list(by_token.values())
    print(f"Unique tokens: {len(tokens)}"
          + (" (fast mode — no API name enrich)" if args.fast else
             f" (enriching names via Greenhouse API, {args.workers} workers)"))

    out_rows = []
    if args.fast:
        for t in tokens:
            jobs = t["open_hint"]
            if jobs < args.min_jobs:
                continue
            # directory "company" is often the token itself
            name = t["company_hint"]
            if name.lower() == t["token"].lower():
                name = title_from_token(t["token"])
            out_rows.append({
                "company": name,
                "careers_page": t["board"],
                "website": "",
                "main_country": "",
                "open_jobs": str(jobs),
                "ats": "Greenhouse",
            })
    else:
        done = 0
        started = time.time()

        def work(t):
            name, jobs = enrich_one(t["token"], t["open_hint"])
            return t, name, jobs

        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futs = [pool.submit(work, t) for t in tokens]
            for fut in as_completed(futs):
                done += 1
                try:
                    t, name, jobs = fut.result()
                except Exception as e:
                    print(f"  ! {e}")
                    continue
                if jobs < args.min_jobs:
                    continue
                out_rows.append({
                    "company": name,
                    "careers_page": t["board"],
                    "website": "",
                    "main_country": "",
                    "open_jobs": str(jobs),
                    "ats": "Greenhouse",
                })
                if done % 100 == 0 or done == len(tokens):
                    print(f"  … {done}/{len(tokens)}  kept {len(out_rows)}  "
                          f"({time.time() - started:.0f}s)")

    write_csv(out_rows)
    # show a few examples matching the user's samples
    want = ("backblaze", "discord", "tanium", "zscaler")
    print("\nSample rows:")
    for r in out_rows:
        slug = re.search(r"greenhouse\.io/([^/?#]+)", r["careers_page"] or "")
        if slug and slug.group(1).lower() in want:
            print(f"  {r['company']}: {r['careers_page']}  ({r['open_jobs']} jobs)")


if __name__ == "__main__":
    main()
