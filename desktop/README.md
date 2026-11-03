# Resume Builder

Personal desktop app (Electron + PostgreSQL + Claude) that tailors a resume and cover letter to each job description, previews it, and saves the PDFs and every bid to the database.

## Run

```
npm install
npm start
```

Login: `jkr` / `jkr`.

PostgreSQL connection lives in `db.config.json` (created on first run; also editable from the login screen under **Database connection**). Tables are created automatically in the `resume` database. `npm run check-db` tests the connection from the command line.

## How it works

1. **Resume tab**: enter company, role, link, and paste the JD. **Generate with AI** sends your base profile + the JD to the selected AI (Claude or OpenAI) using the resume prompt from Settings, which returns the tailored headline, summary, competencies, skills and bullets as JSON. Employers, dates, contact info and education are always taken from the base profile. The bid is saved to the DB (with the provider, model and prompt used) and the PDF is written to disk straight away; **Open PDF** / **Open folder** appear under the Generate button. Switching templates re-saves the PDF automatically.
2. Pick any of the 11 templates on the right; the preview updates instantly (**Fast** = HTML, **Exact PDF** = the real PDF). Edit the JSON directly under *Edit resume content* if you want to tweak wording, then **Download PDF**.
3. **Cover Letter tab**: generate with Claude, edit, export a PDF styled to match the resume template.
4. **History tab**: every bid with its ID, date, company, role, status (draft / generated / applied / interview / offer / rejected / archived), and links to the job, PDFs and folder. **Open** loads a bid back into the editor.
5. **Base Profile tab**: the master resume JSON (seeded from your CV).
6. **Settings tab**: AI provider (Claude or OpenAI) with its own API key and a model picker loaded live from that key, optional proxy for both, the editable resume and cover-letter prompts (with Reset to default), output folder and default template. All saved in the `settings` table.

## Files on disk

```
G:/Document/Resume/<YYYY-MM-DD>/<id>_<Company>/
  Kevin_Burgess_<Company>_Resume.pdf
  Kevin_Burgess_<Company>_Cover_Letter.pdf
  resume.json        # the exact content used for the PDF
  job.txt            # company, role, link, notes and the full JD
  cover_letter.txt
```

## Database

- `resumes`: one row per bid: `id`, `company`, `role`, `job_link`, `location`, `salary`, `jd`, `notes`, `instructions`, `template`, `resume_json`, `cover_letter`, `resume_dir`, `pdf_path`, `cover_letter_path`, `status`, timestamps.
- `settings`: key/value (`claude_api_key`, `claude_model`, `output_dir`, `default_template`, `base_resume`).

## Code map

- `main.js`: Electron main process, IPC handlers, file export
- `preload.js`: safe API bridge to the UI
- `src/db.js`: PostgreSQL schema and queries
- `src/ai.js`: Claude + OpenAI clients (proxy-aware), default prompts, resume tailoring with structured JSON output, cover letter, model listing
- `src/templates.js`: the 11 resume templates and the cover letter renderer
- `src/pdf.js`: HTML to PDF via Chromium `printToPDF`
- `src/baseResume.js`: seed base profile
- `renderer/`: login and app UI
