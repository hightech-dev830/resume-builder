# Dice Auto Bid

A Python bot with a small window. It applies to **remote** jobs from your Dice **Recommended Jobs** list. Each application gets a resume (and cover letter) tailored to the job by the same engine as `extension-v2`, which targets a high ATS score while keeping the work history consistent and believable.

## Setup (once)

1. Install **Python 3.10+** (https://www.python.org/downloads/; tick *Add python.exe to PATH*) and **Node.js** (already used by this repo).
2. Double-click **`start.bat`**. The first time, it creates `.venv` and installs Playwright, then opens the window.
3. **Settings**:
   - **Resume / profile JSON:** your resume file. The format is a full profile like `extension-v2/profiles/sample-profile.json` (personal answers, plus jobs with `industry` and the real `stack`), or a plain JSON Resume.
   - **Save bids in:** the folder for the generated files.
   - **Resume writer:** *DeepSeek chat* (your DeepSeek account in the bot's browser, no API cost), *DeepSeek API*, or *OpenAI API*. Add the key for an API option.
   - **Posted within (days):** 1 = today (24 h), 2 = today and yesterday, and so on.
   - **Daily limit**, **Limit per Start**, **Pause between jobs:** these keep your Dice account safe.
   - **Supabase:** read from the repo's `.env`, so it is normally already filled in.
4. **Run → Open browser.**
   - With **Settings → Browser → My Chrome window (attach)**, the default, this opens a normal Chrome window with a debug port (you can also start it with **`start_chrome.bat`**).
   - Sign in there once to **Dice**, **chat.deepseek.com**, and anything else you need. Keep that window open and use it like your usual Chrome; the bot works in its own tabs, and closing the bot leaves the window open.
   - The window has its own profile (`data/chrome-profile`). Chrome (since version 136) does not allow a debug port on your main profile, so your everyday Chrome profile itself cannot be used.
   - The port only listens on this PC (127.0.0.1).

## How a run goes

Press **Start**. For each card on `dice.com/recommended-jobs` (page 1, 2, …), the bot:

1. **Skips** jobs that are already applied (on Dice, in the local log or in Supabase), not *Remote*, or posted outside your date range.
2. **Opens the job page** and reads the title, company, location, salary and description. It then **confirms remote again**: a *Remote* listing whose description asks for hybrid work, office days, on-site only, "must live within X miles" or similar is skipped. *Strict remote* also skips jobs tagged On-site or Hybrid. Jobs without Dice Easy Apply are skipped.
3. **Writes the resume:**
   - It uses the extension-v2 prompt and checks the result: an ATS score against this job description, and a consistency check (fixed employers and dates, no technology before it existed, every claim backed by the profile, believable "N years" claims).
   - If the ATS score is under target or the check finds problems, it asks the model for a correction round.
   - It then writes the cover letter.
4. **Saves the files** to `<Save bids in>/<YYYY-MM-DD>/<Company>/`:
   - `<First>_<Last>_cv.docx` (for example `Kevin_Burgess_cv.docx`)
   - `<First>_<Last>_cover_letter.docx`
   - `resume.json`, `job.txt`, `cover_letter.txt`
5. **Applies:**
   - It presses *Apply*. In the wizard, if Dice preloaded an older resume, it opens the **…** menu, picks **Replace** and uploads ours. It uploads the cover letter and presses **Next**.
   - On **Review your application**, it checks that our resume is shown, then presses **Submit**.
   - Extra questions are answered from your profile and the saved knowledge base (`extension-v2/data/examples.json`). If Dice still needs something, the bot **leaves that tab open** (*needs you* in the list) and moves on.
6. **On success**, it saves the bid to **Supabase** (table `resumes`, `source = 'dice'`). The row holds the company, title, job link, description, tailored resume JSON, cover letter, folder and file paths, step log, ATS and risk. Only successful applications go to Supabase. Every attempt is also written to `data/bids.jsonl` (the **History** tab).

**Dry run** does everything except pressing Submit, which is good for a first check.

**Manual bid** tab: for a Dice job you applied to by hand. Paste the link, press *Read from Dice*, then **Save to Supabase** (`source = 'dice-manual'`). Manual bids count toward the daily limit.

## Files

| File | Purpose |
|---|---|
| `app.py` | the window (Run, Settings, Manual bid, History) |
| `bot/runner.py` | one run: filters, documents, wizard, saving |
| `bot/dice.py` | Dice pages: list, job page, remote check, Easy Apply wizard |
| `bot/llm.py` | OpenAI API, DeepSeek API, DeepSeek web chat |
| `bot/engine.py`, `engine/engine.mjs` | bridge to the extension-v2 resume engine (prompt, ATS, consistency check, Word files, rule answers) |
| `bot/store.py` | Supabase (`resumes`) and the local log |
| `bot/browser.py`, `bot/worker.py` | the bot's Chrome window and the background thread |
| `tools/make_icon.mjs` | draws `assets/icon.png` / `icon.ico` |

## Running on a server (EC2)

The bot shows a window and a real browser where you sign in to Dice, so the server needs a desktop:

- **Windows Server:** connect with Remote Desktop and follow *Setup* as on a PC.
- **Linux (Ubuntu):** install a desktop and connect with Remote Desktop:
  ```
  sudo apt update && sudo apt install -y xfce4 xrdp python3-tk python3-venv
  echo xfce4-session > ~/.xsession && sudo systemctl enable --now xrdp
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs
  ```
  - Open port 3389 in the EC2 security group, for your IP only, and set a password for your user (`sudo passwd ubuntu`).
  - Connect with Remote Desktop, open a terminal in that desktop, then run:
  ```
  cd resume-builder/dice_bot && python3 -m venv .venv && . .venv/bin/activate
  pip install -r requirements.txt && python -m playwright install --with-deps chromium
  python doctor.py && python app.py
  ```
  - In the app, choose **Settings → Browser → Chromium (Playwright)**.

Copy the **whole** `resume-builder` folder to the server, not only `dice_bot`, because the resume engine lives in `extension-v2`. Also copy `.env`, for Supabase. **`python doctor.py`** lists anything that is still missing.

## When something goes wrong

- Failures save a screenshot and the page HTML in `data/debug/`. Dice changes its pages from time to time, and these files show which lookup in `bot/dice.py` needs updating.
- *"Dice is not signed in"*: press **Open browser** and sign in again.
- If the Supabase insert fails because columns are missing, run `supabase/migrations/002_auto_bid.sql`. Until then the bot puts those details into the bid's notes.
