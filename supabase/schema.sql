-- Resume Builder schema for Supabase. Same tables as the desktop app.
-- Run once in Supabase → SQL Editor → New query → paste → Run. Safe to re-run.

CREATE TABLE IF NOT EXISTS public.settings (
  key        TEXT PRIMARY KEY,
  value      TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.resumes (
  id                 SERIAL PRIMARY KEY,
  company            TEXT NOT NULL DEFAULT '',
  role               TEXT NOT NULL DEFAULT '',
  job_link           TEXT NOT NULL DEFAULT '',
  location           TEXT NOT NULL DEFAULT '',
  salary             TEXT NOT NULL DEFAULT '',
  jd                 TEXT NOT NULL DEFAULT '',
  notes              TEXT NOT NULL DEFAULT '',
  instructions       TEXT NOT NULL DEFAULT '',
  template           TEXT NOT NULL DEFAULT 'professional',
  resume_json        JSONB,
  cover_letter       TEXT NOT NULL DEFAULT '',
  resume_dir         TEXT NOT NULL DEFAULT '',
  pdf_path           TEXT NOT NULL DEFAULT '',
  cover_letter_path  TEXT NOT NULL DEFAULT '',
  status             TEXT NOT NULL DEFAULT 'draft',
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- which AI + prompt produced the resume, so results can be compared per prompt
ALTER TABLE public.resumes ADD COLUMN IF NOT EXISTS ai_provider TEXT NOT NULL DEFAULT '';
ALTER TABLE public.resumes ADD COLUMN IF NOT EXISTS ai_model    TEXT NOT NULL DEFAULT '';
ALTER TABLE public.resumes ADD COLUMN IF NOT EXISTS prompt_used TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS resumes_created_at_idx ON public.resumes (created_at DESC);
CREATE INDEX IF NOT EXISTS resumes_company_idx ON public.resumes (lower(company));

-- Row level security ON with no policies: the publishable (anon) key can read nothing.
-- The web app's server uses the secret key, which bypasses RLS.
-- This matters because settings holds your AI API keys.
ALTER TABLE public.settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.resumes  ENABLE ROW LEVEL SECURITY;

-- Private bucket for the generated PDFs / resume.json / job.txt
INSERT INTO storage.buckets (id, name, public)
VALUES ('resumes', 'resumes', false)
ON CONFLICT (id) DO NOTHING;
