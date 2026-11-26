"""Command-line runner for "Prepare answers", shared by the bots (each bot's prepare_answers.py
calls run() with its own answers + db modules).

    python prepare_answers.py --jobs-file ids.json [--keys-file keys.json] --out result.json

  --jobs-file  jobs to prepare (their unanswered required questions)
  --keys-file  only these answer-bank questions (from the Answers tab); without jobs they get
               one shared answer each
  --out        result for the app: {"shared": {key: answer}, "per_job": n, "skipped": [...]}

Shared answers are NOT saved here: the app shows them as drafts to review. Per-job answers are
saved in the local job database (questions.prepared) and used by Apply instead of writing them
at submit time; edit them in the Answers tab.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import ai_settings
from prepare_answers import prepare

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _qval(q, key, default=None):
    try:
        v = q[key]
    except (KeyError, IndexError):
        return default
    return default if v is None else v


def _choices_of(q, entry):
    if entry and entry.get("choices"):
        return [c if isinstance(c, str) else (c.get("label") or "") for c in entry["choices"]]
    try:
        opts = json.loads(_qval(q, "options", "[]") or "[]")
    except Exception:
        opts = []
    return [o if isinstance(o, str) else (o.get("label") or "") for o in opts if o]


def collect_targets(conn, db, bank_mod, bank, job_ids, keys):
    """(shared_qs, job_qs, skipped) for the selected jobs / questions."""
    by_key = {q["key"]: q for q in bank["questions"]}
    shared, job_qs, skipped = {}, [], []
    for jid in job_ids:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if not job:
            continue
        label = f"{job['company'] or job['org']} - {job['title']}"
        per_job = []
        for item in bank_mod.resolve_job(conn, jid, bank):
            q = item["q"]
            key = _qval(q, "bank_key") or ""
            if not item["missing"] or (keys is not None and key not in keys):
                continue
            title = _qval(q, "title", "")
            if not str(item.get("source") or "").startswith("bank"):
                skipped.append({"job": label, "question": title,
                                "why": "comes from My profile - fill it there"})
                continue
            entry = by_key.get(key)
            if bank_mod.ai_writable(item):
                per_job.append({"qid": _qval(q, "id"), "key": key, "question": title,
                                "type": _qval(q, "type", "String"),
                                "choices": [], "note": _qval(q, "description", "")})
            elif key and key not in shared:
                shared[key] = {"key": key, "question": (entry or {}).get("question") or title,
                               "type": (entry or {}).get("type") or _qval(q, "type", "String"),
                               "choices": _choices_of(q, entry),
                               "note": (entry or {}).get("note") or _qval(q, "description", "")}
        if per_job:
            job_qs.append({"job": db.job_for_ai(job), "questions": per_job})
    if not job_ids and keys:
        # questions picked in the Answers tab without jobs: one shared answer each
        for key in keys:
            e = by_key.get(key)
            if e and bank_mod._is_empty(e.get("answer")) and key not in shared:
                shared[key] = {"key": key, "question": e.get("question") or "",
                               "type": e.get("type") or "String",
                               "choices": [c if isinstance(c, str) else c.get("label", "")
                                           for c in (e.get("choices") or [])],
                               "note": e.get("note") or ""}
    return list(shared.values()), job_qs, skipped


def run(bank_mod, db, open_ctx, stop_requested, platform):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--jobs-file")
    p.add_argument("--keys-file")
    p.add_argument("--out", required=True)
    args = p.parse_args()
    job_ids = json.loads(Path(args.jobs_file).read_text(encoding="utf-8")) if args.jobs_file else []
    keys = (set(json.loads(Path(args.keys_file).read_text(encoding="utf-8")))
            if args.keys_file else None)

    conn = db.connect()
    bank = bank_mod.load_bank()
    answered = [{"question": q.get("question") or "", "type": q.get("type") or "",
                 "answer": q["answer"]}
                for q in bank["questions"] if not bank_mod._is_empty(q.get("answer"))]
    shared_qs, job_qs, skipped = collect_targets(conn, db, bank_mod, bank, job_ids, keys)
    n_job = sum(len(j["questions"]) for j in job_qs)
    print(f"Prepare answers: {len(shared_qs)} shared question(s), {n_job} per-job answer(s) "
          f"in {len(job_qs)} job(s); {len(answered)} saved answers as reference")
    result = {"shared": {}, "per_job": 0, "jobs": len(job_qs), "skipped": skipped}
    if not shared_qs and not job_qs:
        print("Nothing to prepare: every selected question already has an answer.")
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return

    provider = ai_settings.get("resume")[0]
    problem = ai_settings.api_key_problem(provider)
    if problem:
        sys.exit(f"STOPPED: answers are written by '{provider}' (AI tab), but {problem}.")
    resume = {}
    if job_qs:
        try:
            from resume import settings as resume_settings
            resume = resume_settings()["base"]
        except Exception as e:
            print(f"  ! base resume not loaded ({str(e)[:100]}); answers use the profile only")

    started = time.time()
    from web_ai import PROVIDERS
    if provider in PROVIDERS:          # web chat AI needs the bot's Chrome
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            ctx = open_ctx(pw)
            try:
                ai = ai_settings.make_ai("resume", ctx)
                out = prepare(ai, bank["profile"], answered, shared_qs, job_qs, resume=resume,
                              stop=stop_requested)
                ai.finish_web_chat()
            finally:
                ctx.close()
    else:
        ai = ai_settings.make_ai("resume")
        print(f"Answers are written by {ai.label}")
        out = prepare(ai, bank["profile"], answered, shared_qs, job_qs, resume=resume,
                      stop=stop_requested)

    # per-job answers: saved locally for Apply (questions.prepared)
    n = 0
    for jid, answers in out["per_job"].items():
        for qid, ans in answers.items():
            conn.execute("UPDATE questions SET prepared=? WHERE id=?",
                         (json.dumps(ans, ensure_ascii=False), qid))
            n += 1
    conn.commit()
    result.update(shared=out["shared"], per_job=n, skipped=skipped + out["skipped"])
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nPrepared {len(out['shared'])} shared answer(s) (drafts for you to review) and "
          f"{n} per-job answer(s) in {time.time() - started:.0f}s; "
          f"{len(result['skipped'])} left for you.")
    for s in result["skipped"][:30]:
        where = f"[{s['job']}] " if s["job"] else ""
        print(f"  - {where}{s['question'][:70]}: {s['why']}")
