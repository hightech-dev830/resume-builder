"""Auto-answer “when can you start?” style questions with the next day.

Used by every AutoBid platform bot so availability / start-date fields do not
block Apply and are not listed as unanswered in the Answers tab.
"""
import json
import re
from datetime import date, datetime, timedelta

# Broad match: start / availability wording (not salary, school, project end, …)
_START_RE = re.compile(
    r"(?i)"
    r"("
    r"when\s+(can|could|would|will|are)\s+you\s+(be\s+)?(able\s+to\s+)?start"
    r"|when\s+(are|would)\s+you\s+(be\s+)?available(\s+to\s+start)?"
    r"|when\s+would\s+you\s+be\s+able\s+to\s+(start|begin)"
    r"|available\s+(to\s+)?start"
    r"|available\s+start\s*(date|day)?"
    r"|earliest\s+(start|availability|date)"
    r"|start\s*date"
    r"|starting\s*date"
    r"|start\s*day"
    r"|date\s+(you\s+)?(can|could|would)\s+start"
    r"|how\s+soon\s+(can|could|would)\s+you\s+(start|begin)"
    r"|able\s+to\s+start\s+(a\s+)?(new\s+)?role"
    r"|begin\s+(a\s+)?(new\s+)?role"
    r"|first\s+(available\s+)?(day|date)"
    r"|your\s+availability\s+(to\s+start|date)?"
    r"|preferred\s+start\s*(date|day)?"
    r"|expected\s+start\s*(date|day)?"
    r")"
)

_EXCLUDE_RE = re.compile(
    r"(?i)\b("
    r"salary|compensation|pay\b|wage|bonus|"
    r"school|university|semester|graduation|degree|"
    r"project\s+start|contract\s+end|end\s+date|last\s+day|"
    r"visa\s+start|passport|birth"
    r")\b"
)

# Dropdown labels that mean “as soon as possible / next day”
_SOON_LABEL = re.compile(
    r"(?i)^\s*("
    r"immediately|immediate|asap|right\s+away|as\s+soon\s+as\s+possible|"
    r"available\s+now|now|today|tomorrow|next\s+day|"
    r"1\s*day|one\s*day|within\s+(a\s+)?day|"
    r"less\s+than\s+(a\s+)?week|0\s*[-–to]+\s*1(\s*week)?|"
    r"1\s*week|one\s*week|within\s+(a\s+)?week|"
    r"2\s*weeks|two\s*weeks"
    r")\s*$"
)

_DATE_IN_TEXT = re.compile(
    r"(?P<y>20\d{2})[-/](?P<m>\d{1,2})[-/](?P<d>\d{1,2})"
    r"|"
    r"(?P<m2>\d{1,2})[-/](?P<d2>\d{1,2})[-/](?P<y2>20\d{2})"
)


def tomorrow():
    return date.today() + timedelta(days=1)


def tomorrow_iso():
    return tomorrow().isoformat()


def tomorrow_us():
    return tomorrow().strftime("%m/%d/%Y")


def is_start_date_question(title: str) -> bool:
    t = (title or "").strip()
    if not t or _EXCLUDE_RE.search(t):
        return False
    return bool(_START_RE.search(t))


def _parse_date(text: str):
    m = _DATE_IN_TEXT.search(text or "")
    if not m:
        return None
    try:
        if m.group("y"):
            return date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
        return date(int(m.group("y2")), int(m.group("m2")), int(m.group("d2")))
    except Exception:
        return None


def _options_from_q(q) -> list:
    """Normalize Ashby/Greenhouse option shapes to [{label, value}, ...]."""
    options = q.get("options") if isinstance(q, dict) else None
    if isinstance(options, str):
        try:
            options = json.loads(options or "[]")
        except Exception:
            options = []
    if not options and isinstance(q, dict) and q.get("choices"):
        options = q["choices"]
    out = []
    for o in options or []:
        if isinstance(o, str):
            out.append({"label": o, "value": o})
        elif isinstance(o, dict):
            lab = o.get("label") if o.get("label") is not None else o.get("value")
            val = o.get("value") if o.get("value") is not None else o.get("label")
            if lab is not None or val is not None:
                out.append({"label": str(lab if lab is not None else val),
                            "value": val if val is not None else lab})
    return out


def start_date_answer(q=None) -> str:
    """Best answer for a start-date question: soonest choice, else tomorrow.

    Date fields → YYYY-MM-DD. Free text → MM/DD/YYYY. Dropdowns → soonest label/value.
    """
    q = q or {}
    ftype = (q.get("type") or "") if isinstance(q, dict) else ""
    options = _options_from_q(q) if isinstance(q, dict) else []

    if options and ftype in ("ValueSelect", "MultiValueSelect", "Select", "Dropdown", ""):
        # Prefer “Immediately / ASAP / tomorrow / 1 week …”
        for o in options:
            if _SOON_LABEL.match((o.get("label") or "").strip()):
                return str(o.get("value") if o.get("value") is not None else o["label"])
        # Prefer an explicit calendar date on/after tomorrow
        target = tomorrow()
        dated = []
        for o in options:
            d = _parse_date(o.get("label") or "") or _parse_date(str(o.get("value") or ""))
            if d and d >= target:
                dated.append((d, o))
        if dated:
            dated.sort(key=lambda x: x[0])
            o = dated[0][1]
            return str(o.get("value") if o.get("value") is not None else o["label"])
        # Last resort: first option (often “Immediately” at index 0 under other wording)
        o = options[0]
        return str(o.get("value") if o.get("value") is not None else o["label"])

    if ftype == "Date":
        return tomorrow_iso()
    # String / LongText / unknown — US-style date reads well on most ATS forms
    return tomorrow_us()
