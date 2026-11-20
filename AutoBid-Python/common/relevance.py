"""Is a job a software / hardware development or engineering-management role?

Shared by every AutoBid platform bot (stage 1, "job scraping"): only relevant jobs go on
to stage 2 ("job details scraping") and to bidding.

How it decides, cheapest first:
  1. cache  - same title + department decided before (AutoBid-Python/.cache/relevance.json)
  2. rules  - clear titles: "Senior Backend Engineer" -> yes, "Account Executive" -> no
  3. AI     - only the unclear rest ("Solutions Engineer", "Technical Account Manager", ...),
              sent in batches of up to 150 titles per AI call (common/web_ai.py)
Without an AI, unclear jobs are kept (better to look at one job too many than miss one).
"""
import json
import re
import threading
from pathlib import Path

CACHE_PATH = Path(__file__).resolve().parent.parent / ".cache" / "relevance.json"
AI_BATCH = 150

TARGET = """Relevant (YES):
- Software development: software/backend/frontend/full-stack/mobile/web engineers and developers,
  platform, infrastructure, DevOps, SRE, cloud, data engineering, ML/AI engineering, security
  engineering, QA/test automation/SDET, developer tools, forward-deployed / implementation
  ENGINEERS who write code, solutions architects with hands-on engineering.
- Hardware development: hardware, electrical, embedded, firmware, FPGA/ASIC, robotics,
  mechanical/systems engineers building products.
- Engineering management: engineering manager, tech lead, head/director/VP of engineering, CTO,
  technical program manager, and product managers of software / AI / hardware products
  (any seniority, e.g. "Senior Product Manager, AI Products", "Group PM, Platform").
- Data/ML science roles that build models or software: data scientist, applied/research scientist
  in ML/AI, analytics engineer, QA/UAT testers.
Not relevant (NO): sales, account executives, SDR/BDR, marketing, recruiting, HR/people, finance,
accounting, legal, customer success/support (non-engineering), operations, design-only roles,
clinical/medical, teaching, facilities, content, community, product managers of non-software
products (e.g. insurance, food, healthcare operations), generic project managers.
Engineers whose job is mainly selling (pre-sales "Sales Engineer", "Solutions Engineer" in a
sales team, "Demo Engineer") are NO; engineers who build or integrate software for customers
("Forward Deployed Engineer", "Professional Services Engineer", "Salesforce Engineer") are YES."""

_YES_TITLE = re.compile(
    r"\b(software|back[- ]?end|front[- ]?end|full[- ]?stack|fullstack|mobile|ios|android|web|"
    r"platform|infrastructure|devops|dev ?ops|site reliability|sre|cloud|data|machine learning|ml|"
    r"ai|llm|deep learning|computer vision|security|firmware|embedded|hardware|electrical|fpga|asic|"
    r"robotics|systems?|distributed|compiler|kernel|database|qa|test|sdet|automation)\b.*\b"
    r"(engineer|engineering|developer|programmer|architect)s?\b"
    r"|\b(engineer|developer|programmer)\b.*\b(software|backend|frontend|full[- ]?stack|platform|"
    r"infrastructure|ml|ai|data|security|firmware|embedded|hardware)\b"
    r"|^(senior |staff |principal |lead |founding |sr\.? )*(software )?(engineer|developer)\b"
    r"|\b(sre|devops|sdet|cto|chief technology officer)\b"
    r"|\b(engineering manager|manager,? (of )?(software )?engineering|head of engineering|"
    r"director,? (of )?engineering|vp,? (of )?engineering|vice president,? (of )?engineering|"
    r"tech(nical)? lead|technical program manager|tpm|technical product manager)\b", re.I)

_NO_TITLE = re.compile(
    r"\b(account executive|account manager|sales|sdr|bdr|business development|marketing|"
    r"recruit|recruiter|talent|people partner|hr\b|human resources|payroll|finance|financial|"
    r"accountant|accounting|controller|tax|legal|counsel|attorney|paralegal|compliance officer|"
    r"customer success|customer support|support specialist|support agent|customer service|"
    r"community|content|copywriter|writer|editor|social media|brand|communications|comms|pr\b|"
    r"designer|design lead|creative|nurse|physician|therapist|clinician|clinical|medical assistant|"
    r"dental|pharmac|counselor|psychiatr|teacher|tutor|instructor|coach|driver|technician|"
    r"warehouse|facilities|office manager|executive assistant|administrative|receptionist|"
    r"chief of staff|operations (manager|associate|specialist|lead)|partnerships|procurement|"
    r"buyer|insurance agent|loan officer|underwriter|analyst, (finance|sales|marketing))\b", re.I)

# ...but these stay "unclear" (AI decides): they mix engineering and other work
_UNCLEAR_TITLE = re.compile(
    r"\b(solutions? (engineer|architect)|sales engineer|forward[- ]deployed|customer engineer|"
    r"support engineer|implementation (engineer|specialist)|technical account manager|"
    r"product manager|program manager|data analyst|analytics|research(er)?|scientist|"
    r"technical writer|developer (advocate|relations)|devrel)\b", re.I)

_TECH_DEPT = re.compile(r"\b(engineering|r ?& ?d|research and development|technology|tech|"
                        r"infrastructure|platform|software|hardware|security|data|ai|ml|product development)\b", re.I)
