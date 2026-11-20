"""AI settings shared by every AutoBid bot (edited in the desktop app's AI tab).

AutoBid-Python/ai_settings.json:
    {
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
    "cursor": "Cursor (cursor-agent CLI, your Cursor login)",
    "chatgpt": "ChatGPT web page (not recommended: blocks bots)",
    "claude": "Claude.ai web page (not recommended: blocks bots)",
    "deepseek": "DeepSeek web page (not recommended: blocks bots)",
}
CLI_PROVIDERS = {"claude_code", "cursor"}

# Suggestions shown in the model box; any other model name can be typed in.
# "" = the platform's default model.
MODELS = {
    "claude_code": ["", "opus", "sonnet", "haiku", "claude-opus-5-5", "claude-sonnet-5-5",
                    "claude-haiku-5-5", "claude-fable-5-2"],
    "cursor": ["", "auto", "sonnet-5.5", "opus-5.5", "gpt-5", "gemini-2.5-pro"],
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
    return WebAI(provider or p, context, model=m if model is None else model)


def local_prompt():
    try:
        return LOCAL_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def save_local_prompt(text):
    LOCAL_PROMPT_PATH.write_text(text.strip() + "\n", encoding="utf-8")
