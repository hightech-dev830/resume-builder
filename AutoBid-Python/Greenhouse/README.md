# Greenhouse Auto-Bid

Auto-bid bot for **Greenhouse job boards** listed in [`companies.csv`](companies.csv) (~3,400 boards such as Discord, Stripe, Databricks — `https://job-boards.greenhouse.io/<token>`).

Refresh the company list with:

```
python _scrape_companies.py
```

## Flow (Ashby-style collect)

1. **Collect** — public API `boards-api.greenhouse.io` for every board in `companies.csv`. Keeps **Remote + US-eligible** jobs, saves **apply links** (`…/jobs/{id}`), relevance-filters titles, then downloads **descriptions + application questions** (`?questions=true` — same fields as the apply form). Syncs new questions into the Greenhouse answer bank. **No MyGreenhouse login / no browser for Collect.**
2. **Answers / My profile** — fill once in the app (Supabase `platform=greenhouse`).
3. **Apply** — opens the scraped **job-boards** URL (`job-boards.greenhouse.io/{company}/jobs/{id}`), not the company careers listing and not MyGreenhouse browse. Two Chromes: Greenhouse (form) + mail (Gmail). Fills answers, uploads the tailored PDF, submits. If Greenhouse asks for an 8-character email security code, Apply reads it from Gmail and submits again.

## Run

```
pip install -r requirements.txt
python app.py                  # or AutoBid-Greenhouse.bat
python collect.py              # all companies in companies.csv
python collect.py --today      # recently published/updated only
python collect.py --org stripe # one board token (repeatable)
python collect.py --limit 100 --no-ai
python make_resume.py --all
python open_browser_mail.py    # once: sign in to Gmail, then close
python bid.py --dry-run --jobs-file data/selection.json
```

**Open bot browser** — Greenhouse profile `AutoBid-Python/.browser-profile-greenhouse` (MyGreenhouse / AI web logins).

**Open mail browser** — separate profile `AutoBid-Python/.browser-profile-mail`. Sign in once with the Gmail you use on applications, then close it before Apply. Apply reopens Greenhouse + mail together so it can pull security codes from greenhouse-mail.io.

Ashby uses `.browser-profile-ashby`, so both platforms can bid at the same time.

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

Answer bank is shared: `autobid_questions.platform` is a text array (e.g. `{ashby,greenhouse}`). One answer works on every bot.

## Notes

- Collect uses the public boards API only (no Playwright).
- Apply needs **two** Playwright Chromes: `.browser-profile-greenhouse` + `.browser-profile-mail`.
- Jobs with an apply link are `ready`; questions are discovered on the apply form at bid time.
- Tailored resumes: app **AI** tab → `ai_settings-greenhouse.json` (independent of Ashby).
