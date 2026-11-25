"""SQLite storage for collected Ashby jobs and their application questions."""
import json
import re
import sqlite3
from datetime import datetime, timezone

from config import DATA_DIR, DB_PATH
from html_text import html_to_text

# Two-stage scraping:
#   stage 1 (job list)    -> row with title, department, locations; status 'new';
#                            relevant = 1/0 (software/hardware dev + eng management?)
#   stage 2 (job details) -> only relevant jobs: description, pay, questions; status 'ready'
# Then:  ready -> applied / failed / skipped,  ready -> needs_you (bot check) -> applied
#        applied -> 1Interview -> 2Interview -> 3Interview -> Offer / Rejected  (set by you in the app)
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT PRIMARY KEY,
    org              TEXT NOT NULL,
    title            TEXT,
    department       TEXT,
    team             TEXT,
    employment_type  TEXT,
    location         TEXT,
    locations_all    TEXT,      -- JSON list: primary + secondary locations
    workplace_type   TEXT,
    is_remote        INTEGER,
    job_url          TEXT,
    apply_url        TEXT,
    compensation     TEXT,      -- summary, e.g. "$75K – $140K • Offers Equity • Multiple Ranges"
    compensation_tiers TEXT,    -- one line per region, e.g. "US - Remote: $75K – $122K • Offers Equity"
    description      TEXT,      -- Overview tab as clean Markdown-style text
    description_html TEXT,
    published_at     TEXT,
    first_seen_at    TEXT,
    last_seen_at     TEXT,
    is_active        INTEGER DEFAULT 1,   -- 0 once the job disappears from the board
    status           TEXT DEFAULT 'new',
    applied_at       TEXT,
    note             TEXT,
    resume_path      TEXT       -- tailored resume PDF made for this job
);

