-- 010_hc_suggestions_promotion.sql
-- Add explicit linkage from suggestion -> promoted hit.

BEGIN;

ALTER TABLE IF EXISTS hc_suggestions
  ADD COLUMN IF NOT EXISTS promoted_hit_id UUID REFERENCES hc_hits(hit_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS hc_suggestions_promoted_hit_idx ON hc_suggestions (promoted_hit_id);

COMMIT;
