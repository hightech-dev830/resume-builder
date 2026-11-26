"""Answer bank for Wellfound (Supabase platform=wellfound) + profile defaults (Picture 5)."""
import json
import re
import sys
from datetime import datetime, timezone

import db
from config import BASE_DIR
import supabase_db as supa

PLATFORM = "wellfound"
STORAGE_NAME = "the Wellfound answer bank (Supabase)"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Picture 5 defaults — Answers / My profile
DEFAULT_PROFILE = {
    "name": "",
    "email": "",
    "phone": "",
    "linkedin": "",
    "github": "",
    "website": "",
    "location": "",
    "password": "",           # Wellfound account password (stored in profile)
    "years_experience": "",   # e.g. "5"
    "desired_salary": "",     # USD amount
    "open_remote": "true",
    "authorized_us": "true",
    "sponsorship": "false",
    "resume_file": "",
}

PROFILE_BY_PATH = {
    "full_name": "name", "name": "name",
    "email": "email",
    "password": "password", "password_confirm": "password", "confirm_password": "password",
    "location": "location", "set_your_location": "location",
    "open_remote": "open_remote",
    "years_experience": "years_experience",
    "desired_salary": "desired_salary",
    "authorized_us": "authorized_us",
    "sponsorship": "sponsorship",
    "phone": "phone",
    "linkedin": "linkedin", "github": "github",
}

PROFILE_BY_TITLE = [
    (r"^full name$", "name"),
    (r"^e-?mail( address)?$", "email"),
    (r"^set a password$", "password"),
    (r"^confirm password$", "password"),
    (r"^set your location$", "location"),
    (r"^i'?m open to work remotely$", "open_remote"),
    (r"^years of experience$", "years_experience"),
    (r"^desired salary$", "desired_salary"),
    (r"^are you legally authorized to work in the united states", "authorized_us"),
    (r"^do you or will you require sponsorship", "sponsorship"),
    (r"^phone( number)?$", "phone"),
    (r"^linked ?in( url| profile)?$", "linkedin"),
    (r"^github( url| profile)?$", "github"),
]

TEXT_TYPES = {"String", "LongText"}


def profile_key(q):
    path = (q.get("path") or "").strip().lower()
    if path in PROFILE_BY_PATH:
        return PROFILE_BY_PATH[path]
    title = (q.get("title") or "").strip().lower().rstrip("?:*").strip()
    for pattern, key in PROFILE_BY_TITLE:
        if re.match(pattern, title, re.I):
            return key
    return None


try:
    from start_date import is_start_date_question, start_date_answer
except ImportError:
    def is_start_date_question(_title):
        return False

    def start_date_answer(_q=None):
        return None


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_empty(v):
    return v is None or v == "" or v == []


def _entry(row):
    import autobid_qbank as qbank
    e = {"key": row["key"], "question": row["question"], "type": row["type"],
         "form": row.get("form") or "application"}
    if row.get("note"):
        e["note"] = row["note"]
    if row.get("choices"):
        e["choices"] = row["choices"]
    e["asked_in_jobs"] = row.get("asked_in_jobs") or 0
    e["answer"] = row.get("answer")
    e["platforms"] = qbank.normalize_platforms(row.get("platform"), ensure=PLATFORM)
    return e


def _fetch_questions():
    import autobid_qbank as qbank
    return qbank.shared_fetch_questions(PLATFORM)


def _fetch_profile():
    profile = dict(DEFAULT_PROFILE)
    for r in supa.select_all("autobid_profile", {"select": "key,value", "order": "key"}):
        if r["key"] in DEFAULT_PROFILE:
            profile[r["key"]] = "" if r["value"] is None else str(r["value"])
    return profile


def load_bank():
    return {"questions": [_entry(r) for r in _fetch_questions()], "profile": _fetch_profile()}


