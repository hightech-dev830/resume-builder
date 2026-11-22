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

-- auto-bid extension (same as migrations/002_auto_bid.sql)
ALTER TABLE public.resumes ADD COLUMN IF NOT EXISTS source     TEXT NOT NULL DEFAULT 'manual'; -- 'manual' | 'jobright' | 'ashby'
ALTER TABLE public.resumes ADD COLUMN IF NOT EXISTS source_url TEXT NOT NULL DEFAULT '';       -- job-board page (jobright.ai/jobs/info/…)
ALTER TABLE public.resumes ADD COLUMN IF NOT EXISTS apply_url  TEXT NOT NULL DEFAULT '';       -- company application page the bot filled
ALTER TABLE public.resumes ADD COLUMN IF NOT EXISTS bot_status TEXT NOT NULL DEFAULT '';       -- preparing | applying | applied | needs_attention | failed | skipped
ALTER TABLE public.resumes ADD COLUMN IF NOT EXISTS bot_log    JSONB;                          -- [{ t, msg }] step log
ALTER TABLE public.resumes ADD COLUMN IF NOT EXISTS answers    JSONB;                          -- [{ question, answer }] what the bot typed
ALTER TABLE public.resumes ADD COLUMN IF NOT EXISTS applied_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS resumes_source_url_idx ON public.resumes (source_url);
CREATE INDEX IF NOT EXISTS resumes_bot_status_idx ON public.resumes (bot_status);

CREATE INDEX IF NOT EXISTS resumes_created_at_idx ON public.resumes (created_at DESC);
CREATE INDEX IF NOT EXISTS resumes_company_idx ON public.resumes (lower(company));

-- tokens and US$ of every AI call (same as migrations/003_ai_usage.sql)
CREATE TABLE IF NOT EXISTS public.ai_usage (
  id             BIGSERIAL PRIMARY KEY,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  provider       TEXT NOT NULL DEFAULT '',
  model          TEXT NOT NULL DEFAULT '',
  task           TEXT NOT NULL DEFAULT '',
  bid_id         INTEGER,
  input_tokens   INTEGER NOT NULL DEFAULT 0,
  cached_tokens  INTEGER NOT NULL DEFAULT 0,
  output_tokens  INTEGER NOT NULL DEFAULT 0,
  cost_usd       NUMERIC(12, 6)
);
CREATE INDEX IF NOT EXISTS ai_usage_created_at_idx ON public.ai_usage (created_at DESC);
ALTER TABLE public.ai_usage ENABLE ROW LEVEL SECURITY;

-- answer bank + profile of the Python auto-bid bots (same as migrations/004_autobid_answers.sql)
CREATE TABLE IF NOT EXISTS public.autobid_questions (
  platform       TEXT NOT NULL DEFAULT 'ashby',
  key            TEXT NOT NULL,
  question       TEXT NOT NULL DEFAULT '',
  type           TEXT NOT NULL DEFAULT '',
  form           TEXT NOT NULL DEFAULT '',
  note           TEXT,
  choices        JSONB,
  asked_in_jobs  INTEGER NOT NULL DEFAULT 0,
  answer         JSONB,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (platform, key)
);
CREATE TABLE IF NOT EXISTS public.autobid_profile (
  key         TEXT PRIMARY KEY,
  value       TEXT NOT NULL DEFAULT '',
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.autobid_questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.autobid_profile   ENABLE ROW LEVEL SECURITY;

-- jobs of the Python auto-bid bots (same as migrations/005_autobid_jobs.sql)
CREATE TABLE IF NOT EXISTS public.autobid_jobs (
  platform TEXT NOT NULL DEFAULT 'ashby', id TEXT NOT NULL,
  org TEXT NOT NULL DEFAULT '', company TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'new', status_at TEXT, note TEXT, applied_at TEXT, published_at TEXT,
  department TEXT, team TEXT, dept_path TEXT, employment_type TEXT, location TEXT, locations_all JSONB,
  workplace_type TEXT, is_remote INTEGER, job_url TEXT, apply_url TEXT, compensation TEXT,
  compensation_tiers TEXT, description TEXT, first_seen_at TEXT, is_active INTEGER NOT NULL DEFAULT 1,
  relevant INTEGER, relevance_by TEXT, details_at TEXT, questions JSONB, hash TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (platform, id)
);
CREATE INDEX IF NOT EXISTS autobid_jobs_status_idx    ON public.autobid_jobs (status);
CREATE INDEX IF NOT EXISTS autobid_jobs_published_idx ON public.autobid_jobs (published_at DESC);
ALTER TABLE public.autobid_jobs ENABLE ROW LEVEL SECURITY;

-- Row level security ON with no policies: the publishable (anon) key can read nothing.
-- The web app's server uses the secret key, which bypasses RLS.
-- This matters because settings holds your AI API keys.
ALTER TABLE public.settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.resumes  ENABLE ROW LEVEL SECURITY;

-- Private bucket for the generated PDFs / resume.json / job.txt
INSERT INTO storage.buckets (id, name, public)
VALUES ('resumes', 'resumes', false)
ON CONFLICT (id) DO NOTHING;
