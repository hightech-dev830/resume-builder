"""Open mail Chrome (.browser-profile-mail) so you can sign in to Gmail.

Use the same Gmail address you put on Greenhouse applications. During Apply,
Playwright opens this profile alongside Greenhouse Chrome to read the
8-character security code emails from greenhouse-mail.io.

Close this Chrome before Greenhouse Apply (Playwright needs the profile).

    python open_browser_mail.py
"""
import sys

import config  # noqa: F401  — sets AUTOBID_BOT + common on path
from browser import get_mail_profile_dir, open_mail_chrome

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    profile = get_mail_profile_dir()
    print(f"Opening mail Chrome ({profile})")
    print("Sign in to Gmail (the address you use on Greenhouse applications).")
    print("Close this Chrome before Greenhouse Apply "
          "(Apply opens Greenhouse + mail profiles together via Playwright).\n")
    open_mail_chrome()
    print("Chrome launched. This script exits; the browser stays open until you close it.")


if __name__ == "__main__":
    main()
