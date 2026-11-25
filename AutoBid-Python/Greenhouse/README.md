# Greenhouse Auto-Bid

Auto-bid bot for **Greenhouse job boards** listed in [`companies.csv`](companies.csv) (~3,400 boards such as Discord, Stripe, Databricks — `https://job-boards.greenhouse.io/<token>`).

Refresh the company list with:

```
python _scrape_companies.py
```

## Flow (Ashby-style collect)

1. **Collect** — public API `boards-api.greenhouse.io` for every board in `companies.csv`. Keeps **Remote + US-eligible** jobs, saves **apply links** per company, relevance-filters titles, then downloads descriptions. **No MyGreenhouse login / no browser for Collect.**
2. **Answers / My profile** — fill once in the app (Supabase `platform=greenhouse`).
3. **Apply** — opens each job’s apply URL in bot Chrome, fills the form, uploads the AI-tailored PDF, submits.

## Run

```
pip install -r requirements.txt
python app.py                  # or AutoBid-Greenhouse.bat
python collect.py              # all companies in companies.csv
python collect.py --today      # recently published/updated only
python collect.py --org stripe # one board token (repeatable)
python collect.py --limit 100 --no-ai
python make_resume.py --all
python bid.py --dry-run --jobs-file data/selection.json
```

**Open bot browser** is only needed for **Apply** (and optional AI web logins). Profile: `AutoBid-Python/.browser-profile-greenhouse`. Ashby uses `.browser-profile-ashby`, so both can bid at the same time.

## Profile fields

Saved in Supabase `autobid_profile` (shared keys with Ashby/Wellfound):

| Form field | Profile key |
|---|---|
| First / Last / Full Name | `first_name` / `last_name` / `name` |
| Email | `email` |
| Phone | `phone` |
| Location / LinkedIn / GitHub / Website | `location` / `linkedin` / `github` / `website` |
| Authorized to work in US | `authorized_us` |
| Visa sponsorship | `sponsorship` |

Answer bank rows use `platform=greenhouse` in `autobid_questions`.

## Notes

- Collect uses the public boards API only (no Playwright).
- Apply needs **Playwright + Chrome** (`.browser-profile-greenhouse`).
- Jobs with an apply link are `ready`; questions are discovered on the apply form at bid time.
- Tailored resumes: app **AI** tab → `ai_settings-greenhouse.json` (independent of Ashby).
