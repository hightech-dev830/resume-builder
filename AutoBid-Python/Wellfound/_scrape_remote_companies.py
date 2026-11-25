"""One-shot: scrape Wellfound /remote?page=1..219 into companies.csv (Ashby-shaped).

    python _scrape_remote_companies.py

Uses Playwright + installed Chrome. Writes/merges into companies.csv next to this script.
"""
from __future__ import annotations

import csv
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent / "companies.csv"
START, END = 1, 219
BASE = "https://wellfound.com/remote?page={}"

EXTRACT_JS = """() => {
  const out = [];
  try {
    const nd = document.getElementById('__NEXT_DATA__');
    if (nd) {
      const data = JSON.parse(nd.textContent);
      const apollo = data?.props?.pageProps?.apolloState?.data
                  || data?.props?.pageProps?.apolloState || {};
      for (const [k, v] of Object.entries(apollo)) {
        if (!v || typeof v !== 'object') continue;
        if (!(k.startsWith('StartupResult:') || k.startsWith('Startup:'))) continue;
        if (!v.slug) continue;
        out.push({
          slug: String(v.slug),
          name: String(v.name || v.slug),
          website: String(v.companyUrl || ''),
          jobs: Array.isArray(v.highlightedJobListings) ? v.highlightedJobListings.length : 0,
        });
      }
    }
  } catch (e) {}
  if (!out.length) {
    const seen = new Set();
    for (const a of document.querySelectorAll('a[href*="/company/"]')) {
      const m = (a.getAttribute('href') || '').match(/\\/company\\/([a-z0-9-]+)/i);
      if (!m) continue;
      const slug = m[1].toLowerCase();
      if (seen.has(slug)) continue;
      seen.add(slug);
      const name = (a.innerText || '').trim().split('\\n')[0] || slug;
      out.push({ slug, name, website: '', jobs: 0 });
    }
  }
  return out;
}"""


def load_existing():
    by_slug = {}
    if OUT.is_file():
        with OUT.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                m = re.search(r"/company/([^/?#]+)", row.get("careers_page") or "")
                if m:
                    by_slug[m.group(1).lower()] = row
    return by_slug


def write_csv(by_slug: dict):
    rows = sorted(by_slug.values(),
                  key=lambda r: (-int(r.get("open_jobs") or 0), (r.get("company") or "").lower()))
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["company", "careers_page", "website", "main_country", "open_jobs", "ats"])
        w.writeheader()
        w.writerows(rows)


def main():
    by_slug = load_existing()
    print(f"Starting with {len(by_slug)} companies already in {OUT.name}")
    with sync_playwright() as pw:
        # Ephemeral context is fine for public listing; channel=chrome helps with DataDome.
        browser = pw.chromium.launch(channel="chrome", headless=False,
                                     args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(viewport={"width": 1400, "height": 900},
                                      user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                                  "Chrome/120.0.0.0 Safari/537.36"))
        page = context.new_page()
        page.goto(BASE.format(START), wait_until="domcontentloaded", timeout=120_000)
        time.sleep(3)
        # cookie banners
        for sel in ["button:has-text('Reject All')", "button:has-text('Agree & Proceed')",
                    "button:has-text('Accept')"]:
            try:
                page.locator(sel).first.click(timeout=2000)
                time.sleep(0.5)
                break
            except Exception:
                pass

        empty_streak = 0
        for pnum in range(START, END + 1):
            url = BASE.format(pnum)
            try:
                if pnum != START or page.url.split("?")[0] != "https://wellfound.com/remote":
                    page.goto(url, wait_until="domcontentloaded", timeout=120_000)
                # wait for apollo / company links
                page.wait_for_timeout(1500)
                for _ in range(20):
                    companies = page.evaluate(EXTRACT_JS)
                    if companies:
                        break
                    page.wait_for_timeout(500)
                else:
                    companies = []
            except Exception as e:
                print(f"page {pnum}: ERROR {e}")
                empty_streak += 1
                if empty_streak >= 5:
                    print("Too many failures — stopping.")
                    break
                continue

            if not companies:
                print(f"page {pnum}: 0 companies")
                empty_streak += 1
                if empty_streak >= 5:
                    print("Too many empty pages — stopping.")
                    break
                continue
            empty_streak = 0
            added = 0
            for c in companies:
                slug = (c.get("slug") or "").strip().lower()
                if not slug:
                    continue
                name = (c.get("name") or slug).strip() or slug
                jobs = int(c.get("jobs") or 0)
                website = (c.get("website") or "").strip()
                prev = by_slug.get(slug)
                open_jobs = max(jobs, int((prev or {}).get("open_jobs") or 0))
                by_slug[slug] = {
                    "company": name,
                    "careers_page": f"https://wellfound.com/company/{slug}",
                    "website": website or (prev or {}).get("website") or "",
                    "main_country": (prev or {}).get("main_country") or "US",
                    "open_jobs": open_jobs,
                    "ats": "Wellfound",
                }
                if not prev:
                    added += 1
            print(f"page {pnum}/{END}: {len(companies)} on page, +{added} new, "
                  f"{len(by_slug)} unique total")
            if pnum % 10 == 0:
                write_csv(by_slug)
            time.sleep(0.4)

        browser.close()
    write_csv(by_slug)
    print(f"\nDone. {len(by_slug)} companies → {OUT}")


if __name__ == "__main__":
    main()
