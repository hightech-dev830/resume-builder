"""Open the bot's own Chrome window so you can sign in / prepare before the bot runs.

Uses AutoBid-Python/.browser-profile (same profile bid.py and make_resume.py use).
Your everyday Chrome profile does NOT count — prepare accounts in THIS window.

    python open_browser_login.py

Opens real Google Chrome (not Playwright) with ChatGPT, Claude.ai and DeepSeek tabs.
Close that Chrome before Apply / Make resume (only one process can use the profile).
"""
import sys

from browser import PREP_URLS, PROFILE_DIR, open_bot_chrome

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    print(f"Opening bot Chrome\n  profile: {PROFILE_DIR}\n")
    for u in PREP_URLS:
        print(f"  tab: {u}")
    print("\nSign in / prepare everything you need in that window.")
    print("Close bot Chrome before Apply or Make resume.\n")
    open_bot_chrome()
    print("Chrome launched. This script exits; the browser stays open until you close it.")


if __name__ == "__main__":
    main()
