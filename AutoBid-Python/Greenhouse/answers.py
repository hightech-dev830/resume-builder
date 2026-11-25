"""Answer bank for MyGreenhouse (Supabase platform=greenhouse) + profile defaults."""
import json
import re
import sys
from datetime import datetime, timezone

import db
from config import BASE_DIR, TAILORED_RESUME
import supabase_db as supa

PLATFORM = "greenhouse"
STORAGE_NAME = "the Greenhouse answer bank (Supabase)"
TAILORED_PLACEHOLDER = "<tailored resume>"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_PROFILE = {
    "name": "",
    "first_name": "",
    "last_name": "",
    "email": "",
    "phone": "",
    "linkedin": "",
    "github": "",
    "website": "",
    "location": "",
    "authorized_us": "true",
    "sponsorship": "false",
    "resume_file": "",  # only when TAILORED_RESUME = False
}

PROFILE_BY_PATH = {
    "first_name": "first_name", "last_name": "last_name",
    "email": "email", "phone": "phone",
    "resume": "resume_file", "resume_cv": "resume_file",
    "linkedin": "linkedin", "github": "github", "website": "website",
    "location": "location",
}

PROFILE_BY_TITLE = [
    (r"^first name$", "first_name"),
    (r"^last name$", "last_name"),
    (r"^(full )?name$", "name"),
    (r"^e-?mail( address)?$", "email"),
    (r"^phone( number)?$", "phone"),
    (r"^linked ?in( url| profile)?$", "linkedin"),
    (r"^github( url| profile)?$", "github"),
    (r"^(personal )?(website|portfolio)( url)?$", "website"),
    (r"^location$", "location"),
    (r"^are you legally authorized to work in the united states", "authorized_us"),
    (r"^do you or will you require sponsorship", "sponsorship"),
    (r"^resume(/ ?cv)?$", "resume_file"),
]

TEXT_TYPES = {"String", "LongText"}


def profile_key(q):
    path = (q.get("path") if isinstance(q, dict) else q["path"] or "").strip().lower()
    if path in PROFILE_BY_PATH:
        return PROFILE_BY_PATH[path]
    title = (q.get("title") if isinstance(q, dict) else q["title"] or "").strip().lower().rstrip("?:*").strip()
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
    e = {"key": row["key"], "question": row["question"], "type": row["type"],
         "form": row.get("form") or "application"}
    if row.get("note"):
        e["note"] = row["note"]
    if row.get("choices"):
        e["choices"] = row["choices"]
    e["asked_in_jobs"] = row.get("asked_in_jobs") or 0
    e["answer"] = row.get("answer")
    return e


def _fetch_questions():
    return supa.select_all("autobid_questions", {
        "select": "key,question,type,form,note,choices,asked_in_jobs,answer",
        "platform": f"eq.{PLATFORM}", "order": "key"})


def _fetch_profile():
    profile = dict(DEFAULT_PROFILE)
    for r in supa.select_all("autobid_profile", {"select": "key,value", "order": "key"}):
        if r["key"] in DEFAULT_PROFILE:
            profile[r["key"]] = "" if r["value"] is None else str(r["value"])
    # derive first/last from name when empty
    name = (profile.get("name") or "").strip()
    if name and not profile.get("first_name"):
        parts = name.split(" ", 1)
        profile["first_name"] = parts[0]
        if len(parts) > 1 and not profile.get("last_name"):
            profile["last_name"] = parts[1]
    return profile


def load_bank():
    return {"questions": [_entry(r) for r in _fetch_questions()], "profile": _fetch_profile()}


def save_bank(bank):
    rows = []
    for e in bank["questions"]:
        rows.append({
            "platform": PLATFORM, "key": e["key"],
            "question": e.get("question") or "", "type": e.get("type") or "",
            "form": e.get("form") or "", "note": e.get("note"),
            "choices": e.get("choices"), "asked_in_jobs": int(e.get("asked_in_jobs") or 0),
            "answer": e.get("answer"), "updated_at": _now(),
        })
    for i in range(0, len(rows), 200):
        if rows[i:i + 200]:
            supa.upsert("autobid_questions", rows[i:i + 200], "platform,key")
    set_profile(bank.get("profile") or {})


