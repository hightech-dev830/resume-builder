# Auto Bid for Jobright (Chrome extension)

Applies to remote jobs from your Jobright list, one after another. For each job, it:

1. Opens the job on Jobright and reads the title, company, salary and description.
2. Saves a bid in Supabase through the Resume Builder server (`source = jobright`).
3. Clicks **Apply with autofill** and follows it to the company's application page, where it reads the full job description.
4. Generates a tailored resume and cover letter with your prompts. The server saves them like the desktop app does: `G:/Document/Resume_bot/<date>/<id>_<Company>/` gets the resume PDF, the cover letter PDF, `resume.json`, `job.txt` and `cover_letter.txt`. Change the folder under **Jobs & behavior** in the settings. This needs the server running on your PC; on Vercel the files go to Supabase Storage.
5. Fills the application page by page. The AI planner reads each page (fields, buttons, text) and decides what to type, pick, check and upload, and which button to press next. Cookie banners are accepted automatically. If a site asks for an account, it signs in or registers with your email and password.
6. Detects the thank-you page, marks the bid **applied**, and clicks **Yes, I applied!** on Jobright.
7. Shows a notification, then moves to the next job.

If a step needs you (a captcha, an email verification code, or a question it can't answer), it notifies you, leaves that tab open, marks the bid `needs_attention`, and moves on. For a captcha, it first waits a few minutes for you to solve it.

## Setup

1. **Server.** The extension uses the Resume Builder web app for AI, PDFs and Supabase. Run it locally (`npm run dev` in the repo root, which serves http://localhost:3000) or use your Vercel URL.
2. **Database columns.** Run `supabase/migrations/002_auto_bid.sql` once in the Supabase SQL Editor. This adds `source`, `source_url`, `apply_url`, `bot_status`, `bot_log`, `answers` and `applied_at` to `resumes`. Until you run it, the bot keeps its status in the bid's notes, and the step log and answers are not saved.
3. **Load the extension.** Open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and pick this `extension` folder.
4. **Settings** (right-click the icon, then **Options**):
   - **Connection:** enter the server URL and sign in with the web app login (`jkr` / `jkr`).
   - **AI & prompts:** pick Claude, OpenAI or Gemini, paste the API key, and choose models. You can use a faster model for reading application pages. These settings are shared with the web app.
   - **My info:** filled from `profile.local.json` on first load. Your password, SSN last 4 and date of birth never go to the AI; it sees placeholders, and the real values are typed straight into the page.
   - **Jobs & behavior:** remote only, posting age (default 24 h = today's jobs), maximum bids per run, template, auto-submit, cover letter, local copies.
5. On Jobright, set your search filters (role, Remote, Past 24 hours). Then open the extension popup and press **Start bot**. To run the bot on one job only, open that job's Jobright page and press **Apply to this job**.

## Files

| File | Purpose |
|---|---|
| `background.js` | The bid loop: Jobright list → job → application → confirmation |
| `content/jobright.js` | Reads Jobright cards and job pages, clicks Apply and "Yes, I applied!" |
| `content/agent.js` | Runs in every frame of the application tab: snapshots fields and buttons, fills fields, uploads files |
| `lib/config.js` | Settings, profile (and secret placeholders), server client |
| `popup.*` | Start/Stop, live status, counters, log |
| `options.*` | Settings page |
| `profile.local.json` | Your personal details. **Not in git** (it holds your password) |

The AI planner prompt lives on the server in `lib/agent.js`, and the server methods are `bot:plan`, `bot:seen`, `bot:log` and `files:get`.

## Limits

- The bot does not solve captchas. It waits for you to solve them.
- Email or SMS verification codes need you.
- Clicking a notification opens the tab it is about. For a captcha, the tab is brought to the front and scrolled to the captcha. The invisible reCAPTCHA badge in the bottom-right corner needs nothing from you, and the bot ignores it.
- Jobright's page layout can change. The reader works from text and structure ("… hours ago", "Apply with autofill", 24-character job ids) rather than class names, but check the log on your first run.
