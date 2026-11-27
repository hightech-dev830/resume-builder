"""AI fill mode for the Greenhouse bid bot (same idea as the manual-bid extension's ✦ button):

  1. scan the live form; open every dropdown once to read ALL its options
  2. name / email / phone / location come straight from My profile (no AI)
  3. every other empty field goes to the AI in ONE call, with the options of each field,
     your saved answers (this job's resolved answers + the most similar saved answers),
     My profile, the base resume and the job description
  4. choice answers are matched to an offered option; text answers are typed as written

Manual mode (bid.answer_empty_fields) only uses saved answers; this is the "auto" mode.
"""
from __future__ import annotations

import json
import re
import time

import answers as bank_mod
import db
import gh_fill as gf
from prepare_answers import UNKNOWN, match_choice, similar_answers

MAX_OPTIONS = 80            # options listed per field
MAX_EXAMPLES = 70           # similar saved answers per call
CHOICE_KINDS = ("select", "combobox", "radio", "checkbox")


# ------------------------------------------------------------------ 1. read all options

def probe_options(page, fields):
    """Open each dropdown (react-select) that has no options listed yet and read them all.
    Type-ahead boxes (location, school ...) show nothing until you type: they stay [] and
    are answered with text, then picked by gf.fill_field."""
    n = 0
    for f in fields:
        if f["kind"] != "combobox" or f["options"] or f["value"]:
            continue
        loc = gf._locate(page, f)
        if loc is None or not loc.count():
            continue
        try:
            loc.click(timeout=2500)
            opts = gf.read_options(page, 700)
            if not opts:
                page.keyboard.press("ArrowDown")   # react-select opens its menu on ArrowDown
                opts = gf.read_options(page, 700)
            f["options"] = list(dict.fromkeys(opts))[:MAX_OPTIONS * 3]
            n += bool(opts)
        except Exception:
            pass
        finally:
            try:
                page.keyboard.press("Escape")
                loc.evaluate("e => e.blur()")
            except Exception:
                pass
            page.wait_for_timeout(80)
    return n


# ------------------------------------------------------------------ 2. profile fields

def profile_value(f, profile, basics):
    """The My profile value for a contact / profile field, or None."""
    val = basics.get((f["id"] or "").lower()) or None
    if val is None:
        pkey = bank_mod.profile_key({"path": f["id"] or f["name"] or "", "title": f["label"]})
        if pkey and pkey != "resume_file" and not bank_mod._is_empty(profile.get(pkey)):
            val = profile[pkey]
    return val


# ------------------------------------------------------------------ 3. the AI call

def _resume_brief():
    try:
        from resume import settings as resume_settings
        r = resume_settings()["base"]
    except Exception:
        return ""
    return json.dumps(r, ensure_ascii=False)[:9000]


def _known_answers(items):
    """This job's questions that already have an answer (saved bank / per-job / profile)."""
    out = []
    for it in items:
        v = it.get("value")
        if bank_mod._is_empty(v) or v == bank_mod.TAILORED_PLACEHOLDER:
            continue
        q = it["q"]
        title = (q["title"] if not isinstance(q, dict) else q.get("title")) or ""
        if title and not str(v).lower().endswith((".pdf", ".docx")):
            out.append({"question": title, "answer": v})
    return out


def _field_line(n, f):
    kind = {"select": "dropdown", "combobox": "dropdown", "radio": "single choice",
            "checkbox": "single checkbox" if f.get("single") else "multi choice",
            "textarea": "long text", "text": "short text"}.get(f["kind"], f["kind"])
    if f["kind"] == "combobox" and not f["options"]:
        kind = "type-ahead (type the answer; the closest suggestion is picked)"
    line = f'{n}. key="{n}" [{kind}{", REQUIRED" if f["required"] else ""}] {f["label"]}'
    opts = [o for o in f["options"] if o and not re.match(r"^(select|choose|please select|--)", o, re.I)]
    if opts and not f.get("single"):
        line += "\n   options: " + json.dumps(opts[:MAX_OPTIONS], ensure_ascii=False)
    return line


