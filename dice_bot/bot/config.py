"""Settings (data/settings.json) with defaults, plus paths. Supabase credentials default to the repo's
.env / .env.local (SUPABASE_URL, SUPABASE_SECRET_KEY), so nothing has to be typed twice."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

APP_NAME = "Dice Auto Bid"
VERSION = "1.0.0"

ROOT = Path(__file__).resolve().parent.parent          # dice_bot/
REPO = ROOT.parent                                       # resume-builder/
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
SETTINGS_FILE = DATA / "settings.json"
LEDGER_FILE = DATA / "bids.jsonl"
LOG_DIR = DATA / "logs"
DEBUG_DIR = DATA / "debug"
BROWSER_PROFILE = DATA / "browser-profile"
ENGINE = ROOT / "engine" / "engine.mjs"
ASSETS = ROOT / "assets"

DEFAULTS: dict = {
    "profile_path": "",                  # the resume / profile JSON you upload
    "output_dir": str(Path.home() / "Documents" / "DiceBids"),
    "engine": "deepseek_chat",           # openai | deepseek_api | deepseek_chat | chatgpt_chat
    "openai_key": "",
    "openai_model": "gpt-5",
    "deepseek_key": "",
    "deepseek_model": "deepseek-chat",
    "ats_target": 95,
    "fix_rounds": 1,                     # correction rounds when ATS < target or the consistency check finds problems
    "cover_letter": True,
    "template": "professional",          # sets the Word font / size / accent colour
    "max_age_days": 1,                   # 1 = posted today (24 h), 2 = today + yesterday, …
    "daily_limit": 25,                   # Dice applications per day (auto + manual)
    "run_limit": 25,                     # applications per Start
    "delay_min": 45,                     # seconds between applications (random in the range)
    "delay_max": 120,
    "max_pages": 10,                     # recommended-jobs pages to read per pass
    "strict_remote": False,              # True: an "On-site"/"Hybrid" tag on the job page rejects it even if the location says Remote
    "dry_run": False,                    # everything except pressing Submit
    "keep_chat_tab": True,
    "supabase_url": "",
    "supabase_key": "",
    "chrome_channel": "attach",          # attach (your Chrome window with a debug port) | chrome | msedge | chromium
    "cdp_port": 9222,                    # attach mode: Chrome's debug port (only reachable from this PC)
}


def _read_env() -> dict:
    env: dict = {}
    for name in (".env.local", ".env"):
        f = REPO / name
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
            if m and not line.strip().startswith("#"):
                v = m.group(2).strip().strip('"').strip("'")
                env.setdefault(m.group(1), v)
    return env


def load() -> dict:
    s = dict(DEFAULTS)
    if SETTINGS_FILE.exists():
        try:
            s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    env = _read_env()
    s["supabase_url"] = s.get("supabase_url") or os.environ.get("SUPABASE_URL") or env.get("SUPABASE_URL", "")
    s["supabase_key"] = s.get("supabase_key") or os.environ.get("SUPABASE_SECRET_KEY") or env.get("SUPABASE_SECRET_KEY", "")
    return s


def save(s: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
