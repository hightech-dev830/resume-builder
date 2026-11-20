-- Jobs collected by the Python auto-bid bots (AutoBid-Python/), with their application
-- questions and your status (applied, 1Interview, 2Interview, 3Interview, Offer, ...).
-- Filled by the desktop app's "Sync DB" button; every computer syncs the same rows.
-- Run once in Supabase → SQL Editor → New query → paste → Run. Safe to re-run.
CREATE TABLE IF NOT EXISTS public.autobid_jobs (
  platform            TEXT NOT NULL DEFAULT 'ashby',
  id                  TEXT NOT NULL,             -- the job board's job id
  org                 TEXT NOT NULL DEFAULT '',  -- board slug, e.g. openai
  company             TEXT NOT NULL DEFAULT '',
  title               TEXT NOT NULL DEFAULT '',
  status              TEXT NOT NULL DEFAULT 'new', -- new | ready | needs_you | applied | failed | skipped
                                                   -- | 1Interview | 2Interview | 3Interview | Offer | Rejected
  status_at           TEXT,                      -- when the status was last set (newest wins on sync)
  note                TEXT,
  applied_at          TEXT,
  published_at        TEXT,                      -- posted date on the job board
  department          TEXT,
  team                TEXT,
  dept_path           TEXT,
  employment_type     TEXT,
  location            TEXT,
  locations_all       JSONB,
  workplace_type      TEXT,
  is_remote           INTEGER,
  job_url             TEXT,
  apply_url           TEXT,
  compensation        TEXT,
  compensation_tiers  TEXT,
  description         TEXT,
  first_seen_at       TEXT,
  is_active           INTEGER NOT NULL DEFAULT 1, -- 0 = closed / gone from the board
  relevant            INTEGER,                    -- 1 = software/hardware dev or eng management
  relevance_by        TEXT,
  details_at          TEXT,
  questions           JSONB,                      -- the job's application questions
  hash                TEXT,                       -- content fingerprint used by the sync
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (platform, id)
);

CREATE INDEX IF NOT EXISTS autobid_jobs_status_idx    ON public.autobid_jobs (status);
CREATE INDEX IF NOT EXISTS autobid_jobs_published_idx ON public.autobid_jobs (published_at DESC);

ALTER TABLE public.autobid_jobs ENABLE ROW LEVEL SECURITY;
