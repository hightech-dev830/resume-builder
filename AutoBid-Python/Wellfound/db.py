"""SQLite storage for collected Wellfound jobs and application questions."""
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
    department       TEXT,
    team             TEXT,
    employment_type  TEXT,
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
    form_id     TEXT,
    section     TEXT,
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
CREATE INDEX IF NOT EXISTS idx_jobs_org ON jobs(org);
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
    return conn


def epoch_to_iso(epoch):
    if epoch is None or epoch == "":
        return None
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None


def upsert_listing(conn, org, company, job, seen_at):
    """Stage 1: save a job from the company jobs page. True if new."""
    values = {
        "id": job["id"], "org": org, "company": company or org,
        "title": job["title"], "department": job.get("department") or "",
        "team": job.get("team") or "", "employment_type": job.get("employment_type") or "",
        "location": job.get("location") or "",
        "locations_all": json.dumps([l for l in (job.get("locations") or []) if l]),
        "workplace_type": job.get("workplace_type") or ("Remote" if job.get("is_remote") else ""),
        "is_remote": int(bool(job.get("is_remote"))),
        "job_url": job["job_url"], "apply_url": job["apply_url"],
        "compensation": job.get("compensation") or "",
        "published_at": job.get("published_at"),
        "seen": seen_at,
    }
    if conn.execute("SELECT 1 FROM jobs WHERE id=?", (job["id"],)).fetchone():
        conn.execute("""
            UPDATE jobs SET org=:org, company=:company, title=:title, department=:department,
                team=:team, employment_type=:employment_type, location=:location,
                locations_all=:locations_all, workplace_type=:workplace_type,
                is_remote=:is_remote, job_url=:job_url, apply_url=:apply_url,
                compensation=COALESCE(NULLIF(:compensation, ''), compensation),
                published_at=COALESCE(:published_at, published_at),
                last_seen_at=:seen, is_active=1
            WHERE id=:id""", values)
        return False
    conn.execute("""
        INSERT INTO jobs (id, org, company, title, department, team, employment_type,
            location, locations_all, workplace_type, is_remote, job_url, apply_url,
            compensation, published_at, first_seen_at, last_seen_at, status)
        VALUES (:id, :org, :company, :title, :department, :team, :employment_type,
            :location, :locations_all, :workplace_type, :is_remote, :job_url, :apply_url,
            :compensation, :published_at, :seen, :seen, 'new')""", values)
    return True


def mark_inactive(conn, org, active_ids):
    if not active_ids:
        conn.execute("UPDATE jobs SET is_active=0 WHERE org=?", (org,))
        return
    placeholders = ",".join("?" * len(active_ids))
    conn.execute(f"UPDATE jobs SET is_active=0 WHERE org=? AND id NOT IN ({placeholders})",
                 [org, *active_ids])


def set_relevance(conn, job_id, relevant, method):
    conn.execute("UPDATE jobs SET relevant=?, relevance_by=? WHERE id=?",
                 (int(bool(relevant)), method, job_id))


def jobs_needing_relevance(conn):
    return conn.execute(
        "SELECT id, title, COALESCE(department, '') dept FROM jobs "
        "WHERE is_active=1 AND (relevant IS NULL OR relevance_by='unclear-kept')"
    ).fetchall()


def save_details(conn, job_id, details, questions):
    now_s = now()
    conn.execute("""
        UPDATE jobs SET description=?, description_html=?, compensation=COALESCE(NULLIF(?, ''), compensation),
            published_at=COALESCE(?, published_at), details_at=?, status='ready',
            location=COALESCE(NULLIF(?, ''), location),
            workplace_type=COALESCE(NULLIF(?, ''), workplace_type),
            is_remote=COALESCE(?, is_remote), employment_type=COALESCE(NULLIF(?, ''), employment_type)
        WHERE id=?""", (
        details.get("description") or "", details.get("description_html") or "",
        details.get("compensation") or "", details.get("published_at"), now_s,
        details.get("location") or "", details.get("workplace_type") or "",
        details.get("is_remote"), details.get("employment_type") or "", job_id))
    conn.execute("DELETE FROM questions WHERE job_id=?", (job_id,))
    for i, q in enumerate(questions):
        qid = q.get("id") or f"{job_id}:{i}"
        conn.execute("""
            INSERT OR REPLACE INTO questions
            (id, job_id, position, form, form_id, section, path, title, type, required,
             description, options, raw, bank_key)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            qid, job_id, i, q.get("form") or "application", q.get("form_id"),
            q.get("section") or "", q.get("path") or "", q.get("title") or "",
            q.get("type") or "String", int(bool(q.get("required"))),
            q.get("description") or "",
            json.dumps(q.get("options") or []),
            json.dumps(q.get("raw") or {}),
            bank_key(q.get("title") or "", q.get("type") or "String"),
        ))


def set_status(conn, job_id, status, note=None):
    conn.execute(
        "UPDATE jobs SET status=?, status_at=?, note=COALESCE(?, note), "
        "applied_at=CASE WHEN ?='applied' THEN COALESCE(applied_at, ?) ELSE applied_at END "
        "WHERE id=?",
        (status, now(), note, status, now(), job_id))
    conn.commit()


def board_newest_posting(conn, org):
    row = conn.execute(
        "SELECT MAX(COALESCE(NULLIF(published_at, ''), first_seen_at)) AS newest "
        "FROM jobs WHERE org=?", (org,)).fetchone()
    if not row or not row["newest"]:
        return None
    try:
        return datetime.fromisoformat(str(row["newest"]).replace("Z", "+00:00"))
    except Exception:
        return None


def is_stale_board(conn, org, stale_days):
    newest = board_newest_posting(conn, org)
    if not newest:
        return False
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - newest.astimezone(timezone.utc)).days > stale_days
