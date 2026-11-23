"""The one Chrome window all AutoBid bots use (Playwright + your installed Google Chrome).

It keeps its own profile in AutoBid-Python/.browser-profile, shared by every bot and by
the web AIs, so you log in to ChatGPT / Claude / DeepSeek only once and the job sites
see a returning browser (fewer bot checks).
"""
import os
import subprocess
from pathlib import Path

PROFILE_DIR = Path(__file__).resolve().parent.parent / ".browser-profile"

# Sites to open when preparing logins before the bot runs
PREP_URLS = (
    "https://chatgpt.com/",
    "https://claude.ai/new",
    "https://chat.deepseek.com/",
)


def find_chrome():
    """Path to installed Google Chrome (same binary Playwright channel=chrome uses)."""
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for p in candidates:
        if p.is_file():
            return str(p)
    raise FileNotFoundError(
        "Google Chrome not found. Install Chrome (the bot drives your installed Chrome).")


def open_bot_chrome(urls=None):
    """Open the bot's own Chrome window (normal Chrome, bot profile) for manual prep.

    Uses AutoBid-Python/.browser-profile — the same profile bid.py / make_resume.py use.
    This is real Chrome (not Playwright automation). Close it before Apply / Make resume.
    Returns the Popen handle.
    """
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    chrome = find_chrome()
    tabs = list(urls) if urls is not None else list(PREP_URLS)
    args = [
        chrome,
        f"--user-data-dir={PROFILE_DIR}",
        "--profile-directory=Default",
        "--start-maximized",
        "--no-first-run",
        "--no-default-browser-check",
        *tabs,
    ]
    # Do not wait — Chrome stays open so you can prepare; close it before the bot runs.
    return subprocess.Popen(args, cwd=str(PROFILE_DIR.parent))


def open_browser(playwright, headless=False):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        return playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR), channel="chrome", headless=headless,
            no_viewport=True,
            # Playwright's defaults mark the browser as automated (navigator.webdriver = true,
            # the "controlled by automated software" flag). reCAPTCHA Enterprise scores that
            # as a bot -> "There was a problem verifying this submission with CAPTCHA".
            ignore_default_args=["--enable-automation"],
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"])
    except Exception as e:
        if "ProcessSingleton" in str(e) or "in use" in str(e).lower():
            raise SystemExit("The bot's Chrome window is already open (another bot running?). "
                             "Close it and run again.")
        raise
