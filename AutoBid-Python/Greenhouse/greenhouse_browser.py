"""Playwright helpers for MyGreenhouse (login → search → job modal → apply)."""
from __future__ import annotations

import hashlib
import re
import time
from urllib.parse import urljoin, urlparse

from browser import open_browser
from config import (LOGIN_URL, LOGIN_WAIT_MIN, SEARCH_TODAY_DATE_VALUES,
                    SEARCH_URL, SEARCH_URL_TODAY, SCROLL_MAX_CARDS,
                    SCROLL_MAX_ROUNDS, SCROLL_PAUSE_SEC, SCROLL_STABLE_ROUNDS)


def open_ctx(playwright, headless=False):
    """Use Greenhouse's Chrome profile (.browser-profile-greenhouse).

    If Open bot browser / another Collect/Apply still holds that profile, fail clearly.
    """
    from browser import get_profile_dir
    try:
        return open_browser(playwright, headless=headless)
    except SystemExit as e:
        msg = str(e)
        if "already open" in msg.lower() or "in use" in msg.lower():
            raise SystemExit(
                f"Greenhouse Chrome is already open ({get_profile_dir().name}).\n"
                "That is usually the window from “Open bot browser”.\n"
                "Close it completely, then click Apply / Collect again.\n"
                "(Ashby can keep its own Chrome open — different profile.)"
            ) from e
        raise


def dismiss_cookies(page):
    for text in ("Ok", "OK", "Accept", "Agree", "Got it"):
        try:
            page.locator(f"button:has-text('{text}')").first.click(timeout=1500)
            time.sleep(0.2)
            return
        except Exception:
            pass


def _page_text_has(page, pattern: str) -> bool:
    try:
        return bool(page.locator(f"text=/{pattern}/i").count())
    except Exception:
        return False


def is_logged_in(page) -> bool:
    """Positive login signals (Picture: profile menu with Log out, Jobs search)."""
    url = (page.url or "").lower()
    if "my.greenhouse.io" not in url:
        return False
    if "sign_in" in url or "/users/sign_in" in url:
        # still may be mid-redirect after code entry — only trust DOM signals below
        pass
    else:
        # any authenticated app route
        if any(p in url for p in ("/jobs", "/home", "/profile", "/applications", "/dashboard")):
            return True
    try:
        # "Log out" in the profile menu, or Browse jobs / Jobs nav after login
        if page.evaluate("""() => {
          const t = (document.body && document.body.innerText) || '';
          if (/\\blog out\\b/i.test(t)) return true;
          if (/browse jobs/i.test(t) && /date posted|work type|search for a job/i.test(t))
            return true;
          // avatar / account menu without sign-in CTA
          if (document.querySelector('a[href*="sign_out"], button[aria-label*="account" i], [data-testid*="avatar"]'))
            return true;
          return false;
        }"""):
            return True
    except Exception:
        pass
    if _page_text_has(page, r"Log out") or _page_text_has(page, r"Browse jobs"):
        return True
    return False


def is_login_page(page) -> bool:
    if is_logged_in(page):
        return False
    url = (page.url or "").lower()
    if "sign_in" in url or "/users/sign_in" in url:
        return True
    try:
        if page.locator("text=/Send security code/i").count():
            return True
        # marketing headline only counts if we are clearly on the gate, not elsewhere
        if page.locator("text=/Apply for what's next/i").count() and \
                page.locator("text=/Send security code|Or continue with/i").count():
            return True
    except Exception:
        pass
    return False


def _any_context_page_logged_in(page):
    """User may finish login in another tab of the same bot Chrome."""
    ctx = page.context
    for p in list(ctx.pages):
        try:
            if p.is_closed():
                continue
            if is_logged_in(p):
                return p
        except Exception:
            continue
    return None


