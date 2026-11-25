# Ashbyhq Auto-Bid

Auto-bid bot for **remote US jobs at every company that hires through Ashby**. The companies are listed in [`companies.csv`](companies.csv): about 2,165 companies such as OpenAI, Notion, Ramp and Ashby itself, each with a `jobs.ashbyhq.com/<slug>` board. To add or remove a company, edit that file.

## Desktop app

Double-click **`AutoBid-Ashbyhq.bat`**, or run `python app.py`.

- **Jobs:** every collected job. Filters:
  - **Show** (relevant / not relevant / all) and **Status**.
  - **Companies ▾**: a multi-select list with search, *Select all* and *Clear*. The selection is remembered.
  - **Search**, plus click any column header to sort.

  Click a job to see its Overview and, on the **Application** tab, each required question with the answer the bot will use (red means missing). Buttons: Open job page, Make resume, Open resume, Dry run, Apply.
- **Answers:** your profile, plus every required question with how many jobs ask it. Pick a question and answer it with Yes/No, a choice, or text, once for all jobs.
- **Run:** pick the AI and batch size, keep Dry run on to test, then **Start bidding**. Tick *Bid only on the companies selected* to limit bidding to your Companies selection. A live log is at the bottom, and **Stop** always works.
- **AI:** separate settings for **resume writing** and **job sorting** (unclear titles in stage 1). For each one:
  - **Platform:** Claude Code, Cursor Agent Auto (desktop CLI + Auto model), Cursor Agent (pick a model), or an API key. The ChatGPT, Claude.ai and DeepSeek web pages are listed but not recommended. Cursor Agent needs the CLI: `irm 'https://cursor.com/install?win32=true' | iex`, then `agent login`.
  - **Model:** pick one (opus, sonnet, haiku…) or type any model name. Empty means the platform's default.
  - **Test** asks the chosen model a one-line question.

  The **Resume JSON prompt** editor can save either to Resume Builder (shared with the extension and web app) or as a local prompt used only by AutoBid (`AutoBid-Python/resume_prompt.txt`). The JSON output rules are always added after it. Settings are saved in `AutoBid-Python/ai_settings.json` and used by every bot.
