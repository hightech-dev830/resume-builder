"""Shared AutoBid answer-bank helpers for autobid_questions.platform TEXT[].

One row per question `key`. `platform` is a list of bot ids that use it, e.g.
["ashby", "greenhouse"] or ["ashby"]. All bots share answers.
"""
from __future__ import annotations

from datetime import datetime, timezone

import supabase_db as supa

KNOWN_PLATFORMS = ("ashby", "greenhouse", "wellfound", "ashbyhq")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_platforms(raw, ensure=None) -> list[str]:
    """Coerce DB/API value to a sorted unique list of platform ids."""
    out = []
    if isinstance(raw, list):
        out = [str(x).strip().lower() for x in raw if x]
    elif isinstance(raw, str) and raw.strip():
        s = raw.strip()
        if s.startswith("{") and s.endswith("}"):
            # Postgres array literal {ashby,greenhouse}
            inner = s[1:-1]
            out = [p.strip().strip('"').lower() for p in inner.split(",") if p.strip()]
        else:
            out = [s.lower()]
    ensure = (ensure or "").strip().lower()
    if ensure and ensure not in out:
        out.append(ensure)
    # de-dupe, stable order
    seen, ordered = set(), []
    for p in out:
        if p and p not in seen:
            seen.add(p)
            ordered.append(p)
    return ordered or (["ashby"] if not ensure else [ensure])


def fetch_all_questions():
    """Every question in the shared bank (platform is metadata)."""
    return supa.select_all("autobid_questions", {
        "select": "platform,key,question,type,form,note,choices,asked_in_jobs,answer",
        "order": "key",
    })


def fetch_questions_for(platform: str):
    """Questions tagged with this platform, plus any untagged legacy rows.

    After migration to TEXT[], PostgREST filter: platform=cs.{"ashby"}
    Falls back to eq.platform if the column is still TEXT.
    """
    platform = (platform or "").strip().lower()
    try:
        return supa.select_all("autobid_questions", {
            "select": "platform,key,question,type,form,note,choices,asked_in_jobs,answer",
            "platform": f'cs.{{"{platform}"}}',
            "order": "key",
        })
    except Exception as e:
        # Pre-migration TEXT column, or filter not supported yet
        msg = str(e).lower()
        if "cs." in msg or "operator" in msg or "22p02" in msg or "text" in msg:
            return supa.select_all("autobid_questions", {
                "select": "platform,key,question,type,form,note,choices,asked_in_jobs,answer",
                "platform": f"eq.{platform}",
                "order": "key",
            })
        raise


def shared_fetch_questions(platform: str):
    """Prefer the full shared bank; if empty, fall back to platform-filtered rows."""
    try:
        rows = fetch_all_questions()
        if rows:
            return rows
    except Exception:
        pass
    return fetch_questions_for(platform)


def upsert_rows(rows, platform: str):
    """Upsert question rows. Merges `platform` into each row's platform array.

    Uses on_conflict=key (post-migration). Falls back to platform,key for old schema.
    """
    if not rows:
        return
    platform = (platform or "").strip().lower()
    # Merge platforms with whatever is already remote for these keys
    remote = {r["key"]: r for r in shared_fetch_questions(platform)}
    payload = []
    for r in rows:
        key = r.get("key")
        if not key:
            continue
        prev = remote.get(key) or {}
        plats = normalize_platforms(r.get("platform") or prev.get("platform"), ensure=platform)
        payload.append({
            "platform": plats,
            "key": key,
            "question": r.get("question") or "",
            "type": r.get("type") or "",
            "form": r.get("form") or "",
            "note": r.get("note"),
            "choices": r.get("choices"),
            "asked_in_jobs": int(r.get("asked_in_jobs") or 0),
            "answer": r.get("answer"),
            "updated_at": r.get("updated_at") or _now(),
        })
    if not payload:
        return
    try:
        for i in range(0, len(payload), 200):
            supa.upsert("autobid_questions", payload[i:i + 200], "key")
    except Exception as e:
        # Old TEXT platform + PK (platform, key)
        if "on_conflict" in str(e).lower() or "pkey" in str(e).lower() or "409" in str(e):
            legacy = []
            for r in payload:
                for p in r["platform"] or [platform]:
                    leg = dict(r)
                    leg["platform"] = p
                    legacy.append(leg)
            for i in range(0, len(legacy), 200):
                supa.upsert("autobid_questions", legacy[i:i + 200], "platform,key")
        else:
            raise


def patch_answer(key: str, answer, platform: str):
    """Update answer for a key (shared row). Returns representation rows or []."""
    body = {"answer": answer, "updated_at": _now()}
    try:
        rows = supa.request(
            "PATCH", "autobid_questions",
            {"key": f"eq.{key}"},
            body,
            prefer="return=representation")
        return rows or []
    except Exception:
        return supa.request(
            "PATCH", "autobid_questions",
            {"platform": f"eq.{platform}", "key": f"eq.{key}"},
            body,
            prefer="return=representation") or []


def delete_unanswered(keys, platform: str):
    """Delete unanswered questions by key (shared bank)."""
    if not keys:
        return
    for i in range(0, len(keys), 40):
        chunk = keys[i:i + 40]
        try:
            supa.request(
                "DELETE", "autobid_questions",
                {"key": supa.in_list(chunk), "answer": "is.null"},
                prefer="return=minimal")
        except Exception:
            supa.request(
                "DELETE", "autobid_questions",
                {"platform": f"eq.{platform}", "key": supa.in_list(chunk), "answer": "is.null"},
                prefer="return=minimal")
