"""Calls engine/engine.mjs (Node) for everything resume-related, so the bot uses exactly the same
prompt, ATS score, consistency check and Word writer as extension-v2."""
from __future__ import annotations

import json
import shutil
import subprocess

from .config import ENGINE


class EngineError(RuntimeError):
    pass


def _node() -> str:
    node = shutil.which("node")
    if not node:
        raise EngineError("Node.js is needed for the resume engine (https://nodejs.org). Install it and restart.")
    return node


def call(command: str, payload: dict | None = None, timeout: int = 60):
    proc = subprocess.run(
        [_node(), str(ENGINE), command],
        input=json.dumps(payload or {}),
        capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        out = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        raise EngineError(f"Resume engine failed: {(proc.stderr or proc.stdout)[:500]}")
    if not out.get("ok"):
        raise EngineError(out.get("error") or proc.stderr[:500] or "Resume engine failed")
    return out["data"]


def load_profile(path: str) -> dict:
    return call("profile", {"path": path})
