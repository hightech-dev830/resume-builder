"""Read Greenhouse security codes from Gmail in .browser-profile-mail.

After Submit, Greenhouse emails an 8-character code (greenhouse-mail.io) with a
**Copy code** button. During Apply we open the newest matching mail, click
Copy code, read the clipboard, then paste into the job-board Security code boxes.
"""
from __future__ import annotations

import re
import time
import urllib.parse

# 8-char Greenhouse OTP (letters + digits). Avoid matching long hex / tracking ids.
_CODE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{8})(?![A-Za-z0-9])")
# Subject / UI words that look like 8-char "codes" but are not the OTP
_BAD = re.compile(
    r"(?i)^(security|code|verify|account|continue|password|username|greenhouse|"
    r"https?|mailto|subject|message|thanks|please|oddball|applicat)$"
)

GMAIL = "https://mail.google.com/mail/u/0/"
# Inbox search for Greenhouse application security codes
_SEARCH = (
    "from:(greenhouse-mail.io OR greenhouse.io) "
    '("Security code" OR "verification code" OR "security-code") newer_than:1d'
)


def _gmail_search_url(query: str) -> str:
    return GMAIL + "#search/" + urllib.parse.quote(query)


def gmail_logged_in(page) -> bool:
    try:
        url = page.url or ""
        if "accounts.google.com" in url and "ServiceLogin" in url:
            return False
        if page.locator("input[type=email][name=identifier]").count():
            return False
        # Gmail shell
        if "mail.google.com" in url and page.locator(
                "[role=main], div[role=navigation], input[aria-label*='Search' i]").count():
            return True
    except Exception:
        pass
    return False


def wait_gmail_login(page, timeout_sec=300):
    """Open Gmail and wait until you finish Google sign-in in the mail Chrome."""
    page.goto(GMAIL, wait_until="domcontentloaded", timeout=120_000)
    page.wait_for_timeout(1500)
    if gmail_logged_in(page):
        return True
    print("  Mail Chrome: sign in to Gmail (the address you use on Greenhouse applications).")
    print(f"  Waiting up to {timeout_sec // 60} min …")
    end = time.time() + timeout_sec
    while time.time() < end:
        if gmail_logged_in(page):
            print("  Mail Chrome: Gmail ready.")
            return True
        page.wait_for_timeout(2000)
    return False


def looks_like_otp(code: str) -> bool:
    """True for an 8-char Greenhouse security code (not subject/UI words)."""
    code = re.sub(r"\s+", "", (code or "").strip())
    if len(code) != 8 or not re.fullmatch(r"[A-Za-z0-9]{8}", code):
        return False
    if _BAD.match(code):
        return False
    # Prefer real OTPs: reject obvious English words from the subject line
    if code.isalpha() and code.lower() in {
        "security", "password", "username", "continue", "account",
        "message", "thanks", "please", "oddball",
    }:
        return False
    return True


def extract_codes(text: str) -> list[str]:
    """Likely OTP strings from an email body (newest / most prominent first)."""
    found = []
    for m in _CODE_RE.finditer(text or ""):
        code = m.group(1)
        if not looks_like_otp(code):
            continue
        if code not in found:
            found.append(code)
    return found


def _open_first_result(page) -> bool:
    """Click the first (newest) thread in the current Gmail search results."""
    for sel in (
        "tr.zA",
        "div[role=main] tr.zA",
        "div[role=listitem]",
        "table.F tr.zA",
    ):
        try:
            row = page.locator(sel).first
            if row.count():
                row.click(timeout=4000)
                page.wait_for_timeout(1200)
                return True
        except Exception:
            continue
    return False


def _email_body_text(page) -> str:
    for sel in (
        "div.a3s",
        "div[data-message-id] div.a3s",
        "div[role=list] div.a3s",
        "div[role=main]",
    ):
        try:
            loc = page.locator(sel).first
            if loc.count():
                t = loc.inner_text(timeout=3000) or ""
                if len(t) > 20:
                    return t
        except Exception:
            continue
    try:
        return page.locator("body").inner_text(timeout=3000) or ""
    except Exception:
        return ""


def _grant_clipboard(page):
    try:
        page.context.grant_permissions(
            ["clipboard-read", "clipboard-write"],
            origin="https://mail.google.com",
        )
    except Exception:
        pass


def _read_clipboard(page) -> str:
    _grant_clipboard(page)
    try:
        return (page.evaluate("() => navigator.clipboard.readText()") or "").strip()
    except Exception:
        return ""


