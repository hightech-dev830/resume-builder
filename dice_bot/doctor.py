"""Checks everything the bot needs on this machine and says how to fix what is missing.
Run:  python doctor.py   (from the dice_bot folder, with the same Python you run the bot with)"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
LINUX = sys.platform.startswith("linux")
problems = 0


def ok(msg):
    print(f"  OK    {msg}")


def bad(msg, fix):
    global problems
    problems += 1
    print(f"  FAIL  {msg}\n        fix: {fix}")


print(f"Dice Auto Bid check · {platform.system()} {platform.release()} · Python {platform.python_version()} ({sys.executable})\n")

# 1. Python version
if sys.version_info < (3, 10):
    bad(f"Python {platform.python_version()} is too old", "install Python 3.10 or newer")
else:
    ok("Python version")

# 2. the rest of the repo next to dice_bot (the resume engine lives in extension-v2)
for need in ("extension-v2/lib/resume.js", "extension-v2/content/agent.js", "extension-v2/content/chat.js", "extension-v2/data/examples.json"):
    if not (REPO / need).exists():
        bad(f"missing {need}", "copy the WHOLE resume-builder folder to the server, not only dice_bot (the bot uses extension-v2)")
        break
else:
    ok("extension-v2 is next to dice_bot")

# 3. Node.js for the resume engine
node = shutil.which("node")
if not node:
    bad("Node.js not found", "Linux: curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs"
        " · Windows: install from https://nodejs.org, then open a new terminal")
else:
    v = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    if int(v.lstrip("v").split(".")[0]) < 18:
        bad(f"Node.js {v} is too old", "install Node.js 18 or newer")
    else:
        ok(f"Node.js {v}")
        if (REPO / "extension-v2/lib/resume.js").exists():
            r = subprocess.run([node, str(HERE / "engine/engine.mjs"), "templates"], input="{}", capture_output=True, text=True)
            ok("resume engine runs") if '"ok":true' in r.stdout else bad(f"resume engine failed: {(r.stdout or r.stderr)[:300]}", "send this message to Claude")

# 4. Playwright and Tk
try:
    import playwright  # noqa: F401
    ok("Playwright installed")
except ImportError:
    bad("Playwright not installed for this Python", f"{sys.executable} -m pip install -r requirements.txt")
try:
    import tkinter
    tkinter.Tcl()
    ok("Tkinter available")
except Exception as e:
    bad(f"Tkinter missing ({e})", "Linux: sudo apt install -y python3-tk (match your Python version, e.g. python3.12-tk) · Windows: reinstall Python with 'tcl/tk' ticked")

# 5. a screen (the window and the browser are visible; you sign in to Dice in that browser)
if LINUX and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    bad("no desktop / display (DISPLAY is not set)",
        "the bot needs a desktop session. Install one and connect with Remote Desktop: "
        "sudo apt install -y xfce4 xrdp && echo xfce4-session > ~/.xsession && sudo systemctl enable --now xrdp, "
        "open port 3389 in the EC2 security group (your IP only), then run the bot from a terminal inside that desktop")
else:
    ok("a display is available")

# 6. a browser Playwright can drive
try:
    from playwright.sync_api import sync_playwright
    found = []
    with sync_playwright() as p:
        for ch in ("chrome", "msedge", None):
            try:
                b = p.chromium.launch(channel=ch, headless=True)
                b.close()
                found.append(ch or "chromium")
            except Exception:
                pass
    if found:
        ok(f"browsers: {', '.join(found)} (choose one of these under Settings → Browser)")
    else:
        bad("no browser Playwright can start",
            f"{sys.executable} -m playwright install --with-deps chromium   (then Settings → Browser → Chromium)")
except ImportError:
    pass

# 7. settings that must be filled in
sys.path.insert(0, str(HERE))
try:
    from bot import config
    s = config.load()
    ok(f"Supabase {'set' if s['supabase_url'] and s['supabase_key'] else 'NOT set (bids stay in the local log; copy the repo .env or fill Settings)'}")
    if s.get("profile_path") and not Path(s["profile_path"]).exists():
        bad(f"profile JSON not found: {s['profile_path']}", "Settings → Resume / profile JSON: choose the file again (paths differ on the server)")
    if s.get("chrome_channel") == "chrome" and LINUX and not shutil.which("google-chrome"):
        print("  NOTE  Settings → Browser is 'Google Chrome' but Chrome is not installed: choose Chromium (Playwright)")
except Exception as e:
    bad(f"could not read settings: {e}", "delete dice_bot/data/settings.json and set it again")

print("\n" + ("Everything looks ready: run  python app.py" if not problems else f"{problems} problem(s) to fix first."))
