-- Answer bank of the Python auto-bid bots (AutoBid-Python/): every distinct required
-- application question once, with your answer, plus your profile (name, email, ...).
-- Stored here instead of a local answers.json so every computer uses the same answers.
-- Run once in Supabase → SQL Editor → New query → paste → Run. Safe to re-run.
CREATE TABLE IF NOT EXISTS public.autobid_questions (
  platform       TEXT NOT NULL DEFAULT 'ashby',  -- which bot: ashby, ...
  key            TEXT NOT NULL,                  -- "<type>:<normalized question text>"
  question       TEXT NOT NULL DEFAULT '',
  type           TEXT NOT NULL DEFAULT '',       -- Boolean | ValueSelect | MultiValueSelect | String | LongText | ...
  form           TEXT NOT NULL DEFAULT '',       -- application | survey
  note           TEXT,                           -- small note under the question
  choices        JSONB,                          -- list of choice labels, for select questions
  asked_in_jobs  INTEGER NOT NULL DEFAULT 0,     -- relevant jobs that require it (0 = none right now)
  answer         JSONB,                          -- your answer: "Yes", "text", ["a","b"]; null = not answered
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (platform, key)
);

CREATE TABLE IF NOT EXISTS public.autobid_profile (
  key         TEXT PRIMARY KEY,                  -- name | email | phone | linkedin | github | website | location | resume_file
  value       TEXT NOT NULL DEFAULT '',
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- RLS on, no policies: only the secret key (the bots, the web app's server) can read them.
ALTER TABLE public.autobid_questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.autobid_profile   ENABLE ROW LEVEL SECURITY;
