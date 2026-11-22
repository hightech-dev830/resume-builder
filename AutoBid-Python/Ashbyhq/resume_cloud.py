"""Push AutoBid tailored resumes into Supabase table `resumes` (pdf_path / resume_dir).

Same table the Resume Builder web app and Chrome extension use. Each Ashby job with a
local PDF gets a row:
  source      = 'ashby'
  source_url  = the job page URL  (used to find / update the same row next time)
  pdf_path    = absolute path of the PDF on this computer
  resume_dir  = its folder
  bot_status  = mapped from the local job status

A failure only warns - bidding never depends on this.
"""
from pathlib import Path

import db
import supabase_db as supa

SOURCE = "ashby"

# local jobs.status -> resumes.bot_status (Chrome extension vocabulary)
_BOT_STATUS = {
    "applied": "applied",
    "needs_you": "needs_attention",
    "failed": "failed",
    "skipped": "skipped",
    "ready": "preparing",
    "new": "preparing",
}


def _bot_status(job_status):
    return _BOT_STATUS.get(job_status or "", "")


def _resume_status(job_status):
    if job_status in db.APPLIED_STATUSES:
        return "applied"
    return "generated"


def _row(job, ai_provider="", ai_model=""):
    pdf = Path(job["resume_path"] or "")
    return {
        "company": job["company"] or job["org"] or "",
        "role": job["title"] or "",
        "job_link": job["job_url"] or "",
        "location": job["location"] or "",
        "salary": job["compensation_tiers"] or job["compensation"] or "",
        "jd": job["description"] or "",
        "resume_dir": str(pdf.parent) if pdf.name else "",
        "pdf_path": str(pdf) if pdf.name else "",
        "status": _resume_status(job["status"]),
        "source": SOURCE,
        "source_url": job["job_url"] or "",
        "apply_url": job["apply_url"] or "",
        "bot_status": _bot_status(job["status"]),
        "ai_provider": ai_provider or "",
        "ai_model": ai_model or "",
        "applied_at": job["applied_at"] if job["status"] in db.APPLIED_STATUSES else None,
        "updated_at": db.now(),
    }


def _find_id(job_url, resume_id=None):
    """Existing resumes.id for this Ashby job, or None."""
    if resume_id:
        return int(resume_id)
    if not job_url:
        return None
    rows = supa.request("GET", "resumes", {
        "select": "id", "source": f"eq.{SOURCE}", "source_url": f"eq.{job_url}",
        "order": "id.desc", "limit": "1"}) or []
    return rows[0]["id"] if rows else None


def push_one(conn, job_id, ai_provider="", ai_model=""):
    """Upsert this job's resume into public.resumes. Returns resumes.id or None."""
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job or not job["resume_path"]:
        return None
    body = _row(job, ai_provider, ai_model)
    rid = _find_id(job["job_url"], job["resume_id"] if "resume_id" in job.keys() else None)
    if rid:
        supa.request("PATCH", "resumes", {"id": f"eq.{rid}"}, body,
                     prefer="return=minimal")
    else:
        out = supa.request("POST", "resumes", None, body,
                           prefer="return=representation")
        if not out:
            return None
        rid = out[0]["id"] if isinstance(out, list) else out["id"]
    conn.execute("UPDATE jobs SET resume_id=? WHERE id=?", (rid, job_id))
    conn.commit()
    return rid


def push_all(conn, say=print):
    """Upload every local job that has a resume_path into public.resumes."""
    rows = conn.execute(
        "SELECT id FROM jobs WHERE resume_path IS NOT NULL AND resume_path != ''").fetchall()
    if not rows:
        say("  resumes table: nothing to upload")
        return 0
    ok = fail = 0
    for r in rows:
        try:
            if push_one(conn, r["id"]) is not None:
                ok += 1
            else:
                fail += 1
        except Exception as e:
            fail += 1
            say(f"  ! resume {r['id'][:8]}…: {str(e)[:120]}")
    say(f"  resumes table: {ok} uploaded / updated" + (f", {fail} failed" if fail else ""))
    return ok


def update_status(conn, job_id):
    """After a bid status change: keep resumes.bot_status / applied_at in sync."""
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        return
    rid = job["resume_id"] if "resume_id" in job.keys() else None
    if not rid and job["resume_path"]:
        try:
            push_one(conn, job_id)
            return
        except Exception:
            return
    if not rid:
        return
    body = {
        "bot_status": _bot_status(job["status"]),
        "status": _resume_status(job["status"]),
        "applied_at": job["applied_at"] if job["status"] in db.APPLIED_STATUSES else None,
        "updated_at": db.now(),
    }
    if job["resume_path"]:
        pdf = Path(job["resume_path"])
        body["pdf_path"] = str(pdf)
        body["resume_dir"] = str(pdf.parent)
    try:
        supa.request("PATCH", "resumes", {"id": f"eq.{rid}"}, body, prefer="return=minimal")
    except Exception:
        pass
