-- Tokens and US$ cost of every AI call (resume, cover letter, application pages).
-- Run once in Supabase → SQL Editor → New query → paste → Run. Safe to re-run.
CREATE TABLE IF NOT EXISTS public.ai_usage (
  id             BIGSERIAL PRIMARY KEY,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  provider       TEXT NOT NULL DEFAULT '',
  model          TEXT NOT NULL DEFAULT '',
  task           TEXT NOT NULL DEFAULT '',   -- resume | cover | page | test
  bid_id         INTEGER,                    -- resumes.id, when the call was for a bid
  input_tokens   INTEGER NOT NULL DEFAULT 0, -- not counting cached tokens
  cached_tokens  INTEGER NOT NULL DEFAULT 0,
  output_tokens  INTEGER NOT NULL DEFAULT 0, -- includes reasoning tokens
  cost_usd       NUMERIC(12, 6)              -- null when the model has no price set
);

CREATE INDEX IF NOT EXISTS ai_usage_created_at_idx ON public.ai_usage (created_at DESC);
CREATE INDEX IF NOT EXISTS ai_usage_bid_idx ON public.ai_usage (bid_id);

ALTER TABLE public.ai_usage ENABLE ROW LEVEL SECURITY;
