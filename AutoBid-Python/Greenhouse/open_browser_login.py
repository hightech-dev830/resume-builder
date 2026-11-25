"""Open Greenhouse bot Chrome on MyGreenhouse so you can sign in before Collect / Apply.

Uses AutoBid-Python/.browser-profile-greenhouse (separate from Ashby / Wellfound).

    python open_browser_login.py
"""
import sys

import config  # noqa: F401  — sets AUTOBID_BOT=greenhouse before browser import
from browser import PREP_URLS, get_profile_dir, open_bot_chrome
from config import LOGIN_URL, SEARCH_URL

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

URLS = (LOGIN_URL, SEARCH_URL, *PREP_URLS)


def main():
    profile = get_profile_dir()
    print(f"Opening Greenhouse bot Chrome ({profile})")
    print("Sign in to MyGreenhouse (email → security code, or Google / LinkedIn / Apple).")
    print("Close this Chrome before Greenhouse Collect / Apply "
          "(Ashby can keep its own Chrome open).\n")
    open_bot_chrome(URLS)
    print("Chrome launched. This script exits; the browser stays open until you close it.")


if __name__ == "__main__":
    main()
