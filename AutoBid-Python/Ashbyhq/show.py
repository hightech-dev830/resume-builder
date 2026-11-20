"""Look at what is in the DB.

Usage:
    python show.py                 # list jobs (id, status, title, location, pay)
    python show.py <job-id>        # one job: link, description, all questions
    python show.py --status ready  # filter list by status
"""
import argparse
import json
import sys

import db

# Windows consoles default to cp1252; job text often has other characters.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def list_jobs(conn, status):
    sql = "SELECT * FROM jobs WHERE is_active=1 AND relevant=1"
    params = []
    if status:
        sql += " AND status=?"
        params.append(status)
    rows = conn.execute(sql + " ORDER BY department, title", params).fetchall()
    for r in rows:
        print(f"{r['id']}  {r['status']:<8} {r['title'][:45]:<45} "
              f"{(r['location'] or '')[:28]:<28} {r['compensation'] or ''}")
    print(f"\n{len(rows)} jobs")


def show_job(conn, job_id):
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        sys.exit(f"No job with id {job_id}")
    print(f"{job['title']}  [{job['status']}]")
    print(f"{job['department']} / {job['team']} | {job['employment_type']} | {job['workplace_type']}")
    print(f"Locations: {', '.join(json.loads(job['locations_all']))}")
    print(f"Pay: {job['compensation']}")
    for tier in (job["compensation_tiers"] or "").splitlines():
        print(f"     {tier}")
    print(f"Job:   {job['job_url']}\nApply: {job['apply_url']}\n")
    print(job["description"])
    section = None
    print("\n=== Application ===")
    for q in conn.execute("SELECT * FROM questions WHERE job_id=? ORDER BY position", (job_id,)):
        if q["section"] != section:
            section = q["section"]
            if section:
                print(f"\n--- {section} ---")
        req = "*" if q["required"] else " "
        print(f"{req} [{q['type']}] {q['title']}")
        if q["description"]:
            print(f"      note: {q['description']}")
        options = json.loads(q["options"] or "[]")
        if options:
            print(f"      options: {' | '.join(o['label'] for o in options)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id", nargs="?")
    parser.add_argument("--status")
    args = parser.parse_args()
    conn = db.connect()
    if args.job_id:
        show_job(conn, args.job_id)
    else:
        list_jobs(conn, args.status)


if __name__ == "__main__":
    main()