def wait_until_logged_in(page, minutes=LOGIN_WAIT_MIN):
    """Picture 1: wait until MyGreenhouse session is real (Log out / Jobs), not only URL change."""
    print(f"  Waiting up to {minutes} min for MyGreenhouse login …")
    print("  Finish login in THIS bot Chrome window (email → security code, or Google / LinkedIn / Apple).")
    print("  Tip: after the code works, open Jobs — the bot looks for “Log out” / Browse jobs.")
    end = time.time() + minutes * 60
    reminded = 0
    last_probe = 0
    while time.time() < end:
        hit = _any_context_page_logged_in(page)
        if hit is not None:
            print(f"  ✓ Logged in ({hit.url})")
            if hit is not page and not page.is_closed():
                try:
                    page.bring_to_front()
                except Exception:
                    pass
            return True
        if not is_login_page(page) and "my.greenhouse.io" in (page.url or "").lower():
            # left the gate; double-check by probing search once
            if is_logged_in(page) or "jobs" in (page.url or "").lower():
                print(f"  ✓ Logged in ({page.url})")
                return True
        # every ~25s, probe SEARCH_URL in case cookies landed but this tab is stuck on sign_in
        if time.time() - last_probe > 25:
            last_probe = time.time()
            try:
                probe = page.context.new_page()
                probe.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=45_000)
                dismiss_cookies(probe)
                probe.wait_for_timeout(600)
                if is_logged_in(probe):
                    print(f"  ✓ Logged in (session OK → {probe.url})")
                    try:
                        page.goto(probe.url, wait_until="domcontentloaded", timeout=60_000)
                    except Exception:
                        pass
                    probe.close()
                    return True
                probe.close()
            except Exception:
                try:
                    probe.close()
                except Exception:
                    pass
        if time.time() - reminded > 90:
            print("  … still waiting — use the Playwright/bot Chrome window (close “Open bot browser” first if it is open)")
            print(f"     current tab: {page.url}")
            reminded = time.time()
        time.sleep(1.0)
    raise RuntimeError("MyGreenhouse login timed out — close other bot Chrome, run Collect, sign in in that window")


def ensure_logged_in(page):
    """Prefer Jobs search: if the shared profile already has a session, skip the gate."""
    print("  Checking MyGreenhouse session …")
    try:
        page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=120_000)
        dismiss_cookies(page)
        page.wait_for_timeout(900)
    except Exception as e:
        print(f"  ! search page load: {e}")
    if is_logged_in(page):
        print(f"  Already signed in ({page.url})")
        return
    # not logged in — show the gate and wait
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=120_000)
    dismiss_cookies(page)
    page.wait_for_timeout(800)
    if is_logged_in(page):
        print(f"  Already signed in ({page.url})")
        return
    wait_until_logged_in(page)


def go_search(page, url=None):
    url = url or SEARCH_URL
    page.goto(url, wait_until="domcontentloaded", timeout=120_000)
    dismiss_cookies(page)
    page.wait_for_timeout(1500)
    if is_login_page(page) or not is_logged_in(page):
        wait_until_logged_in(page)
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_timeout(1500)


def posted_looks_like_today(text: str) -> bool:
    """True if a MyGreenhouse 'Posted · …' label is from today (not yesterday / N days ago)."""
    t = (text or "").lower()
    if not t:
        return False
    if re.search(r"\b(yesterday|\d+\s*days?\s*ago|\d+\s*weeks?\s*ago|month|last week)\b", t):
        return False
    if re.search(r"just now|\d+\s*min|\d+\s*minute|\d+\s*hour|\btoday\b|posted\s*·\s*$", t):
        return True
    # "Posted · 3 hours ago" / "Viewed · 7 minutes ago"
    if re.search(r"(posted|viewed).{0,20}\d+\s*(min|minute|hour)", t):
        return True
    return False


def apply_today_date_filter(page) -> bool:
    """Set Date posted → Today / Past 24 hours via the filter control (fallback to URL)."""
    # Prefer clicking the filter so the SPA state matches the UI
    labels = (
        "Today", "Past 24 hours", "Past 24 Hours", "Within 24 hours",
        "Within 1 day", "Last 24 hours", "Past day", "1 day",
    )
    try:
        # Open the Date posted dropdown
        for open_sel in (
            'button:has-text("Date posted")',
            'button:has-text("Within")',
            '[aria-label*="Date posted" i]',
            'text=/Date posted/i',
        ):
            try:
                page.locator(open_sel).first.click(timeout=2000)
                page.wait_for_timeout(400)
                break
            except Exception:
                continue
        for lab in labels:
            try:
                opt = page.get_by_role("option", name=re.compile(re.escape(lab), re.I))
                if opt.count():
                    opt.first.click(timeout=2000)
                    page.wait_for_timeout(1200)
                    print(f"  Date posted filter → {lab}")
                    return True
            except Exception:
                pass
            try:
                page.locator(f'[role="option"]:has-text("{lab}"), '
                             f'button:has-text("{lab}"), '
                             f'li:has-text("{lab}"), '
                             f'label:has-text("{lab}")').first.click(timeout=1500)
                page.wait_for_timeout(1200)
                print(f"  Date posted filter → {lab}")
                return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def go_search_today(page):
    """Jobs search: remote + posted today (past day)."""
    print(f"  Today's search: {SEARCH_URL_TODAY}")
    go_search(page, SEARCH_URL_TODAY)
    if apply_today_date_filter(page):
        return
    # URL param may use a different enum — try alternates
    base = "https://my.greenhouse.io/jobs/search"
    for val in SEARCH_TODAY_DATE_VALUES:
        if val == "past_day":
            continue
        url = f"{base}?date_posted={val}&work_type[]=remote"
        print(f"  Trying date_posted={val} …")
        go_search(page, url)
        if apply_today_date_filter(page):
            return
        # If the chip already says Today / 24 hours, stop
        try:
            if page.locator("text=/Date posted.*(Today|24 hour|1 day|Past day)/i").count():
                print(f"  Date posted chip looks like today ({val})")
                return
        except Exception:
            pass
    print("  Using search page as loaded (could not confirm Today chip — will filter by Posted label)")


