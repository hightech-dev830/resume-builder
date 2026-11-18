-- Chrome extension v2 (extension-v2/): profiles and applications, per signed-in Supabase user.
-- Run once in Supabase → SQL Editor → New query → paste → Run. Safe to re-run.
--
-- The extension signs in with Supabase Auth (e-mail + password) and talks to these tables with the
-- publishable key + the user's token, so row level security keeps each user to their own rows.
-- Profile secrets (account password, SSN last 4, date of birth) and resume files never come here.

CREATE TABLE IF NOT EXISTS public.bid_profiles (
  user_id    UUID NOT NULL DEFAULT auth.uid() REFERENCES auth.users (id) ON DELETE CASCADE,
  id         TEXT NOT NULL,
  name       TEXT NOT NULL DEFAULT '',
  data       JSONB NOT NULL DEFAULT '{}'::jsonb,    -- personal answers (no secrets), resume base, preferences
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, id)
);

CREATE TABLE IF NOT EXISTS public.bid_applications (
  user_id      UUID NOT NULL DEFAULT auth.uid() REFERENCES auth.users (id) ON DELETE CASCADE,
  id           TEXT NOT NULL,
  profile_id   TEXT NOT NULL DEFAULT '',
  profile_name TEXT NOT NULL DEFAULT '',
  company      TEXT NOT NULL DEFAULT '',
  title        TEXT NOT NULL DEFAULT '',
  url          TEXT NOT NULL DEFAULT '',            -- job posting
  apply_url    TEXT NOT NULL DEFAULT '',            -- application form that was filled
  status       TEXT NOT NULL DEFAULT 'draft',       -- draft | resume_ready | filling | applied | interview | offer | rejected | skipped
  source       TEXT NOT NULL DEFAULT '',            -- manual | remotive | greenhouse | …
  platform     TEXT NOT NULL DEFAULT '',            -- Greenhouse | Lever | Workday | …
  ats_score    INTEGER,
  risk         INTEGER,
  jd           TEXT NOT NULL DEFAULT '',
  data         JSONB NOT NULL DEFAULT '{}'::jsonb,  -- everything else: location, salary, answers, cover letter, file names
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  applied_at   TIMESTAMPTZ,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, id)
);

CREATE INDEX IF NOT EXISTS bid_applications_created_idx ON public.bid_applications (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS bid_applications_status_idx ON public.bid_applications (user_id, status);
CREATE INDEX IF NOT EXISTS bid_applications_company_idx ON public.bid_applications (user_id, lower(company));

ALTER TABLE public.bid_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bid_applications ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "own profiles" ON public.bid_profiles;
CREATE POLICY "own profiles" ON public.bid_profiles FOR ALL TO authenticated
  USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

DROP POLICY IF EXISTS "own applications" ON public.bid_applications;
CREATE POLICY "own applications" ON public.bid_applications FOR ALL TO authenticated
  USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

GRANT SELECT, INSERT, UPDATE, DELETE ON public.bid_profiles, public.bid_applications TO authenticated;