def set_profile(values, bank=None):
    payload = [{"key": k, "value": "" if v is None else str(v), "updated_at": _now()}
               for k, v in values.items() if k in DEFAULT_PROFILE]
    if payload:
        supa.upsert("autobid_profile", payload, "key")
    if bank is None:
        return load_bank()
    bank["profile"] = _fetch_profile()
    return bank


def set_answer_local(key, answer, bank):
    """Update ONE answer in memory only. Flush later via flush_answers()."""
    found = False
    for q in bank["questions"]:
        if q["key"] == key:
            q["answer"] = None if _is_empty(answer) else answer
            found = True
            break
    if not found:
        raise KeyError(f"question not in the answer bank: {key}")
    return bank


def flush_answers(changes, bank=None):
    """Multi-save pending answers to Supabase. changes: {key: answer}."""
    if not changes:
        return bank
    by_key = {q["key"]: q for q in (bank or {}).get("questions", [])}
    rows = []
    for key, answer in changes.items():
        q = by_key.get(key)
        if not q:
            continue
        rows.append({
            "platform": PLATFORM, "key": key,
            "question": q.get("question") or "", "type": q.get("type") or "",
            "form": q.get("form") or "", "note": q.get("note"),
            "choices": q.get("choices"), "asked_in_jobs": int(q.get("asked_in_jobs") or 0),
            "answer": None if _is_empty(answer) else answer, "updated_at": _now(),
        })
    for i in range(0, len(rows), 100):
        chunk = rows[i:i + 100]
        if chunk:
            supa.upsert("autobid_questions", chunk, "platform,key")
    return bank


def set_answer(key, answer, bank=None):
    """Save ONE answer in Supabase immediately. Prefer set_answer_local + flush_answers."""
    rows = supa.request(
        "PATCH", "autobid_questions",
        {"platform": f"eq.{PLATFORM}", "key": f"eq.{key}"},
        {"answer": None if _is_empty(answer) else answer, "updated_at": _now()},
        prefer="return=representation")
    if not rows:
        q = next((x for x in (bank or {}).get("questions", []) if x["key"] == key), None)
        if q:
            supa.upsert("autobid_questions", [{
                "platform": PLATFORM, "key": key, "question": q.get("question") or "",
                "type": q.get("type") or "", "form": q.get("form") or "",
                "answer": answer, "updated_at": _now(), "asked_in_jobs": q.get("asked_in_jobs") or 0,
            }], "platform,key")
    if bank is None:
        return load_bank()
    for q in bank["questions"]:
        if q["key"] == key:
            q["answer"] = answer
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
    by_key = {q["key"]: q for q in bank["questions"]}
    # Always include a synthetic resume field so bid replaces the base PDF
    rows = list(conn.execute(
        "SELECT * FROM questions WHERE job_id=? ORDER BY position", (job_id,)).fetchall())
    if not any((r["type"] == "File" or "resume" in (r["title"] or "").lower()) for r in rows):
        rows = list(rows) + [{
            "id": f"{job_id}:resume", "job_id": job_id, "position": 999,
            "form": "application", "path": "resume", "title": "Resume/CV",
            "type": "File", "required": 1, "description": "", "options": "[]",
            "raw": "{}", "bank_key": db.bank_key("Resume/CV", "File"), "answer": None,
        }]
    return _resolve_rows(rows, bank, by_key)


def resolve_all(conn, bank):
    return {r["id"]: resolve_job(conn, r["id"], bank)
            for r in conn.execute(
                "SELECT id FROM jobs WHERE is_active=1 AND relevant=1")}


