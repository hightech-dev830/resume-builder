"""Sync the local job DB (data/ashbyhq.db) with Supabase table autobid_jobs, both ways.

    python sync_db.py          # both ways: pull jobs changed elsewhere, then push jobs changed here
    python sync_db.py --pull   # only download (app: "Sync from DB")
    python sync_db.py --push   # only upload   (app: "Sync to DB")

Every job is one row in Supabase: the job (title, company, pay, description, ...), its
application questions and your status (applied, 1Interview, 2Interview, 3Interview, Offer, ...).

How changes are matched: each row carries a fingerprint (hash) of its content. Locally,
jobs.sync_hash is the fingerprint at the last sync, so
  - remote hash != our sync_hash  -> the job was changed on another computer -> pull it
  - our content hash != sync_hash -> the job was changed here                -> push it
  - both changed                  -> keep this computer's job data, and the NEWEST status
                                     (status_at) from either side
Not synced into autobid_jobs: description_html, raw Ashby field JSON, last_seen_at.
Resume PDF paths go to the shared `resumes` table (pdf_path / resume_dir, source='ashby').

Needs supabase/migrations/005_autobid_jobs.sql run once in the Supabase SQL editor.
"""
import hashlib
import json
import sys
import time

import db
import config  # noqa: F401  (puts AutoBid-Python/common on sys.path)
import supabase_db as supa

PLATFORM = "ashby"
TABLE = "autobid_jobs"

# local jobs column -> synced as is (locations_all is sent as JSON, questions are added)
JOB_FIELDS = ["org", "company", "title", "status", "status_at", "note", "applied_at", "published_at",
              "department", "team", "dept_path", "employment_type", "location", "workplace_type",
              "is_remote", "job_url", "apply_url", "compensation", "compensation_tiers", "description",
              "first_seen_at", "is_active", "relevant", "relevance_by", "details_at"]
STATUS_FIELDS = ["status", "status_at", "note", "applied_at"]
Q_FIELDS = ["id", "position", "form", "form_id", "section", "path", "title", "type", "required",
            "description", "options", "bank_key", "answer"]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- local row <-> Supabase row

def _questions(conn, job_id):
    out = []
    for q in conn.execute(f"SELECT {', '.join(Q_FIELDS)} FROM questions WHERE job_id=? ORDER BY position",
                          (job_id,)):
        d = dict(q)
        d["options"] = json.loads(d["options"] or "[]")
        out.append(d)
    return out


def payload(conn, job):
    """The Supabase row for one local job (without hash / updated_at)."""
    row = {"platform": PLATFORM, "id": job["id"], **{f: job[f] for f in JOB_FIELDS}}
    row["org"] = row["org"] or ""
    row["company"] = row["company"] or ""
    row["title"] = row["title"] or ""
    row["status"] = row["status"] or "new"
    row["is_active"] = 1 if row["is_active"] is None else row["is_active"]
    row["locations_all"] = json.loads(job["locations_all"] or "[]")
    row["questions"] = _questions(conn, job["id"])
    return row


def fingerprint(row):
    body = {k: v for k, v in row.items() if k not in ("hash", "updated_at", "platform")}
    return hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                   separators=(",", ":")).encode("utf-8")).hexdigest()


def write_local(conn, r, content=True):
    """Store a Supabase row locally. content=False: only the status fields."""
    fields = (JOB_FIELDS if content else STATUS_FIELDS)
    values = {f: r.get(f) for f in fields}
    if content:
        values["locations_all"] = json.dumps(r.get("locations_all") or [])
    if conn.execute("SELECT 1 FROM jobs WHERE id=?", (r["id"],)).fetchone():
        conn.execute(f"UPDATE jobs SET {', '.join(f'{k}=?' for k in values)} WHERE id=?",
                     [*values.values(), r["id"]])
    else:
        values["last_seen_at"] = values.get("first_seen_at")
        conn.execute(f"INSERT INTO jobs (id, {', '.join(values)}) VALUES (?{', ?' * len(values)})",
                     [r["id"], *values.values()])
    if content:
        conn.execute("DELETE FROM questions WHERE job_id=?", (r["id"],))
        for q in r.get("questions") or []:
            conn.execute(f"INSERT OR REPLACE INTO questions (job_id, {', '.join(Q_FIELDS)}) "
                         f"VALUES (?{', ?' * len(Q_FIELDS)})",
                         [r["id"], *[json.dumps(q.get("options") or []) if f == "options" else q.get(f)
                                     for f in Q_FIELDS]])


def _set_hash(conn, job_id):
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    h = fingerprint(payload(conn, job))
    conn.execute("UPDATE jobs SET sync_hash=? WHERE id=?", (h, job_id))
    return h


# ---------------------------------------------------------------- Supabase I/O

def _fetch_full(ids):
    rows = []
    for i in range(0, len(ids), 40):
        rows += supa.request("GET", TABLE, {"select": "*", "platform": f"eq.{PLATFORM}",
                                            "id": supa.in_list(ids[i:i + 40])}) or []
    return rows


