# Auto Bid v2 (Chrome extension)

A half-automatic job application assistant. It does not run on its own. You pick the job, it writes an ATS-tailored resume through your ChatGPT or DeepSeek chat, and on each form page you press one button to fill what the page shows. Then you check the page and press Next or Submit yourself.

No server is needed. Data stays in the browser, and you can sync profiles and applications to this project's Supabase if you want them on more than one PC.

## Install

1. Open `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked** and pick this `extension-v2` folder.
3. Click the toolbar icon. The panel opens on the right.
4. Sign in with `jkrjkr` / `jkrjkr`. With Supabase sync off, this login is checked on this PC only.

## From A to Z

1. **Profiles.** Create one profile per person you bid for, or use **Import** with a JSON file. The format is in [profiles/sample-profile.json](profiles/sample-profile.json). A profile has three parts:
   - **Personal:** what forms ask for (contact, address, work authorization, EEO answers, links). The password, SSN last 4 and date of birth stay on this PC. The AI only ever sees `{{placeholders}}`.
   - **Experience:** for each job, the employer, title, dates and location. These are printed exactly as entered. Each job also has an **Industry** and a **Real stack**. Neither is printed. The resume writer and the consistency check use them as evidence of what the job really involved.
   - **Preferences:** the default template, how flexible the stack may be (*Strict* or *Adjacent*), and extra instructions for the writer.
2. **Browse Jobs.** Press ↻ to fetch. You can filter by source, type, work mode, posting age, currency, platform (Greenhouse, Lever, Workday and others), seniority, category and skills. You can also hide jobs you've already applied to, companies you've applied to, viewed jobs and flagged jobs. **Match %** compares each job with the active profile's technologies. "Applied" is tracked per profile.
3. **Apply.** Pressing **Apply** on a job opens it in a new tab and starts the wizard with its title, company and description. For your own job links, open the job page, then use **Apply to Job (current tab)** on the dashboard, or press ↻ next to *Job URL* to read the page.
4. **Build Resume.** Choose **DeepSeek**, **ChatGPT** or **OpenAI API**, then press **Generate with AI**.
   - In chat mode, the extension opens the chat site in a tab, types the prompt, waits for the full answer and fills the text box. You must be signed in to that site.
   - Next it checks the result. If the ATS score is below the target, or the consistency check finds real problems, it sends the list back to the same chat for one correction round. You can change the number of rounds in Settings.
   - Edit the text if you like, then **Analyze**, or press **Fix with AI** for another round.
   - Pick a template. The 15 templates are the same ones the desktop app uses. Then press **Preview** or download **PDF**, **Word** or **Both**.
5. **Fill Application.** Open the application form and press the round ✦ button on the right edge of the page (or **Auto fill current tab** in the panel). It fills contact details, work authorization, EEO, "how did you hear" and similar questions from the profile and saved answers. Everything else goes to OpenAI. It also uploads the resume. Check the page, press Next, then press ✦ again for the next step.
   - **Cover letter:** generate it on this step if the job asks for one. You can download it as PDF or Word. From then on, the fill types it into cover-letter boxes and uploads it to cover-letter file fields.
   - **Applied:** reaching a thank-you page marks the application **applied** by itself. You can also press **Mark as applied**.

## Where data goes

| What | Where |
|---|---|
| Profiles, applications (job details, JD, ATS score, risk, status, answers, cover letter text, file names) | `chrome.storage.local`, and Supabase tables `bid_profiles` / `bid_applications` when sync is on |
| Tailored resume text and the files ready for upload | this PC only (`chrome.storage.local`) |
| PDF / Word files, plus a `job.txt` | `Downloads/AutoBid/<Profile>/<date>_<Company>_<Title>/` |
| API keys, profile secrets | this PC only, never synced |

## Supabase sync (optional)

It uses the same Supabase project as the web app, with its own tables (the web app's `resumes` table is not touched).

1. Supabase → **SQL Editor** → run [supabase/migrations/004_extension_v2.sql](../supabase/migrations/004_extension_v2.sql). It creates `bid_profiles` and `bid_applications` with row level security, so each user sees only their own rows.
2. Supabase → **Authentication → Users → Add user → Create new user**: e-mail `jkrjkr@autobid.app`, password `jkrjkr`, and tick **Auto Confirm User**. The username you type in the extension, `jkrjkr`, becomes that e-mail.
3. On the extension's sign-in screen, open *Supabase sync*, tick **Sync profiles and applications with Supabase**, and sign in. The project URL and publishable key are already filled in.

Every PC that signs in this way sees the same profiles and applications. Profiles and applications made earlier in local mode are uploaded at the first sync. The extension only uses the publishable key plus the signed-in user's token, never the secret key.

Applications are stored with real columns (company, title, status, url, apply_url, ats_score, risk, jd, applied_at and so on), so you can query them in the SQL Editor.

## Where the jobs come from

| Source | Key | Notes |
|---|---|---|
| Remotive, Himalayas, Jobicy, Remote OK | none | remote tech jobs; public APIs |
| Arbeitnow | none | EU-leaning; off by default |
| Company boards | none | your list of `greenhouse:slug`, `lever:slug`, `ashby:slug`. Direct ATS links and full descriptions; the best source for direct applies. Edit the list in Settings (a slug that doesn't exist shows up as a source problem) |
| Adzuna | free app id + key from developer.adzuna.com | large US/UK index, searched with your *Search words* |
| JSearch (RapidAPI) | RapidAPI key | Google for Jobs: LinkedIn, Indeed, Glassdoor and company sites |

Jobs are cleaned up and de-duplicated by company and title. When the same job comes from several sources, the copy with a direct ATS link wins. Each job is tagged with a category (Backend, Frontend, AI/ML and so on), seniority, work mode, ATS platform and the technologies it names. They are kept for *Keep jobs posted within* days and refreshed every *Refresh every* hours while you're signed in.

## How the resume stays credible

The writer may change the headline, summary, skills, titles (at most one level up), job descriptions and bullets. It never changes employers, dates, locations or education. After every generation, the **consistency check** ([lib/validate.js](lib/validate.js)) looks for:

- changed facts (employers, dates, number of jobs, education) and inflated titles
- a technology used before it existed (for example LangChain in a job that ended in 2017), or one the job's real stack doesn't back. In *Adjacent* mode, close equivalents count: PostgreSQL→MySQL, RabbitMQ→Kafka, Jenkins→GitHub Actions
- "N+ years" claims that don't fit the dates, either for total experience or for one technology
- duplicated bullets, numbers in nearly every bullet, "100%" and "10x" claims, identical bullet shapes, repeated opening verbs
- em dashes, filler words ("leveraged", "spearheaded", "robust" and others), and keyword stuffing

**Risk 0%** means none of these were found. The panel also shows the years per technology implied by the job dates, which helps answer "how many years of X" questions consistently.

## Files

| File | Purpose |
|---|---|
| `sidepanel.*` | the app: login, dashboard, jobs, wizard, applications, profiles, settings |
| `background.js` | service worker: fills for the page button, marks applications applied on thank-you pages, refreshes jobs |
| `content/fab.js` | the ✦ button on pages |
| `content/agent.js` | reads and fills forms in every frame (from v1) |
| `content/chat.js` | types into ChatGPT / DeepSeek and reads the answer |
| `lib/chat.js` | drives the chat tab |
| `lib/resume.js` | resume prompt, JSON parsing, the editable text format, cover letter prompt |
| `lib/ats.js` | technology dictionary, JD keywords, ATS score, Match % |
| `lib/validate.js` | consistency check |
| `lib/fill.js` | one fill pass: rules, saved answers, examples, then OpenAI |
| `lib/rules.js`, `lib/examples.js`, `data/examples.json` | rule answers and the example knowledge base (from v1, now profile-neutral) |
| `lib/templates.js` | **generated** from `desktop/src/templates.js` by `npm run sync` in the repo root |
| `lib/pdf.js`, `lib/docx.js` | PDF through Chrome's print engine, native Word files |
| `lib/jobs.js` | job sources, tagging, cache |
| `lib/store.js`, `lib/supabase.js` | local storage + Supabase (Auth and REST) sync |

Tests for the parts that need no browser: run `npm test` in this folder (Node 20 or newer).

## Limits

- The chat sites change their pages from time to time. If **Generate** stops finding the chat box or the answer, update the selectors in `content/chat.js`. You can always switch to **OpenAI API**.
- Making a PDF briefly shows Chrome's "started debugging this browser" bar. That's how the extension gets Chrome's exact print output.
- Captchas, e-mail codes and account sign-ups are left to you.
