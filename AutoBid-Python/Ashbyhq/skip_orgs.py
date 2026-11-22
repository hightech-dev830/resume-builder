"""Companies the bot remembers as "failed" and skips in automatic collections.

When listing a company's jobs fails (for example Ashby keeps answering "429 Too Many
Requests" even after the retries, or the board is gone), the company is written to
data/skipped_orgs.json. From then on collect / scrape_jobs / scrape_details pass it by,
so one broken board does not slow down or break every run.

companies.csv is NOT changed: the company stays in the list, it is only skipped.

Ways back in:
  - name it explicitly:   python collect.py --org <slug>   (a success removes the mark)
  - remove it by hand:    python skip_orgs.py --clear <slug>
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--clear", action="append", metavar="SLUG",
                        help="stop skipping this company (repeatable)")
    parser.add_argument("--clear-all", action="store_true", help="stop skipping every company")
    args = parser.parse_args(argv)

    if args.clear_all:
        clear_all()
        print("Cleared: no company is skipped any more.")
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
