-- 009: the 24x7 Verify worker (flightbridge/verify.py). Each school waiting for a decision is claimed by one runner at
-- a time (Mac Mini and CRM server share the queue), and comes back on its own schedule when it is waiting for
-- something (the monthly web-search allowance, a list upload, a week before re-checking).
ALTER TABLE flightbridge.organisation ADD COLUMN IF NOT EXISTS verify_claim_until TIMESTAMPTZ;
ALTER TABLE flightbridge.organisation ADD COLUMN IF NOT EXISTS verify_next_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_fb_org_verify_queue ON flightbridge.organisation (verify_next_at NULLS FIRST)
  WHERE deleted_at IS NULL AND eligibility IN ('unknown', 'eligible_claimed');
-- What the AI read on the school's website: which credential claims are real, the company's legal name(s), with quotes
ALTER TABLE flightbridge.organisation_profile ADD COLUMN IF NOT EXISTS ai JSONB;
