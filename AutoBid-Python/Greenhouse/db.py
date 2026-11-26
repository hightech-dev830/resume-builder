"""SQLite storage for MyGreenhouse jobs and application questions."""
import json
import re
import sqlite3
from datetime import datetime, timezone

from config import DATA_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT PRIMARY KEY,
    org              TEXT NOT NULL,
    company          TEXT,
    title            TEXT,
    location         TEXT,
    locations_all    TEXT,
    workplace_type   TEXT,
    is_remote        INTEGER,
    job_url          TEXT,
    apply_url        TEXT,
    compensation     TEXT,
    description      TEXT,
    description_html TEXT,
    published_at     TEXT,
    first_seen_at    TEXT,
    last_seen_at     TEXT,
    is_active        INTEGER DEFAULT 1,
    status           TEXT DEFAULT 'new',
    relevant         INTEGER,
    relevance_by     TEXT,
    details_at       TEXT,
    applied_at       TEXT,
    note             TEXT,
    resume_path      TEXT,
    status_at        TEXT
);

CREATE TABLE IF NOT EXISTS questions (
    id          TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    position    INTEGER,
    form        TEXT,
    path        TEXT,
    title       TEXT,
    type        TEXT,
    required    INTEGER,
    description TEXT,
    options     TEXT,
    raw         TEXT,
    bank_key    TEXT,
    answer      TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_questions_job ON questions(job_id);
"""

BOT_STATUSES = ["new", "ready", "needs_you", "applied", "failed", "skipped"]
MY_STATUSES = ["1Interview", "2Interview", "3Interview", "Offer", "Rejected"]
STATUSES = BOT_STATUSES + MY_STATUSES
APPLIED_STATUSES = ("applied",) + tuple(MY_STATUSES)


def bank_key(title, ftype):
    text = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()
    return f"{ftype}:{text}"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    qcols = {r["name"] for r in conn.execute("PRAGMA table_info(questions)")}
    if "prepared" not in qcols:
        # per-job answer written by "Prepare answers" (JSON); Apply uses it first
        conn.execute("ALTER TABLE questions ADD COLUMN prepared TEXT")
    return conn


def upsert_job(conn, job, seen_at):
    """Insert/update a scraped job. True if new."""
    jid = job["id"]
    exists = conn.execute("SELECT 1 FROM jobs WHERE id=?", (jid,)).fetchone()
    locs = json.dumps(job.get("locations") or [])
    apply_url = job.get("apply_url") or ""
    desc = job.get("description") or ""
    if exists:
        conn.execute("""
            UPDATE jobs SET company=?, title=?, location=?, locations_all=?,
                workplace_type=?, is_remote=?,
                job_url=COALESCE(NULLIF(?,''), job_url),
                apply_url=COALESCE(NULLIF(?,''), apply_url),
                compensation=COALESCE(NULLIF(?,''), compensation),
                description=COALESCE(NULLIF(?,''), description),
                description_html=COALESCE(NULLIF(?,''), description_html),
                published_at=COALESCE(?, published_at),
                last_seen_at=?, is_active=1,
                details_at=CASE WHEN ? != '' THEN ? ELSE details_at END,
                status=CASE WHEN ? != '' AND status='new' THEN 'ready' ELSE status END
            WHERE id=?""", (
            job.get("company") or "", job["title"], job.get("location") or "", locs,
            job.get("workplace_type") or "Remote", int(bool(job.get("is_remote", True))),
            job.get("job_url") or "", apply_url, job.get("compensation") or "",
            desc, job.get("description_html") or "", job.get("published_at"),
            seen_at, desc, seen_at, apply_url, jid))
        return False
    status = "ready" if apply_url else "new"
    conn.execute("""
        INSERT INTO jobs (id, org, company, title, location, locations_all, workplace_type,
            is_remote, job_url, apply_url, compensation, description, description_html,
            published_at, first_seen_at, last_seen_at, status, details_at, relevant)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)""", (
        jid, job.get("org") or "greenhouse", job.get("company") or "", job["title"],
        job.get("location") or "", locs, job.get("workplace_type") or "Remote",
        int(bool(job.get("is_remote", True))), job.get("job_url") or "", apply_url,
        job.get("compensation") or "", desc, job.get("description_html") or "",
        job.get("published_at"), seen_at, seen_at, status,
        seen_at if desc else None))
    return True


def set_relevance(conn, job_id, relevant, method):
    conn.execute("UPDATE jobs SET relevant=?, relevance_by=? WHERE id=?",
                 (int(bool(relevant)), method, job_id))


def jobs_needing_relevance(conn):
    return conn.execute(
        "SELECT id, title, COALESCE(location, '') AS dept FROM jobs "
        "WHERE is_active=1 AND (relevant IS NULL OR relevance_by='unclear-kept')"
    ).fetchall()


def mark_inactive(conn, org, active_ids):
    placeholders = ",".join("?" * len(active_ids)) or "''"
    cur = conn.execute(
        f"UPDATE jobs SET is_active=0 WHERE org=? AND is_active=1 AND id NOT IN ({placeholders})",
        [org, *active_ids],
    )
    return cur.rowcount


def jobs_needing_details(conn, orgs=None, refresh=False):
    """Relevant active jobs missing description and/or application questions."""
    if refresh:
        sql = ("SELECT id, org, company FROM jobs WHERE is_active=1 AND relevant=1")
    else:
        # Need description OR no questions rows yet (Answers column = "no details yet")
        sql = (
            "SELECT j.id, j.org, j.company FROM jobs j "
            "WHERE j.is_active=1 AND j.relevant=1 AND ("
            "  j.description IS NULL OR j.description='' OR j.details_at IS NULL OR "
            "  NOT EXISTS (SELECT 1 FROM questions q WHERE q.job_id = j.id)"
            ")"
        )
    rows = conn.execute(sql).fetchall()
    return [r for r in rows if not orgs or r["org"] in orgs]


def save_details(conn, job_id, details, questions=None):
    """Stage 2: description + application questions from the boards API."""
    conn.execute("""
        UPDATE jobs SET description=?, description_html=?,
            published_at=COALESCE(?, published_at), details_at=?,
            status=CASE WHEN status='new' AND apply_url != '' AND apply_url IS NOT NULL
                        THEN 'ready' ELSE status END
        WHERE id=?""",
        (details.get("description") or "", details.get("description_html") or "",
         details.get("published_at"), now(), job_id))
    if questions is not None:
        save_questions(conn, job_id, questions)


def set_status(conn, job_id, status, note=None):
    conn.execute(
        "UPDATE jobs SET status=?, status_at=?, note=COALESCE(?, note), "
        "applied_at=CASE WHEN ?='applied' THEN COALESCE(applied_at, ?) ELSE applied_at END "
        "WHERE id=?",
        (status, now(), note, status, now(), job_id))
    conn.commit()


def applied_orgs(conn):
    """Board tokens (companies) with at least one job applied to (or a later status)."""
    ph = ",".join("?" * len(APPLIED_STATUSES))
    return {r[0] for r in conn.execute(
        f"SELECT DISTINCT org FROM jobs WHERE status IN ({ph})", APPLIED_STATUSES)}


def job_for_ai(job):
    """Shape expected by common/resume.py (same as the extension)."""
    locs = []
    try:
        locs = json.loads(job["locations_all"] or "[]")
    except Exception:
        pass
    return {
        "id": job["id"],
        "company": job["company"] or job["org"],
        "role": job["title"],
        "location": ", ".join(locs) or job["location"] or "",
        "salary": job["compensation"] or "",
        "job_link": job["apply_url"] or job["job_url"] or "",
        "jd": job["description"] or "",
    }


def set_resume(conn, job_id, path, ai_provider="", ai_model=""):
    conn.execute("UPDATE jobs SET resume_path=? WHERE id=?", (str(path), job_id))
    conn.commit()
    try:
        import resume_cloud
        rid = resume_cloud.push_one(conn, job_id, ai_provider=ai_provider, ai_model=ai_model)
        if rid:
            print(f"    resume saved to Supabase resumes.id={rid}")
    except Exception as e:
        print(f"    ! resume path not uploaded to Supabase yet: {str(e)[:160]}")


def save_answer(conn, qid, answer):
    if isinstance(answer, (dict, list)):
        answer = json.dumps(answer)
    elif answer is not None and not isinstance(answer, str):
        answer = str(answer)
    conn.execute("UPDATE questions SET answer=? WHERE id=?", (answer, qid))


def add_questions(conn, job_id, questions):
    """Add questions seen only on the live apply form. Never replaces stored (API)
    questions, which carry the real field types and options. Returns how many were added."""
    have = {r[0] for r in conn.execute(
        "SELECT bank_key FROM questions WHERE job_id=?", (job_id,))}
    pos = conn.execute("SELECT COALESCE(MAX(position), -1) FROM questions WHERE job_id=?",
                       (job_id,)).fetchone()[0]
    added = 0
    for q in questions or []:
        title, ftype = q.get("title") or "", q.get("type") or "String"
        key = bank_key(title, ftype)
        if not title or key in have:
            continue
        have.add(key)
        pos += 1
        conn.execute("""
            INSERT OR IGNORE INTO questions
            (id, job_id, position, form, path, title, type, required, description, options, raw, bank_key)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
            f"{job_id}:live:{q.get('path') or pos}", job_id, pos, "application",
            q.get("path") or "", title, ftype, int(bool(q.get("required"))), "",
            json.dumps(q.get("options") or []), json.dumps({"live_form": True}), key))
        added += 1
    return added


