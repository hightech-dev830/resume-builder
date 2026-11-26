"""Chrome profiles for AutoBid bots (Playwright + installed Google Chrome).

Each platform bot uses its OWN profile so Ashby and Greenhouse can bid at the same time:

  AutoBid-Python/.browser-profile-ashby
  AutoBid-Python/.browser-profile-greenhouse
  AutoBid-Python/.browser-profile-wellfound

Mail inbox (Gmail) for Greenhouse security codes — separate from every bid bot:

  AutoBid-Python/.browser-profile-mail

Set AUTOBID_BOT in that bot's config.py (ashby / greenhouse / wellfound) before
importing this module (or call open_browser(..., bot=\"ashby\")).
Use bot=\"mail\" (or open_mail_browser) for the Gmail profile.

If a per-bot profile is empty, it is seeded once from the legacy shared
`.browser-profile` (so existing logins carry over). The mail profile is never
seeded from the shared folder (keep Gmail separate).
"""
import os
import shutil
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SHARED = _ROOT / ".browser-profile"

_BOT_DIRS = {
    "ashby": ".browser-profile-ashby",
    "ashbyhq": ".browser-profile-ashby",
    "greenhouse": ".browser-profile-greenhouse",
    "wellfound": ".browser-profile-wellfound",
    "mail": ".browser-profile-mail",
}

# Sites to open when preparing logins before the bot runs
PREP_URLS = (
    "https://chatgpt.com/",
    "https://claude.ai/new",
    "https://chat.deepseek.com/",
)

MAIL_PREP_URLS = (
    "https://mail.google.com/",
)


def get_profile_dir(bot=None):
    """Profile folder for this bot (or AUTOBID_BOT / shared legacy)."""
    name = (bot or os.environ.get("AUTOBID_BOT") or "").strip().lower()
    folder = _BOT_DIRS.get(name)
    path = _ROOT / folder if folder else _SHARED
    path.mkdir(parents=True, exist_ok=True)
    if name != "mail":
        _seed_from_shared(path)
    return path


def get_mail_profile_dir():
    """Gmail / inbox Chrome profile (.browser-profile-mail)."""
    return get_profile_dir("mail")


def _seed_from_shared(dest: Path):
    """One-time copy of legacy shared profile into an empty per-bot profile."""
    if dest.resolve() == _SHARED.resolve():
        return
    if dest.name == ".browser-profile-mail":
        return
    try:
        if any(dest.iterdir()):
            return
    except Exception:
        return
    if not _SHARED.is_dir():
        return
    try:
        ignore = shutil.ignore_patterns(
            "Singleton*", "lockfile*", "RunningChrome*", "*.lock", "Crashpad")
        shutil.copytree(_SHARED, dest, dirs_exist_ok=True, ignore=ignore)
        print(f"  Seeded Chrome profile {dest.name} from .browser-profile (logins copied)")
    except Exception as e:
        print(f"  ! could not seed {dest.name} from shared profile: {e}")


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


def profile_busy(bot=None) -> bool:
    """True if Chrome is already using this bot's profile (Open bot browser / another run)."""
    profile = get_profile_dir(bot)
    # SingletonLock exists while Chrome holds the profile (symlink/file on Windows)
    for name in ("SingletonLock", "lockfile", "SingletonCookie", "SingletonSocket"):
        if (profile / name).exists():
            return True
    # Also scan running chrome.exe command lines for this user-data-dir
    try:
        needle = str(profile).replace("'", "''")
        ps = (
            f"$n='{needle}'; "
            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" -ErrorAction SilentlyContinue | "
            "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($n) } | "
            "Select-Object -First 1"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if (r.stdout or "").strip():
            return True
    except Exception:
        pass
    return False


def close_bot_chrome(bot=None) -> int:
    """Force-close Chrome processes that use this bot's profile. Returns how many were killed."""
    profile = get_profile_dir(bot)
    needle = str(profile).replace("'", "''")
    killed = 0
    try:
        ps = (
            f"$n='{needle}'; $ids=@(); "
            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" -ErrorAction SilentlyContinue | "
            "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($n) } | "
            "ForEach-Object { $ids += $_.ProcessId; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; "
            "$ids.Count"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            killed = int((r.stdout or "0").strip().splitlines()[-1] or 0)
        except Exception:
            killed = 0
    except Exception:
        pass
    # Drop stale lock files if Chrome died uncleanly
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"):
        try:
            p = profile / name
            if p.exists() or p.is_symlink():
                p.unlink(missing_ok=True)
        except Exception:
            pass
    return killed


def open_bot_chrome(urls=None, bot=None):
    """Open this bot's Chrome window (normal Chrome, per-bot profile) for manual prep.

    Close it before Apply / Make resume for that same bot (only one process per profile).
    Other bots can keep their own Chrome open. Returns the Popen handle.
    """
    profile = get_profile_dir(bot)
    chrome = find_chrome()
    tabs = list(urls) if urls is not None else list(PREP_URLS)
    args = [
        chrome,
        f"--user-data-dir={profile}",
        "--profile-directory=Default",
        "--start-maximized",
        "--no-first-run",
        "--no-default-browser-check",
        *tabs,
    ]
    return subprocess.Popen(args, cwd=str(_ROOT))


def open_mail_chrome(urls=None):
    """Open the mail Chrome (.browser-profile-mail) so you can sign in to Gmail.

    Close it before Greenhouse Apply (Playwright needs this profile for code reading).
    """
    return open_bot_chrome(urls if urls is not None else list(MAIL_PREP_URLS), bot="mail")


def open_browser(playwright, headless=False, bot=None):
    """Playwright persistent context for this bot's Chrome profile."""
    profile = get_profile_dir(bot)
    try:
        return playwright.chromium.launch_persistent_context(
            str(profile), channel="chrome", headless=headless,
            no_viewport=True,
            ignore_default_args=["--enable-automation"],
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"])
    except Exception as e:
        if "ProcessSingleton" in str(e) or "in use" in str(e).lower():
            raise SystemExit(
                f"This bot's Chrome profile is already open ({profile.name}).\n"
                f"Close that Chrome window (Open bot browser / Open mail browser) and run again.\n"
                f"(Ashby / Greenhouse / Wellfound / mail each have their own profile — "
                f"other bots can keep running.)"
            ) from e
        raise


def open_mail_browser(playwright, headless=False):
    """Playwright persistent context for the Gmail / mail profile."""
    return open_browser(playwright, headless=headless, bot="mail")


def __getattr__(name):
    # `from browser import PROFILE_DIR` always resolves to the current bot's folder
    if name == "PROFILE_DIR":
        return get_profile_dir()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