def _scroll_page_to_bottom(page):
    """Scroll window + every tall overflow container (MyGreenhouse lazy-loads in a pane)."""
    page.evaluate("""() => {
      const roots = [
        document.scrollingElement, document.documentElement, document.body,
        ...document.querySelectorAll('main, [role="main"], [class*="scroll" i], [class*="results" i], [class*="feed" i], [class*="list" i], [class*="Jobs" i]')
      ].filter(Boolean);
      const seen = new Set();
      for (const el of roots) {
        if (seen.has(el)) continue;
        seen.add(el);
        try {
          const can = el.scrollHeight > el.clientHeight + 40;
          if (can || el === document.scrollingElement || el === document.documentElement || el === document.body) {
            el.scrollTop = el.scrollHeight;
            el.scrollTo && el.scrollTo(0, el.scrollHeight);
          }
        } catch (e) {}
      }
      window.scrollTo(0, document.body.scrollHeight);
    }""")
    # Also nudge with the mouse wheel (some listeners only fire on wheel)
    try:
        page.mouse.wheel(0, 4000)
    except Exception:
        pass
    # Click "Load more" / "Show more" if present
    for text in ("Load more", "Show more", "See more", "More jobs"):
        try:
            btn = page.locator(f'button:has-text("{text}"), a:has-text("{text}")').first
            if btn.count() and btn.is_visible():
                btn.click(timeout=1500)
                break
        except Exception:
            pass


def count_job_cards(page) -> int:
    try:
        return len(list_job_cards(page) or [])
    except Exception:
        return 0


def scroll_results(page, max_rounds=None, stable_rounds=None, pause=None, max_cards=None):
    """Keep scrolling until the Browse jobs grid stops growing (infinite scroll)."""
    max_rounds = SCROLL_MAX_ROUNDS if max_rounds is None else max_rounds
    stable_rounds = SCROLL_STABLE_ROUNDS if stable_rounds is None else stable_rounds
    pause = SCROLL_PAUSE_SEC if pause is None else pause
    max_cards = SCROLL_MAX_CARDS if max_cards is None else max_cards

    prev = count_job_cards(page)
    print(f"  Scroll: {prev} job card(s) visible — loading more …")
    stable = 0
    for i in range(1, max_rounds + 1):
        _scroll_page_to_bottom(page)
        time.sleep(pause)
        # wait briefly for network after scroll (lazy fetch)
        try:
            page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:
            pass
        n = count_job_cards(page)
        if n > prev:
            print(f"  Scroll {i}: {n} cards (+{n - prev})")
            prev = n
            stable = 0
        else:
            stable += 1
            if stable == 1 and i % 5 == 0:
                print(f"  Scroll {i}: still {n} cards (waiting for more …)")
        if max_cards and n >= max_cards:
            print(f"  Scroll stop: reached max_cards={max_cards}")
            break
        if stable >= stable_rounds:
            print(f"  Scroll done: {n} card(s) after {i} rounds (no new cards for {stable_rounds} rounds)")
            break
    else:
        print(f"  Scroll capped at {max_rounds} rounds → {prev} card(s)")
    # Jump back to top so opening cards starts from the first row
    try:
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(400)
    except Exception:
        pass
    return prev


