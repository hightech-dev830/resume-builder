"""Answer bank: answer each distinct REQUIRED Ashby question ONCE, reuse it on every job.

Only required questions are collected and filled. Optional ones (LinkedIn when it
is optional, the Diversity Survey, ...) are left empty.

The same question ("Will you now or in the future require sponsorship...",
the Diversity Survey, ...) appears on many jobs with a different field id each time,
so answers are matched by question text + type.

The bank and your profile are stored in Supabase (the Resume Builder project), not in a
local file, so the same answers are used on every computer.

Usage:
    python answers.py              # add new questions from the DB to the bank, show coverage
    python answers.py <job-id>     # preview how one job's form would be autofilled

Workflow:
    1. python collect.py
    2. python answers.py           -> adds new questions to the bank in Supabase
    3. in the desktop app's Answers tab, fill "My profile" and answer the questions
    4. python answers.py           -> shows how many jobs can now be filled fully
"""
import json
import re
import sys
from datetime import datetime, timezone

import db
from config import BASE_DIR, TAILORED_RESUME
import supabase_db as supa

PLATFORM = "ashby"                       # autobid_questions.platform for this bot
LEGACY_PATH = BASE_DIR / "answers.json"  # old local bank: imported into Supabase once
STORAGE_NAME = "the answer bank (Supabase)"
TAILORED_PLACEHOLDER = "<tailored resume>"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_PROFILE = {
    "name": "",
    "email": "",
    "phone": "",
    "linkedin": "",
    "github": "",
    "website": "",
    "location": "",          # e.g. "Austin, Texas, United States"
    "resume_file": "",       # only used when TAILORED_RESUME = False in config.py
}

# Questions answered straight from "profile" (never listed in the bank).
PROFILE_BY_PATH = {
    "_systemfield_name": "name",
    "_systemfield_email": "email",
    "_systemfield_phone": "phone",
    "_systemfield_location": "location",
    "_systemfield_resume": "resume_file",
}
PROFILE_BY_TITLE = [  # (regex on question title, profile key) for plain text fields
    (r"^linked ?in( profile| url)?$", "linkedin"),
    (r"^github( profile| url)?$", "github"),
    (r"^(personal )?(website|portfolio)( url)?$", "website"),
    (r"^(phone|phone number|mobile)$", "phone"),
    (r"^(full )?name$", "name"),
    (r"^e-?mail( address)?$", "email"),
]

# Question types that are answered with a free-form text by the bid step (AI) when
# no saved answer exists.
TEXT_TYPES = {"String", "LongText"}


def profile_key(q):
    if q["path"] in PROFILE_BY_PATH:
        return PROFILE_BY_PATH[q["path"]]
    if q["type"] in ("String", "Email", "Phone"):
        title = q["title"].strip().lower().rstrip("?:*").strip()
        for pattern, key in PROFILE_BY_TITLE:
            if re.match(pattern, title):
                return key
    return None


# ---------------------------------------------------------------- storage: Supabase
# The bank lives in Supabase (tables autobid_questions + autobid_profile, created by
# supabase/migrations/004_autobid_answers.sql), so every computer uses the same answers.
# Nothing is kept locally. An old local answers.json is imported once, then renamed.

META_FIELDS = ("question", "type", "form", "note", "choices", "asked_in_jobs")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sort(questions):
    """Survey last; within a form, most common questions first."""
    return sorted(questions, key=lambda e: (e.get("form") == "survey", -e.get("asked_in_jobs", 0),
                                            e["question"]))


def _entry(row):
    """Supabase row -> bank entry (same shape as the old answers.json entries)."""
    e = {"key": row["key"], "question": row["question"], "type": row["type"], "form": row["form"]}
    if row.get("note"):
        e["note"] = row["note"]
    if row.get("choices"):
        e["choices"] = row["choices"]
    e["asked_in_jobs"] = row.get("asked_in_jobs") or 0
    e["answer"] = row.get("answer")
    return e


def _meta(e):
    """Bank entry -> the question columns (never the answer)."""
    return {"platform": PLATFORM, "key": e["key"], "question": e.get("question") or "",
            "type": e.get("type") or "", "form": e.get("form") or "", "note": e.get("note") or None,
            "choices": e.get("choices") or None, "asked_in_jobs": int(e.get("asked_in_jobs") or 0)}


def _fetch_questions():
    return supa.select_all("autobid_questions", {
        "select": "key,question,type,form,note,choices,asked_in_jobs,answer",
        "platform": f"eq.{PLATFORM}", "order": "key"})