def _push(rows, label=None):
    """Upsert rows in chunks of ~2 MB (descriptions are large)."""
    chunk, size, sent, t0 = [], 0, 0, time.time()
    for r in rows:
        chunk.append(r)
        size += len(r.get("description") or "") + 2000
        if size > 2_000_000 or len(chunk) >= 300:
            supa.upsert(TABLE, chunk, "platform,id", chunk=len(chunk))
            sent += len(chunk)
            chunk, size = [], 0
            if label:
                print(f"  {label}: {sent}/{len(rows)} | {time.time() - t0:.0f}s")
    if chunk:
        supa.upsert(TABLE, chunk, "platform,id", chunk=len(chunk))


def push_jobs(conn, ids):
    """Push these jobs now (e.g. right after you change a status in the app)."""
    rows = []
    for jid in ids:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if job:
            p = payload(conn, job)
            p["hash"] = fingerprint(p)
            p["updated_at"] = db.now()
            rows.append(p)
    if rows:
        _push(rows)
        for r in rows:
            conn.execute("UPDATE jobs SET sync_hash=? WHERE id=?", (r["hash"], r["id"]))
        conn.commit()
    return len(rows)


# ---------------------------------------------------------------- the sync

def sync(conn=None, quiet=False, pull=True, push=True):
    """pull: Supabase -> this computer ("Sync from DB"). push: this computer -> Supabase
    ("Sync to DB"). Both (default, used after collections / bid runs): pull, then push.
    Returns (pulled_new, pulled_updated, pushed)."""
    conn = conn or db.connect()
    say = (lambda *a: None) if quiet else print
    t0 = time.time()
    what = "both ways" if pull and push else ("from Supabase" if pull else "to Supabase")
    say(f"Sync {what}: comparing ...")
    remote = {r["id"]: r for r in supa.select_all(TABLE, {
        "select": "id,hash,status,status_at,note,applied_at", "platform": f"eq.{PLATFORM}",
        "order": "id"})}
    local = {r["id"]: r for r in conn.execute("SELECT * FROM jobs")}
    new = upd = 0

    # ---- pull: rows another computer changed since our last sync
    changed_remote = [jid for jid, r in remote.items()
                      if jid not in local or r["hash"] != local[jid]["sync_hash"]]
    if pull:
        for i in range(0, len(changed_remote), 200):
            for r in _fetch_full(changed_remote[i:i + 200]):
                jid = r["id"]
                loc = local.get(jid)
                local_changed = False
                if loc is None:
                    write_local(conn, r)
                    new += 1
                else:
                    h_local = fingerprint(payload(conn, loc))
                    if h_local == r["hash"]:                   # already identical (e.g. never synced)
                        conn.execute("UPDATE jobs SET sync_hash=? WHERE id=?", (r["hash"], jid))
                        continue
                    local_changed = loc["sync_hash"] is None or h_local != loc["sync_hash"]
                    if not local_changed:
                        write_local(conn, r)                   # take the other computer's version
                    elif (r.get("status_at") or "") > (loc["status_at"] or ""):
                        write_local(conn, r, content=False)    # both changed: newest status wins
                    upd += 1
                if not local_changed:
                    conn.execute("UPDATE jobs SET sync_hash=? WHERE id=?", (r["hash"], jid))
            conn.commit()
            if len(changed_remote) > 200:
                say(f"  pulled {min(i + 200, len(changed_remote))}/{len(changed_remote)}")
    elif changed_remote:
        say(f"  ({len(changed_remote)} jobs changed in Supabase are not downloaded - "
            f"use 'Sync from DB' for those)")

    # ---- push: rows changed here since our last sync
    rows = []
    if push:
        for job in conn.execute("SELECT * FROM jobs"):
            p = payload(conn, job)
            h = fingerprint(p)
            if h == job["sync_hash"] and job["id"] in remote:
                continue                                       # unchanged here
            r = remote.get(job["id"])
            if (not pull and r and r["hash"] != job["sync_hash"]
                    and (r.get("status_at") or "") > (job["status_at"] or "")):
                # upload only, but the job got a NEWER status on another computer: keep that
                write_local(conn, r, content=False)
                job = conn.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
                p = payload(conn, job)
                h = fingerprint(p)
            p["hash"], p["updated_at"] = h, db.now()
            rows.append(p)
        if rows:
            say(f"  pushing {len(rows)} jobs ...")
            _push(rows, label="pushed" if len(rows) > 300 else None)
            for r in rows:
                conn.execute("UPDATE jobs SET sync_hash=? WHERE id=?", (r["hash"], r["id"]))
            conn.commit()
    # ---- also push tailored resume paths into public.resumes (pdf_path / resume_dir)
    if push:
        try:
            import resume_cloud
            say("  uploading resume paths to resumes table ...")
            resume_cloud.push_all(conn, say=say)
        except Exception as e:
            say(f"  ! resumes table upload skipped: {str(e)[:160]}")

    parts = []
    if pull:
        parts.append(f"{new} new + {upd} updated from Supabase")
    if push:
        parts.append(f"{len(rows)} sent to Supabase")
    say(f"Sync done: {', '.join(parts)} ({len(local) + new} jobs here); {time.time() - t0:.0f}s")
    return new, upd, len(rows)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Sync jobs with Supabase table autobid_jobs.")
    parser.add_argument("--pull", action="store_true", help="only download: Supabase -> this computer")
    parser.add_argument("--push", action="store_true", help="only upload: this computer -> Supabase")
    args = parser.parse_args()
    both = args.pull == args.push            # neither or both flags: both directions
    try:
        sync(pull=both or args.pull, push=both or args.push)
    except supa.SupabaseError as e:
        print(f"! {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