def list_job_cards(page):
    """Cards on the Browse jobs grid (Picture 2)."""
    return page.evaluate("""() => {
      const cards = [];
      const seen = new Set();
      // Prefer job-looking blocks that include Posted/Viewed/Remote (grid cards)
      const candidates = [];
      for (const el of document.querySelectorAll('a, div, article, li, section')) {
        const text = (el.innerText || '').trim();
        if (!text || text.length < 12 || text.length > 800) continue;
        if (!/posted|viewed/i.test(text)) continue;
        if (!/remote|united states|\\$/i.test(text)) continue;
        const lines = text.split('\\n').map(s => s.trim()).filter(Boolean);
        if (lines.length < 2) continue;
        // title is usually first meaningful line (skip "Remote" alone)
        let title = lines[0];
        let company = lines[1] || '';
        // if first line is a tag, try heading
        const heading = el.querySelector('h1, h2, h3, h4, [class*="title" i], [class*="Title"]');
        if (heading) {
          const ht = (heading.innerText || '').trim().split('\\n')[0];
          if (ht && ht.length >= 4 && ht.length <= 140) title = ht;
        }
        if (!title || title.length < 4 || title.length > 140) continue;
        if (/^remote$/i.test(title) || /^posted/i.test(title)) continue;
        if (/^remote$/i.test(company) || /^posted/i.test(company) || /^viewed/i.test(company)) {
          company = lines.find((l, i) => i > 0 && !/^(remote|posted|viewed|united states)/i.test(l)) || company;
        }
        if (!company || company.length > 80) continue;
        const key = title.toLowerCase() + '|' + company.toLowerCase();
        if (seen.has(key)) continue;
        // Prefer the smallest element that still has title+company (avoid page chrome)
        candidates.push({ el, title, company, lines, key, len: text.length });
      }
      candidates.sort((a, b) => a.len - b.len);
      for (const c of candidates) {
        if (seen.has(c.key)) continue;
        // skip if a smaller ancestor-like card already took this key
        seen.add(c.key);
        let href = '';
        const a = c.el.closest('a') || c.el.querySelector('a') || (c.el.tagName === 'A' ? c.el : null);
        if (a && a.href) href = a.href;
        cards.push({
          title: c.title,
          company: c.company,
          text: c.lines.slice(0, 8).join(' · '),
          href: href.startsWith('http') ? href : '',
        });
      }
      return cards;
    }""")


def job_id_from(title, company, apply_url=""):
    raw = (apply_url or f"{company}|{title}").strip().lower()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def open_job_card(page, title, company=""):
    """Click a job title on the search grid to open the detail modal (Picture 3)."""
    # Try exact title link/button
    for sel in (
        f'text="{title}"',
        f'a:has-text("{title[:60]}")',
        f'h2:has-text("{title[:60]}")',
        f'h3:has-text("{title[:60]}")',
    ):
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=3000)
                page.wait_for_timeout(1200)
                return True
        except Exception:
            continue
    return False


def extract_modal_details(page):
    """From the open job modal: description + Apply button href (Picture 3)."""
    data = page.evaluate("""() => {
      const dialog = document.querySelector('[role="dialog"]')
                  || document.querySelector('[class*="Modal"]')
                  || document.querySelector('[class*="Drawer"]')
                  || document.body;
      const title = (dialog.querySelector('h1, h2')?.innerText || '').trim().split('\\n')[0];
      let company = '';
      const lines = (dialog.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
      if (lines.length > 1) company = lines[1];
      // Apply link
      let apply = '';
      for (const a of dialog.querySelectorAll('a, button')) {
        const t = (a.innerText || a.getAttribute('aria-label') || '').trim();
        if (/^apply$/i.test(t) || /^apply now$/i.test(t)) {
          apply = a.href || a.getAttribute('href') || '';
          if (!apply) {
            const parentA = a.closest('a');
            if (parentA) apply = parentA.href || '';
          }
          break;
        }
      }
      // Description body
      let desc = '';
      const about = [...dialog.querySelectorAll('h2, h3, div, section')].find(
        el => /responsibilit|about|overview|what you/i.test(el.innerText || '')
      );
      if (about) {
        const root = about.closest('section,div') || about.parentElement || dialog;
        desc = (root.innerText || '').trim().slice(0, 30000);
      } else {
        desc = (dialog.innerText || '').trim().slice(0, 30000);
      }
      const salary = (dialog.innerText.match(/\\$[\\d,]+\\s*[-–]\\s*\\$[\\d,]+/) || [''])[0];
      const posted = (dialog.innerText.match(/Posted[^\\n]{0,40}/i) || [''])[0];
      const remote = /\\bremote\\b/i.test(dialog.innerText || '');
      return {
        title, company, apply, desc, salary, posted, remote,
        html: dialog.innerHTML.slice(0, 400000),
      };
    }""")
    apply = (data.get("apply") or "").strip()
    if apply and apply.startswith("/"):
        apply = urljoin("https://my.greenhouse.io", apply)
    # Picture 3: if Apply is a button with no href, click once and remember destination
    if not apply or apply.startswith("javascript"):
        apply = capture_apply_navigation(page) or apply
    return data, apply


