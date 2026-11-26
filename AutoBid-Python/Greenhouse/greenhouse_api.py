"""Public Greenhouse Job Board API (no login).

  Stage 1  fetch_job_list(token)     - every job on a board (title, location, apply URL)
  Stage 2  fetch_job_details(token, id) - description HTML + application questions
           (?questions=true — same fields as the job-boards apply form)

API: https://boards-api.greenhouse.io/v1/boards/{token}/jobs
"""
from __future__ import annotations

import html as html_lib
import json
import random
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from config import TIMEOUT
from html_text import html_to_text

API = "https://boards-api.greenhouse.io/v1/boards/{token}"
HEADERS = {
    "User-Agent": "AutoBid-Greenhouse/1.0 (job collector)",
    "Accept": "application/json",
}
RETRIES = 5

# Greenhouse field type → AutoBid form type (same vocabulary as Ashby answers bank)
_FIELD_TYPE = {
    "input_text": "String",
    "textarea": "LongText",
    "input_file": "File",
    "multi_value_single_select": "ValueSelect",
    "multi_value_multi_select": "MultiValueSelect",
    "input_hidden": None,  # skip (lat/long helpers)
}

_SSL_CTX = None


def _ssl_context():
    global _SSL_CTX
    if _SSL_CTX is not None:
        return _SSL_CTX
    try:
        import certifi
        _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        _SSL_CTX = ssl._create_unverified_context()
    return _SSL_CTX


def _request(url):
    for attempt in range(RETRIES):
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ssl_context()) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise RuntimeError("no job board") from e
            if (e.code != 429 and e.code < 500) or attempt == RETRIES - 1:
                raise
            wait = int(e.headers.get("Retry-After") or 0) or 5 * 2 ** attempt
        except (urllib.error.URLError, TimeoutError, ssl.SSLError):
            if attempt == RETRIES - 1:
                raise
            wait = 3 * (attempt + 1)
        time.sleep(min(wait, 90) + random.random())


def board_token_from_url(url: str) -> str:
    """https://job-boards.greenhouse.io/stripe → stripe"""
    u = (url or "").strip().rstrip("/")
    if "greenhouse.io/" not in u.lower():
        return ""
    return urllib.parse.unquote(u.rsplit("/", 1)[-1]).strip()


def fetch_company_name(token: str) -> str:
    data = _request(API.format(token=urllib.parse.quote(token)))
    name = (data.get("name") or token or "").strip()
    for junk in (" External Website", " Careers Page", " Careers", " Job Board", " Jobs"):
        if name.lower().endswith(junk.lower()):
            name = name[: -len(junk)].strip()
    return name or token


def _locations(job: dict) -> list[str]:
    locs = []
    loc = job.get("location") or {}
    if isinstance(loc, dict) and loc.get("name"):
        locs.append(loc["name"])
    elif isinstance(loc, str) and loc.strip():
        locs.append(loc.strip())
    for o in job.get("offices") or []:
        if o.get("name"):
            locs.append(o["name"])
        if o.get("location"):
            locs.append(o["location"])
    # unique, keep order
    seen, out = set(), []
    for L in locs:
        k = L.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(L.strip())
    return out


def _apply_url(token: str, job: dict) -> str:
    """Prefer the public job-boards apply page (stable for Playwright bid)."""
    jid = job.get("id")
    if jid is not None:
        return f"https://job-boards.greenhouse.io/{urllib.parse.quote(token)}/jobs/{jid}"
    abs_url = (job.get("absolute_url") or "").strip()
    return abs_url


def _normalize(token: str, company: str, job: dict, with_content: bool = False) -> dict:
    locs = _locations(job)
    location = locs[0] if locs else ""
    blob = " | ".join(locs)
    remote = bool(looks_remote(blob) or looks_remote(job.get("title") or ""))
    jid = str(job.get("id"))
    apply = _apply_url(token, job)
    abs_url = (job.get("absolute_url") or "").strip() or apply
    content_html = ""
    content_text = ""
    if with_content:
        raw = job.get("content") or ""
        # API often HTML-escapes the whole blob (&lt;p&gt;…)
        if "&lt;" in raw[:200]:
            raw = html_lib.unescape(raw)
        content_html = raw
        content_text = html_to_text(raw)
    depts = []
    for d in job.get("departments") or []:
        if d.get("name"):
            depts.append(d["name"])
    return {
        "id": f"{token}:{jid}",
        "org": token,
        "company": company or token,
        "title": (job.get("title") or "").strip(),
        "department": depts[0] if depts else "",
        "dept_path": " > ".join(depts),
        "location": location,
        "locations": locs,
        "workplace_type": "Remote" if remote else "OnSite",
        "is_remote": int(remote),
        "job_url": abs_url,
        "apply_url": apply,
        "compensation": "",
        "description": content_text,
        "description_html": content_html,
        "published_at": (job.get("first_published") or job.get("updated_at") or "")[:40] or None,
        "updated_at": (job.get("updated_at") or "")[:40] or None,
        "gh_id": jid,
    }


