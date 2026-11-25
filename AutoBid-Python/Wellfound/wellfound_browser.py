"""Playwright helpers for Wellfound company / job / apply pages.

Wellfound is DataDome-protected — always use installed Chrome (channel=chrome)
and the shared bot profile from common/browser.py.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from browser import open_browser

JOB_URL_RE = re.compile(r"/jobs/(\d+)(?:-([a-z0-9-]+))?", re.I)


def company_jobs_url(slug: str) -> str:
    return f"https://wellfound.com/company/{slug.strip('/')}/jobs"


def job_url(job_id: str, slug: str = "") -> str:
    slug = (slug or "").strip("-")
    return f"https://wellfound.com/jobs/{job_id}" + (f"-{slug}" if slug else "")


def apply_url(job_id: str, slug: str = "") -> str:
    return job_url(job_id, slug) + "?autoOpenApplication=true"


def open_ctx(playwright, headless=False):
    """Prefer the shared bot profile; if it is already open, use a temporary Chrome."""
    try:
        return open_browser(playwright, headless=headless)
    except SystemExit as e:
        if "already open" not in str(e).lower() and "in use" not in str(e).lower():
            raise
        print("  (bot Chrome profile busy — using a temporary Chrome for this scrape)")
        browser = playwright.chromium.launch(
            channel="chrome", headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"])
        return browser.new_context(no_viewport=True)


def _apollo(page):
    return page.evaluate("""() => {
      const nd = document.getElementById('__NEXT_DATA__');
      if (!nd) return {};
      try {
        const data = JSON.parse(nd.textContent);
        return data?.props?.pageProps?.apolloState?.data
            || data?.props?.pageProps?.apolloState || {};
      } catch (e) { return {}; }
    }""")


def _locs(value):
    if value is None:
        return []
    if isinstance(value, list):
        out = []
        for v in value:
            if isinstance(v, dict):
                out.append(v.get("displayName") or v.get("name") or "")
            else:
                out.append(str(v))
        return [x for x in out if x]
    if isinstance(value, dict) and "json" in value:
        return _locs(value.get("json"))
    return [str(value)] if value else []


def epoch_iso(epoch):
    if epoch in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None


def extract_company_jobs(page):
    """Jobs currently visible / in Apollo on a company jobs page."""
    apollo = _apollo(page)
    jobs = {}
    for key, v in (apollo or {}).items():
        if not key.startswith("JobListing:") or not isinstance(v, dict) or not v.get("title"):
            continue
        jid = str(v.get("id") or key.split(":", 1)[-1])
        slug = (v.get("slug") or "").strip()
        remote = bool(v.get("remote"))
        locs = _locs(v.get("locationNames"))
        jobs[jid] = {
            "id": jid,
            "title": (v.get("title") or "").strip(),
            "slug": slug,
            "is_remote": remote,
            "workplace_type": "Remote" if remote else "OnSite",
            "location": locs[0] if locs else ("Remote" if remote else ""),
            "locations": locs or (["Remote"] if remote else []),
            "compensation": v.get("compensation") or "",
            "employment_type": (v.get("jobType") or "full_time") or "",
            "published_at": epoch_iso(v.get("liveStartAt")),
            "job_url": job_url(jid, slug),
            "apply_url": apply_url(jid, slug),
            "department": "",
            "team": "",
        }
    # DOM fallback / merge
    dom = page.evaluate("""() => {
      const out = [];
      for (const a of document.querySelectorAll('a[href*="/jobs/"]')) {
        const m = (a.getAttribute('href') || '').match(/\\/jobs\\/(\\d+)(?:-([a-z0-9-]+))?/i);
        if (!m) continue;
        const title = (a.innerText || '').trim().split('\\n')[0];
        if (!title || title.length > 160) continue;
        out.push({id: m[1], slug: m[2] || '', title, href: a.href.split('?')[0]});
      }
      return out;
    }""")
    for row in dom or []:
        jid = str(row["id"])
        if jid in jobs:
            continue
        slug = row.get("slug") or ""
        jobs[jid] = {
            "id": jid, "title": row["title"], "slug": slug, "is_remote": True,
            "workplace_type": "Remote", "location": "Remote", "locations": ["Remote"],
            "compensation": "", "employment_type": "full_time", "published_at": None,
            "job_url": job_url(jid, slug), "apply_url": apply_url(jid, slug),
            "department": "", "team": "",
        }
    return list(jobs.values())


def scroll_load_jobs(page, rounds=8, pause=0.8):
    """Scroll the company jobs page so more listings hydrate into Apollo/DOM."""
    seen = 0
    for _ in range(rounds):
        jobs = extract_company_jobs(page)
        if len(jobs) <= seen:
            page.mouse.wheel(0, 2400)
            time.sleep(pause)
            jobs = extract_company_jobs(page)
            if len(jobs) <= seen:
                break
        seen = len(jobs)
        page.mouse.wheel(0, 2400)
        time.sleep(pause)
    return extract_company_jobs(page)


def extract_job_details(page):
    """Summary + about-the-job text from a job page."""
    data = page.evaluate("""() => {
      const h1 = document.querySelector('h1');
      const title = h1 ? h1.innerText.trim() : '';
      const about = document.querySelector('h2');
      let desc = '';
      if (about && /about the job/i.test(about.innerText)) {
        let n = about.parentElement;
        desc = n ? n.innerText.trim() : '';
      }
      if (!desc) {
        const main = document.querySelector('main') || document.body;
        desc = (main.innerText || '').slice(0, 20000);
      }
      const text = document.body.innerText || '';
      const salary = (text.match(/\\$[\\d.,]+k?\\s*[–-]\\s*\\$[\\d.,]+k?/i) || [''])[0];
      const remote = /\\bremote\\b/i.test(text);
      const posted = (text.match(/posted:\\s*([^\\n•]+)/i) || ['', ''])[1].trim();
      const companyA = document.querySelector('a[href*="/company/"]');
      const company = companyA ? companyA.innerText.trim().split('\\n')[0] : '';
      return {title, desc, salary, remote, posted, company,
              html: (document.querySelector('main') || document.body).innerHTML.slice(0, 500000)};
    }""")
    apollo = _apollo(page)
    jid = None
    slug = ""
    m = JOB_URL_RE.search(page.url or "")
    if m:
        jid, slug = m.group(1), m.group(2) or ""
    listing = None
    for key, v in (apollo or {}).items():
        if key.startswith("JobListing:") and str(v.get("id")) == str(jid):
            listing = v
            break
    published = None
    if listing and listing.get("liveStartAt"):
        published = epoch_iso(listing.get("liveStartAt"))
    return {
        "id": jid,
        "title": (listing or {}).get("title") or data.get("title") or "",
        "description": data.get("desc") or "",
        "description_html": data.get("html") or "",
        "compensation": (listing or {}).get("compensation") or data.get("salary") or "",
        "is_remote": bool((listing or {}).get("remote") if listing else data.get("remote")),
        "workplace_type": "Remote" if (listing or data).get("remote") else "",
        "location": ", ".join(_locs((listing or {}).get("locationNames"))) or (
            "Remote" if data.get("remote") else ""),
        "employment_type": (listing or {}).get("jobType") or "full_time",
        "published_at": published,
        "posted_label": data.get("posted") or "",
        "company": data.get("company") or "",
        "slug": slug or (listing or {}).get("slug") or "",
        "job_url": job_url(jid, slug) if jid else page.url,
        "apply_url": apply_url(jid, slug) if jid else (page.url + "?autoOpenApplication=true"),
    }


def extract_application_fields(page):
    """Read the open application modal / form fields (Picture 5)."""
    return page.evaluate("""() => {
      const root = document.querySelector('[role="dialog"]')
                || document.querySelector('form')
                || document.body;
      const fields = [];
      const push = (el, title, type, required) => {
        const id = el.name || el.id || title;
        if (!id || !title) return;
        const opts = [];
        if (el.tagName === 'SELECT') {
          for (const o of el.options) {
            if (o.value) opts.push({label: o.textContent.trim(), value: o.value});
          }
        }
        fields.push({
          id: String(id), title, type, required: !!required,
          path: String(id), form: 'application', section: '',
          options: opts, description: '', raw: {tag: el.tagName, name: el.name, type: el.type}
        });
      };
      const labels = [...root.querySelectorAll('label')];
      for (const lab of labels) {
        const title = (lab.innerText || '').replace(/\\*$/,'').trim();
        if (!title || title.length > 120) continue;
        const required = /\\*/.test(lab.innerText || '') || lab.querySelector('[required]');
        let el = lab.querySelector('input,textarea,select');
        if (!el && lab.getAttribute('for'))
          el = root.querySelector('#' + CSS.escape(lab.getAttribute('for')));
        if (!el) continue;
        let type = 'String';
        const t = (el.type || '').toLowerCase();
        if (el.tagName === 'TEXTAREA') type = 'LongText';
        else if (el.tagName === 'SELECT') type = 'ValueSelect';
        else if (t === 'email') type = 'Email';
        else if (t === 'password') type = 'Password';
        else if (t === 'tel') type = 'Phone';
        else if (t === 'checkbox') type = 'Boolean';
        else if (t === 'radio') type = 'Boolean';
        else if (t === 'file') type = 'File';
        push(el, title, type, required);
      }
      // radio groups for Yes/No questions without neat labels
      const radios = [...root.querySelectorAll('input[type=radio]')];
      const groups = {};
      for (const r of radios) {
        const name = r.name || '';
        if (!name) continue;
        (groups[name] = groups[name] || []).push(r);
      }
      for (const [name, rs] of Object.entries(groups)) {
        if (fields.some(f => f.id === name)) continue;
        const wrap = rs[0].closest('fieldset,div,section') || rs[0].parentElement;
        const title = (wrap?.innerText || '').split('\\n').map(s => s.trim()).find(s => s.length > 8 && s.length < 140) || name;
        fields.push({
          id: name, title, type: 'Boolean', required: true, path: name,
          form: 'application', section: '', options: [
            {label: 'Yes', value: 'true'}, {label: 'No', value: 'false'}
          ], description: '', raw: {name}
        });
      }
      return fields;
    }""")


def dismiss_cookies(page):
    for text in ("Reject All", "Agree & Proceed", "Accept all", "Accept"):
        try:
            page.locator(f"button:has-text('{text}')").first.click(timeout=1500)
            time.sleep(0.3)
            return
        except Exception:
            pass


def wait_for_apply_form(page, timeout_ms=20000):
    page.wait_for_timeout(800)
    try:
        page.wait_for_selector(
            'text=/Your Application|Full Name|Set Your Location|legally authorized/i',
            timeout=timeout_ms)
        return True
    except Exception:
        return False


def is_email_verify_page(page) -> bool:
    url = page.url or ""
    if "email_verification" in url or "onboarding/email" in url:
        return True
    try:
        return bool(page.locator("text=/Verify your email|verification code/i").count())
    except Exception:
        return False


def org_from_careers_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] == "company":
        return parts[1]
    return parts[-1] if parts else ""
