# Wellfound Auto-Bid

Auto-bid bot for companies that hire on **Wellfound** (`wellfound.com/company/<slug>`).  
Company list: [`companies.csv`](companies.csv) (~4,089 remote-directory companies, `ats=Wellfound`).

## Flow (matches your screenshots)

1. Open company jobs page → `https://wellfound.com/company/<slug>/jobs`
2. Collect job titles / links (`/jobs/<id>-<slug>`)
3. Open each job for summary + “About the job”
4. Discover questions via `?autoOpenApplication=true`
5. **Apply**: fill Picture-5 fields from **Answers / My profile**, click Submit
6. **Email verify** (Picture 6): you enter the 6-digit code in bot Chrome; app pauses until Continue

## Run

```
pip install -r requirements.txt
python app.py                  # or AutoBid-Wellfound.bat
python collect.py --org bitwarden
python collect.py --limit 20
python bid.py --dry-run --jobs-file data/selection.json
```

**Open bot browser** first and sign in to Wellfound (profile: `AutoBid-Python/.browser-profile-wellfound`). Close that Chrome before Collect/Apply. Ashby/Greenhouse use separate profiles, so they can run in parallel.

## Profile fields (Picture 5)

Saved in Supabase `autobid_profile` (shared with Ashby for name/email/phone/etc.):

| Form field | Profile key |
|---|---|
| Full Name | `name` |
| Email | `email` |
| Set / Confirm Password | `password` |
| Set Your Location | `location` |
| Open to work remotely | `open_remote` |
| Years of experience | `years_experience` |
| Desired Salary | `desired_salary` |
| Authorized to work in US | `authorized_us` |
| Visa sponsorship | `sponsorship` |
| Phone / LinkedIn / GitHub | `phone` / `linkedin` / `github` |

Answer bank rows use `platform=wellfound` in `autobid_questions`.

## Notes

- Scraping needs **Playwright + installed Chrome** (DataDome). Same bot profile as Ashby.
- Boards with no posting newer than ~4 months (`STALE_BOARD_DAYS`) are skipped on automatic collect.
- Start small: `collect.py --org bitwarden` or `--limit 20` before a full CSV run.
