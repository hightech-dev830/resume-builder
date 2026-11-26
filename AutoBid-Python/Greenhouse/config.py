"""Settings for the Greenhouse auto-bid bot."""
import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "greenhouse.db"

sys.path.insert(0, str(BASE_DIR.parent / "common"))

# Own Chrome profile (.browser-profile-greenhouse) so Ashby/Wellfound can bid in parallel.
os.environ["AUTOBID_BOT"] = "greenhouse"

# Company boards: every row of companies.csv (company, careers_page, …),
# e.g. "Discord,https://job-boards.greenhouse.io/discord,..."
# Collect scrapes boards-api.greenhouse.io for each token (Ashby-style, no MyGreenhouse).
COMPANIES_FILE = BASE_DIR / "companies.csv"
ONLY_ORGS = None  # e.g. ["discord", "stripe"]; None = all in COMPANIES_FILE

# Parallel board fetches. Too high → HTTP 429 from Greenhouse.
BOARD_WORKERS = 6
# Parallel detail fetches (description) for relevant jobs.
WORKERS = 4
TIMEOUT = 30

# Keep remote US-eligible postings. LOCATION_FILTER="US" uses city/state matcher;
# None = every country.
LOCATION_FILTER = "US"

# "Collect today's jobs": first_published or updated_at within this many hours.
TODAY_HOURS = 36

# Legacy MyGreenhouse helpers (optional open_browser_login / greenhouse_browser).
# Collect is API-only; Apply goes straight to scraped job-boards …/jobs/<id> URLs.
SEARCH_URL = (
    "https://my.greenhouse.io/jobs/search"
    "?date_posted=past_ten_days&work_type[]=remote"
)
SEARCH_URL_TODAY = (
    "https://my.greenhouse.io/jobs/search"
    "?date_posted=past_day&work_type[]=remote"
)
SEARCH_TODAY_DATE_VALUES = (
    "past_day", "past_twenty_four_hours", "past_24_hours", "today",
)
LOGIN_URL = "https://my.greenhouse.io/users/sign_in"
LOGIN_WAIT_MIN = 30
SCROLL_MAX_CARDS = 500
SCROLL_MAX_ROUNDS = 40
SCROLL_PAUSE_SEC = 1.2
SCROLL_STABLE_ROUNDS = 4

TAILORED_RESUME = True
NOTIFY_SOUND = True
# Apply continuously — no cool-down between jobs (BATCH_WAIT_MIN=0).
# BATCH_SIZE is only used if you set BATCH_WAIT_MIN > 0 again.
BATCH_SIZE = 50
BATCH_WAIT_MIN = 0
COLLECT_LIMIT = 0  # 0 = no cap

import notify as _notify  # noqa: E402
_notify.NOTIFY_SOUND = NOTIFY_SOUND

COLLECT_LOCK = DATA_DIR / "collect.lock"
STOP_FLAG = DATA_DIR / "stop.flag"
PAUSE_FLAG = DATA_DIR / "pause.flag"


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
