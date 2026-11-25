"""Companies the bot remembers as "failed" and skips in automatic collections.

When listing a company's jobs fails with a hard error (board gone, etc.), the company
is written to data/skipped_orgs.json. From then on collect / scrape_jobs / scrape_details
pass it by. HTTP 429 is NOT stored here anymore — it is retried on the next collect.

Separately, scrape_jobs skips boards whose newest known job is older than ~4 months
(see STALE_BOARD_DAYS in config.py) so quiet companies do not burn request quota.

companies.csv is NOT changed: the company stays in the list, it is only skipped.

Ways back in:
  - name it explicitly:   python collect.py --org <slug>   (a success removes the mark)
  - remove it by hand:    python skip_orgs.py --clear <slug>
  - forget 429 leftovers: python skip_orgs.py --clear-429
  - forget all of them:   python skip_orgs.py --clear-all
  - see the list:         python skip_orgs.py
"""
import argparse
import json
import sys
from datetime import datetime

from config import DATA_DIR

SKIP_FILE = DATA_DIR / "skipped_orgs.json"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load():
    """{slug: {"company", "reason", "failed_at"}}"""
    try:
        return json.loads(SKIP_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(data):
    SKIP_FILE.parent.mkdir(parents=True, exist_ok=True)
    SKIP_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def is_skipped(slug):
    return slug in load()


def mark_failed(slug, company, reason):
    data = load()
    data[slug] = {
        "company": company,
        "reason": " ".join(str(reason).split())[:200],
        "failed_at": datetime.now().isoformat(timespec="minutes"),
    }
    save(data)


def clear(slug):
    """Forget a failure (the company works again). True if it was marked."""
    data = load()
    if slug in data:
        del data[slug]
        save(data)
        return True
    return False


def clear_all():
    save({})


def clear_rate_limits():
    """Forget companies skipped only because of HTTP 429 / Too Many Requests.

    Those should be retried on the next collect (permanent skip is for dead boards).
    Returns how many entries were removed.
    """
    data = load()
    keep, n = {}, 0
    for slug, info in data.items():
        reason = (info.get("reason") or "")
        if "429" in reason or "Too Many Requests" in reason or "too many requests" in reason.lower():
            n += 1
            continue
        keep[slug] = info
    if n:
        save(keep)
    return n


def is_rate_limit_error(err):
    text = str(err)
    return "429" in text or "Too Many Requests" in text or "too many requests" in text.lower()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--clear", action="append", metavar="SLUG",
                        help="stop skipping this company (repeatable)")
    parser.add_argument("--clear-all", action="store_true", help="stop skipping every company")
    parser.add_argument("--clear-429", action="store_true",
                        help="stop skipping companies that failed only with HTTP 429")
    args = parser.parse_args(argv)

    if args.clear_all:
        clear_all()
        print("Cleared: no company is skipped any more.")
        return
    if args.clear_429:
        n = clear_rate_limits()
        print(f"Cleared {n} rate-limit (429) skip(s).")
        return
    if args.clear:
        for slug in args.clear:
            print(f"{'Cleared' if clear(slug) else 'Not in the list'}: {slug}")
        return

    data = load()
    if not data:
        print("No company is skipped.")
        return
    print(f"{len(data)} companies are skipped (collection failed):")
    for slug, info in sorted(data.items(), key=lambda kv: kv[1].get("failed_at", "")):
        print(f"  {slug:<28} {info.get('company', '')[:30]:<30} {info.get('failed_at', '')}  "
              f"{info.get('reason', '')[:70]}")


if __name__ == "__main__":
    main()
