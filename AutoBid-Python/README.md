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
3. **AI:** only the unclear rest, such as "Solutions Engineer", "Product Manager" or "Data Scientist", sent 150 titles per call. It uses Claude Code (`claude -p`) with your login, so there's no API key. The criteria are the `TARGET` text at the top of `relevance.py`. Edit it to widen or narrow what counts as relevant.

A new platform bot needs only two functions: `list jobs` (stage 1) and `job details` (stage 2). It reuses `relevance.py`, `resume.py`, `web_ai.py`, `captcha.py`, `notify.py` and `browser.py`.

## common/

| File | Purpose |
|---|---|
| `relevance.py` | Stage-1 filter: software/hardware development + engineering management |
| `resume.py` + `resume_bridge.js` | Tailored resume with the same prompt, base profile, template and PDF as the Resume Builder extension |
| `web_ai.py` | AI without an API key: Claude Code / Cursor CLI (web chat sites are supported but not recommended) |
| `browser.py` | The bot's own Chrome profile (Playwright) |
| `captcha.py`, `notify.py` | Bot-check detection and Windows notifications |
