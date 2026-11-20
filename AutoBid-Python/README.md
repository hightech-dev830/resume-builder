# AutoBid-Python

Fast Python auto-bid bots. Each job site has its own folder, and shared code lives in [`common/`](common/).

| Bot | Site | Status |
|---|---|---|
| [Ashbyhq](Ashbyhq/) | ~2,165 companies on Ashby (`jobs.ashbyhq.com/<company>`), remote US | Scraping, answer bank, tailored resumes, batch bidding, desktop app |

Requires Python 3.9+. The Ashbyhq bot also needs `pip install playwright` (it drives your installed Chrome) and Node.js (it reuses Resume Builder's resume template).

## How every bot scrapes: two stages

All platform bots follow the same pattern, so the expensive work only happens for jobs worth applying to.

| Stage | What it downloads | For which jobs |
|---|---|---|
| **1. Job scraping** | The job **list**: title, department, locations, workplace type, pay summary | Every job on the site (small and fast) |
| *filter* | Location/workplace filter (e.g. remote US), then **relevance**: is it a software/hardware development or engineering-management role? | — |
| **2. Job details scraping** | Description, pay details, application questions | **Relevant jobs only** |

Relevance is decided by [`common/relevance.py`](common/relevance.py), the same for every platform:

1. **Cache:** the same title and department were decided before (`.cache/relevance.json`).
2. **Rules:** clear titles. "Senior Backend Engineer" → yes, "Account Executive" → no. Titles containing *engineer*, *developer* or *engineering* are never rejected by rules alone.
3. **AI:** only the unclear rest, such as "Solutions Engineer", "Product Manager" or "Data Scientist", sent 150 titles per call. It uses the platform chosen in the desktop app's AI tab: Claude Code (`claude -p`, your login, no API key), or the Claude / OpenAI API with your own key. The criteria are the `TARGET` text at the top of `relevance.py`. Edit it to widen or narrow what counts as relevant.

A new platform bot needs only two functions: `list jobs` (stage 1) and `job details` (stage 2). It reuses `relevance.py`, `resume.py`, `web_ai.py`, `captcha.py`, `notify.py` and `browser.py`.

## common/

| File | Purpose |
|---|---|
| `relevance.py` | Stage-1 filter: software/hardware development + engineering management |
| `resume.py` + `resume_bridge.js` | Tailored resume with the same prompt, base profile, template and PDF as the Resume Builder extension |
| `web_ai.py` | One AI interface for every platform: Claude Code / Cursor CLI (your login), Claude API / OpenAI API (your key), web chat sites (supported but not recommended) |
| `ai_settings.py` | Platform + model per task (resume writing, job sorting), set in the app's AI tab. API keys are Resume Builder's `claude_api_key` / `openai_api_key` settings in Supabase, shared with the web app and the extension |
| `supabase_db.py` | Small Supabase client (answer bank, jobs sync, settings) |

**API platforms** (`claude_api`, `openai_api`) call the API through `resume_bridge.js ai-complete`, i.e. Resume Builder's own `lib/shared/ai.js` (official SDKs, the proxy from Settings, Claude server-side fallbacks). A resume is the same request as the extension's (system prompt + base profile + job, JSON schema enforced). Tokens of every call are saved in the `ai_usage` table (task `autobid-resume` / `autobid-relevance`). An empty model box means the model chosen in Resume Builder Settings.
| `browser.py` | The bot's own Chrome profile (Playwright) |
| `captcha.py`, `notify.py` | Bot-check detection and Windows notifications |