_NONTECH_DEPT = re.compile(r"\b(sales|marketing|go[- ]to[- ]market|gtm|people|talent|recruiting|finance|"
                           r"legal|customer success|support|operations|clinical|care|medical|g&a|revenue)\b", re.I)


# A clear engineering job title wins over anything else in the title
# ("Software Engineer, Legal", "Backend Engineer, Financial Engineering").
_STRONG_YES = re.compile(
    r"\b(software|back[- ]?end|front[- ]?end|full[- ]?stack|fullstack|mobile|ios|android|web|"
    r"platform|infrastructure|devops|site reliability|reliability|firmware|embedded|hardware|"
    r"electrical|fpga|asic|ml|machine learning|ai|llm|security|data|cloud|robotics|systems|"
    r"distributed systems|compiler|kernel|database|test automation|qa automation|network)\s+"
    r"(engineer|developer|engineering manager|architect)s?\b"
    r"|\b(engineering manager|head of engineering|director,? (of )?engineering|"
    r"vp,? (of )?engineering|vice president,? (of )?engineering|cto|chief technology officer|"
    r"technical program manager|technical product manager|tech lead|technical lead)\b", re.I)


def rule_decision(title, department=""):
    """True / False when the title (and department) make it clear, else None (ask the AI)."""
    title = (title or "").strip()
    if not title:
        return False
    if _STRONG_YES.search(title):
        return True
    unclear = bool(_UNCLEAR_TITLE.search(title))
    yes = bool(_YES_TITLE.search(title))
    no = bool(_NO_TITLE.search(title))
    if unclear:
        return None
    if yes and not no:
        return True
    # "...Engineer" / "Developer" / "Engineering" is never rejected by rules (e.g. a
    # "Salesforce Engineer" in Operations): unclear ones go to the AI instead
    engineering_word = re.search(r"engineer|developer|programmer|engineering", title, re.I)
    if no and not yes:
        return None if engineering_word and not re.search(
            r"\b(recruit|sourcer|coach|technician|marketing|sales)\w*\b", title, re.I) else False
    if engineering_word:
        return None
    if not yes and not no:
        # e.g. "Member of Technical Staff", "Founding Engineer, Payments": look at the department
        if department and _TECH_DEPT.search(department) and not _NONTECH_DEPT.search(department):
            return None if not re.search(r"engineer|developer|technical staff", title, re.I) else True
        if department and _NONTECH_DEPT.search(department):
            return False
    return None


def _key(title, department):
    norm = lambda s: re.sub(r"\s+", " ", (s or "").strip().lower())
    return f"{norm(title)}||{norm(department)}"


class RelevanceFilter:
    """classify([(id, title, department), ...]) -> {id: (relevant: bool, method: str)}"""

    def __init__(self, ai=None, log=print):
        self.ai = ai          # a web_ai.WebAI, or None for rules only
        self.log = log
        self._lock = threading.Lock()
        try:
            self.cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            self.cache = {}

    def _save(self):
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(self.cache, indent=0, ensure_ascii=False), encoding="utf-8")

    def classify(self, items):
        result, unclear = {}, {}
        for jid, title, dept in items:
            k = _key(title, dept)
            if k in self.cache:
                result[jid] = (self.cache[k], "cache")
                continue
            d = rule_decision(title, dept)
            if d is None:
                unclear.setdefault(k, []).append((jid, title, dept))
            else:
                self.cache[k] = d
                result[jid] = (d, "rule")

        if unclear:
            self.log(f"  relevance: {len(result)} decided by rules/cache, {len(unclear)} unclear titles"
                     + (f" -> asking {self.ai.provider}" if self.ai else " -> kept (no AI)"))
        keys = list(unclear)
        for start in range(0, len(keys), AI_BATCH):
            chunk = keys[start:start + AI_BATCH]
            decisions = self._ask_ai(chunk, unclear) if self.ai else {}
            for k in chunk:
                d = decisions.get(k)
                method = "ai" if d is not None else "unclear-kept"
                if d is None:
                    d = True
                else:
                    self.cache[k] = d
                for jid, _, _ in unclear[k]:
                    result[jid] = (d, method)
        self._save()
        return result

    def _ask_ai(self, keys, unclear):
        lines = [f"{n}. {unclear[k][0][1]}  [department: {unclear[k][0][2] or '-'}]"
                 for n, k in enumerate(keys, 1)]
        prompt = (
            "You sort job postings for a job seeker.\n\n" + TARGET + "\n\n"
            "Job postings (number. title [department]):\n" + "\n".join(lines) + "\n\n"
            "Reply with ONLY one JSON object inside a ```json code block: "
            '{"relevant": [numbers of the YES jobs]}. Every number not listed counts as NO.')
        try:
            # schema / effort are used by the API platforms only (structured JSON output)
            data = self.ai.ask_json(prompt, required_keys=["relevant"], effort="low", schema={
                "type": "object", "additionalProperties": False, "required": ["relevant"],
                "properties": {"relevant": {"type": "array", "items": {"type": "integer"}}}})
        except Exception as e:
            self.log(f"  ! relevance AI failed ({str(e)[:120]}); unclear jobs are kept")
            return {}
        yes = {int(n) for n in data.get("relevant") or [] if str(n).isdigit()}
        self.log(f"  relevance AI: {len(yes)} of {len(keys)} unclear titles are relevant")
        return {k: (n in yes) for n, k in enumerate(keys, 1)}