def _resolve_rows(rows, bank, by_key):
    result = []
    profile = bank["profile"]
    for q in rows:
        q = dict(q) if not isinstance(q, dict) else q
        item = {"q": q, "value": None, "source": None, "missing": False, "error": None}
        if is_start_date_question(q.get("title") or ""):
            raw = start_date_answer(q)
            item["value"], item["source"] = raw, "auto.start_date"
            if _is_empty(raw) and q.get("required"):
                item["missing"] = True
            result.append(item)
            continue
        pkey = profile_key(q)
        if pkey == "resume_file" and TAILORED_RESUME:
            item["value"], item["source"] = TAILORED_PLACEHOLDER, "tailored resume (AI)"
            result.append(item)
            continue
        if pkey:
            raw = profile.get(pkey)
            # name → first/last helpers
            if pkey == "first_name" and _is_empty(raw) and profile.get("name"):
                raw = profile["name"].split(" ", 1)[0]
            if pkey == "last_name" and _is_empty(raw) and profile.get("name"):
                parts = profile["name"].split(" ", 1)
                raw = parts[1] if len(parts) > 1 else ""
            if pkey == "name" and _is_empty(raw):
                raw = f"{profile.get('first_name','')} {profile.get('last_name','')}".strip()
            item["value"], item["source"] = raw, f"profile.{pkey}"
            if _is_empty(raw) and q.get("required"):
                item["missing"] = True
        else:
            e = by_key.get(q.get("bank_key") or db.bank_key(q.get("title") or "", q.get("type") or "String"))
            if e and not _is_empty(e.get("answer")):
                item["value"], item["source"] = e["answer"], "bank"
            elif q.get("required"):
                item["missing"] = True
                item["source"] = "bank"
        result.append(item)
    return result


def blocking(items):
    return [i for i in items if i["missing"] or i.get("error")]


def ai_writable(item):
    return (item["missing"] and not item.get("error") and item.get("source") == "bank"
            and item["q"]["type"] in TEXT_TYPES)


def print_coverage(conn, bank):
    jobs = conn.execute(
        "SELECT id, title FROM jobs WHERE is_active=1 AND relevant=1 "
        "AND status IN ('ready','needs_you','new')").fetchall()
    ready = sum(1 for j in jobs if not blocking(resolve_job(conn, j["id"], bank)))
    qs = bank["questions"]
    answered = sum(not _is_empty(q["answer"]) for q in qs)
    profile_missing = [k for k, v in bank["profile"].items()
                       if _is_empty(v) and k not in ("resume_file", "first_name", "last_name",
                                                     "github", "website", "linkedin")
                       and not (k == "resume_file" and TAILORED_RESUME)]
    if _is_empty(bank["profile"].get("name")) and (
            _is_empty(bank["profile"].get("first_name")) or _is_empty(bank["profile"].get("last_name"))):
        if "name" not in profile_missing:
            profile_missing.append("name")
    print(f"Answer bank: {answered}/{len(qs)} questions answered  ->  {STORAGE_NAME}")
    if profile_missing:
        print(f"Profile still empty: {', '.join(profile_missing)}")
    print(f"\nJobs: {len(jobs)} · {ready} can autofill now")


def main():
    conn = db.connect()
    bank = load_bank()
    added = sync_bank(conn, bank)
    save_bank(bank)
    if added:
        print(f"Added {added} new questions to the answer bank\n")
    args = sys.argv[1:]
    if args:
        job_id = args[0]
        job = conn.execute("SELECT title FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            sys.exit(f"No job with id {job_id}")
        print(job["title"], "\n")
        for i in resolve_job(conn, job_id, bank):
            q = i["q"]
            if i.get("error"):
                shown = f"!! {i['error']}"
            elif i["missing"]:
                shown = "-- MISSING"
            else:
                shown = json.dumps(i["value"], ensure_ascii=False)[:70]
                shown += f"   <- {i['source']}"
            print(f"* {q['title'][:70]:<70} {shown}")
    else:
        print_coverage(conn, bank)


if __name__ == "__main__":
    main()
