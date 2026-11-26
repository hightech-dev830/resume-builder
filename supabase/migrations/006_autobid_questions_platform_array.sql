-- Shared AutoBid answer bank: platform TEXT → TEXT[] so one question/answer
-- can belong to several bots, e.g. {"ashby","greenhouse"} or {"ashby"}.
-- Primary key becomes `key` only (one row per question across all platforms).
--
-- Run once in Supabase → SQL Editor. Safe to re-run (IF NOT EXISTS / guards).

DO $$
BEGIN
  -- Already migrated?
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'autobid_questions'
      AND column_name = 'platform' AND data_type = 'ARRAY'
  ) THEN
    RAISE NOTICE 'autobid_questions.platform is already text[] — nothing to do';
    RETURN;
  END IF;

  -- 1) Staging table with merged rows (same key across ashby/greenhouse/wellfound → one row)
  CREATE TEMP TABLE _aq_merge ON COMMIT DROP AS
  SELECT
    key,
    (array_agg(DISTINCT platform ORDER BY platform)
      FILTER (WHERE platform IS NOT NULL AND platform <> '')) AS platforms,
    -- Prefer the longest question text / richest type/form
    (array_agg(question ORDER BY length(coalesce(question, '')) DESC))[1] AS question,
    (array_agg(type ORDER BY length(coalesce(type, '')) DESC))[1] AS type,
    (array_agg(form ORDER BY length(coalesce(form, '')) DESC))[1] AS form,
    (array_agg(note ORDER BY note NULLS LAST))[1] AS note,
    (array_agg(choices ORDER BY choices NULLS LAST))[1] AS choices,
    COALESCE(MAX(asked_in_jobs), 0) AS asked_in_jobs,
    -- Prefer a non-null answer; if several, take the most recently updated
    (array_agg(answer ORDER BY (answer IS NULL), updated_at DESC NULLS LAST))[1] AS answer,
    MIN(created_at) AS created_at,
    MAX(updated_at) AS updated_at
  FROM public.autobid_questions
  GROUP BY key;

  -- 2) Rebuild table
  ALTER TABLE public.autobid_questions RENAME TO autobid_questions_old_text;

  CREATE TABLE public.autobid_questions (
    platform       TEXT[] NOT NULL DEFAULT ARRAY['ashby']::TEXT[],
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
    PRIMARY KEY (key)
  );

  INSERT INTO public.autobid_questions (
    platform, key, question, type, form, note, choices, asked_in_jobs, answer, created_at, updated_at
  )
  SELECT
    CASE WHEN platforms IS NULL OR cardinality(platforms) = 0
         THEN ARRAY['ashby']::TEXT[]
         ELSE platforms END,
    key, coalesce(question, ''), coalesce(type, ''), coalesce(form, ''),
    note, choices, asked_in_jobs, answer,
    coalesce(created_at, now()), coalesce(updated_at, now())
  FROM _aq_merge;

  DROP TABLE public.autobid_questions_old_text;

  CREATE INDEX IF NOT EXISTS autobid_questions_platform_gin
    ON public.autobid_questions USING GIN (platform);

  ALTER TABLE public.autobid_questions ENABLE ROW LEVEL SECURITY;

  RAISE NOTICE 'Migrated autobid_questions.platform to text[] (PK = key)';
END $$;
