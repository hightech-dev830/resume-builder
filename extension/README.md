# Auto Bid (Chrome extension)

One extension, two modes (switch at the top of the side panel):

- **Jobright:** applies to the jobs on your Jobright list.
- **Job links:** applies to the job links you paste (Jobicy, Himalayas, DevITjobs, company pages, …).

Both work the same way, one job at a time; only where the next job comes from differs. For each job, it:

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
2. **Database.** Run `supabase/migrations/002_auto_bid.sql` and `003_ai_usage.sql` once each in the Supabase SQL Editor. This adds `source`, `source_url`, `apply_url`, `bot_status`, `bot_log`, `answers` and `applied_at` to `resumes`. `003` adds the `ai_usage` table (tokens and US$ of every AI call). Until you run `002`, the bot keeps its status in the bid's notes, and the step log and answers are not saved.
3. **Load the extension.** Open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and pick this `extension` folder.
4. **Settings** (right-click the icon, then **Options**):
   - **Connection:** enter the server URL and sign in with the web app login (`jkr` / `jkr`).
   - **AI & prompts:** pick Claude, OpenAI or Gemini, paste the API key, and choose models. You can use a faster model for reading application pages. These settings are shared with the web app.
   - **My info:** comes pre-filled with Kevin's profile (built into `lib/config.js`), so a new browser or a fresh clone starts filled in. Only the street address, password, SSN last 4 and date of birth are not built in (this repo is public): they come from `profile.local.json` when it is present, or you type them once in My info. Your password, SSN last 4 and date of birth never go to the AI; it sees placeholders, and the real values are typed straight into the page.
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

## Notes

- **Order:** the bot opens the application first and creates the bid and the tailored resume + cover letter only when a real form needs them. Jobs it skips cost no AI calls.
- **Side panel:** the toolbar icon opens the Auto Bid panel on the right side of Chrome. It stays open while you browse and while the bot switches tabs, so you can watch progress all the time. Close it with its ✕; the bot keeps running either way.
- **Skip job:** each job being applied to has its own card in the panel with a **⏭ Skip** button. It drops that job at once (even mid-AI call or while waiting on a captcha), marks its bid "skipped", closes its tabs (if "close tabs" is on) and moves on. Skipped jobs are not retried.
- **Job links mode:** in the panel, switch to **Job links**, paste links (one per line or any text with links; hundreds at once), press **Add links** and press Start. Links are done **one by one** in your current window: open the link, read the job description (after "Show more"), press its **Apply** button, and continue on the company page that opens (usually a new tab), the same way Jobright mode works. A link that is already the application form is filled right there. The same link can be added and applied to more than once (the log notes when a link was applied before). **Retry failed** puts failed/stopped links back in the queue.
- **Jobicy links:** the bot opens the posting, reads the title and company, clicks every **Show more** and reads the whole **About this role**, the AI summary, the **Core skills** and the details (remote from, salary, employment, experience). All of that becomes the job description for the tailored resume and cover letter. Then it presses **Apply Now**, follows the company tab it opens and fills the application as usual. No sign-in on Jobicy; an account is made only if the company's application asks for one. The bid keeps the Jobicy link as the job link and the company page as the apply address.
- **Other job boards:** Himalayas (description + Skills, then **Apply now**), DevITjobs (Requirements + Technologies; the newsletter pop-up after **APPLY** is closed, never filled in, and **APPLY** is pressed again) and any other site: the bot reads the page and clicks the button that means "apply" (Apply, Apply now, Create application, Start application, Apply on company site, …). Each job link is opened once and Apply clicked once; if it opens nothing, the job is skipped. No sign-in on the board.
- **Accounts:** sites that need an account are tried: the bot signs up with your email and password (or signs in) and carries on. If it cannot get past the account step, the job is skipped. Settings → Jobs & behavior → "Sites that need an account" can skip them right away instead.
- **Email verification:** keep Gmail open in a tab (signed in as the email in My info). When a site sends a code or a "verify your email" link, the bot reads the newest one or two messages, uses only an email that names the company/site (or a verification email that arrived while it waited), types the code or opens the link, and continues. Nothing from your mailbox is sent to the AI or the server. No email within the wait time (default 120 s): the job is skipped.
- **Example knowledge base ([data/examples.json](data/examples.json)):** taken from applications filled in correctly: College Board (Workday), GridUnity (Ashby), NRC Health (Jobvite), Canonical (Greenhouse), Toast, The Intect (SmartRecruiters), PDI Technologies (Lever) and VetsEZ. It has four parts:
  - `rules`: questions that always get the same answer. They are filled without the AI, before the built-in rules, so they always win.
  - `applications`: the worked examples, question by question.
  - `notes`: how forms should be answered.
  - `ats`: how each application system's steps go.

  On every page it sends to the AI, the bot also sends the notes, the steps of that ATS and the closest example questions with their answers. It learns too: each new question the AI answers is kept, and is given to the AI the next time a similar question comes up. Options > Jobs & behavior > **Learned examples** shows the count and downloads them as JSON, to check and merge into `data/examples.json`. Passwords, placeholders, e-mail, phone and address are never learned. Profile values are written as `{profile.key}`, so the street address stays out of the repo.
