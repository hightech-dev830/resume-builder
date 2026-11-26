"""Answer bank for MyGreenhouse (Supabase platform=greenhouse) + profile defaults."""
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

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
    "address": "",          # mailing / street address (not the Location City typeahead)
    "salary": "",           # desired / target salary (USD etc.)
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
    "address": "address", "mailing_address": "address",
    "salary": "salary", "desired_salary": "salary",
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
    # Greenhouse location typeahead / city questions
    (r"^location(\s*\(city\))?$", "location"),
    (r"^city$", "location"),
    (r"^current (city|location)$", "location"),
    (r"^what city are you (located|based) in", "location"),
    (r"^which city are you (located|based|in)", "location"),
    (r"^city (are you|you are) (located|based) in", "location"),
    # Mailing / street address (required on many boards; not the city typeahead)
    (r"^(please )?provide (your )?mailing address$", "address"),
    (r"^mailing address$", "address"),
    (r"^(home |street |postal )?address$", "address"),
    (r"^what is your (mailing |home )?address$", "address"),
    # Desired / target salary — profile.salary when set; else AI / Answers bank
    (r"^what (approximate )?salary", "salary"),
    (r"^(desired |expected |target )?salary", "salary"),
    (r"^salary expectation", "salary"),
    # Work authorization / sponsorship (single-topic only — see profile_key)
    (r"^are you (legally |currently )?authorized to work in the (us|u\.s\.|united states)",
     "authorized_us"),
    (r"^are you legally authorized to work in the united states", "authorized_us"),
    (r"^(do you|will you)( now)?(,?| or will you).{0,30}require.{0,40}sponsorship",
     "sponsorship"),
    (r"^do you or will you require sponsorship", "sponsorship"),
    (r"^resume(/ ?cv)?$", "resume_file"),
]

TEXT_TYPES = {"String", "LongText"}

# Titles that must be answered by you (Answers / profile) — never "(AI will write)"
_YOU_ANSWER_TITLE = re.compile(
    r"authoriz(ed|ation).{0,60}work|require sponsorship|visa sponsorship|"
    r"what city|which city|city are you (located|based|in)|"
    r"check all that apply|select all that apply|which of these|"
    r"how did you hear|gender|race|ethnicity|disability|veteran",
    re.I,
)


def _question_title(q) -> str:
    if isinstance(q, dict):
        return (q.get("title") or q.get("question") or "").strip()
    try:
        return (q["title"] or "").strip()
    except Exception:
        return ""


def must_you_answer_title(title: str) -> bool:
    """True for yes/no, city, multi-select, auth — not free-form essays."""
    t = (title or "").strip().lower().rstrip("?:*").strip()
    if not t:
        return False
    if _YOU_ANSWER_TITLE.search(t):
        return True
    # Short yes/no style prompts mis-typed as String by the API
    if re.match(r"^(are you|do you|have you|will you|can you)\b", t) and len(t) < 160:
        if not re.search(r"describe|explain|tell us about|please (share|provide|list)", t):
            return True
    return False


def is_ai_text_question(q) -> bool:
    """Bank/job question the Apply AI step may fill when unanswered."""
    typ = q.get("type") if isinstance(q, dict) else q["type"]
    if typ not in TEXT_TYPES:
        return False
    return not must_you_answer_title(_question_title(q))


def profile_key(q):
    path = (q.get("path") if isinstance(q, dict) else q["path"] or "").strip().lower()
    if path in PROFILE_BY_PATH:
        return PROFILE_BY_PATH[path]
    title = _question_title(q).lower().rstrip("?:*").strip()
    # Combined auth + sponsorship in one field → Answers tab (not a single profile key)
    if re.search(r"authoriz", title) and re.search(r"sponsorship", title):
        return None
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
    import autobid_qbank as qbank
    by_key = {q["key"]: q for q in (bank or {}).get("questions", [])}
    rows = []
    for key, answer in changes.items():
        q = by_key.get(key)
        if not q:
            continue
        rows.append({
            "platform": q.get("platforms") or [PLATFORM], "key": key,
            "question": q.get("question") or "", "type": q.get("type") or "",
            "form": q.get("form") or "", "note": q.get("note"),
            "choices": q.get("choices"), "asked_in_jobs": int(q.get("asked_in_jobs") or 0),
            "answer": None if _is_empty(answer) else answer, "updated_at": _now(),
        })
    qbank.upsert_rows(rows, PLATFORM)
    return bank


