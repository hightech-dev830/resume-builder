"""Settings for the Ashbyhq auto-bid bot."""
import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "ashbyhq.db"

# Shared code for all AutoBid bots (browser AI, resume, notifications, bot checks).
sys.path.insert(0, str(BASE_DIR.parent / "common"))

# Own Chrome profile (.browser-profile-ashby) so Greenhouse/Wellfound can bid in parallel.
os.environ["AUTOBID_BOT"] = "ashby"

# ---------------------------------------------------------------- collecting

# Companies to collect from: every row of companies.csv (company, careers_page, ...),
# e.g. "OpenAI,https://jobs.ashbyhq.com/openai,...". Add or remove rows there.
COMPANIES_FILE = BASE_DIR / "companies.csv"

# Optional: only these board slugs (the part after jobs.ashbyhq.com/), e.g. ["openai", "notion"].
# None = all companies in COMPANIES_FILE.
ONLY_ORGS = None

# How many company boards to download in parallel. Ashby answers "429 Too Many
# Requests" when this is too high (the bot then waits and retries).

# Keep this modest — Ashby 429s wipe out boards mid-run when workers are too high.
BOARD_WORKERS = 4

# Skip a board when its newest known job (published_at, else first_seen_at) is older
# than this many days (~6 months). Saves requests on dead boards. Untouched boards
# (never scraped) are always fetched. Stale boards are re-checked every
# STALE_BOARD_RECHECK_DAYS in case they start posting again.
STALE_BOARD_DAYS = 180
STALE_BOARD_RECHECK_DAYS = 14

# Only keep jobs whose workplace type is in this set ("Remote", "Hybrid", "OnSite").
# Hybrid is included: many Ashby boards (Notion, etc.) list almost everything as Hybrid
# even when the role is US-based / remote-friendly.
WORKPLACE_TYPES = {"Remote", "Hybrid"}

# Location matching is implemented in scrape_jobs.location_ok (US states/cities,
# "North America", bare "Remote", …). LOCATION_FILTER=None keeps every country;
# a custom regex string still works as an override.
LOCATION_FILTER = "US"  # built-in US matcher (not a bare regex — see location_ok)

# How many job-detail requests (stage 2) run in parallel. Higher => Ashby answers 429.
WORKERS = 4

# Seconds before an HTTP request gives up.
TIMEOUT = 30

# ---------------------------------------------------------------- AI + resume

# AI platform + model for resumes and for job sorting, and the resume prompt, are set in
# the desktop app's AI tab (saved in AutoBid-Python/ai_settings.json, shared by all bots).

# True: a tailored resume per job (same prompt, base profile and template as the
# extension / Resume Builder). False: always upload profile.resume_file from answers.json.
TAILORED_RESUME = True


# ---------------------------------------------------------------- applying

# Beep when the bot needs you (in addition to the Windows notification).
NOTIFY_SOUND = True

# Batch mode: prepare this many applications (one tab each), then you solve all
# bot checks in a row. Jobs without a bot check are submitted right away.
BATCH_SIZE = 5
# Minutes to wait for you to clear a batch's bot checks before moving on
# (unsolved tabs stay marked 'needs_you' and are retried on the next run).
BATCH_WAIT_MIN = 20

# Company application limits by board slug, e.g. Ashby: "max 3 applications in any 60 days".
# org -> (max applications, days). The bot never goes over these.
APPLY_LIMITS = {
    "ashby": (3, 60),
    "elevenlabs": (2, 90),    # "90 days cooling time after two applications"
    "temporal": (5, 30),      # "limited to 5 applications within a rolling 30 day period"
}
# Limits the bot meets on its own (the page says so) are remembered in data/org_blocks.json.

import notify as _notify  # noqa: E402  (after sys.path is set)
_notify.NOTIFY_SOUND = NOTIFY_SOUND
COLLECT_LOCK = DATA_DIR / "collect.lock"

# "Stop after this job" in the app creates this file; every bot script checks it between
# jobs / companies and stops cleanly. ("Stop now" ends the process tree at once.)
STOP_FLAG = DATA_DIR / "stop.flag"

# "Pause" in the app creates this file; scripts finish the current step, then wait until
# the file is removed ("Continue") or Stop is pressed.
PAUSE_FLAG = DATA_DIR / "pause.flag"


def stop_requested():
    return STOP_FLAG.exists()


def pause_requested():
    return PAUSE_FLAG.exists()


def wait_while_paused():
    """Block while Pause is on. Returns True if Stop was requested (caller should exit).

    Pause does not kill the current AI call / browser step - it waits at the next
    safe checkpoint (between jobs, companies, resumes).
    """
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