def _fetch_profile():
    profile = dict(DEFAULT_PROFILE)
    for r in supa.select_all("autobid_profile", {"select": "key,value", "order": "key"}):
        profile[r["key"]] = r["value"] or ""
    return profile


def _import_local_file():
    """One time: copy an old local answers.json into Supabase, then rename the file."""
    if not LEGACY_PATH.exists():
        return
    try:
        old = json.loads(LEGACY_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Could not read {LEGACY_PATH.name} to import it: {e}")
        return
    remote = {r["key"]: r for r in _fetch_questions()}
    rows = []
    for q in old.get("questions", []):
        if not q.get("key"):
            continue
        r = _meta(q)
        have = remote.get(q["key"])
        # an answer already in Supabase wins over the old file
        r["answer"] = have["answer"] if have and not _is_empty(have.get("answer")) else q.get("answer")
        r["updated_at"] = _now()
        rows.append(r)
    supa.upsert("autobid_questions", rows, "platform,key")
    profile = _fetch_profile()
    prows = [{"key": k, "value": str(v), "updated_at": _now()}
             for k, v in (old.get("profile") or {}).items() if not _is_empty(v) and _is_empty(profile.get(k))]
    if prows:
        supa.upsert("autobid_profile", prows, "key")
    done = LEGACY_PATH.with_name("answers.json.imported")
    if done.exists():
        done.unlink()
    LEGACY_PATH.rename(done)
    print(f"Imported {len(rows)} questions and {len(prows)} profile fields from answers.json "
          f"into Supabase (old file kept as {done.name}; you can delete it).")


def load_bank():
    """The whole bank from Supabase: {"profile": {...}, "questions": [...]}."""
    _import_local_file()
    return {"profile": _fetch_profile(), "questions": _sort(_entry(r) for r in _fetch_questions())}


def save_bank(bank):
    """Save the questions after sync_bank: new questions are added, changed question
    details updated, dropped unanswered ones deleted. Answers and the profile are never
    written here (only set_answer / set_profile do), so an answer saved meanwhile on
    another computer is never lost - it is copied into `bank` instead."""
    remote = {r["key"]: r for r in _fetch_questions()}
    changed = []
    for q in bank["questions"]:
        r = remote.get(q["key"])
        if r and _is_empty(q.get("answer")) and not _is_empty(r.get("answer")):
            q["answer"] = r["answer"]
        m = _meta(q)
        if r is None or any(m[f] != (r.get(f) if f != "asked_in_jobs" else int(r.get(f) or 0))
                            for f in META_FIELDS):
            m["updated_at"] = _now()
            changed.append(m)
    if changed:
        supa.upsert("autobid_questions", changed, "platform,key")
    keys = {q["key"] for q in bank["questions"]}
    gone = [k for k, r in remote.items() if k not in keys and _is_empty(r.get("answer"))]
    for i in range(0, len(gone), 40):
        supa.request("DELETE", "autobid_questions", {"platform": f"eq.{PLATFORM}",
                     "key": supa.in_list(gone[i:i + 40]), "answer": "is.null"}, prefer="return=minimal")
    # answered questions only another computer knows about: keep them in this bank too
    for k, r in remote.items():
        if k not in keys and not _is_empty(r.get("answer")):
            bank["questions"].append(_entry(r))
    bank["questions"] = _sort(bank["questions"])


def set_answer(key, answer, bank=None):
    """Save ONE answer in Supabase (only this question changes). answer=None clears it.
    Returns `bank` with the answer updated, or a freshly loaded bank when none is given."""
    rows = supa.request("PATCH", "autobid_questions", {"platform": f"eq.{PLATFORM}", "key": f"eq.{key}"},
                        {"answer": None if _is_empty(answer) else answer, "updated_at": _now()},
                        prefer="return=representation")
    if not rows:
        raise KeyError(f"question not in the answer bank: {key}")
    if bank is None:
        return load_bank()
    for q in bank["questions"]:
        if q["key"] == key:
            q["answer"] = rows[0]["answer"]
    return bank


def set_profile(values, bank=None):
    """Save profile fields (dict) in Supabase. Returns `bank` with the profile refreshed
    (or a freshly loaded bank)."""
    supa.upsert("autobid_profile", [{"key": k, "value": "" if v is None else str(v), "updated_at": _now()}
                                    for k, v in values.items()], "key")
    if bank is None:
        return load_bank()
    bank["profile"] = _fetch_profile()
    return bank


def sync_bank(conn, bank):
    """Bank = every distinct REQUIRED question of the relevant jobs (optional ones never),
    keeping existing answers."""
    existing = {q["key"]: q for q in bank["questions"]}
    stats = {}
    rows = conn.execute("""
        SELECT q.* FROM questions q JOIN jobs j ON j.id = q.job_id
        WHERE j.is_active = 1 AND j.relevant = 1 AND q.required = 1 ORDER BY q.position""").fetchall()
    for q in rows:
        if profile_key(q):
            continue
        s = stats.setdefault(q["bank_key"], {"row": q, "jobs": set()})
        s["jobs"].add(q["job_id"])

    added = 0
    for key, s in stats.items():
        q = s["row"]
        entry = existing.get(key)
        if entry is None:
            entry = {"key": key, "answer": None}
            existing[key] = entry
            added += 1
        entry.update({
            "question": q["title"],
            "type": q["type"],
            "form": q["form"],
            "note": q["description"] or "",
            "choices": [o["label"] for o in json.loads(q["options"] or "[]")],
            "asked_in_jobs": len(s["jobs"]),
        })
        if not entry["choices"]:
            del entry["choices"]
        if not entry["note"]:
            del entry["note"]
        # keep "answer" last so it is easy to spot when editing
        entry["answer"] = entry.pop("answer")

    # Only REQUIRED questions are kept. One that no relevant job requires (any more) is
    # removed - unless you already answered it: then it stays (hidden, asked_in_jobs = 0)
    # so the answer is not lost if such a job comes back.
    # No relevant jobs here at all (e.g. a new computer before its first 'Sync DB'): remove
    # nothing, other computers still need these questions.
    for key in list(existing) if stats else []:
        if key not in stats:
            if _is_empty(existing[key].get("answer")):
                del existing[key]
            else:
                existing[key]["asked_in_jobs"] = 0

    # Survey last; within a form, most common questions first.
    bank["questions"] = sorted(
        existing.values(),
        key=lambda e: (e.get("form") == "survey", -e.get("asked_in_jobs", 0), e["question"]),
    )
    return added


# ---------------------------------------------------------------- resolving answers

def _is_empty(v):
    return v is None or v == "" or v == []


def to_form_value(q, answer):
    """Convert a human answer from the bank into the value Ashby expects.

    Returns (value, error). Boolean -> True/False, ValueSelect -> option value,
    MultiValueSelect -> list of option values, everything else -> text.
    """
    ftype = q["type"]
    options = json.loads(q["options"] or "[]")

    def match_option(a):
        a_norm = str(a).strip().lower()
        for o in options:
            if a_norm in (str(o["label"]).lower(), str(o["value"]).lower()):
                return o["value"]
        return None

    if ftype == "Boolean":
        if isinstance(answer, bool):
            return answer, None
        a = str(answer).strip().lower()
        if a in ("yes", "y", "true"):
            return True, None
        if a in ("no", "n", "false"):
            return False, None
        return None, f"expected Yes/No, got {answer!r}"
    if ftype == "ValueSelect":
        v = match_option(answer)
        return (v, None) if v is not None else (None, f"{answer!r} is not one of the choices")
    if ftype == "MultiValueSelect":
        items = answer if isinstance(answer, list) else [answer]
        values = [match_option(a) for a in items]
        bad = [a for a, v in zip(items, values) if v is None]
        return (values, None) if not bad else (None, f"not a choice: {bad}")
    return str(answer), None


def resolve_job(conn, job_id, bank):
    """Answer the REQUIRED questions of one job from the profile + bank.
    Optional questions (incl. the Diversity Survey) are left empty.

    Returns a list of dicts: question row, value, source ('profile'|'bank'),
    or missing=True / error=... when it cannot be answered yet.
    """
    # Exception: the resume is always uploaded, even when the job marks it optional.
    rows = conn.execute("SELECT * FROM questions WHERE job_id=? AND (required=1 "
                        "OR path='_systemfield_resume') ORDER BY position", (job_id,)).fetchall()
    return _resolve_rows(rows, bank, {q["key"]: q for q in bank["questions"]})


def resolve_all(conn, bank):
    """resolve_job() for every active job with ONE query: {job_id: items}."""
    by_key = {q["key"]: q for q in bank["questions"]}
    grouped = {}
    for q in conn.execute("SELECT q.* FROM questions q JOIN jobs j ON j.id = q.job_id "
                          "WHERE j.is_active=1 AND j.relevant=1 AND (q.required=1 OR q.path='_systemfield_resume') "
                          "ORDER BY q.job_id, q.position"):
        grouped.setdefault(q["job_id"], []).append(q)
    return {jid: _resolve_rows(rows, bank, by_key) for jid, rows in grouped.items()}


def _resolve_rows(rows, bank, by_key):
    result = []
    for q in rows:
        item = {"q": q, "value": None, "source": None, "missing": False, "error": None}
        pkey = profile_key(q)
        if pkey == "resume_file" and TAILORED_RESUME:
            # made per job by bid.py (common/resume.py) right before filling the form
            item["value"], item["source"] = TAILORED_PLACEHOLDER, "tailored resume (AI)"
            result.append(item)
            continue
        if pkey:
            raw, item["source"] = bank["profile"].get(pkey), f"profile.{pkey}"
        else:
            raw, item["source"] = (by_key.get(q["bank_key"]) or {}).get("answer"), "bank"
        if _is_empty(raw):
            item["missing"] = True
        else:
            item["value"], item["error"] = to_form_value(q, raw)
        result.append(item)
    return result


def blocking(items):
    """Required questions that still have no usable answer."""
    return [i for i in items if i["missing"] or i["error"]]


def ai_writable(item):
    """A missing text/essay answer from the bank: the AI step can write it per job.
    (Missing PROFILE fields like Name or Email never go to the AI.)"""
    return (item["missing"] and not item["error"] and item["source"] == "bank"
            and item["q"]["type"] in TEXT_TYPES)


# ---------------------------------------------------------------- CLI

def print_coverage(conn, bank):
    jobs = conn.execute(
        "SELECT id, title FROM jobs WHERE is_active=1 AND relevant=1 AND status IN ('ready','needs_you')").fetchall()
    ready, need_text, need_other = 0, 0, 0
    for job in jobs:
        missing = blocking(resolve_job(conn, job["id"], bank))
        if not missing:
            ready += 1
        elif all(ai_writable(i) for i in missing):
            need_text += 1
        else:
            need_other += 1

    qs = bank["questions"]
    answered = sum(not _is_empty(q["answer"]) for q in qs)
    profile_missing = [k for k, v in bank["profile"].items() if _is_empty(v)
                       and not (k == "resume_file" and TAILORED_RESUME)]
    print(f"Answer bank: {answered}/{len(qs)} questions answered  ->  {STORAGE_NAME}")
    if profile_missing:
        print(f"Profile still empty: {', '.join(profile_missing)}")
    print(f"\nJobs to bid: {len(jobs)}")
    print(f"  {ready:>4}  can be autofilled fully right now")
    print(f"  {need_text:>4}  only missing text/essay answers (AI can write these per job)")
    print(f"  {need_other:>4}  missing yes/no, choice or profile answers -> answer them in the app")

    todo = [q for q in qs if _is_empty(q["answer"]) and q.get("asked_in_jobs")
            and q["type"] not in TEXT_TYPES]
    if todo:
        print("\nMost useful to answer next (non-text):")
        for q in todo[:15]:
            print(f"  [{q['asked_in_jobs']:>3} jobs] [{q['type']}] {q['question'][:90]}")


def print_job(conn, job_id, bank):
    job = conn.execute("SELECT title FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        sys.exit(f"No job with id {job_id}")
    print(job["title"], "\n")
    print("(required questions only - optional ones are left empty)\n")
    for i in resolve_job(conn, job_id, bank):
        q = i["q"]
        mark = "*"
        if i["error"]:
            shown = f"!! {i['error']}"
        elif i["missing"]:
            shown = "-- (AI will write)" if ai_writable(i) else "-- MISSING"
        else:
            shown = json.dumps(i["value"], ensure_ascii=False)
            shown = shown if len(shown) < 70 else shown[:67] + '..."'
            shown += f"   <- {i['source']}"
        print(f"{mark} {q['title'][:70]:<70} {shown}")


def main():
    conn = db.connect()
    bank = load_bank()
    added = sync_bank(conn, bank)
    save_bank(bank)
    if added:
        print(f"Added {added} new questions to the answer bank\n")
    if len(sys.argv) > 1:
        print_job(conn, sys.argv[1], bank)
    else:
        print_coverage(conn, bank)


if __name__ == "__main__":
    main()