def _click_copy_code(page) -> bool:
    """Click the Greenhouse email's 'Copy code' button in the open message."""
    # Prefer the open message pane so we don't hit a list-row ghost
    scopes = [
        "div.a3s",
        "div[data-message-id]",
        "div[role=list] div.a3s",
        "div[role=main]",
        "body",
    ]
    labels = ("Copy code", "Copy Code", "Copy security code")
    for scope in scopes:
        root = page.locator(scope).first
        try:
            if not root.count():
                continue
        except Exception:
            continue
        for label in labels:
            for sel in (
                f'button:has-text("{label}")',
                f'a:has-text("{label}")',
                f'[role=button]:has-text("{label}")',
                f'div[role=button]:has-text("{label}")',
                f'text="{label}"',
            ):
                try:
                    btn = root.locator(sel).first
                    if not btn.count():
                        continue
                    btn.scroll_into_view_if_needed(timeout=2000)
                    btn.click(timeout=4000)
                    page.wait_for_timeout(500)
                    return True
                except Exception:
                    continue
    return False


def _code_near_copy_button(page) -> str | None:
    """DOM fallback: 8-char OTP near the Copy code control (if clipboard blocked)."""
    try:
        code = page.evaluate(
            """() => {
              const labels = ['copy code', 'copy security code'];
              const all = Array.from(document.querySelectorAll(
                'button, a, [role=button], div, span'));
              let btn = null;
              for (const el of all) {
                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (labels.includes(t)) { btn = el; break; }
              }
              if (!btn) return '';
              let root = btn.closest('div.a3s, table, td, div') || btn.parentElement;
              for (let i = 0; i < 6 && root; i++) {
                const text = (root.innerText || '').replace(/\\s+/g, ' ');
                const m = text.match(/(?<![A-Za-z0-9])([A-Za-z0-9]{8})(?![A-Za-z0-9])/g);
                if (m) {
                  for (const raw of m) {
                    const c = raw.trim();
                    if (!/^(security|password|username|continue|account|message|thanks|please|oddball)$/i.test(c))
                      return c;
                  }
                }
                root = root.parentElement;
              }
              return '';
            }"""
        )
        if looks_like_otp(code or ""):
            return (code or "").strip()
    except Exception:
        pass
    return None


def copy_code_from_open_email(page) -> str | None:
    """Click Copy code on the open mail and return the 8-char OTP from the clipboard."""
    _grant_clipboard(page)
    if not _click_copy_code(page):
        return None
    # Clipboard may update slightly after the click
    for _ in range(6):
        clip = _read_clipboard(page)
        # Clipboard should be the bare code, not the subject sentence
        clip_one = re.sub(r"\s+", "", clip.splitlines()[0] if clip else "")
        if looks_like_otp(clip_one):
            return clip_one[:8]
        # Sometimes clipboard has "Code: XXXXXXXX"
        for c in extract_codes(clip):
            if looks_like_otp(c):
                return c
        page.wait_for_timeout(250)
    # Clipboard blocked or empty — scrape near the button
    return _code_near_copy_button(page)


def fetch_greenhouse_code(page, company: str = "", wait_sec: int = 90) -> str | None:
    """Open the newest Greenhouse security-code email, click Copy code, return OTP.

    `page` must be a tab in the mail Playwright context (.browser-profile-mail).
    """
    query = _SEARCH
    if company:
        # Prefer the company-named subject when we know it
        safe = re.sub(r"[^\w\s\-]", " ", company)[:60].strip()
        if safe:
            query = (
                f'from:(greenhouse-mail.io OR greenhouse.io) '
                f'"Security code for your application to" {safe} newer_than:1d'
            )
    url = _gmail_search_url(query)
    end = time.time() + wait_sec
    last_err = ""
    while time.time() < end:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2000)
            if not gmail_logged_in(page):
                if not wait_gmail_login(page, timeout_sec=min(120, int(end - time.time()))):
                    return None
                continue
            if not _open_first_result(page):
                # broaden search once
                page.goto(_gmail_search_url(_SEARCH), wait_until="domcontentloaded",
                          timeout=60_000)
                page.wait_for_timeout(2000)
                if not _open_first_result(page):
                    last_err = "no matching email yet"
                    page.wait_for_timeout(4000)
                    continue

            # Primary: click "Copy code" (not the subject sentence)
            code = copy_code_from_open_email(page)
            if code and looks_like_otp(code):
                print(f"    Gmail: Copy code → {code}")
                return code

            # Last resort: parse body, but never accept subject words like "Security"
            body = _email_body_text(page)
            codes = extract_codes(body)
            # Prefer codes that appear near “code” wording and contain a digit
            ranked = sorted(
                codes,
                key=lambda c: (
                    0 if re.search(r"\d", c) else 1,
                    0 if re.search(
                        rf"(?i)(code|security).{{0,40}}{re.escape(c)}|"
                        rf"{re.escape(c)}.{{0,40}}(code|security)",
                        body,
                    ) else 1,
                ),
            )
            for c in ranked:
                if looks_like_otp(c):
                    print(f"    Gmail: code from email body → {c}")
                    return c
            last_err = "opened mail but Copy code / OTP not found"
        except Exception as e:
            last_err = str(e).splitlines()[0][:120]
        page.wait_for_timeout(4000)
    print(f"    ! Gmail code not found ({last_err})")
    return None
