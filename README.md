# Resume Builder — Web (Vercel + Supabase)

The same app as the desktop version, running in the browser:

- **Database:** Supabase Postgres, with the same `settings` and `resumes` tables as the desktop app.
- **Files:** PDFs, `resume.json` and `job.txt` go to the private Supabase Storage bucket `resumes`, under `<YYYY-MM-DD>/<id>_<Company>/`. On the desktop they go to `G:/Document/Resume` instead. **Open PDF** and **Open folder** open short-lived download links.
- **PDFs:** headless Chromium (`@sparticuz/chromium` on Vercel), from the same templates as the desktop app. Windows fonts are swapped for metric-compatible Google Fonts (Calibri → Carlito, Times New Roman → Tinos, Georgia → Gelasio and so on), so line and page breaks stay close to the desktop output.
- **AI:** Claude or OpenAI, with the editable prompts and proxy settings, all stored in Supabase.

## 1. Create the tables (once)

Supabase dashboard → **SQL Editor** → New query → paste [`supabase/schema.sql`](supabase/schema.sql) → **Run**.

This creates the tables and the private `resumes` bucket. It also turns on row level security, so the publishable key can't read your data (the `settings` table holds your AI API keys).

## 2. Environment variables

Copy `.env.example` to `.env.local` for local runs, and add the same variables in **Vercel → Project → Settings → Environment Variables**:

| Variable | Value |
|---|---|
| `SUPABASE_URL` | `https://tlkatzpkwczzzkfnopsz.supabase.co` |
| `SUPABASE_SECRET_KEY` | Supabase → Project Settings → API keys → **Secret keys** (`sb_secret_…`). Server only. |
| `SUPABASE_PUBLISHABLE_KEY` | optional; used only by `npm run check` |
| `SESSION_SECRET` | any long random string (signs the login cookie) |
| `APP_USERNAME` / `APP_PASSWORD` | login, default `jkr` / `jkr` |
| `APP_TIMEZONE` | optional; time zone for folder dates, default `America/Los_Angeles` |

Check the setup:

```
npm install
npm run check
```

## 3. Run locally

```
npm run dev      # http://localhost:3000
```

This uses your installed Chrome or Edge for PDFs. Set `CHROME_PATH` if neither is found.

## 4. Deploy to Vercel

1. Push the repo and import it in Vercel.
2. Leave **Root Directory** empty (the repo root). Framework preset: **Other**. Leave Build Command, Output Directory and Install Command on their defaults; `vercel.json` sets them.
3. Add the environment variables above.
4. Deploy.

`vercel.json` gives the API function 300 s and 2 GB of memory, which AI generation and Chromium need. The Hobby plan may cap the duration lower; if a generation times out, use a faster model or upgrade the plan.

## Shared code

`lib/shared/*` and `public/{app.html,app.js,combobox.js,profile-editor.js,styles.css}` are **generated** from the desktop app (`desktop/src`, `desktop/renderer`). After changing those, run:

```
npm run sync
```

Then commit the result. Vercel serves the generated copies; `desktop/` itself isn't deployed (see `.vercelignore`).

| File | Purpose |
|---|---|
| `api/rpc.js` | single API endpoint, `POST /api/rpc { method, args }` |
| `lib/methods.js` | the server methods, mirroring the desktop IPC handlers in `desktop/main.js` |
| `lib/db.js` | Supabase queries (same functions as `desktop/src/db.js`) |
| `lib/storage.js` | Supabase Storage in place of local folders |
| `lib/pdf.js` | HTML → PDF with headless Chromium |
| `lib/fonts.js` | web font substitution |
| `lib/auth.js` | login cookie |
| `public/api-web.js` | browser version of the desktop `preload.js` (`window.api`) |
| `dev-server.js` | local server that behaves like Vercel |