def set_answer(key, answer, bank=None):
    """Save ONE answer in Supabase immediately. Prefer set_answer_local + flush_answers."""
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


def _synthetic_resume_row(job_id):
    return {
        "id": f"{job_id}:resume", "job_id": job_id, "position": 999,
        "form": "application", "path": "resume", "title": "Resume/CV",
        "type": "File", "required": 1, "description": "", "options": "[]",
        "raw": "{}", "bank_key": db.bank_key("Resume/CV", "File"), "answer": None,
    }


def _rows_with_resume(job_id, rows):
    rows = list(rows)
    if not any((r["type"] == "File" or "resume" in (r["title"] or "").lower()) for r in rows):
        rows.append(_synthetic_resume_row(job_id))
    return rows


def resolve_job(conn, job_id, bank):
    by_key = {q["key"]: q for q in bank["questions"]}
    rows = list(conn.execute(
        "SELECT * FROM questions WHERE job_id=? ORDER BY position", (job_id,)).fetchall())
    return _resolve_rows(_rows_with_resume(job_id, rows), bank, by_key)


def resolve_all(conn, bank):
    """resolve_job() for jobs that have stored questions only — {job_id: items}."""
    by_key = {q["key"]: q for q in bank["questions"]}
    grouped = {}
    for q in conn.execute(
            "SELECT q.* FROM questions q JOIN jobs j ON j.id = q.job_id "
            "WHERE j.is_active=1 AND j.relevant=1 ORDER BY q.job_id, q.position"):
        grouped.setdefault(q["job_id"], []).append(q)
    return {jid: _resolve_rows(_rows_with_resume(jid, rows), bank, by_key)
            for jid, rows in grouped.items()}


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
            if not _is_empty(raw):
                item["value"], item["source"] = raw, f"profile.{pkey}"
                result.append(item)
                continue
            # address / salary: empty profile → fall through to Answers bank / AI essays
            if pkey in ("address", "salary"):
                e = by_key.get(
                    q.get("bank_key")
                    or db.bank_key(q.get("title") or "", q.get("type") or "String"))
                if e and not _is_empty(e.get("answer")):
                    item["value"], item["source"] = e["answer"], "bank"
                elif q.get("required"):
                    item["missing"] = True
                    item["source"] = "bank"  # AI-writable String/LongText
                result.append(item)
                continue
            item["value"], item["source"] = raw, f"profile.{pkey}"
            if q.get("required"):
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
    """Missing free-form text/essay from the bank — Apply AI may write it per job.

    Yes/no, city, multi-select, work-auth, etc. are NOT AI-writable even when the
    Greenhouse API stored them as String (those show as “missing answers”).
    """
    return (item["missing"] and not item.get("error") and item.get("source") == "bank"
            and is_ai_text_question(item["q"]))


def _parse_backup_answer(raw):
    if raw is None or raw == "":
        return None
    if isinstance(raw, (dict, list, bool, int, float)):
        return raw
    text = str(raw).strip()
    if not text or text.lower() in ("null", "none"):
        return None
    if text[0] in "[{":
        try:
            return json.loads(text)
        except Exception:
            pass
    return text


