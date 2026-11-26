"""Open mail Chrome (.browser-profile-mail) so you can sign in to Gmail or Outlook.

Use the mailbox you put on Greenhouse applications. During Apply, Playwright opens
this profile alongside Greenhouse Chrome to read the security-code emails
("Here's your MyGreenhouse security code" / "Security code for your application").

Close this Chrome before Greenhouse Apply (Playwright needs the profile).

    python open_browser_mail.py            # Gmail
    python open_browser_mail.py outlook    # Outlook
"""
import sys

import config  # noqa: F401  — sets AUTOBID_BOT + common on path
from browser import get_mail_profile_dir, open_mail_chrome
from mail_code import MAIL_LABEL, MAIL_URLS, normalize_provider

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(provider="gmail"):
    provider = normalize_provider(provider)
    name = MAIL_LABEL[provider]
    profile = get_mail_profile_dir()
    print(f"Opening mail Chrome ({profile})")
    print(f"Sign in to {name} (the address you use on Greenhouse applications).")
    print("Close this Chrome before Greenhouse Apply "
          "(Apply opens Greenhouse + mail profiles together via Playwright).\n")
    open_mail_chrome([MAIL_URLS[provider]])
    print("Chrome launched. This script exits; the browser stays open until you close it.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "gmail")
