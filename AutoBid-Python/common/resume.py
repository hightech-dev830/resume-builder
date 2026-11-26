"""Tailored resume per job: same result as the extension, but the AI runs in a web browser.

Same as the extension (via resume_bridge.js -> Resume Builder code):
  - your resume prompt from Resume Builder Settings (Supabase), plus the app's output rules
  - your base profile (Base Profile tab), the JSON schema and the merge rules
  - the HTML template (Settings default) and PDF renderer
  - the folder:  <Resume folder>/<platform>/<date>/<Company>/<Role>/KevinBurgess_Resume.pdf
                 + resume.json + job.txt
                 e.g. .../Resume/ashby/2026-11-22/Titan_AI/Principal_Forward_Deployed_Engineer/KevinBurgess_Resume.pdf
Different: the prompt goes to ChatGPT / Claude / DeepSeek in the browser (or a local AI CLI)
instead of a paid API.
"""
import json
import re
import subprocess
import tempfile
import unicodedata
from datetime import date
from pathlib import Path

import ai_settings
from web_ai import API

BRIDGE = Path(__file__).resolve().parent / "resume_bridge.js"
FALLBACK_DIR = Path.home() / "Documents" / "Resume_bot"
REQUIRED = ["label", "summary", "highlights", "skills", "work", "keywords_matched"]

_settings_cache = None


def _node(*args):
    res = subprocess.run(["node", str(BRIDGE), *args], capture_output=True, text=True,
                         encoding="utf-8", timeout=300)
    if res.returncode != 0:
        raise RuntimeError(f"resume_bridge.js {args[0]} failed: {res.stderr.strip()[-500:]}")
    return json.loads(res.stdout)


def settings():
    """Prompt, base profile, template and output folder from Resume Builder (cached per run)."""
    global _settings_cache
    if _settings_cache is None:
        s = _node("settings")
        if s.get("warning"):
            print(f"  ! {s['warning']}")
        out = Path(s["outputDir"]).expanduser()
        # the saved folder may belong to another PC (e.g. C:/Users/<someone else>/...)
        if not out.exists() and not out.parent.exists():
            print(f"  ! resume folder {out} does not exist on this PC; using {FALLBACK_DIR}")
            out = FALLBACK_DIR
        s["outputDir"] = str(out)
        # prompt source chosen in the app's AI tab: Resume Builder (shared) or a local file
        if ai_settings.load()["prompt_source"] == "local" and ai_settings.local_prompt():
            s["instructions"] = ai_settings.local_prompt()
            s["source"] += " + local resume prompt"
        s["prompt"] = s["instructions"] + s["rules"]
        _settings_cache = s
    return _settings_cache