def _backup_rows_from_file(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".csv" or (not text.lstrip().startswith(("[", "{")) and "," in text[:200]):
        return list(csv.DictReader(text.splitlines()))
    data = json.loads(text)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("autobid_questions", "questions", "data", "rows"):
            if isinstance(data.get(k), list):
                return data[k]
        if data.get("key") or data.get("question"):
            return [data]
    raise ValueError("backup must be a JSON array/object of autobid_questions rows, or a CSV export")


def _backup_lookup(rows):
    by_key, by_text = {}, {}
    for r in rows:
        ans = _parse_backup_answer(r.get("answer"))
        if _is_empty(ans):
            continue
        key = (r.get("key") or "").strip()
        qtext = (r.get("question") or "").strip()
        qtype = (r.get("type") or "").strip()
        if key:
            by_key[key] = {"answer": ans, "question": qtext, "type": qtype, "key": key}
        if qtext and qtype:
            nk = db.bank_key(qtext, qtype)
            by_text[nk] = {"answer": ans, "question": qtext, "type": qtype, "key": key or nk}
            by_key.setdefault(nk, by_text[nk])
    return by_key, by_text


def import_matched_answers_from_backup(path, bank=None, dry_run=False):
    rows = _backup_rows_from_file(path)
    by_key, by_text = _backup_lookup(rows)
    if bank is None:
        bank = load_bank()
    stats = {"filled": 0, "skipped_has_answer": 0, "no_match": 0, "backup_answered": len(by_key),
             "examples": []}
    updates = []
    for q in bank["questions"]:
        if not _is_empty(q.get("answer")):
            if q["key"] in by_key or db.bank_key(q.get("question") or "", q.get("type") or "") in by_text:
                stats["skipped_has_answer"] += 1
            continue
        hit = by_key.get(q["key"]) or by_text.get(db.bank_key(q.get("question") or "", q.get("type") or ""))
        if not hit or _is_empty(hit.get("answer")):
            stats["no_match"] += 1
            continue
        q["answer"] = hit["answer"]
        updates.append({"key": q["key"], "answer": hit["answer"], "updated_at": _now()})
        stats["filled"] += 1
        if len(stats["examples"]) < 8:
            stats["examples"].append(q.get("question") or q["key"])
    if updates and not dry_run:
        import autobid_qbank as qbank
        remote = {r["key"]: r for r in _fetch_questions()}
        payload = []
        for u in updates:
            r = remote.get(u["key"])
            if not r:
                continue
            payload.append({
                "platform": r.get("platform"), "key": u["key"],
                "question": r.get("question") or "", "type": r.get("type") or "",
                "form": r.get("form") or "", "note": r.get("note"),
                "choices": r.get("choices"), "asked_in_jobs": int(r.get("asked_in_jobs") or 0),
                "answer": u["answer"], "updated_at": u["updated_at"],
            })
        qbank.upsert_rows(payload, PLATFORM)
    return bank, stats


def print_coverage(conn, bank):
    jobs = conn.execute(
        "SELECT id, title FROM jobs WHERE is_active=1 AND relevant=1 "
        "AND status IN ('ready','needs_you','new')").fetchall()
    ready, need_text, need_other = 0, 0, 0
    for job in jobs:
        missing = blocking(resolve_all(conn, bank).get(job["id"], []))
        if not missing:
            ready += 1
        elif all(ai_writable(i) for i in missing):
            need_text += 1
        else:
            need_other += 1
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
    print(f"\nJobs to bid: {len(jobs)}")
    print(f"  {ready:>4}  can be autofilled fully right now")
    print(f"  {need_text:>4}  only missing text/essay answers (AI can write these per job)")
    print(f"  {need_other:>4}  missing yes/no, choice or profile answers -> answer them in the app")


def main():
    args = sys.argv[1:]
    if args and args[0] in ("--import-backup", "--import"):
        if len(args) < 2:
            sys.exit("Usage: python answers.py --import-backup path/to/autobid_questions.json|.csv")
        path = args[1]
        dry = "--dry-run" in args
        conn = db.connect()
        bank = load_bank()
        sync_bank(conn, bank)
        save_bank(bank)
        bank, stats = import_matched_answers_from_backup(path, bank, dry_run=dry)
        print(f"Backup answers available: {stats['backup_answered']}")
        print(f"Filled empty matches:     {stats['filled']}"
              + (" (dry-run, not saved)" if dry else " → saved to this Supabase"))
        print(f"Skipped (already had answer): {stats['skipped_has_answer']}")
        print(f"No match in backup:      {stats['no_match']}")
        if stats["examples"]:
            print("Examples filled:")
            for t in stats["examples"]:
                print(f"  · {t[:90]}")
        return
    conn = db.connect()
    bank = load_bank()
    added = sync_bank(conn, bank)
    save_bank(bank)
    if added:
        print(f"Added {added} new questions to the answer bank\n")
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
