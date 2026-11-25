"""AI settings per AutoBid bot (edited in each desktop app's AI tab).

Each bot has its own file (Ashby / Greenhouse / Wellfound are independent):

    AutoBid-Python/ai_settings-ashby.json
    AutoBid-Python/ai_settings-greenhouse.json
    AutoBid-Python/ai_settings-wellfound.json

AUTOBID_BOT (set in that bot's config.py) picks the file. If missing, it is seeded
once from the legacy shared `ai_settings.json`.

Local resume prompt (when prompt_source=local):

    AutoBid-Python/resume_prompt-ashby.txt  (etc.), seeded from resume_prompt.txt
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SHARED_SETTINGS = ROOT / "ai_settings.json"
_SHARED_PROMPT = ROOT / "resume_prompt.txt"

_BOT_SUFFIX = {
    "ashby": "ashby",
    "ashbyhq": "ashby",
    "greenhouse": "greenhouse",
    "wellfound": "wellfound",
}

PROVIDERS = {
    # Safer: no browser automation against a free chat site
    "claude_code": "Claude Code (CLI, your Claude login) - safest / recommended",
    "claude_api": "Claude API (your Anthropic API key) - safest paid",
    "openai_api": "OpenAI API (your OpenAI API key) - safest paid",
    "cursor_auto": "Cursor Agent Auto (desktop CLI, your Cursor login) - Auto model",
    "cursor": "Cursor Agent (desktop CLI, pick a model)",
    # Risky: free web UIs ban automated bulk use (accounts can die)
    "chatgpt": "ChatGPT web (RISKY: rate limits / bans — prefer Claude Code or API)",
    "claude": "Claude.ai web (RISKY: rate limits / bans — prefer Claude Code or API)",
    "deepseek": "DeepSeek web (RISKY: free accounts often banned — prefer Claude Code or API)",
}
CLI_PROVIDERS = {"claude_code", "cursor", "cursor_auto"}
# Paid API with your own key. The keys are Resume Builder's settings in Supabase
# (claude_api_key / openai_api_key), shared with the web app and the Chrome extension.
API_PROVIDERS = {"claude_api": "anthropic", "openai_api": "openai"}
KEY_SETTINGS = {"claude_api": "claude_api_key", "openai_api": "openai_api_key"}
# Chat web pages: no model picker (the page's own model is used).
WEB_PROVIDERS = {"chatgpt", "claude", "deepseek"}
# Platforms that need no browser (CLI or API).
NO_BROWSER = CLI_PROVIDERS | set(API_PROVIDERS)

# Suggestions shown in the model box; any other model name can be typed in.
# "" = the platform's default model (for the API platforms: the model chosen in Resume
# Builder Settings, else claude-opus-5-5 / gpt-5).
MODELS = {
    "claude_code": ["", "opus", "sonnet", "haiku", "claude-opus-5-5", "claude-sonnet-5-5",
                    "claude-haiku-5-5", "claude-fable-5-2"],
    "claude_api": ["", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2"],
    "openai_api": ["", "gpt-5", "gpt-5-mini", "gpt-5-nano"],
    "cursor_auto": [""],   # always Auto routing (Cursor picks the model)
    "cursor": ["", "auto", "composer-2.5", "sonnet-5.5", "opus-5.5", "gpt-5", "gemini-2.5-pro"],
    "chatgpt": [""], "claude": [""], "deepseek": [""],
}

DEFAULTS = {
    "resume": {"provider": "claude_code", "model": ""},
    "relevance": {"provider": "claude_code", "model": "haiku"},
    "prompt_source": "resume_builder",
}


def _bot_key():
    return _BOT_SUFFIX.get((os.environ.get("AUTOBID_BOT") or "").strip().lower())


def settings_path():
    """Per-bot ai_settings-*.json, or legacy shared file if AUTOBID_BOT is unset."""
    bot = _bot_key()
    if not bot:
        return _SHARED_SETTINGS
    path = ROOT / f"ai_settings-{bot}.json"
    if not path.is_file() and _SHARED_SETTINGS.is_file():
        try:
            path.write_text(_SHARED_SETTINGS.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"  Seeded {path.name} from ai_settings.json")
        except Exception:
            pass
    return path


def local_prompt_path():
    bot = _bot_key()
    if not bot:
        return _SHARED_PROMPT
    path = ROOT / f"resume_prompt-{bot}.txt"
    if not path.is_file() and _SHARED_PROMPT.is_file():
        try:
            path.write_text(_SHARED_PROMPT.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass
    return path


# Back-compat aliases (resolved at call time via functions above)
SETTINGS_PATH = _SHARED_SETTINGS
LOCAL_PROMPT_PATH = _SHARED_PROMPT


def load():
    path = settings_path()
    try:
        s = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        s = {}
    for k, v in DEFAULTS.items():
        if isinstance(v, dict):
            s[k] = {**v, **(s.get(k) or {})}
        else:
            s.setdefault(k, v)
    return s


def save(s):
    path = settings_path()
    path.write_text(json.dumps(s, indent=2), encoding="utf-8")


def get(task):
    """(provider, model) for "resume" or "relevance"."""
    t = load()[task]
    return t.get("provider") or "claude_code", t.get("model") or ""


def make_ai(task, context=None, provider=None, model=None):
    """A web_ai.WebAI set up for this task (provider/model override the saved settings)."""
    from web_ai import WebAI
    p, m = get(task)
    return WebAI(provider or p, context, model=m if model is None else model, task=task)


# ---------------------------------------------------------------- API keys (Supabase settings)

def load_api_keys():
    """{"claude_api": key, "openai_api": key} from Resume Builder settings ("" = not set)."""
    import supabase_db as supa
    rows = supa.request("GET", "settings", {"select": "key,value",
                                            "key": supa.in_list(list(KEY_SETTINGS.values()))}) or []
    saved = {r["key"]: (r["value"] or "").strip() for r in rows}
    return {p: saved.get(k, "") for p, k in KEY_SETTINGS.items()}


def save_api_keys(keys):
    """Save {"claude_api": key, "openai_api": key} (only the given ones) for every app."""
    import supabase_db as supa
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [{"key": KEY_SETTINGS[p], "value": (v or "").strip(), "updated_at": now}
            for p, v in keys.items() if p in KEY_SETTINGS]
    if rows:
        supa.upsert("settings", rows, "key")


def api_key_problem(provider):
    """None when the API platform can be used, else a short reason."""
    if provider not in API_PROVIDERS:
        return None
    try:
        if not load_api_keys().get(provider):
            return f"no {PROVIDERS[provider].split(' (')[0]} key saved - add it in the app's AI tab"
    except Exception as e:
        return f"could not read the API key from Supabase ({str(e)[:120]})"
    return None


def local_prompt():
    try:
        return local_prompt_path().read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def save_local_prompt(text):
    local_prompt_path().write_text(text.strip() + "\n", encoding="utf-8")