- **Salary:** when the job posts a yearly range, the bot gives the midpoint rounded to 5,000 (for example $180K–$199K becomes 190000). Without a range it uses My info's salary expectation, or the AI gives a market figure.
- **Captchas / bot checks:** solved by you, not the bot. The tab comes to the front with a notification; the bot continues by itself once it is solved.
- **Need you:** a job the bot leaves for you keeps its tabs, its bid and its resume files; nothing is closed or deleted, and the bot does not touch it again.
- **Models per task:** Settings → AI & prompts lets the resume, the cover letter and the application pages each use their own model (e.g. strong / mid / small).
- **LinkedIn:** jobs that send you to LinkedIn, or only allow Easy Apply / signing in with LinkedIn, are skipped.
- **Job list:** reloaded every 5 minutes (setting). When no new jobs are left, the bot waits for the next reload instead of stopping.
- **Low AI use:** each page is first handled by rules (`lib/rules.js`) with no AI:
  - The "Apply" and "Autofill with Resume" buttons are clicked without the AI.
  - Contact details, address, links, work authorization and sponsorship are filled from My info, along with relocation, the EEO questions, "how did you hear", uploads and consent boxes.
  - The AI is only asked about the required questions that are left, and it sees only those fields. Usually that's the "Why this company?" kind of question.
  - Other screening questions the AI answered once are saved (Settings → Jobs & behavior → Saved answers) and reused on later jobs.
  - If a page doesn't move on after a rules-only step (a validation error, an unusual form), the next step goes to the AI with the whole form.
  - For the AI calls that remain, choose a small model under Settings → AI & prompts → Agent model.
- **AI cost:** the popup shows today's and the last 30 days' cost; Settings → AI & prompts shows totals per model and lets you set prices (US$ per 1M tokens) for models without a built-in price.
- **Dropdowns:** the bot opens each custom dropdown once to read its real options, and the AI must pick one of them (your answer is mapped to the closest offered option, e.g. "Male (Man)" → "Male"). A value the form does not offer is never forced; the AI is told the options and picks again. "Other" is avoided; for "How did you hear about us" it picks Jobright, else Indeed, else another job board.
- **Answers:** edit `profile.local.json` and raise its `_version` to push changes into the extension; changes made on the settings page stay until the version goes up again.

## Limits

- The bot does not solve captchas. It waits for you to solve them.
- Email or SMS verification codes need you.
- Clicking a notification opens the tab it is about. For a captcha, the tab is brought to the front and scrolled to the captcha. The invisible reCAPTCHA badge in the bottom-right corner needs nothing from you, and the bot ignores it.
- Jobright's page layout can change. The reader works from text and structure ("… hours ago", "Apply with autofill", 24-character job ids) rather than class names, but check the log on your first run.
