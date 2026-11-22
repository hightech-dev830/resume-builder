"""AI settings shared by every AutoBid bot (edited in the desktop app's AI tab).

AutoBid-Python/ai_settings.json:
    {
      # provider: claude_code | claude_api | openai_api | cursor | cursor_auto |
      #           chatgpt | claude | deepseek
      "resume":    {"provider": "claude_code", "model": "opus"},    # writes tailored resumes
      "relevance": {"provider": "claude_code", "model": "haiku"},   # sorts unclear job titles
      "prompt_source": "resume_builder"   # or "local"
    }

Resume prompt:
  "resume_builder" - the prompt saved in Resume Builder Settings (Supabase), the same one the
                     Chrome extension uses
  "local"          - AutoBid-Python/resume_prompt.txt (AutoBid only)
The JSON output rules (schema, fixed company name, ...) are always added after the prompt.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "ai_settings.json"
LOCAL_PROMPT_PATH = ROOT / "resume_prompt.txt"

PROVIDERS = {
    "claude_code": "Claude Code (CLI, your Claude login) - recommended",
    "claude_api": "Claude API (your Anthropic API key)",
    "openai_api": "OpenAI API (your OpenAI API key)",
    "cursor_auto": "Cursor Agent Auto (desktop CLI, your Cursor login) - Auto model",
    "cursor": "Cursor Agent (desktop CLI, pick a model)",
    "chatgpt": "ChatGPT web page (not recommended: blocks bots)",
    "claude": "Claude.ai web page (not recommended: blocks bots)",
    "deepseek": "DeepSeek web page (not recommended: blocks bots)",
}
CLI_PROVIDERS = {"claude_code", "cursor", "cursor_auto"}
# Paid API with your own key. The keys are Resume Builder's settings in Supabase
# (claude_api_key / openai_api_key), shared with the web app and the Chrome extension.
API_PROVIDERS = {"claude_api": "anthropic", "openai_api": "openai"}
KEY_SETTINGS = {"claude_api": "claude_api_key", "openai_api": "openai_api_key"}
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


def load():
    try:
        s = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        s = {}
    for k, v in DEFAULTS.items():
        if isinstance(v, dict):
            s[k] = {**v, **(s.get(k) or {})}
        else:
            s.setdefault(k, v)
    return s


def save(s):
    SETTINGS_PATH.write_text(json.dumps(s, indent=2), encoding="utf-8")


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
        return LOCAL_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def save_local_prompt(text):
    LOCAL_PROMPT_PATH.write_text(text.strip() + "\n", encoding="utf-8")
