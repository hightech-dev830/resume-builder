"""The one Chrome window all AutoBid bots use (Playwright + your installed Google Chrome).

It keeps its own profile in AutoBid-Python/.browser-profile, shared by every bot and by
the web AIs, so you log in to ChatGPT / Claude / DeepSeek only once and the job sites
see a returning browser (fewer bot checks).
"""
from pathlib import Path

PROFILE_DIR = Path(__file__).resolve().parent.parent / ".browser-profile"


def open_browser(playwright, headless=False):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        return playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR), channel="chrome", headless=headless,
            no_viewport=True, args=["--start-maximized"])
    except Exception as e:
        if "ProcessSingleton" in str(e) or "in use" in str(e).lower():
            raise SystemExit("The bot's Chrome window is already open (another bot running?). "
                             "Close it and run again.")
        raise