def save_bank(bank):
    import autobid_qbank as qbank
    rows = []
    for e in bank["questions"]:
        rows.append({
            "platform": e.get("platforms") or [PLATFORM], "key": e["key"],
            "question": e.get("question") or "", "type": e.get("type") or "",
            "form": e.get("form") or "", "note": e.get("note"),
            "choices": e.get("choices"), "asked_in_jobs": int(e.get("asked_in_jobs") or 0),
            "answer": e.get("answer"), "updated_at": _now(),
        })
    qbank.upsert_rows(rows, PLATFORM)
    set_profile(bank.get("profile") or {})


def set_profile(values, bank=None):
    payload = [{"key": k, "value": "" if v is None else str(v), "updated_at": _now()}
               for k, v in values.items()]
    if payload:
        supa.upsert("autobid_profile", payload, "key")
    if bank is None:
        return load_bank()
    bank["profile"] = _fetch_profile()
    return bank


def set_answer(key, answer, bank=None):
    import autobid_qbank as qbank
    ans = None if _is_empty(answer) else answer
    rows = qbank.patch_answer(key, ans, PLATFORM)
    if not rows:
        q = next((x for x in (bank or {}).get("questions", []) if x["key"] == key), None)
        if q:
            qbank.upsert_rows([{
                "platform": q.get("platforms") or [PLATFORM], "key": key,
                "question": q.get("question") or "", "type": q.get("type") or "",
                "form": q.get("form") or "",
                "answer": ans, "updated_at": _now(),
                "asked_in_jobs": q.get("asked_in_jobs") or 0,
            }], PLATFORM)
    if bank is None:
        return load_bank()
    for q in bank["questions"]:
        if q["key"] == key:
            q["answer"] = ans
    return bank


def sync_bank(conn, bank):
    existing = {q["key"]: q for q in bank["questions"]}
    stats = {}
    rows = conn.execute("""
        SELECT q.* FROM questions q JOIN jobs j ON j.id = q.job_id
        WHERE j.is_active=1 AND j.relevant=1 AND q.required=1 ORDER BY q.position""").fetchall()
    for q in rows:
        if profile_key(q) or is_start_date_question(q["title"] or ""):
            continue
        s = stats.setdefault(q["bank_key"], {"row": q, "jobs": set()})
        s["jobs"].add(q["job_id"])
    added = 0
    for key, s in stats.items():
        q = s["row"]
        if key not in existing:
            bank["questions"].append({
                "key": key, "question": q["title"], "type": q["type"],
                "form": q["form"] or "application", "asked_in_jobs": len(s["jobs"]),
                "answer": None,
                "choices": json.loads(q["options"] or "[]") or None,
            })
            added += 1
        else:
            existing[key]["asked_in_jobs"] = len(s["jobs"])
    return added


def resolve_job(conn, job_id, bank):
    profile = bank["profile"]
    by_key = {q["key"]: q for q in bank["questions"]}
    out = []
    for q in conn.execute(
            "SELECT * FROM questions WHERE job_id=? ORDER BY position", (job_id,)).fetchall():
        q = dict(q)
        item = {"q": q, "value": None, "source": None, "missing": False, "error": None}
        if is_start_date_question(q.get("title") or ""):
            raw = start_date_answer(q)
            item["value"], item["source"] = raw, "auto.start_date"
            if _is_empty(raw) and q.get("required"):
                item["missing"] = True
            out.append(item)
            continue
        pkey = profile_key(q)
        if pkey:
            item["value"] = profile.get(pkey)
            item["source"] = f"profile.{pkey}"
            if _is_empty(item["value"]) and q["required"]:
                item["missing"] = True
        else:
            e = by_key.get(q["bank_key"])
            if e and not _is_empty(e.get("answer")):
                item["value"] = e["answer"]
                item["source"] = "bank"
            elif q["required"]:
                item["missing"] = True
        out.append(item)
    return out


def resolve_all(conn, bank):
    return {r["id"]: resolve_job(conn, r["id"], bank)
            for r in conn.execute("SELECT id FROM jobs WHERE is_active=1 AND relevant=1")}


def ai_writable(item):
    return item["q"]["type"] in TEXT_TYPES


def blocking(items):
    return [i for i in items if i["missing"] and not ai_writable(i)]