def capture_apply_navigation(page):
    """Click Apply in the modal and remember the destination URL (new tab or same page)."""
    apply_btn = page.locator(
        '[role="dialog"] a:has-text("Apply"), [role="dialog"] button:has-text("Apply"), '
        '[class*="Modal"] a:has-text("Apply"), [class*="Modal"] button:has-text("Apply"), '
        'a:has-text("Apply"), button:has-text("Apply")'
    ).first
    try:
        if not apply_btn.count():
            return ""
        with page.context.expect_page(timeout=4000) as new_info:
            apply_btn.click(timeout=3000)
        new_page = new_info.value
        new_page.wait_for_load_state("domcontentloaded", timeout=30_000)
        url = new_page.url or ""
        new_page.close()
        return url if "greenhouse" in url or "/jobs/" in url else url
    except Exception:
        pass
    try:
        before = page.url
        apply_btn.click(timeout=3000)
        page.wait_for_timeout(2000)
        url = page.url
        if url != before and ("greenhouse" in url or "application" in url.lower()):
            # go back to search modal context if we navigated away
            page.go_back(wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(800)
            return url
        # maybe opened hash #app on boards.greenhouse.io in same tab via SPA
        if "/jobs/" in url and url != before:
            return url
    except Exception:
        pass
    return ""


def close_modal(page):
    for sel in ('button[aria-label="Close"]', 'button:has-text("Close")',
                '[role="dialog"] button:has(svg)', 'button:has-text("×")'):
        try:
            page.locator(sel).first.click(timeout=1200)
            page.wait_for_timeout(400)
            return
        except Exception:
            pass
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass


def extract_apply_form_fields(page):
    """Discover visible apply-form fields (including long required custom questions)."""
    return page.evaluate("""() => {
      const root = document.querySelector('#application')
                || document.querySelector('form#application-form')
                || document.querySelector('form[id*="application"]')
                || document.querySelector('[role="dialog"]')
                || document.querySelector('form')
                || document.body;
      const fields = [];
      const seen = new Set();
      for (const lab of root.querySelectorAll('label')) {
        const rawLab = (lab.innerText || '').trim();
        // First line only; keep long titles (salary / address questions often > 80 chars)
        let title = rawLab.replace(/\\s*\\*$/,'').trim().split('\\n')[0].trim();
        if (!title || title.length < 2) continue;
        if (title.length > 200) title = title.slice(0, 200);
        const required = /\\*/.test(rawLab) || lab.querySelector('.required, [aria-required=true]') != null;
        let el = lab.querySelector('input,textarea,select');
        if (!el && lab.htmlFor) {
          try { el = root.querySelector('#' + CSS.escape(lab.htmlFor)); } catch (e) {}
        }
        if (!el) {
          // Greenhouse often puts the control next to / under the label
          const wrap = lab.closest('div, fieldset, li') || lab.parentElement;
          if (wrap) el = wrap.querySelector('input:not([type=hidden]),textarea,select');
        }
        if (!el) continue;
        const key = (el.name || el.id || title).toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        let type = 'String';
        const t = (el.type || '').toLowerCase();
        if (el.tagName === 'TEXTAREA') type = 'LongText';
        else if (el.tagName === 'SELECT') type = 'ValueSelect';
        else if (t === 'email') type = 'Email';
        else if (t === 'tel') type = 'Phone';
        else if (t === 'file') type = 'File';
        else if (t === 'checkbox' || t === 'radio') type = 'Boolean';
        fields.push({
          id: el.name || el.id || title, title, type, required,
          path: el.name || el.id || title, form: 'application',
          options: [], description: '', raw: {tag: el.tagName, type: el.type}
        });
      }
      return fields;
    }""")


def remove_existing_resume(page):
    """Picture 4: click the X next to the already-uploaded base resume."""
    # Common patterns: button next to filename, aria-label Remove/Delete
    selectors = [
        'text=/Resume\\/CV/i >> xpath=ancestor::*[self::div or self::fieldset][1] >> button:has-text("×")',
        'text=/Resume\\/CV/i >> xpath=ancestor::*[self::div or self::fieldset][1] >> button[aria-label*="Remove" i]',
        'text=/Resume\\/CV/i >> xpath=ancestor::*[self::div or self::fieldset][1] >> button[aria-label*="Delete" i]',
        'text=/Resume\\/CV/i >> xpath=ancestor::*[self::div or self::fieldset][1] >> button[aria-label*="Clear" i]',
        '[class*="resume"] button[aria-label*="Remove" i]',
        '[class*="Resume"] button:has(svg)',
        'button[aria-label*="Remove resume" i]',
        'button[aria-label*="Delete file" i]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=2000)
                page.wait_for_timeout(600)
                print("    removed existing resume attachment")
                return True
        except Exception:
            continue
    # Fallback: click any small X near a .pdf filename
    try:
        pdf = page.locator("text=/\\.pdf/i").first
        if pdf.count():
            box = pdf.bounding_box()
            if box:
                # try a click slightly to the right of the filename
                page.mouse.click(box["x"] + box["width"] + 12, box["y"] + box["height"] / 2)
                page.wait_for_timeout(500)
                print("    tried remove via PDF filename proximity click")
                return True
    except Exception:
        pass
    print("    ! could not find resume remove (X) — will try upload anyway")
    return False


def upload_resume(page, pdf_path: str):
    """Attach the tailored resume PDF after clearing the old one."""
    removed = remove_existing_resume(page)
    # Prefer a file input under Resume/CV
    try:
        root = page.locator('text=/Resume\\/CV/i').locator(
            'xpath=ancestor::*[self::div or self::fieldset or self::section][1]')
        inp = root.locator('input[type="file"]').first
        if inp.count():
            inp.set_input_files(pdf_path, timeout=5000)
            page.wait_for_timeout(800)
            print(f"    uploaded resume: {pdf_path}")
            return True
    except Exception:
        pass
    try:
        page.locator('input[type="file"]').first.set_input_files(pdf_path, timeout=5000)
        page.wait_for_timeout(800)
        print(f"    uploaded resume (first file input): {pdf_path}")
        return True
    except Exception as e:
        print(f"    ! resume upload failed: {e}")
        return False


def fill_location_city(page, value: str) -> bool:
    """Fill Greenhouse Location (City) geocode typeahead (#candidate-location).

    Must type trusted keystrokes and click a suggestion so hidden lat/lon commit.
    Plain fill() leaves Location required and validation fails on every job.
    """
    value = (value or "").strip()
    if not value:
        return False
    # Prefer the official Greenhouse candidate-location input
    candidates = [
        page.locator("input#candidate-location").first,
        page.locator("input[name='job_application[location]'], input[name='location']").first,
        page.get_by_label(re.compile(r"Location\s*\(City\)|^Location$|^City$", re.I)).first,
    ]
    box = None
    for loc in candidates:
        try:
            if loc.count() and loc.is_visible():
                box = loc
                break
        except Exception:
            continue
    if box is None:
        return False
    try:
        box.click(timeout=3000)
        box.fill("")
        # Real typing triggers geocode-earth-proxy (synthetic fill alone does not)
        box.type(value, delay=35)
        page.wait_for_timeout(900)
        # Pick best matching option from the listbox / react-select menu
        opted = False
        for opt_sel in (
            '[role="listbox"] [role="option"]',
            '[role="option"]',
            ".select__option",
            '[class*="option"]',
            "ul[role=listbox] li",
        ):
            try:
                opts = page.locator(opt_sel)
                n = min(opts.count(), 12)
                if not n:
                    continue
                # Prefer exact / prefix match, else first suggestion
                pick = None
                needle = value.lower()
                for i in range(n):
                    t = (opts.nth(i).inner_text(timeout=800) or "").strip()
                    if not t:
                        continue
                    tl = t.lower()
                    if tl == needle or needle in tl or tl.startswith(needle.split(",")[0]):
                        pick = opts.nth(i)
                        break
                if pick is None:
                    pick = opts.first
                pick.click(timeout=2500)
                opted = True
                break
            except Exception:
                continue
        if not opted:
            # Fallback: ArrowDown + Enter on focused combobox
            try:
                page.keyboard.press("ArrowDown")
                page.wait_for_timeout(200)
                page.keyboard.press("Enter")
                opted = True
            except Exception:
                pass
        page.wait_for_timeout(400)
        if opted:
            print(f"    location: {value}")
        return opted
    except Exception as e:
        print(f"    ! location typeahead: {str(e).splitlines()[0][:100]}")
        return False


def fill_basic_fields(page, profile: dict):
    """Fill Full Name / First / Last / Email / Phone / Location from profile."""
    name = (profile.get("name") or "").strip()
    first = (profile.get("first_name") or "").strip()
    last = (profile.get("last_name") or "").strip()
    if name and not (first or last):
        parts = name.split(" ", 1)
        first, last = parts[0], (parts[1] if len(parts) > 1 else "")
    if not name:
        name = f"{first} {last}".strip()
    pairs = [
        (r"Full Name", name),
        (r"First Name", first),
        (r"Last Name", last),
        (r"^Email$", profile.get("email") or ""),
        (r"^Phone$", profile.get("phone") or ""),
    ]
    for label, value in pairs:
        if not value:
            continue
        try:
            page.get_by_label(re.compile(label, re.I)).fill(str(value), timeout=2000)
        except Exception:
            try:
                page.locator(
                    f'input[name*="{label.replace("^","").replace("$","").lower().replace(" ","_")}" i], '
                    f'input[id*="{label.replace("^","").replace("$","").lower().replace(" ","_")}" i], '
                    f'input[placeholder*="{label.replace("^","").replace("$","")}" i]'
                ).first.fill(str(value), timeout=1500)
            except Exception:
                pass
    # Classic boards often use #first_name / #last_name / #email / #phone
    for sel, value in (
        ("#first_name, input[name='job_application[first_name]']", first),
        ("#last_name, input[name='job_application[last_name]']", last),
        ("#email, input[name='job_application[email]']", profile.get("email") or ""),
        ("#phone, input[name='job_application[phone]']", profile.get("phone") or ""),
    ):
        if not value:
            continue
        try:
            loc = page.locator(sel).first
            if loc.count():
                loc.fill(str(value), timeout=1500)
        except Exception:
            pass
    # Location (City) — geocode combobox on nearly every job-boards form
    fill_location_city(page, profile.get("location") or "")


def click_submit(page):
    """Click Submit only. Does NOT mean the application succeeded."""
    for text in ("Submit application", "Submit Application", "Submit", "Apply"):
        try:
            page.locator(f'button:has-text("{text}")').first.click(timeout=3000)
            page.wait_for_timeout(800)
            return True
        except Exception:
            continue
    return False


# ── Email security code (post-Submit OTP) ─────────────────────────────────────

def has_email_verification(page) -> bool:
    """True when Greenhouse shows the 8-box application security-code step."""
    try:
        if page.locator("fieldset#email-verification").count():
            return True
        if page.locator("#security-input-0").count():
            return True
        if page.locator("text=/verification code was sent|enter the 8-character code/i").count():
            return True
    except Exception:
        pass
    return False


def email_verification_error(page) -> str:
    try:
        err = page.locator("#email-verification-error").first
        if err.count() and err.is_visible():
            return (err.inner_text(timeout=1000) or "").strip()
    except Exception:
        pass
    return ""


def _security_boxes_value(page) -> str:
    try:
        return page.evaluate(
            """() => {
              let s = '';
              for (let i = 0; i < 8; i++) {
                const el = document.getElementById('security-input-' + i);
                if (!el) break;
                s += (el.value || '');
              }
              return s;
            }"""
        ) or ""
    except Exception:
        return ""


def fill_security_code(page, code: str) -> bool:
    """Paste the 8-character OTP into Greenhouse Security code boxes, then verify.

    Prefers a real paste into #security-input-0 (Greenhouse splits across boxes).
    Falls back to per-box typing. Never pastes the email subject sentence.
    """
    from mail_code import looks_like_otp
    code = re.sub(r"\s+", "", (code or "").strip())
    if not looks_like_otp(code) and len(code) >= 8:
        # Still allow digit-mixed codes that passed fetch; trim to 8
        code = code[:8]
    if len(code) < 8:
        print(f"    ! security code too short / invalid: {code!r}")
        return False
    code = code[:8]
    if not looks_like_otp(code):
        print(f"    ! refusing non-OTP value for Security code: {code!r}")
        return False

    first = page.locator("#security-input-0").first
    if first.count():
        try:
            first.click(timeout=3000)
            # Clear any prior attempt
            page.keyboard.press("Control+a")
            page.keyboard.press("Backspace")
            page.wait_for_timeout(100)
            # Paste path: write clipboard in this page, then Ctrl+V
            try:
                page.context.grant_permissions(
                    ["clipboard-read", "clipboard-write"])
            except Exception:
                pass
            try:
                page.evaluate(
                    """async (c) => { await navigator.clipboard.writeText(c); }""",
                    code,
                )
                page.keyboard.press("Control+v")
                page.wait_for_timeout(400)
            except Exception:
                # Synthetic paste event (works when clipboard API is blocked)
                page.evaluate(
                    """(c) => {
                      const el = document.getElementById('security-input-0');
                      if (!el) return;
                      el.focus();
                      el.value = '';
                      const dt = new DataTransfer();
                      dt.setData('text/plain', c);
                      el.dispatchEvent(new ClipboardEvent('paste', {
                        clipboardData: dt, bubbles: true, cancelable: true
                      }));
                      // If the app did not split on paste, set chars directly
                      if (!(el.value && el.value.length)) {
                        for (let i = 0; i < c.length; i++) {
                          const box = document.getElementById('security-input-' + i);
                          if (!box) break;
                          box.value = c[i];
                          box.dispatchEvent(new Event('input', { bubbles: true }));
                          box.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                      }
                    }""",
                    code,
                )
                page.wait_for_timeout(300)
            got = re.sub(r"\s+", "", _security_boxes_value(page))
            if got.upper() == code.upper() or len(got) >= 8:
                page.wait_for_timeout(200)
                return True
        except Exception:
            pass

        # Per-box typing fallback
        filled = 0
        for i, ch in enumerate(code):
            sel = f"#security-input-{i}"
            try:
                loc = page.locator(sel).first
                if not loc.count():
                    break
                loc.click(timeout=2000)
                loc.fill("")
                loc.type(ch, delay=40)
                filled += 1
            except Exception:
                try:
                    page.evaluate(
                        """([i, ch]) => {
                          const el = document.getElementById('security-input-' + i);
                          if (!el) return false;
                          el.focus();
                          el.value = ch;
                          el.dispatchEvent(new Event('input', { bubbles: true }));
                          el.dispatchEvent(new Event('change', { bubbles: true }));
                          return true;
                        }""",
                        [i, ch],
                    )
                    filled += 1
                except Exception:
                    break
        if filled == 8:
            page.wait_for_timeout(400)
            return True
        print(f"    ! filled {filled}/8 security-input boxes")
        return filled >= 8

    # Fallback: single labelled field
    try:
        page.get_by_label(re.compile(r"Security code|verification code", re.I)).fill(
            code, timeout=2000)
        return True
    except Exception:
        pass
    print("    ! Security code inputs not found")
    return False


def _norm_url(url: str) -> str:
    """Compare post-submit URLs without noisy trailing slash / fragment noise."""
    u = (url or "").strip()
    try:
        p = urlparse(u)
        path = (p.path or "").rstrip("/") or "/"
        return f"{p.scheme}://{p.netloc}{path}?{p.query}".rstrip("?").lower()
    except Exception:
        return u.lower().rstrip("/")


def url_changed_since(page, before: str) -> bool:
    return _norm_url(page.url or "") != _norm_url(before)


def application_confirmed(page, url_before: str | None = None) -> bool:
    """Success only when the page left the apply URL (URL change /confirmation).

    Clicking Submit alone is never enough. Body “thank you” text without a URL
    change is ignored — leave the tab open for you to finish.
    """
    url = (page.url or "").lower()
    if "/confirmation" in url:
        return True
    if url_before is not None and url_changed_since(page, url_before):
        # Left the apply form URL → treat as success (Greenhouse thank-you / next step)
        # unless we are still on the email OTP step (that is not done yet).
        if has_email_verification(page):
            return False
        return True
    return False


def wait_after_submit(page, url_before: str, timeout_ms: int = 20_000) -> str:
    """After Submit: wait for URL change, email OTP UI, or timeout.

    Returns: "confirmed" | "email_verify" | "unchanged"
    """
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        if has_email_verification(page):
            return "email_verify"
        if application_confirmed(page, url_before=url_before):
            return "confirmed"
        try:
            page.wait_for_timeout(300)
        except Exception:
            break
    if has_email_verification(page):
        return "email_verify"
    if application_confirmed(page, url_before=url_before):
        return "confirmed"
    return "unchanged"


def handle_email_verification(page, mail_page, company: str = "", wait_sec: int = 90,
                              provider: str = "gmail") -> bool:
    """If the OTP step is showing, read the code from Gmail / Outlook (mail Chrome) and submit it.

    `mail_page` is a tab in `.browser-profile-mail`. Returns True only when the
    URL changes after the code Submit (same rule as the main form).
    """
    if not has_email_verification(page):
        return application_confirmed(page)
    if mail_page is None:
        print("    ! email verification shown but mail Chrome is not open")
        return False
    from mail_code import MAIL_LABEL, normalize_provider
    print(f"    email verification — reading the code from {MAIL_LABEL[normalize_provider(provider)]} …")
    from mail_code import fetch_greenhouse_code, looks_like_otp
    code = fetch_greenhouse_code(mail_page, company=company or "", wait_sec=wait_sec,
                                 provider=provider)
    if not code or not looks_like_otp(code):
        print(f"    ! bad / missing security code from the mailbox: {code!r}")
        return False
    print(f"    pasting security code into Greenhouse: {code}")
    if not fill_security_code(page, code):
        return False
    err = email_verification_error(page)
    if err:
        print(f"    ! {err}")
    # Submit again after pasting the code (required)
    url_before = page.url or ""
    page.wait_for_timeout(400)
    if not click_submit(page):
        page.wait_for_timeout(800)
        if not click_submit(page):
            print("    ! Submit after security code failed")
            return False
    print("    clicked Submit after security code …")
    outcome = wait_after_submit(page, url_before, timeout_ms=20_000)
    if outcome == "confirmed":
        return True
    # Wrong/expired code: try once more with a fresher email (Copy code again)
    if has_email_verification(page) or email_verification_error(page) or outcome == "unchanged":
        print("    retrying with a newer security-code email …")
        code2 = fetch_greenhouse_code(mail_page, company=company or "", wait_sec=45,
                                      provider=provider)
        if code2 and looks_like_otp(code2) and code2 != code and fill_security_code(page, code2):
            url_before2 = page.url or ""
            if click_submit(page):
                print("    clicked Submit after security code (retry) …")
                return wait_after_submit(page, url_before2, timeout_ms=20_000) == "confirmed"
    return application_confirmed(page, url_before=url_before)
