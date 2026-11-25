"""Open Wellfound bot Chrome so you can sign in before Apply.

Uses AutoBid-Python/.browser-profile-wellfound (separate from Ashby / Greenhouse).
"""
import sys

import config  # noqa: F401  — sets AUTOBID_BOT=wellfound before browser import
from browser import PREP_URLS, get_profile_dir, open_bot_chrome

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

URLS = ("https://wellfound.com/login", "https://wellfound.com/remote", *PREP_URLS)


def main():
    profile = get_profile_dir()
    print(f"Opening Wellfound bot Chrome ({profile})")
    print("Sign in to Wellfound (and AI sites if needed), then close this Chrome before Apply.\n")
    open_bot_chrome(URLS)


if __name__ == "__main__":
    main()
