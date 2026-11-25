"""Settings for the Wellfound auto-bid bot."""
import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "wellfound.db"

sys.path.insert(0, str(BASE_DIR.parent / "common"))

# Own Chrome profile (.browser-profile-wellfound) so Ashby/Greenhouse can bid in parallel.
os.environ["AUTOBID_BOT"] = "wellfound"

COMPANIES_FILE = BASE_DIR / "companies.csv"
ONLY_ORGS = None  # e.g. ["bitwarden", "quora"]; None = all in companies.csv

# Skip boards whose newest listed job is older than ~4 months.
STALE_BOARD_DAYS = 120

# Keep Remote (+ optional US) jobs. Wellfound marks remote with a boolean; location
# text is checked when present.
REMOTE_ONLY = True
LOCATION_FILTER = r"\b(US|USA|U\.S\.|United States|Everywhere|Remote)\b"

TAILORED_RESUME = True
NOTIFY_SOUND = True
BATCH_SIZE = 3
BATCH_WAIT_MIN = 20

import notify as _notify  # noqa: E402
_notify.NOTIFY_SOUND = NOTIFY_SOUND

COLLECT_LOCK = DATA_DIR / "collect.lock"
STOP_FLAG = DATA_DIR / "stop.flag"
PAUSE_FLAG = DATA_DIR / "pause.flag"
VERIFY_FLAG = DATA_DIR / "email_verify.flag"  # set while waiting for 6-digit code


def stop_requested():
    return STOP_FLAG.exists()


def pause_requested():
    return PAUSE_FLAG.exists()


def wait_while_paused():
    if not PAUSE_FLAG.exists():
        return stop_requested()
    print("  ⏸ Paused - press Continue in AutoBid to resume (or Stop to end) ...")
    while PAUSE_FLAG.exists():
        if stop_requested():
            return True
        time.sleep(0.4)
    if stop_requested():
        return True
    print("  ▶ Continued")
    return False