def save_prompt_to_resume_builder(text):
    """Save the prompt as Resume Builder's resume prompt (shared with the extension)."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text)
        path = f.name
    try:
        return _node("save-prompt", path)
    finally:
        Path(path).unlink(missing_ok=True)


def slug(s, max_len=40):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^\w\s-]", "", s).strip()
    return re.sub(r"[\s-]+", "_", s)[:max_len] or "x"


def safe_name(s, max_len=60):
    """Readable but safe for Windows file names: "Lead, Partnerships - AMER" -> "Lead Partnerships AMER"."""
    s = re.sub(r'[<>:"/\\|?*,;()\[\]{}]+', " ", str(s or ""))
    s = re.sub(r"\s+-\s+|\s+", " ", s).strip(" .-")
    return s[:max_len].strip() or "x"


def job_block(job, jd_limit=None):
    """Same layout as jobBlock() in lib/shared/ai.js.
    jd_limit: optional max chars for the job description (web ChatGPT Free handles shorter prompts better)."""
    head = [
        f"Company: {job.get('company') or '(not given)'}",
        f"Role: {job.get('role') or '(not given)'}",
        f"Location: {job['location']}" if job.get("location") else None,
        f"Salary: {job['salary']}" if job.get("salary") else None,
        f"Job link: {job['job_link']}" if job.get("job_link") else None,
    ]
    jd = job.get("jd") or "(no job description provided)"
    if jd_limit and len(jd) > jd_limit:
        jd = jd[:jd_limit] + "\n…[truncated for chat length]"
    return "\n".join([l for l in head if l] + [
        "<job_description>", jd, "</job_description>"])


def build_prompt(s, job, jd_limit=None):
    """The extension sends system + user + a JSON schema to the API. A web chat has one
    message box, so everything goes in one message, with the schema spelled out."""
    schema = s["schema"]
    schema_txt = json.dumps(schema, ensure_ascii=False)
    # Keep web prompts smaller: full schema can be huge and ChatGPT Free truncates/refuses.
    if jd_limit and len(schema_txt) > 8000:
        schema_txt = schema_txt[:8000] + "…[schema truncated]"
    return (
        "<instructions>\n" + s["prompt"] + "\n</instructions>\n\n"
        "<base_profile>\n" + json.dumps(s["base"], indent=2, ensure_ascii=False) + "\n</base_profile>\n\n"
        + job_block(job, jd_limit=jd_limit) + "\n\n"
        "<json_schema>\n" + schema_txt + "\n</json_schema>\n\n"
        "Write the resume for this job exactly as the instructions say. Reply with ONLY one JSON "
        "object that matches <json_schema>, inside a single ```json code block. No text before or after it."
    )


def api_user_message(s, job):
    """Same user message as tailorResume() in lib/shared/ai.js."""
    return ("<base_profile>\n" + json.dumps(s["base"], indent=2, ensure_ascii=False) + "\n</base_profile>\n\n"
            + job_block(job)
            + "\n\nWrite the resume for this job exactly as the system instructions say, and return it as JSON.")


def make_resume(ai, job, source="bot"):
    """Generate, render and save a tailored resume. Returns the PDF path.

    job: {id, company, role, location, salary, job_link, jd}
    ai:  a web_ai.WebAI
    """
    s = settings()
    if ai.provider in API:
        # API key: the exact request of the extension / web app (lib/shared/ai.js tailorResume):
        # system = prompt + output rules, user = base profile + job, JSON schema enforced
        tailored = ai.ask_json(api_user_message(s, job), required_keys=REQUIRED, system=s["prompt"],
                               schema=s["schema"], max_tokens=32000, effort="high")
    else:
        # Web ChatGPT (Free or Plus): shorter JD so the composer accepts the prompt.
        # Free does not need a special OpenAI permission - same chat UI as Plus.
        from web_ai import PROVIDERS
        jd_limit = 6000 if ai.provider in PROVIDERS else None
        tailored = ai.ask_json(build_prompt(s, job, jd_limit=jd_limit), required_keys=REQUIRED)
    if len(tailored.get("work") or []) != len(s["base"]["work"]):
        print(f"  ! AI returned {len(tailored.get('work') or [])} jobs, base profile has "
              f"{len(s['base']['work'])}; missing ones keep the base text")

    company = job.get("company") or "Company"
    role = job.get("role") or "Role"
    platform = (source or "bot").strip().lower() or "bot"
    # <Resume>/<platform>/<date>/<Company>/<Role>/KevinBurgess_Resume.pdf
    #   e.g. .../Resume/ashby/2026-11-22/Titan_AI/Principal_Forward_Deployed_Engineer/KevinBurgess_Resume.pdf
    out_dir = (Path(s["outputDir"]) / platform / date.today().isoformat()
               / safe_name(company) / safe_name(role, 90))
    file_base = "KevinBurgess"

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"base": s["base"], "tailored": tailored, "template": s["template"],
                   "outDir": str(out_dir), "fileBase": file_base}, f, ensure_ascii=False)
        req = f.name
    try:
        result = _node("build", req)
    finally:
        Path(req).unlink(missing_ok=True)

    (out_dir / "job.txt").write_text("\n".join([
        f"Source:    {source}",
        f"Job ID:    {job.get('id', '')}",
        f"Company:   {company}",
        f"Role:      {job.get('role', '')}",
        f"Location:  {job.get('location', '')}",
        f"Salary:    {job.get('salary', '')}",
        f"Link:      {job.get('job_link', '')}",
        f"AI:        {ai.provider}" + (f" / {ai.last_model or ai.model}" if ai.provider in API else
                                       (f" / {ai.model}" if ai.model else "")),
        f"Keywords:  {', '.join(tailored.get('keywords_matched') or [])}",
        "", job.get("jd") or "",
    ]), encoding="utf-8")
    return result["pdf"]


def make_cover_letter(ai, job, resume_pdf, source="bot"):
    """Write a cover letter for this job and render it next to the tailored resume.

    Uses Resume Builder's cover-letter prompt (Settings, else the app default) and the
    tailored resume.json from the same folder, so the letter matches the resume sent.
    Reuses <folder>/KevinBurgess_CoverLetter.pdf when it already exists. Returns the PDF path.
    """
    out_dir = Path(resume_pdf).parent
    file_base = "KevinBurgess"
    pdf = out_dir / f"{file_base}_CoverLetter.pdf"
    if pdf.is_file():
        return str(pdf)
    s = settings()
    resume_json = out_dir / "resume.json"
    resume = (json.loads(resume_json.read_text(encoding="utf-8"))
              if resume_json.is_file() else s["base"])
    prompt = s.get("coverPrompt") or ""
    user = (f"<resume>\n{json.dumps(resume, indent=2, ensure_ascii=False)}\n</resume>\n\n"
            f"{job_block(job)}\n\nWrite the cover letter. Output only the letter text.")
    if ai.provider in API:
        text = ai.ask(user, system=prompt, max_tokens=4000, effort="medium")
    else:
        try:
            text = ai.ask(f"{prompt}\n\n{user}")
        finally:
            ai.finish_web_chat()
    text = re.sub(r"^```[a-z]*\s*|\s*```$", "", (text or "").strip()).strip()
    if len(text) < 200:
        raise RuntimeError(f"{ai.provider} returned no usable cover letter")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"resume": resume, "letter": text, "template": s["template"],
                   "job": {"company": job.get("company") or "", "role": job.get("role") or ""},
                   "outDir": str(out_dir), "fileBase": file_base}, f, ensure_ascii=False)
        req = f.name
    try:
        result = _node("cover", req)
    finally:
        Path(req).unlink(missing_ok=True)
    return result["pdf"]
