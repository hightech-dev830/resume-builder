"""Read Greenhouse security codes from Gmail or Outlook in .browser-profile-mail.

When a bid asks for a verification code, Greenhouse emails it ("Here's your MyGreenhouse
security code" / "Security code for your application to …"). fetch_greenhouse_code()
looks at the mail list (newest first), prefers an UNREAD code email, opens it and reads
the code from the body ("Your security code is: XXXXXXXX"; Gmail: Copy code button).
Codes already used are remembered (.cache/mail_codes_used.json) so an old email is never
typed twice. Provider = "gmail" | "outlook" (the app's "Codes from" setting).
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from pathlib import Path

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


def _legacy_gmail_fetch(page, company: str = "", wait_sec: int = 90) -> str | None:
    """Previous Gmail-only search + Copy code flow (kept as a fallback)."""
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


# ====================================================================== Gmail + Outlook

PROVIDERS = ("gmail", "outlook")
MAIL_URLS = {
    "gmail": GMAIL + "#inbox",
    "outlook": "https://outlook.live.com/mail/0/",
}
MAIL_LABEL = {"gmail": "Gmail", "outlook": "Outlook"}

# Subjects of Greenhouse code emails (MyGreenhouse + per-application codes)
_SUBJECT_RE = (r"MyGreenhouse security code|security code for your application|"
               r"your security code|verification code")
# Gmail search: every category (Primary / Updates / …), newest first
_GMAIL_CODE_SEARCH = '"security code" newer_than:1d'

_USED_PATH = Path(__file__).resolve().parent.parent / ".cache" / "mail_codes_used.json"


def normalize_provider(provider) -> str:
    p = str(provider or "").strip().lower()
    return p if p in PROVIDERS else "gmail"


def _used_codes() -> list:
    try:
        return list(json.loads(_USED_PATH.read_text(encoding="utf-8")))
    except Exception:
        return []


def mark_code_used(code: str):
    """Remember a code that was typed, so the same email is never used again."""
    codes = [c for c in _used_codes() if c != code] + [code]
    try:
        _USED_PATH.parent.mkdir(parents=True, exist_ok=True)
        _USED_PATH.write_text(json.dumps(codes[-100:]), encoding="utf-8")
    except Exception:
        pass


def outlook_logged_in(page) -> bool:
    try:
        url = (page.url or "").lower()
        if "login.live.com" in url or "login.microsoftonline.com" in url:
            return False
        if ("outlook.live.com" in url or "outlook.office" in url) and "/mail" in url:
            return bool(page.locator(
                "[role=listbox], [role=tree], #topSearchInput, "
                "input[aria-label*='Search' i], button[aria-label*='New mail' i]").count())
    except Exception:
        pass
    return False


def mail_logged_in(page, provider="gmail") -> bool:
    return outlook_logged_in(page) if normalize_provider(provider) == "outlook" \
        else gmail_logged_in(page)


def wait_mail_login(page, provider="gmail", timeout_sec=300):
    """Open the mailbox and wait until you are signed in in the mail Chrome."""
    provider = normalize_provider(provider)
    name = MAIL_LABEL[provider]
    page.goto(MAIL_URLS[provider], wait_until="domcontentloaded", timeout=120_000)
    end = time.time() + timeout_sec
    told = False
    while time.time() < end:
        page.wait_for_timeout(1500)
        if mail_logged_in(page, provider):
            if told:
                print(f"  Mail Chrome: {name} ready.")
            return True
        if not told:
            print(f"  Mail Chrome: sign in to {name} "
                  f"(the address you use on Greenhouse applications).")
            print(f"  Waiting up to {timeout_sec // 60} min …")
            told = True
    return False


# Mark matching message rows (newest first) with data-ab-mail=<n>; report unread state.
_ROWS_JS = r"""(pat) => {
  const re = new RegExp(pat, 'i');
  document.querySelectorAll('[data-ab-mail]').forEach(e => e.removeAttribute('data-ab-mail'));
  const rows = document.querySelectorAll(
    'tr.zA, div[role="option"], div[role="listitem"][data-convid], div[data-convid]');
  const out = [];
  for (const r of rows) {
    if (!r.getClientRects().length) continue;
    if (r.parentElement && r.parentElement.closest('[data-ab-mail]')) continue;
    const label = r.getAttribute('aria-label') || '';
    const text = (r.innerText || '').replace(/\s+/g, ' ').trim();
    if (!re.test(label + ' ' + text)) continue;
    // unread: Gmail row class zE; Outlook aria-label "Unread …"; else a bold subject line
    let unread = r.classList.contains('zE') || /\bunread\b/i.test(label);
    if (!unread) {
      for (const el of r.querySelectorAll('span, div')) {
        if (el.children.length || !re.test(el.textContent || '')) continue;
        if (parseInt(getComputedStyle(el).fontWeight, 10) >= 600) { unread = true; break; }
      }
    }
    r.setAttribute('data-ab-mail', String(out.length));
    out.push({ text: text.slice(0, 300), unread });
  }
  return out;
}"""

# Text of the open message(s), newest message first.
_BODY_JS = r"""(provider) => {
  const vis = e => e.getClientRects().length > 0;
  let bodies = [];
  if (provider === 'gmail') {
    bodies = [...document.querySelectorAll('div.a3s')].filter(vis).reverse();  // thread: newest last
  } else {
    bodies = [...document.querySelectorAll(
      '[aria-label="Message body"], div[role="document"], div.allowTextSelection')].filter(vis);
  }
  return bodies.map(b => (b.innerText || '').trim()).filter(Boolean);
}"""

_CODE_IN_TEXT = (
    re.compile(r"(?i)security code(?: is)?\s*[:\-]?\s*([A-Za-z0-9]{6,10})(?![A-Za-z0-9])"),
    re.compile(r"(?i)verification code(?: is)?\s*[:\-]?\s*([A-Za-z0-9]{6,10})(?![A-Za-z0-9])"),
    re.compile(r"(?i)\bcode(?: is)?\s*[:\-]\s*([A-Za-z0-9]{6,10})(?![A-Za-z0-9])"),
)


def code_from_text(text: str) -> list:
    """Codes in an email text, in order: “Your security code is: X” first, then 8-char OTPs."""
    out = []
    for rx in _CODE_IN_TEXT:
        for m in rx.finditer(text or ""):
            c = m.group(1)
            if (c.lower() not in ("is", "for", "below", "within", "expires")
                    and not _BAD.match(c) and re.search(r"\d", c) and c not in out):
                out.append(c)
    for c in extract_codes(text):
        if re.search(r"\d", c) and c not in out:
            out.append(c)
    return out


def _list_rows(page, provider):
    """Code-email rows in the list, newest first (Outlook: Focused, then Other)."""
    rows = page.evaluate(_ROWS_JS, _SUBJECT_RE) or []
    if rows or provider != "outlook":
        return rows
    for tab in ("Other", "Focused"):
        try:
            t = page.get_by_role("tab", name=re.compile(rf"^{tab}$", re.I)).first
            if t.count() and t.get_attribute("aria-selected") != "true":
                t.click(timeout=3000)
                page.wait_for_timeout(1200)
                rows = page.evaluate(_ROWS_JS, _SUBJECT_RE) or []
                if rows:
                    return rows
        except Exception:
            continue
    return rows


def _refresh_list(page, provider, first):
    if provider == "gmail":
        page.goto(_gmail_search_url(_GMAIL_CODE_SEARCH), wait_until="domcontentloaded",
                  timeout=60_000)
    elif first:
        page.goto(MAIL_URLS["outlook"], wait_until="domcontentloaded", timeout=60_000)
    else:
        # Outlook updates its list live; reload only while nothing new showed up
        page.reload(wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(2500)


def fetch_greenhouse_code(page, company: str = "", wait_sec: int = 90,
                          provider: str = "gmail") -> str | None:
    """Wait for the Greenhouse security-code email and return the code.

    The newest UNREAD "Here's your MyGreenhouse security code" / "Security code for your
    application" email wins; else the newest one whose code was not used before.
    `page` is a tab in the mail Chrome (.browser-profile-mail); provider gmail | outlook.
    """
    provider = normalize_provider(provider)
    name = MAIL_LABEL[provider]
    used = set(_used_codes())
    end = time.time() + wait_sec
    first, last_err = True, ""
    while time.time() < end:
        try:
            _refresh_list(page, provider, first)
            first = False
            if not mail_logged_in(page, provider):
                if not wait_mail_login(page, provider,
                                       timeout_sec=max(30, min(120, int(end - time.time())))):
                    return None
                first = True
                continue
            rows = _list_rows(page, provider)
            if not rows:
                last_err = "no security-code email yet"
                page.wait_for_timeout(3000)
                continue
            # unread code emails first (newest first), then the newest read one
            order = [i for i, r in enumerate(rows) if r["unread"]] + \
                    [i for i, r in enumerate(rows) if not r["unread"]][:1]
            for i in order:
                # the list preview often shows the whole code already
                codes = [c for c in code_from_text(rows[i]["text"]) if c not in used]
                code = next((c for c in codes if len(c) >= 8), None)
                if not code:
                    page.locator(f'[data-ab-mail="{i}"]').first.click(timeout=5000)
                    page.wait_for_timeout(1500)
                    bodies = page.evaluate(_BODY_JS, provider) or []
                    codes = [c for b in bodies for c in code_from_text(b) if c not in used]
                    code = codes[0] if codes else None
                    if not code and provider == "gmail":
                        c = copy_code_from_open_email(page)
                        code = c if c and c not in used else None
                    if provider == "gmail":
                        page.go_back(wait_until="domcontentloaded", timeout=30_000)
                        page.wait_for_timeout(1200)
                        page.evaluate(_ROWS_JS, _SUBJECT_RE)   # re-mark rows after going back
                if code:
                    state = "unread" if rows[i]["unread"] else "newest"
                    print(f"    {name}: security code from the {state} email → {code}")
                    mark_code_used(code)
                    return code
            last_err = "only already-used codes so far"
        except Exception as e:
            last_err = str(e).splitlines()[0][:120]
        page.wait_for_timeout(3000)
    print(f"    ! {name}: security code not found ({last_err})")
    return None
