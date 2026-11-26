"""Prepare answers ahead of Apply, from the answers you already saved.

Two kinds of answers (each bot's prepare_answers.py collects them and stores the result):

  shared   one answer for every job (yes/no, choices, short facts). The AI sees your saved
           answers to the MOST SIMILAR questions + your profile and must reuse them; when they
           do not tell, it says UNKNOWN (never guesses work authorization, sponsorship,
           demographics, ...). Choice answers are mapped onto the form's own choices.
  per job  "AI writes per job" text / essays. One call per job with that job's description,
           your base resume and your related saved answers (facts + style).

prepare() returns {"shared": {key: answer}, "per_job": {job_id: {qid: answer}},
                   "skipped": [{"job", "question", "why"}]}. Nothing is written here.
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher

SHARED_BATCH = 20          # questions per AI call
SIMILAR_PER_Q = 6          # saved answers shown per question
MAX_EXAMPLES = 60          # saved answers per AI call
UNKNOWN = "UNKNOWN"
CHOICE_TYPES = {"ValueSelect", "MultiValueSelect", "Boolean"}

_STOP = set("a an the of to for in on and or is are you your do does did have has with "
            "this that at as be by it we our us if any please".split())


def _words(s):
    return [w for w in re.findall(r"[a-z0-9]+", str(s or "").lower()) if w not in _STOP]


def similarity(a, b) -> float:
    wa, wb = set(_words(a)), set(_words(b))
    jac = len(wa & wb) / len(wa | wb) if wa and wb else 0.0
    seq = SequenceMatcher(None, " ".join(sorted(wa)), " ".join(sorted(wb))).ratio()
    return max(jac, 0.8 * seq)


def similar_answers(question, answered, k=SIMILAR_PER_Q):
    """The k saved answers whose question is closest to `question`."""
    scored = sorted(((similarity(question, a["question"]), n) for n, a in enumerate(answered)),
                    reverse=True)
    return [answered[n] for s, n in scored[:k] if s >= 0.2]


def _fmt_answer(v):
    if isinstance(v, list):
        v = " | ".join(map(str, v))
    v = str(v).strip()
    return v if len(v) <= 400 else v[:400] + "…"


def _examples_block(examples):
    lines = [f"- Q: {e['question'].strip()[:300]}\n  A: {_fmt_answer(e['answer'])}"
             for e in examples]
    return "\n".join(lines) or "(none)"


def _profile_block(profile):
    keep = {k: v for k, v in (profile or {}).items() if str(v or "").strip()}
    return json.dumps(keep, indent=1, ensure_ascii=False)


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def match_choice(answer, choices):
    """The choice closest to answer, or None when nothing is close enough."""
    a = _norm(answer)
    if not a or not choices:
        return None
    for c in choices:
        if _norm(c) == a:
            return c
    yn = "yes" if a in ("yes", "true", "y") else ("no" if a in ("no", "false", "n") else None)
    if yn:
        for c in choices:
            if _norm(c).split()[:1] == [yn]:
                return c
    best, best_s = None, 0.0
    for c in choices:
        s = SequenceMatcher(None, a, _norm(c)).ratio()
        if f" {a} " in f" {_norm(c)} " or f" {_norm(c)} " in f" {a} ":
            s = max(s, 0.8)
        if s > best_s:
            best, best_s = c, s
    return best if best_s >= 0.6 else None


def _clean(q, raw):
    """Validate one AI answer for question q. Returns (answer | None, why)."""
    text = str(raw or "").strip().strip('"').strip()
    if not text or text.upper().startswith(UNKNOWN):
        return None, "your saved answers do not tell"
    qtype = q.get("type") or "String"
    choices = q.get("choices") or []
    if qtype == "Boolean" and not choices:
        choices = ["Yes", "No"]
    if qtype == "MultiValueSelect":
        picked = [match_choice(p, choices) for p in re.split(r"\s*\|\s*", text) if p.strip()]
        picked = list(dict.fromkeys(p for p in picked if p))
        return (picked, "") if picked else (None, f"“{text[:60]}” is not one of the choices")
    if qtype in CHOICE_TYPES or choices:
        c = match_choice(text, choices)
        return (c, "") if c else (None, f"“{text[:60]}” is not one of the choices")
    return text, ""


def _shared_prompt(profile, batch, examples):
    lines = []
    for n, q in enumerate(batch):
        qtype = q.get("type") or "String"
        choices = q.get("choices") or (["Yes", "No"] if qtype == "Boolean" else [])
        how = ("one of the choices, copied exactly" if qtype != "MultiValueSelect"
               else "one or more of the choices, copied exactly, separated by ' | '")
        line = f'{n}. key="{n}" [{qtype}] {q["question"].strip()}'
        if q.get("note"):
            line += f"\n   note under the question: {q['note'][:300]}"
        if choices:
            line += "\n   choices: " + " / ".join(map(str, choices[:40])) + f"\n   answer: {how}"
        else:
            line += "\n   answer: a short answer (a phrase, a number or 1-2 sentences)"
        lines.append(line)
    keys = [str(n) for n in range(len(batch))]
    example = "{" + ", ".join(f'"{k}": "..."' for k in keys) + "}"
    return (
        "You prepare answers to job-application questions for ONE candidate, from the "
        "candidate's OWN previous answers and profile. Those are the source of truth: when a "
        "question means the same thing as a previous one (different wording), give the same "
        "answer. Stay consistent with all previous answers.\n"
        f"If the previous answers and the profile do not tell, answer exactly {UNKNOWN}. Never "
        "guess facts about the candidate (work authorization, visa sponsorship, location or "
        "relocation, salary, demographics, gender, race, veteran or disability status, "
        "criminal history, notice period, ...).\n\n"
        f"<candidate_profile>\n{_profile_block(profile)}\n</candidate_profile>\n\n"
        f"<previous_answers>\n{_examples_block(examples)}\n</previous_answers>\n\n"
        "<questions>\n" + "\n".join(lines) + "\n</questions>\n\n"
        "Reply with ONLY one JSON object inside a ```json code block, one string per key:\n"
        f"{example}\nNo text before or after the JSON."
    )


def _job_prompt(profile, resume, job, qs, examples):
    lines = []
    for n, q in enumerate(qs):
        tip = ("1-2 short sentences or a phrase" if q.get("type") == "String"
               else "a short answer, about 80-180 words, first person")
        if re.search(r"salary|compensation|pay\b", q["question"], re.I):
            tip = "the salary from the profile / previous answers, else UNKNOWN"
        line = f'{n}. key="{n}" [{q.get("type") or "String"}] {q["question"].strip()}'
        if q.get("note"):
            line += f"\n   note under the question: {q['note'][:300]}"
        lines.append(line + f"\n   write: {tip}")
    keys = [str(n) for n in range(len(qs))]
    example = "{" + ", ".join(f'"{k}": "..."' for k in keys) + "}"
    jd = (job.get("jd") or "")[:7000]
    return (
        "You write a candidate's answers to the application questions of THIS job. Use the "
        "job description to make each answer specific to the company and role, the resume for "
        "experience, and the candidate's previous answers for facts and tone. Truthful: do not "
        "invent employers, degrees, dates, numbers, URLs or contact details. "
        f"Answer {UNKNOWN} only for a personal fact you cannot know.\n\n"
        f"<candidate_profile>\n{_profile_block(profile)}\n</candidate_profile>\n\n"
        f"<resume>\n{json.dumps(resume, ensure_ascii=False)[:12000]}\n</resume>\n\n"
        f"<previous_answers>\n{_examples_block(examples)}\n</previous_answers>\n\n"
        f"<job>\nCompany: {job.get('company')}\nRole: {job.get('role')}\n"
        f"Location: {job.get('location') or ''}\n<job_description>\n{jd}\n</job_description>\n</job>\n\n"
        "<questions>\n" + "\n".join(lines) + "\n</questions>\n\n"
        "Reply with ONLY one JSON object inside a ```json code block, one string per key:\n"
        f"{example}\nNo text before or after the JSON."
    )


def prepare(ai, profile, answered, shared_qs, job_qs, resume=None, log=print,
            stop=lambda: False):
    """shared_qs: [{key, question, type, choices, note}]
    job_qs:    [{"job": {id, company, role, location, jd}, "questions": [{qid, key, question,
                 type, choices, note}]}]
    answered:  [{question, type, answer}] - your saved answers (the source of truth)"""
    out = {"shared": {}, "per_job": {}, "skipped": []}

    for start in range(0, len(shared_qs), SHARED_BATCH):
        if stop():
            break
        batch = shared_qs[start:start + SHARED_BATCH]
        examples, seen = [], set()
        for q in batch:
            for e in similar_answers(q["question"], answered):
                if id(e) not in seen and len(examples) < MAX_EXAMPLES:
                    seen.add(id(e))
                    examples.append(e)
        log(f"Shared answers {start + 1}-{start + len(batch)} of {len(shared_qs)} "
            f"({len(examples)} similar saved answers as reference) ...")
        try:
            data = ai.ask_json(_shared_prompt(profile, batch, examples),
                               required_keys=[str(n) for n in range(len(batch))])
        except Exception as e:
            for q in batch:
                out["skipped"].append({"job": "", "question": q["question"],
                                       "why": f"AI failed: {str(e).splitlines()[0][:120]}"})
            continue
        for n, q in enumerate(batch):
            ans, why = _clean(q, data.get(str(n)))
            if ans is None:
                out["skipped"].append({"job": "", "question": q["question"], "why": why})
            else:
                out["shared"][q["key"]] = ans
                log(f"  · {q['question'][:60]}  ->  {_fmt_answer(ans)[:60]}")

    for n, entry in enumerate(job_qs, 1):
        if stop():
            break
        job, qs = entry["job"], entry["questions"]
        label = f"{job.get('company')} - {job.get('role')}"
        examples, seen = [], set()
        for q in qs:
            for e in similar_answers(q["question"], answered, k=3):
                if id(e) not in seen and len(examples) < 20:
                    seen.add(id(e))
                    examples.append(e)
        log(f"Job {n}/{len(job_qs)}: {label} - {len(qs)} answer(s) ...")
        try:
            data = ai.ask_json(_job_prompt(profile, resume or {}, job, qs, examples),
                               required_keys=[str(i) for i in range(len(qs))])
        except Exception as e:
            for q in qs:
                out["skipped"].append({"job": label, "question": q["question"],
                                       "why": f"AI failed: {str(e).splitlines()[0][:120]}"})
            continue
        for i, q in enumerate(qs):
            ans, why = _clean(q, data.get(str(i)))
            if ans is None:
                out["skipped"].append({"job": label, "question": q["question"], "why": why})
            else:
                out["per_job"].setdefault(job["id"], {})[q["qid"]] = ans
                log(f"  · {q['question'][:60]}  ->  {_fmt_answer(ans)[:60]}")
    return out