def _prompt(fields, profile, job, known, examples, resume):
    lines = [_field_line(n, f) for n, f in enumerate(fields)]
    keys = [str(n) for n in range(len(fields))]
    example = "{" + ", ".join(f'"{k}": "..."' for k in keys) + "}"
    ex = "\n".join(f"- Q: {e['question'].strip()[:250]}\n  A: {_fmt(e['answer'])}"
                   for e in examples) or "(none)"
    kn = "\n".join(f"- Q: {e['question'].strip()[:250]}\n  A: {_fmt(e['answer'])}"
                   for e in known) or "(none)"
    prof = {k: v for k, v in (profile or {}).items() if str(v or "").strip()}
    return (
        "You fill in a Greenhouse job application form for the candidate. Below are the form's "
        "empty fields with their options. Answer each one.\n\n"
        "Rules:\n"
        "- The candidate's SAVED ANSWERS are the source of truth: when a saved question means the "
        "same as a field (even worded differently), give the same answer. Profile facts are "
        "exact.\n"
        "- Fields with options: the answer MUST be one of the listed options, copied exactly. "
        "Map the candidate's answer to the option that means the same. Multi choice: join "
        "several options with \" | \".\n"
        "- Single checkbox (consent / agree / certify): \"Yes\" to tick it, \"\" to leave it.\n"
        "- Work authorization, visa sponsorship, citizenship, criminal history, disability, "
        "veteran status, gender, race and other personal facts: ONLY from the saved answers / "
        "profile. If they do not tell, answer " + UNKNOWN + ". Demographic questions with a "
        "\"decline to answer\" option may use it when nothing is saved.\n"
        "- Free-text questions (why this company, about a project, motivation): 2-5 plain, "
        "specific first-person sentences from the resume and this job. No hype, no em dashes.\n"
        "- Salary: the profile / saved answer; else the job's posted range. Years with a skill: "
        "realistic from the resume dates.\n"
        "- Optional fields you have nothing truthful for: \"\". Never invent employers, degrees, "
        "licenses, URLs or contact details.\n\n"
        f"<candidate_profile>\n{json.dumps(prof, indent=1, ensure_ascii=False)}\n</candidate_profile>\n\n"
        f"<resume>\n{resume}\n</resume>\n\n"
        f"<saved_answers_for_this_job>\n{kn}\n</saved_answers_for_this_job>\n\n"
        f"<saved_answers_similar_questions>\n{ex}\n</saved_answers_similar_questions>\n\n"
        f"<job>\nCompany: {job.get('company')}\nRole: {job.get('role')}\n"
        f"Location: {job.get('location') or ''}\n<job_description>\n"
        f"{(job.get('jd') or '')[:6000]}\n</job_description>\n</job>\n\n"
        "<fields>\n" + "\n".join(lines) + "\n</fields>\n\n"
        "Reply with ONLY one JSON object inside a ```json code block, one string per key:\n"
        f"{example}\nNo text before or after the JSON."
    )


def _fmt(v):
    if isinstance(v, list):
        v = " | ".join(map(str, v))
    v = str(v).strip().replace("\n", " ")
    return v if len(v) <= 300 else v[:300] + "…"


def _check(f, raw):
    """(value for gf.fill_field | None, problem | None) for one AI answer."""
    text = str(raw if raw is not None else "").strip().strip('"').strip()
    if not text:
        return None, ("no answer" if f["required"] else None)
    if text.upper().startswith(UNKNOWN):
        return None, "your saved answers / profile do not tell"
    if f["kind"] == "checkbox" and f.get("single"):
        return ("Yes" if gf._bool_word(text) != "no" else None), None
    opts = [o for o in f["options"] if o]
    if f["kind"] in CHOICE_KINDS and opts:
        parts = re.split(r"\s*\|\s*", text) if f["kind"] == "checkbox" else [text]
        picked = list(dict.fromkeys(c for c in (match_choice(p, opts) for p in parts) if c))
        if not picked:
            return None, f"“{text[:50]}” is not one of the options"
        return (picked if f["kind"] == "checkbox" else picked[0]), None
    return text, None


def ai_answers(ai, fields, bank, items, job_row):
    """One AI call for all `fields`. Returns (plan [(f, value, multi)], missing [label])."""
    profile = bank["profile"]
    answered = [{"question": q.get("question") or "", "answer": q["answer"]}
                for q in bank["questions"] if not bank_mod._is_empty(q.get("answer"))]
    examples, seen = [], set()
    for f in fields:
        for e in similar_answers(f["label"], answered, k=5):
            if id(e) not in seen and len(examples) < MAX_EXAMPLES:
                seen.add(id(e))
                examples.append(e)
    job = db.job_for_ai(job_row)
    prompt = _prompt(fields, profile, job, _known_answers(items), examples, _resume_brief())
    print(f"    asking {ai.provider} about {len(fields)} field(s) "
          f"({len(examples)} similar saved answers as reference) ...")
    started = time.time()
    data = ai.ask_json(prompt, required_keys=[str(n) for n in range(len(fields))])
    print(f"    AI answered in {time.time() - started:.0f}s")
    plan, missing = [], []
    for n, f in enumerate(fields):
        val, problem = _check(f, data.get(str(n)))
        if val is not None:
            plan.append((f, val, f["kind"] == "checkbox" and not f.get("single")))
        elif problem and f["required"]:
            missing.append(f"{f['label'][:50]} ({problem})")
    return plan, missing


# ------------------------------------------------------------------ 4. the fill

def answer_empty_fields_ai(page, conn, ai, job, items, bank, basics_fn, fill_plan):
    """AI mode counterpart of bid.answer_empty_fields. Returns (fields, problem)."""
    if ai is None:
        return gf.scan_form(page), "AI fill mode, but no AI is set up (AI tab)"
    fields = gf.scan_form(page)
    profile = bank["profile"]
    basics = basics_fn(profile)
    plan, ask = [], []
    for f in fields:
        if f["kind"] == "file" or f["value"] or not f["label"]:
            continue
        val = profile_value(f, profile, basics)
        if val is not None:
            plan.append((f, val, False))
        else:
            ask.append(f)
    if any(f["kind"] == "combobox" and not f["options"] for f in ask):
        n = probe_options(page, ask)
        print(f"    read the options of {n} dropdown(s)")
    missing = []
    if ask:
        try:
            ai_plan, missing = ai_answers(ai, ask, bank, items, job)
        except Exception as e:
            return fields, f"AI fill failed: {str(e).splitlines()[0][:200]}"
        plan += ai_plan
    print(f"    {len(plan)} empty field(s) to answer ({len(ask)} asked to the AI)")
    failed = fill_plan(page, plan)
    if failed:
        print(f"    ! could not fill: {'; '.join(x[:40] for x in failed[:5])}")
    if missing:
        return fields, "AI could not answer: " + "; ".join(missing[:4])
    return fields, None
