"""Tiny Supabase (PostgREST) client for the Python bots - standard library only.

Uses the same project and key as Resume Builder: SUPABASE_URL and SUPABASE_SECRET_KEY
(or SUPABASE_SERVICE_ROLE_KEY) from the environment, else from resume-builder/.env.local
or resume-builder/.env (the same files the web app reads).
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent   # resume-builder/
TIMEOUT = 30
PAGE = 1000            # Supabase returns at most 1000 rows per request


class SupabaseError(RuntimeError):
    pass


_env_cache = None


def _env(name):
    global _env_cache
    if os.environ.get(name):
        return os.environ[name]
    if _env_cache is None:
        _env_cache = {}
        for fname in (".env.local", ".env"):
            f = REPO_ROOT / fname
            if not f.exists():
                continue
            for line in f.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
                if not m or line.strip().startswith("#"):
                    continue
                v = m.group(2)
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                if v and m.group(1) not in _env_cache:   # .env.local wins; empty never hides a value
                    _env_cache[m.group(1)] = v
    return _env_cache.get(name, "")


def _config():
    url = _env("SUPABASE_URL").rstrip("/")
    key = _env("SUPABASE_SECRET_KEY") or _env("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SupabaseError(f"SUPABASE_URL / SUPABASE_SECRET_KEY are not set. Put them in "
                            f"{REPO_ROOT / '.env'} (the same values as Resume Builder).")
    return url, key


def request(method, table, params=None, body=None, prefer=None, headers=None):
    """One PostgREST call. Returns the parsed JSON (or None). Retries network errors / 5xx."""
    url, key = _config()
    full = f"{url}/rest/v1/{table}"
    if params:
        full += "?" + urllib.parse.urlencode(params, safe=",.()*:\"", quote_via=urllib.parse.quote)
    h = {"apikey": key, "Content-Type": "application/json", "Accept": "application/json"}
    if not key.startswith("sb_"):          # legacy JWT service_role key
        h["Authorization"] = f"Bearer {key}"
    if prefer:
        h["Prefer"] = prefer
    h.update(headers or {})
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    last = None
    for attempt in range(4):
        req = urllib.request.Request(full, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            if e.code < 500 and e.code != 429:
                hint = ""
                if "PGRST205" in text or "does not exist" in text:
                    hint = ("\nRun supabase/migrations/004_autobid_answers.sql in the Supabase "
                            "SQL editor first.")
                raise SupabaseError(f"Supabase {method} {table}: HTTP {e.code} {text[:300]}{hint}")
            last = f"HTTP {e.code} {text[:200]}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = str(getattr(e, "reason", e))
        time.sleep(1.5 * (attempt + 1))
    raise SupabaseError(f"Supabase not reachable ({method} {table}): {last}")


def select_all(table, params):
    """Every matching row (pages past Supabase's 1000-row limit). params needs an 'order'."""
    rows, start = [], 0
    while True:
        page = request("GET", table, params, headers={"Range-Unit": "items",
                                                      "Range": f"{start}-{start + PAGE - 1}"})
        rows += page or []
        if not page or len(page) < PAGE:
            return rows
        start += PAGE


def upsert(table, rows, on_conflict, chunk=500):
    """Insert or update rows (all with the same keys). Columns not in the rows are kept."""
    for i in range(0, len(rows), chunk):
        request("POST", table, {"on_conflict": on_conflict}, rows[i:i + chunk],
                prefer="resolution=merge-duplicates,return=minimal")


def in_list(values):
    """PostgREST in.(...) filter value with every item double-quoted."""
    return "in.(" + ",".join('"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'
                             for v in values) + ")"
