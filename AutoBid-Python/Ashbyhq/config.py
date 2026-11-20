"""Settings for the Ashbyhq auto-bid bot."""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "ashbyhq.db"

# Shared code for all AutoBid bots (browser AI, resume, notifications, bot checks).
sys.path.insert(0, str(BASE_DIR.parent / "common"))

# ---------------------------------------------------------------- collecting

# Companies to collect from: every row of companies.csv (company, careers_page, ...),
# e.g. "OpenAI,https://jobs.ashbyhq.com/openai,...". Add or remove rows there.
COMPANIES_FILE = BASE_DIR / "companies.csv"

# Optional: only these board slugs (the part after jobs.ashbyhq.com/), e.g. ["openai", "notion"].
# None = all companies in COMPANIES_FILE.
ONLY_ORGS = None

# How many company boards to download in parallel. Ashby answers "429 Too Many
# Requests" when this is too high (the bot then waits and retries).

BOARD_WORKERS = 6

# Only keep jobs whose workplace type is in this set ("Remote", "Hybrid", "OnSite").
WORKPLACE_TYPES = {"Remote"}

# Only keep jobs where one of the locations matches (regex). Default: US only.
# "Remote - US; Remote - Canada" is kept (US is one of them); "Remote - European Union" is not.
# Set to None for every country.
LOCATION_FILTER = r"\b(US|USA|U\.S\.|United States)\b"

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
APPLY_LIMITS = {"ashby": (3, 60)}

import notify as _notify  # noqa: E402  (after sys.path is set)
_notify.NOTIFY_SOUND = NOTIFY_SOUND
COLLECT_LOCK = DATA_DIR / "collect.lock"

# "Stop after this job" in the app creates this file; every bot script checks it between
# jobs / companies and stops cleanly. ("Stop now" ends the process tree at once.)
STOP_FLAG = DATA_DIR / "stop.flag"


def stop_requested():
    return STOP_FLAG.exists()