def save_questions(conn, job_id, questions):
    """Store apply-form questions. Keep required fields + Resume (even if optional)."""
    keep = []
    for q in questions or []:
        title = q.get("title") or ""
        path = (q.get("path") or "").lower()
        is_resume = q.get("type") == "File" and ("resume" in title.lower() or path == "resume")
        if q.get("required") or is_resume:
            keep.append(q)
    # If API returned nothing required, still store everything non-hidden so Answers works
    if not keep and questions:
        keep = list(questions)
    # keep answers made by "Prepare answers" when the questions are fetched again
    prepared = {r["bank_key"]: r["prepared"] for r in conn.execute(
        "SELECT bank_key, prepared FROM questions WHERE job_id=? AND prepared IS NOT NULL",
        (job_id,))}
    conn.execute("DELETE FROM questions WHERE job_id=?", (job_id,))
    for i, q in enumerate(keep):
        qid = q.get("id") or f"{job_id}:{i}"
        ftype = q.get("type") or "String"
        title = q.get("title") or ""
        key = bank_key(title, ftype)
        conn.execute("""
            INSERT OR REPLACE INTO questions
            (id, job_id, position, form, path, title, type, required, description, options, raw,
             bank_key, prepared)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            qid, job_id, i, q.get("form") or "application", q.get("path") or "",
            title, ftype, int(bool(q.get("required"))),
            q.get("description") or "", json.dumps(q.get("options") or []),
            json.dumps(q.get("raw") or {}),
            key, prepared.get(key)))
    conn.execute(
        "UPDATE jobs SET status='ready' WHERE id=? AND status='new' "
        "AND apply_url IS NOT NULL AND apply_url != ''",
        (job_id,))