- **Auto-collection** (Run tab): runs at fixed **US Eastern** times while the app is open: **1:00, 4:00, 8:00, 10:00, 12:00, 14:00, 16:00, 18:00, 20:00**. Choose *today's jobs* or *all companies*. If Apply is running, Collect is **queued** and starts when Apply finishes (you are not blocked). The header shows the next Eastern slot (it does not drift when a run finishes).
- **Collection roadmap** (Collect jobs / Collect today's jobs / auto): **1)** scrape boards from `companies.csv` → **2)** update the Jobs list → **3)** sync Supabase. Jobs tab defaults to **Posted: today**; use **Today's jobs** to jump back to that view.

Only one collection runs at a time (`data/collect.lock`). A second one, from the app, the auto timer or the command line, stops with a message instead of doubling the load. If Ashby answers "429 Too Many Requests", the bot waits and retries that run (it is **not** permanent-skipped). Boards whose newest known job is older than **~4 months** (`STALE_BOARD_DAYS` in `config.py`) are skipped on automatic collects and re-checked about monthly.

## How it works

It does not open a browser to scrape the page. It calls Ashby's own public endpoints, the same ones the careers page uses:

| Data | Source |
|---|---|
| Job list, links, description, salary | `api.ashbyhq.com/posting-api/job-board/<slug>` |
| Application questions (type, required, options) | `jobs.ashbyhq.com/api/non-user-graphql` (`ApiJobPosting`) |

It needs no login, no API key, and no extra packages (standard library only). Only Remote/Hybrid jobs with a US-eligible location are kept (city/state names like "Chicago, IL (Remote)" count — not only the words "United States").

## Run

Scraping has two stages (see [the shared README](../README.md#how-every-bot-scrapes-two-stages)):

```
python collect.py                    # stage 1 + stage 2, all companies
python scrape_jobs.py                # stage 1 only: job lists + relevance (software/hardware/eng mgmt)
python scrape_details.py             # stage 2 only: description + questions of relevant jobs
python collect.py --org openai       # one company (its board slug); can be repeated
python collect.py --no-ai            # relevance by rules only
python show.py                       # list relevant jobs
python show.py <job-id>              # full description + questions for one job
```

- **Stage 1** uses Ashby's small job-list request (`ApiJobBoardWithTeams`), which includes the department path, for example "Engineering > Platform". It's about 40 times smaller than the full board, so all companies take a few minutes. It keeps remote US jobs and marks each one relevant or not.
- **Stage 2** makes one request per **relevant** job (`ApiJobPosting`) for the description, pay per region and all questions. The job is then `ready`.

Run it again at any time. New jobs are added, closed ones are marked `is_active=0`, relevance is only decided once per title, and details are only downloaded for new relevant jobs.

## Database (`data/ashbyhq.db`, SQLite)

- **jobs**: one row per job. `status` moves through `new` → `ready` (questions collected) → `applied` / `skipped` / `failed`. After applying, you set `1Interview` → `2Interview` → `3Interview` → `Offer` / `Rejected` in the app (Jobs tab → **Status** → **Set status**, for the ticked jobs or the one shown).
- **questions**: one row per application-form field for each job, with `type` (String, LongText, Boolean, ValueSelect, MultiValueSelect, File, Location, Email, ...), `required`, `options`, and an empty `answer` column for the bid step.

### Jobs in Supabase (`sync_db.py`, **⬇ Sync from DB** / **⬆ Sync to DB** buttons)

Every job, with its questions and your status, is also stored in Supabase table `autobid_jobs` (run [`supabase/migrations/005_autobid_jobs.sql`](../../supabase/migrations/005_autobid_jobs.sql) once). - **⬇ Sync from DB** (`sync_db.py --pull`): download. Jobs changed in Supabase (for example on another computer) come to this board; nothing is uploaded.
- **⬆ Sync to DB** (`sync_db.py --push`): upload. Jobs changed on this computer go to Supabase; nothing else is downloaded, but a newer status set on another computer is kept, never overwritten.

Only changed jobs are sent. When both sides changed the same job, the newest status wins.

Both directions also run by themselves (`sync_db.py` without options) after every collection and every bid run, and a status you set in the app is saved to Supabase right away. On a new computer: copy the `.env`, open the app and click **⬇ Sync from DB** to get every job.

Not synced: the resume PDF path (it points into one computer's disk), `description_html` and the raw Ashby field JSON (large and unused).

### Collect / Collect today's jobs

Roadmap every time:

1. **Scrape** company career boards listed in `companies.csv` (stage 1 + stage 2).
2. **Update** the local job list (`data/ashbyhq.db` → Jobs tab).
3. **Sync** Supabase (`sync_db.py`).

`collect.py --today` still lists every board (Ashby lists have no posted date — that is how new jobs are found). It then focuses details on new/missing jobs, prints today's haul, and the app switches the Jobs list to **Posted: today**. If you click Collect while Apply is running, the collect is queued and starts when Apply finishes.

## Settings (`config.py`)

- `COMPANIES_FILE` / `ONLY_ORGS`: which companies to collect. `ONLY_ORGS = ["openai", "notion"]` limits collection to those.
- `WORKPLACE_TYPES` (default `{"Remote", "Hybrid"}`) and `LOCATION_FILTER` (default `"US"` = built-in US city/state matcher; set `None` for every country).
- `BOARD_WORKERS` / `WORKERS`: how many boards and question requests run in parallel.
- `APPLY_LIMITS`: per-company application limits, for example `{"ashby": (3, 60)}`.

## Answer bank (Supabase)

Only **required** questions are answered. Optional ones, including the Diversity Survey, are left empty. The one exception is the resume, which is always uploaded.

Most Ashby questions repeat on every job, but each job gives them a different field id. `answers.py` stores each distinct required question once, matched by its text and type. You answer it once, and that answer fills it on every job.

The questions, your answers and your profile are saved in **Supabase** (the same project as Resume Builder), not in a local file, so every computer uses the same answers:

- table `autobid_questions`: one row per question (`platform` = `ashby`, `key`, question text, type, choices, `answer`)
- table `autobid_profile`: name, email, phone, LinkedIn, GitHub, website, location, resume file

Setup, once: run [`supabase/migrations/004_autobid_answers.sql`](../../supabase/migrations/004_autobid_answers.sql) in Supabase → SQL Editor. The bot reads `SUPABASE_URL` and `SUPABASE_SECRET_KEY` from `resume-builder/.env.local` or `.env`, the same file as the web app. On a new computer, copy that `.env` and you have all your answers.

An old local `answers.json` is imported automatically on the first start, then renamed to `answers.json.imported`.

```
python answers.py            # add new questions to the bank + show coverage
python answers.py <job-id>   # preview how one job's form will be autofilled
python answers.py --import-backup path/to/autobid_questions.json   # fill empty matches
python answers.py --import-backup path/to/export.csv --dry-run     # preview only
```

### Import answers from another Supabase (backup file)

If you backed up `autobid_questions` from one Supabase project and point this bot at another (via `.env` / `.env.local`), you can copy over matching answers **without overwriting** anything already filled:

1. Point `SUPABASE_URL` / `SUPABASE_SECRET_KEY` at the **destination** account (this board).
2. Export `autobid_questions` from the old project (Supabase Table Editor → Download as CSV/JSON, or SQL dump as JSON array).
3. In the app **Answers** tab click **Import from backup…**, or run the CLI above.

Matching: same bank `key`, or same question text + type. Only empty answers on this board are updated; answers already set stay as they are.

Answer questions in the desktop app's **Answers** tab:

- **My profile**: name, email, phone, LinkedIn, GitHub, website, location, resume. These fill the Name, Email, Resume, Location, LinkedIn URL and GitHub URL fields.
- **Answers**: `"Yes"`/`"No"`, one choice label (for example `"30-39"`), several choices for multi-select (for example `["Asian or Asian American"]`), or text.
- Text and essay questions left empty are written per job by the AI in the bid step.

## Bot check (captcha)

When a bot check shows up (for example after clicking "Apply for this Job"), the bot never touches it. Instead it:

1. brings the browser to the front and scrolls to the captcha,
2. shows a **Windows notification** with a sound ("AutoBid: bot check - your turn"),
3. waits up to `CAPTCHA_WAIT_MIN` minutes (default 5) while you solve it, sends one reminder halfway, then continues by itself.

If the check isn't solved in time, the job is skipped and you get a notification.

It reacts to a reCAPTCHA checkbox or image challenge, hCaptcha, Cloudflare Turnstile and "verify you are human" pages. The invisible reCAPTCHA badge in the bottom-right corner needs nothing from you, so it's ignored. These are the same rules the Chrome extension uses.

Run `python notify.py` to test the notification. In batch mode (below), you get one notification for the whole batch instead of one per job.

## Apply: batch mode (`bid.py`)

```
pip install -r requirements.txt
python bid.py --dry-run --job <job-id>   # fill one form, don't submit: check it first
python bid.py                            # apply for real, in batches
```

1. It opens up to `BATCH_SIZE` jobs (default 5), one tab each. It fills the required fields from the answer bank, uploads the resume, and clicks **Submit Application**.
   - If no bot check appears, the application is submitted, the tab closes, and the job is marked `applied`.
   - If a bot check appears, the tab stays open and the job is marked `needs_you`.
2. You get **one** notification: "N applications are waiting for you". Solve the bot check in each tab. Each tab submits and closes by itself, and the next tab comes to the front.
3. After all are done, or after `BATCH_WAIT_MIN` minutes (default 20), the next batch starts. Unsolved jobs stay `needs_you` and are retried on the next run.

Notes:
- Ashby uses **invisible reCAPTCHA Enterprise**. There's no checkbox on the form; an image challenge appears only after Submit, when reCAPTCHA isn't sure.
- The bot uses your installed Chrome with its own profile (`data/chrome-profile`). Cookies build up over time, so bot checks get rarer.
- `APPLY_LIMITS` in `config.py` keeps the bot within company limits. Ashby allows 3 applications in 60 days.
- Empty required text/essay questions are written by the AI per job during the bid step (same AI as resumes). Yes/no, choice and profile answers must still be filled in the Answers tab.
- The "Autofill from resume" box at the top of the form is never used, because it would overwrite answers.
