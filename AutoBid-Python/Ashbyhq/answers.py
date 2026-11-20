"""Answer bank: answer each distinct REQUIRED Ashby question ONCE, reuse it on every job.

Only required questions are collected and filled. Optional ones (LinkedIn when it
is optional, the Diversity Survey, ...) are left empty.

The same question ("Will you now or in the future require sponsorship...",
the Diversity Survey, ...) appears on many jobs with a different field id each time,
so answers are matched by question text + type.

Usage:
    python answers.py              # add new questions from the DB to answers.json, show coverage
    python answers.py <job-id>     # preview how one job's form would be autofilled

Workflow:
    1. python collect.py
    2. python answers.py           -> creates/updates answers.json
    3. open answers.json, fill "profile" and the "answer" of each question
    4. python answers.py           -> shows how many jobs can now be filled fully
"""
import json
import os
import re
import sys
import time

import db
from config import BASE_DIR, TAILORED_RESUME

ANSWERS_PATH = BASE_DIR / "answers.json"
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


# ---------------------------------------------------------------- answers.json I/O

def load_bank():
    if ANSWERS_PATH.exists():
        bank = json.loads(ANSWERS_PATH.read_text(encoding="utf-8"))
    else:
        bank = {}
    bank.setdefault("profile", {})
    for k, v in DEFAULT_PROFILE.items():
        bank["profile"].setdefault(k, v)
    bank.setdefault("questions", [])
    return bank


def _write(bank):
    """Atomic write: a crash or a second process never leaves a half-written file."""
    tmp = ANSWERS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(bank, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for attempt in range(10):                       # Windows: the file may be open for a moment
        try:
            os.replace(tmp, ANSWERS_PATH)
            return
        except PermissionError:
            time.sleep(0.1 * (attempt + 1))
    os.replace(tmp, ANSWERS_PATH)


def save_bank(bank):
    """Save the whole bank (after sync_bank). Never loses an answer someone else saved
    meanwhile: answers / profile fields that are empty here but filled on disk are kept."""
    if ANSWERS_PATH.exists():
        try:
            disk = json.loads(ANSWERS_PATH.read_text(encoding="utf-8"))
        except Exception:
            disk = {}
        disk_answers = {q["key"]: q.get("answer") for q in disk.get("questions", [])}
        for q in bank["questions"]:
            if _is_empty(q.get("answer")) and not _is_empty(disk_answers.get(q["key"])):
                q["answer"] = disk_answers[q["key"]]
        for k, v in (disk.get("profile") or {}).items():
            if _is_empty(bank["profile"].get(k)) and not _is_empty(v):
                bank["profile"][k] = v
    _write(bank)


def set_answer(key, answer):
    """Save ONE answer: re-read the file, change only this question, write it back.
    answer=None clears it. Returns the fresh bank."""
    bank = load_bank()
    for q in bank["questions"]:
        if q["key"] == key:
            q["answer"] = answer
            break
    else:
        raise KeyError(f"question not in answers.json: {key}")
    _write(bank)
    return bank


def set_profile(values):
    """Save profile fields (dict) the same way. Returns the fresh bank."""
    bank = load_bank()
    bank["profile"].update(values)
    _write(bank)
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
    for key in list(existing):
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
    """Convert a human answer from answers.json into the value Ashby expects.

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
    print(f"Answer bank: {answered}/{len(qs)} questions answered  ->  {ANSWERS_PATH}")
    if profile_missing:
        print(f"Profile still empty: {', '.join(profile_missing)}")
    print(f"\nJobs to bid: {len(jobs)}")
    print(f"  {ready:>4}  can be autofilled fully right now")
    print(f"  {need_text:>4}  only missing text/essay answers (AI can write these per job)")
    print(f"  {need_other:>4}  missing yes/no, choice or profile answers -> fill answers.json")

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
        print(f"Added {added} new questions to answers.json\n")
    if len(sys.argv) > 1:
        print_job(conn, sys.argv[1], bank)
    else:
        print_coverage(conn, bank)


if __name__ == "__main__":
    main()
