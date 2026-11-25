"""Open Ashby's Chrome window so you can sign in / prepare before the bot runs.

Uses AutoBid-Python/.browser-profile-ashby (separate from Greenhouse / Wellfound).
Your everyday Chrome profile does NOT count — prepare accounts in THIS window.

    python open_browser_login.py

Opens real Google Chrome with ChatGPT, Claude.ai and DeepSeek tabs.
Close Ashby bot Chrome before Apply / Make resume for Ashby only
(Greenhouse can keep its own Chrome open).
"""
import sys

import config  # noqa: F401  — sets AUTOBID_BOT=ashby before browser import
from browser import PREP_URLS, get_profile_dir, open_bot_chrome

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    profile = get_profile_dir()
    print(f"Opening Ashby bot Chrome\n  profile: {profile}\n")
    for u in PREP_URLS:
        print(f"  tab: {u}")
    print("\nSign in / prepare everything you need in that window.")
    print("Close this Chrome before Ashby Apply / Make resume "
          "(other bots use different profiles).\n")
    open_bot_chrome()
    print("Chrome launched. This script exits; the browser stays open until you close it.")


if __name__ == "__main__":
    main()
