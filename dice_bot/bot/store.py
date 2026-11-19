"""Where bids are recorded.
  - Supabase: table `resumes` (the web app's bid history) with source 'dice' (bot) or 'dice-manual'.
    Only successful applications are saved. Uses the project URL + secret key from the repo's .env
    (this bot runs on your own PC, like the local web server).
  - data/bids.jsonl: a local copy of every attempt (applied / needs_attention / skipped / failed),
    used for the daily limit when Supabase is unreachable and for the History tab."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .config import LEDGER_FILE

# columns added by supabase/migrations/002_auto_bid.sql; without that migration they go into notes
EXTRA_COLUMNS = ("source", "source_url", "apply_url", "bot_status", "bot_log", "answers", "applied_at")


class Supabase:
    def __init__(self, url: str, key: str):
        self.url = (url or "").rstrip("/")
        self.key = key or ""

    @property
    def ready(self) -> bool:
        return bool(self.url and self.key)

    def _req(self, method: str, path: str, body=None, headers=None):
        req = urllib.request.Request(
            f"{self.url}/rest/v1/{path}", method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                txt = r.read().decode()
                return json.loads(txt) if txt else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="ignore")
            try:
                d = json.loads(detail)
                raise SupabaseError(d.get("message") or detail, d.get("code", ""))
            except json.JSONDecodeError:
                raise SupabaseError(detail[:300], str(e.code))
        except urllib.error.URLError as e:
            raise SupabaseError(f"Could not reach Supabase: {e.reason}", "network")

    def insert_bid(self, row: dict) -> dict:
        """Inserts into resumes; falls back to the base columns if migration 002 has not been run."""
        try:
            out = self._req("POST", "resumes", row, {"Prefer": "return=representation"})
        except SupabaseError as e:
            if e.code not in ("PGRST204", "42703"):
                raise
            base = {k: v for k, v in row.items() if k not in EXTRA_COLUMNS}
            extra = {k: row[k] for k in EXTRA_COLUMNS if k in row and k not in ("bot_log", "answers")}
            base["notes"] = (row.get("notes", "") + "\n\n" + json.dumps(extra, indent=1)).strip()
            out = self._req("POST", "resumes", base, {"Prefer": "return=representation"})
        return (out or [{}])[0]

    def already_applied(self, url: str) -> bool:
        if not url:
            return False
        q = urllib.parse.quote(url, safe="")
        for col in ("source_url", "job_link"):
            try:
                if self._req("GET", f"resumes?select=id&{col}=eq.{q}&limit=1"):
                    return True
            except SupabaseError:
                continue
        return False

    def applied_today(self, sources=("dice", "dice-manual")) -> int | None:
        start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
        src = ",".join(sources)
        try:
            rows = self._req("GET", f"resumes?select=id&source=in.({src})&status=eq.applied&created_at=gte.{urllib.parse.quote(start)}")
            return len(rows or [])
        except SupabaseError:
            return None


class SupabaseError(RuntimeError):
    def __init__(self, msg, code=""):
        super().__init__(f"Supabase: {msg}")
        self.code = code


# ---------- local ledger ----------

def ledger_add(entry: dict) -> None:
    entry = {"t": time.time(), **entry}
    with LEDGER_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def ledger(limit: int = 500) -> list[dict]:
    if not LEDGER_FILE.exists():
        return []
    rows = []
    for line in LEDGER_FILE.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def ledger_seen(job_id: str) -> str:
    """Last status for a Dice job id ('' if never tried)."""
    for r in reversed(ledger(5000)):
        if r.get("job_id") == job_id:
            return r.get("status", "")
    return ""


def ledger_applied_today() -> int:
    today = datetime.now().date()
    return sum(1 for r in ledger(5000) if r.get("status") == "applied" and datetime.fromtimestamp(r["t"]).date() == today)


def bid_row(*, job: dict, source: str, resume: dict | None = None, letter: str = "", folder: str = "",
            resume_path: str = "", cover_path: str = "", notes: str = "", log: list | None = None,
            engine: str = "", model: str = "", apply_url: str = "") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "company": job.get("company", ""), "role": job.get("title", ""), "job_link": job.get("url", ""),
        "location": job.get("location", ""), "salary": job.get("salary", ""), "jd": job.get("jd", ""),
        "notes": notes, "template": "docx", "resume_json": resume, "cover_letter": letter or "",
        "resume_dir": folder, "pdf_path": resume_path, "cover_letter_path": cover_path,
        "status": "applied", "ai_provider": engine, "ai_model": model,
        "source": source, "source_url": job.get("url", ""), "apply_url": apply_url,
        "bot_status": "applied", "bot_log": [{"t": now, "msg": m} for m in (log or [])][-60:], "applied_at": now,
    }
