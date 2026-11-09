-- Columns for bids made by the Chrome auto-bid extension (extension/).
-- Run once in Supabase → SQL Editor → New query → paste → Run. Safe to re-run.
ALTER TABLE public.resumes ADD COLUMN IF NOT EXISTS source     TEXT NOT NULL DEFAULT 'manual'; -- 'manual' | 'jobright'
ALTER TABLE public.resumes ADD COLUMN IF NOT EXISTS source_url TEXT NOT NULL DEFAULT '';       -- job-board page (jobright.ai/jobs/info/…)
ALTER TABLE public.resumes ADD COLUMN IF NOT EXISTS apply_url  TEXT NOT NULL DEFAULT '';       -- company application page the bot filled
ALTER TABLE public.resumes ADD COLUMN IF NOT EXISTS bot_status TEXT NOT NULL DEFAULT '';       -- preparing | applying | applied | needs_attention | failed | skipped
ALTER TABLE public.resumes ADD COLUMN IF NOT EXISTS bot_log    JSONB;                          -- [{ t, msg }] step log
ALTER TABLE public.resumes ADD COLUMN IF NOT EXISTS answers    JSONB;                          -- [{ question, answer }] what the bot typed
ALTER TABLE public.resumes ADD COLUMN IF NOT EXISTS applied_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS resumes_source_url_idx ON public.resumes (source_url);
CREATE INDEX IF NOT EXISTS resumes_bot_status_idx ON public.resumes (bot_status);