def looks_remote(text: str) -> bool:
    return bool(re.search(
        r"\bremote\b|\bdistributed\b|\banywhere\b|\bwork from home\b|\bwfh\b",
        text or "", re.I))


def _map_field_type(raw_type: str, values: list) -> str | None:
    t = _FIELD_TYPE.get(raw_type)
    if t is None and raw_type in _FIELD_TYPE:
        return None
    if t is None:
        # Unknown → treat as text unless it has choices
        t = "ValueSelect" if values else "String"
    # Yes/No single-select → Boolean (same as Ashby)
    if t == "ValueSelect" and len(values) == 2:
        labels = {(v.get("label") or "").strip().lower() for v in values}
        if labels <= {"yes", "no"} or labels == {"true", "false"}:
            return "Boolean"
    return t


def normalize_questions(job: dict, job_key: str) -> list[dict]:
    """Map Greenhouse API questions (+ location_questions) to AutoBid question rows.

    Same shape as scrape/bid expect: title, type, required, path, options, id, form.
    """
    out = []
    seen_paths = set()
    buckets = (
        ("application", job.get("questions") or []),
        ("location", job.get("location_questions") or []),
        ("demographic", job.get("demographic_questions") or []),
    )
    for form, items in buckets:
        # demographic_questions can be {questions: [...]} on some boards
        if isinstance(items, dict):
            items = items.get("questions") or []
        for i, q in enumerate(items):
            if not isinstance(q, dict):
                continue
            fields = q.get("fields") or []
            if not fields:
                continue
            f0 = fields[0] or {}
            raw_type = (f0.get("type") or "").strip()
            values = f0.get("values") or []
            ftype = _map_field_type(raw_type, values)
            if not ftype:
                continue
            title = (q.get("label") or f0.get("name") or "").strip()
            # Keep helper lines (“- Only select one”) but flatten for the Answers UI
            title = re.sub(r"\s*\n\s*", " — ", title).strip()
            if not title:
                continue
            path = (f0.get("name") or "").strip().rstrip("[]")
            if path in seen_paths:
                continue
            if path:
                seen_paths.add(path)
            opts = []
            for v in values:
                lab = (v.get("label") if isinstance(v, dict) else str(v)) or ""
                if lab:
                    opts.append({"label": lab, "value": v.get("value") if isinstance(v, dict) else lab})
            required = bool(q.get("required"))
            # Always keep Resume even when optional (we upload a tailored PDF)
            if ftype == "File" and re.search(r"resume", title, re.I):
                required = True
            qid = f"{job_key}:{path or i}"
            out.append({
                "id": qid,
                "form": form,
                "path": path or title,
                "title": title,
                "type": ftype,
                "required": required,
                "description": (q.get("description") or "").strip(),
                "options": [o["label"] for o in opts] if opts else [],
                "raw": {"greenhouse_type": raw_type, "name": path, "values": values[:50]},
            })
    return out


def fetch_job_list(token: str, company: str = "") -> list[dict]:
    """All jobs on a board (no HTML content — fast)."""
    url = API.format(token=urllib.parse.quote(token)) + "/jobs"
    data = _request(url)
    jobs = data.get("jobs") or []
    return [_normalize(token, company or token, j, with_content=False) for j in jobs]


def fetch_job_details(token: str, job_id: str, company: str = "") -> tuple[dict | None, list]:
    """One job with description HTML + application questions (public API).

    Returns (details_dict, questions_list). details is None if the job is gone.
    """
    jid = str(job_id).split(":")[-1]
    url = (API.format(token=urllib.parse.quote(token))
           + f"/jobs/{urllib.parse.quote(jid)}?questions=true")
    try:
        data = _request(url)
    except RuntimeError:
        return None, []
    if not data or not data.get("id"):
        return None, []
    details = _normalize(token, company or token, data, with_content=True)
    questions = normalize_questions(data, details["id"])
    return details, questions
