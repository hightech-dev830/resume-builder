"""Bot-check (captcha) handling for Playwright pages.

The bot never solves or clicks a captcha. When one needs a person, it:
  1. brings the browser window to the front and scrolls the captcha into view,
  2. notifies you (Windows notification + sound),
  3. waits until you solved it (or CAPTCHA_WAIT_MIN runs out), then continues.

Same rules as the Chrome extension: only checks that need a person count.
The invisible reCAPTCHA badge (bottom-right corner) and reCAPTCHA v3 are ignored.
"""
import re
import time

from notify import notify

CAPTCHA_WAIT_MIN = 5

_HUMAN_PAGE = re.compile(r"verify (that )?you are (a )?human|are you a robot|press (&|and) hold", re.I)


def _frame_box(frame):
    try:
        el = frame.frame_element()
        if not el.is_visible():
            return None
        return el.bounding_box()
    except Exception:
        return None


def detect_captcha(page):
    """Name of a visible bot check that needs a person, or '' when there is none."""
    for frame in page.frames:
        url = frame.url or ""
        if not re.search(r"recaptcha/(api2|enterprise)/(anchor|bframe)|hcaptcha\.com|challenges\.cloudflare\.com", url):
            continue
        box = _frame_box(frame)
        if not box:
            continue
        w, h = box["width"], box["height"]
        invisible = re.search(r"[?&]size=invisible", url)
        if re.search(r"recaptcha/(api2|enterprise)/bframe", url) and h > 150 and w > 150:
            return "reCAPTCHA image challenge"
        if re.search(r"recaptcha/(api2|enterprise)/anchor", url) and not invisible and h > 40:
            try:
                in_badge = frame.frame_element().evaluate("e => !!e.closest('.grecaptcha-badge')")
            except Exception:
                in_badge = False
            if not in_badge:
                return "reCAPTCHA checkbox"
        if "hcaptcha.com" in url and not invisible and h > 40:
            return "hCaptcha"
        if "challenges.cloudflare.com" in url and h > 40:
            return "Cloudflare Turnstile"
    try:
        text = page.inner_text("body", timeout=2000)[:3000]
    except Exception:
        text = ""
    if _HUMAN_PAGE.search(text):
        return "human verification page"
    return ""


def captcha_solved(page):
    """Solved when a captcha token is filled in (and no image challenge is open), or it is gone."""
    try:
        has_token = page.evaluate("""() => [...document.querySelectorAll(
            'textarea[name="g-recaptcha-response"], textarea[name="h-captcha-response"], input[name="cf-turnstile-response"]'
        )].some(t => t.value)""")
    except Exception:
        has_token = False
    current = detect_captcha(page)
    if has_token and "image challenge" not in current:
        return True
    return not current


def _show(page):
    try:
        page.bring_to_front()
        page.evaluate("""() => {
            const f = [...document.querySelectorAll('iframe')].find(x =>
                /recaptcha\\/(api2|enterprise)\\/(anchor|bframe)|hcaptcha|challenges\\.cloudflare/.test(x.src || '')
                && !/size=invisible/.test(x.src || '') && x.offsetHeight > 40);
            if (f) f.scrollIntoView({block: 'center'});
        }""")
    except Exception:
        pass


def handle_captcha(page, job_title="", form_filled=False, wait_min=CAPTCHA_WAIT_MIN):
    """If a bot check is on the page: notify you and wait for it (one job at a time;
    batch mode in a bot's bid.py uses detect_captcha / captcha_solved directly).

    Returns True when there was no captcha or you solved it, False on timeout.
    """
    CAPTCHA_WAIT_MIN = wait_min
    what = detect_captcha(page)
    if not what:
        return True
    _show(page)
    filled = "The form is filled. " if form_filled else ""
    notify("AutoBid: bot check - your turn",
           f"{filled}Solve the {what}{f' for {job_title}' if job_title else ''}. "
           f"The bot continues by itself when it is done.")
    print(f"    waiting up to {CAPTCHA_WAIT_MIN} min for you to solve the {what} ...")

    deadline = time.time() + CAPTCHA_WAIT_MIN * 60
    reminded = False
    while time.time() < deadline:
        time.sleep(1.5)
        try:
            if captcha_solved(page):
                print("    bot check solved, continuing")
                time.sleep(1)   # let the page react to the token
                return True
        except Exception:
            pass   # page navigating
        if not reminded and time.time() > deadline - CAPTCHA_WAIT_MIN * 30:
            notify("AutoBid: still waiting", f"The {what} is not solved yet.")
            reminded = True
    notify("AutoBid: bot check timed out", f"Skipped {job_title or 'this job'} - the {what} was not solved.")
    return False