CREATE TABLE IF NOT EXISTS questions (
    id          TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    position    INTEGER,
    form        TEXT,      -- 'application' or 'survey' (Diversity Survey)
    form_id     TEXT,
    section     TEXT,
    path        TEXT,
    title       TEXT,
    type        TEXT,
    required    INTEGER,
    description TEXT,      -- small note under the question, e.g. "If not, please do not apply"
    options     TEXT,      -- JSON list of {label, value} for select/multi-select
    raw         TEXT,      -- full field JSON from Ashby
    bank_key    TEXT,      -- links to the same question in answers.json across jobs
    answer      TEXT       -- JSON answer used for this job (filled by the bid step)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_questions_job ON questions(job_id);
"""


# Every status a job can have. The bot sets the first six; you set the rest in the app.
BOT_STATUSES = ["new", "ready", "needs_you", "applied", "failed", "skipped"]
MY_STATUSES = ["1Interview", "2Interview", "3Interview", "Offer", "Rejected"]
STATUSES = BOT_STATUSES + MY_STATUSES
# Statuses that mean "an application was sent" (counted as applied).
APPLIED_STATUSES = ("applied",) + tuple(MY_STATUSES)


def bank_key(title, ftype):
    """Same question text + type => same key on every job (field ids differ per job)."""
    text = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()
    return f"{ftype}:{text}"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # timeout: wait (instead of failing) while another process - e.g. a collection - writes
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        # WAL: the app can read while a collection writes (set once, stored in the DB file)
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass   # another process holds the DB right now; it is set on a later connect
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    # columns added after the first version of the DB
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    if "resume_path" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN resume_path TEXT")
    for col, typ in [("company", "TEXT"), ("dept_path", "TEXT"), ("relevant", "INTEGER"),
                     ("relevance_by", "TEXT"), ("details_at", "TEXT"),
                     ("status_at", "TEXT"),      # when the status was last set (sync: newest wins)
                     ("sync_hash", "TEXT"),      # fingerprint of the row as last synced with Supabase
                     ("resume_id", "INTEGER")]:  # public.resumes.id in Supabase
        if col not in cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_relevant ON jobs(relevant, is_active)")
    return conn


def job_for_ai(job):
    """The job as the resume prompt needs it (same fields as the extension sends)."""
    return {
        "id": job["id"],
        "company": job["company"] or job["org"],
        "role": job["title"],
        "location": ", ".join(json.loads(job["locations_all"] or "[]")) or job["location"],
        "salary": job["compensation_tiers"] or job["compensation"],
        "job_link": job["job_url"],
        "jd": job["description"],
    }


def set_resume(conn, job_id, path, ai_provider="", ai_model=""):
    """Save the local PDF path and upload it to Supabase `resumes` (pdf_path / resume_dir)."""
    conn.execute("UPDATE jobs SET resume_path=? WHERE id=?", (str(path), job_id))
    conn.commit()
    try:
        import resume_cloud
        rid = resume_cloud.push_one(conn, job_id, ai_provider=ai_provider, ai_model=ai_model)
        if rid:
            print(f"    resume saved to Supabase resumes.id={rid}")
    except Exception as e:
        print(f"    ! resume path not uploaded to Supabase yet: {str(e)[:160]}")


# ---------------------------------------------------------------- stage 1: job list

def parse_job_time(value):
    """ISO timestamp / date string → aware UTC datetime, or None."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        try:
            dt = datetime.strptime(text[:10], "%Y-%m-%d")
        except Exception:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def board_newest_posting(conn, org):
    """Newest known posting time for a board: max(published_at, first_seen_at), or None."""
    row = conn.execute(
        "SELECT MAX(COALESCE(NULLIF(published_at, ''), first_seen_at)) AS newest, "
        "MAX(last_seen_at) AS last_listed FROM jobs WHERE org=?",
        (org,)).fetchone()
    if not row:
        return None, None
    return parse_job_time(row["newest"]), parse_job_time(row["last_listed"])


def is_stale_board(conn, org, stale_days, recheck_days):
    """True → skip this board's job search (newest posting older than stale_days).

    Never-scraped boards are not stale. After we skip a stale board, we still
    re-list it every `recheck_days` so a quiet company that starts hiring again
    is not ignored forever.
    """
    newest, last_listed = board_newest_posting(conn, org)
    if not newest:
        return False
    now = datetime.now(timezone.utc)
    if (now - newest).days <= stale_days:
        return False
    if last_listed and (now - last_listed).days >= recheck_days:
        return False  # due for a monthly peek
    return True


def upsert_listing(conn, org, company, job, seen_at):
    """Stage 1: save a job from the board list (no description yet). True if it is new.
    Never touches the details saved by stage 2."""
    values = {
        "id": job["id"], "org": org, "company": company or org,
        "title": job["title"], "department": job["department"], "team": job["team"],
        "dept_path": job["dept_path"], "employment_type": job["employment_type"],
        "location": job["location"],
        "locations_all": json.dumps([l for l in job["locations"] if l]),
        "workplace_type": job["workplace_type"],
        "is_remote": int(job["workplace_type"] in ("Remote", "Hybrid")),
        "job_url": job["job_url"], "apply_url": job["apply_url"],
        "compensation": job["compensation"], "seen": seen_at,
    }
    if conn.execute("SELECT 1 FROM jobs WHERE id=?", (job["id"],)).fetchone():
        conn.execute("""
            UPDATE jobs SET org=:org, company=:company, title=:title, department=:department,
                team=:team, dept_path=:dept_path, employment_type=:employment_type,
                location=:location, locations_all=:locations_all, workplace_type=:workplace_type,
                is_remote=:is_remote, job_url=:job_url, apply_url=:apply_url,
                compensation=COALESCE(NULLIF(:compensation, ''), compensation),
                last_seen_at=:seen, is_active=1
            WHERE id=:id""", values)
        return False
    conn.execute("""
        INSERT INTO jobs (id, org, company, title, department, team, dept_path, employment_type,
            location, locations_all, workplace_type, is_remote, job_url, apply_url, compensation,
            first_seen_at, last_seen_at)
        VALUES (:id, :org, :company, :title, :department, :team, :dept_path, :employment_type,
            :location, :locations_all, :workplace_type, :is_remote, :job_url, :apply_url,
            :compensation, :seen, :seen)""", values)
    return True


def set_relevance(conn, job_id, relevant, method):
    conn.execute("UPDATE jobs SET relevant=?, relevance_by=? WHERE id=?",
                 (int(bool(relevant)), method, job_id))


def jobs_needing_relevance(conn):
    return conn.execute("SELECT id, title, COALESCE(NULLIF(dept_path, ''), department, '') dept "
                        # 'unclear-kept' = kept without the AI (rules only, e.g. after Stop): the AI
                        # decides them on the next collection
                        "FROM jobs WHERE is_active=1 AND (relevant IS NULL "
                        "OR relevance_by='unclear-kept')").fetchall()


# ---------------------------------------------------------------- stage 2: job details

def jobs_needing_details(conn, refresh=False, orgs=None, since=None):
    """Relevant, active jobs whose details (description + questions) are not loaded yet.

    since: optional hint for logging / callers; missing-details jobs are always included
    even if first_seen is older (e.g. reopened from not-relevant via rule-reopen).
    """
    sql, params = "SELECT id, org FROM jobs WHERE is_active=1 AND relevant=1", []
    if not refresh:
        sql += " AND details_at IS NULL"
    # Do not filter by `since` here: any relevant job without details must be fetched
    # or Apply stays blocked on "no details yet" / status=new.
    rows = conn.execute(sql, params).fetchall()
    return [(r["org"], r["id"]) for r in rows if not orgs or r["org"] in orgs]


def save_details(conn, job_id, details, questions):
    """Stage 2: description, pay, questions. The job becomes 'ready' to bid."""
    conn.execute("""
        UPDATE jobs SET description=?, description_html=?,
            compensation=COALESCE(NULLIF(?, ''), compensation), compensation_tiers=?,
            published_at=COALESCE(?, published_at), details_at=?
        WHERE id=?""",
        (details["description"], details["description_html"], details["compensation"],
         details["compensation_tiers"], details["published_at"], now(), job_id))
    save_questions(conn, job_id, questions)


def mark_inactive(conn, org, active_ids):
    """Jobs no longer on the board are kept but flagged inactive."""
    placeholders = ",".join("?" * len(active_ids)) or "''"
    cur = conn.execute(
        f"UPDATE jobs SET is_active=0 WHERE org=? AND is_active=1 AND id NOT IN ({placeholders})",
        [org, *active_ids],
    )
    return cur.rowcount


def save_questions(conn, job_id, questions):
    """Store only REQUIRED questions (the ones with * on the Ashby form).
    Optional fields are never saved or answered. Exception: the resume field is kept
    even when Ashby marks it optional, because we always upload a resume."""
    questions = [q for q in questions
                 if q.get("required") or q.get("path") == "_systemfield_resume"]
    # drop previously stored optional rows for this job (and any required row we no longer see)
    keep_ids = [q["id"] for q in questions]
    if keep_ids:
        conn.execute(
            f"DELETE FROM questions WHERE job_id=? AND id NOT IN ({','.join('?' * len(keep_ids))})",
            [job_id, *keep_ids])
    else:
        conn.execute("DELETE FROM questions WHERE job_id=?", (job_id,))
    for pos, q in enumerate(questions):
        conn.execute("""
            INSERT INTO questions (id, job_id, position, form, form_id, section, path, title,
                type, required, description, options, raw, bank_key)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET position=excluded.position, form=excluded.form,
                form_id=excluded.form_id, section=excluded.section, title=excluded.title,
                type=excluded.type, required=excluded.required,
                description=excluded.description, options=excluded.options,
                raw=excluded.raw, bank_key=excluded.bank_key""",
            (q["id"], job_id, pos, q["form"], q["form_id"], q["section"], q["path"],
             q["title"], q["type"], int(bool(q["required"])), q["description"],
             json.dumps(q["options"]), json.dumps(q["raw"]),
             bank_key(q["title"], q["type"])))
    conn.execute("UPDATE jobs SET status='ready' WHERE id=? AND status='new'", (job_id,))


def set_status(conn, job_id, status, note=None):
    """status: one of STATUSES (ready | needs_you (waiting on a bot check) | applied | failed |
    skipped | 1Interview | 2Interview | 3Interview | Offer | Rejected)"""
    t = now()
    applied_at = t if status in APPLIED_STATUSES else None
    conn.execute("UPDATE jobs SET status=?, note=?, applied_at=COALESCE(applied_at, ?), status_at=? WHERE id=?",
                 (status, note, applied_at, t, job_id))
    conn.commit()
    try:
        import resume_cloud
        resume_cloud.update_status(conn, job_id)
    except Exception:
        pass


def save_answer(conn, question_id, value):
    conn.execute("UPDATE questions SET answer=? WHERE id=?",
                 (json.dumps(value, ensure_ascii=False), question_id))


def applied_recently(conn, org, days):
    row = conn.execute(
        # by applied_at, not status: a job moved on to 1Interview / Offer / ... still counts
        "SELECT COUNT(*) n FROM jobs WHERE org=? AND applied_at IS NOT NULL "
        "AND datetime(applied_at) >= datetime('now', ?)", (org, f"-{days} days")).fetchone()
    return row["n"]


