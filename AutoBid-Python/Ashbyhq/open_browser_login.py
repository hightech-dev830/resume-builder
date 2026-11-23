"""Open the bot's own Chrome so you can sign in before resumes / Apply.

Uses AutoBid-Python/.browser-profile (same window bid.py and make_resume.py use).
Your normal Chrome login does NOT count — sign in here once; it is remembered.

    python open_browser_login.py

Opens ChatGPT, Claude.ai and DeepSeek in tabs. Close the window when done.
Close this Chrome before starting Apply / Make resume (only one process can use
the profile at a time).
"""
import sys
import time

from playwright.sync_api import sync_playwright

from browser import PROFILE_DIR, open_browser
from web_ai import PROVIDERS

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LOGIN_SITES = ("chatgpt", "claude", "deepseek")


def main():
    urls = [PROVIDERS[k]["url"] for k in LOGIN_SITES if k in PROVIDERS]
    print(f"Opening bot Chrome  ({PROFILE_DIR})")
    print("Sign in on each tab, then close the window — sessions stay in this profile.")
    print("Close this Chrome before Apply / Make resume (profile is single-use).\n")
    with sync_playwright() as pw:
        ctx = open_browser(pw)
        for i, url in enumerate(urls):
            page = ctx.pages[0] if i == 0 and ctx.pages else ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            except Exception as e:
                print(f"! Could not open {url}: {e}")
            print(f"  tab: {url}")
        print("\nWaiting until you close the Chrome window …")
        while ctx.pages:
            time.sleep(0.5)
    print("Chrome closed. Logins (if any) are saved.")


if __name__ == "__main__":
    main()
