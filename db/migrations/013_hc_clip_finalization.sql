-- vidops/db/migrations/013_hc_clip_finalization.sql
-- Phase 4: clip finalization + rebuild semantics

BEGIN;

-- ---------------------------------------------------------------------------
-- Clip logical ids + versioning
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hc_clip_logicals (
  clip_logical_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES hc_projects(project_id) ON DELETE CASCADE,
  logical_key TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(project_id, logical_key)
);

ALTER TABLE IF EXISTS hc_clips
  ADD COLUMN IF NOT EXISTS clip_logical_id UUID,
  ADD COLUMN IF NOT EXISTS clip_version INTEGER,
  ADD COLUMN IF NOT EXISTS input_fingerprint TEXT,
  ADD COLUMN IF NOT EXISTS render_input_fingerprint TEXT,
  ADD COLUMN IF NOT EXISTS render_output_fingerprint TEXT,
  ADD COLUMN IF NOT EXISTS render_params JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS render_started_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS render_completed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS render_error TEXT;

-- Backfill existing clips into logical+version shape (best-effort, non-destructive)
DO $$
DECLARE
  r RECORD;
  lid UUID;
BEGIN
  FOR r IN
    SELECT clip_id, project_id
    FROM hc_clips
    WHERE clip_logical_id IS NULL OR clip_version IS NULL
  LOOP
    -- Create a logical id for each existing row; legacy rows did not model versions.
    INSERT INTO hc_clip_logicals (project_id, logical_key)
    VALUES (r.project_id, 'legacy:' || r.clip_id::text)
    ON CONFLICT (project_id, logical_key) DO UPDATE SET project_id = EXCLUDED.project_id
    RETURNING clip_logical_id INTO lid;

    UPDATE hc_clips
      SET clip_logical_id = COALESCE(clip_logical_id, lid),
          clip_version = COALESCE(clip_version, 1)
      WHERE clip_id = r.clip_id;
  END LOOP;
END $$;

ALTER TABLE IF EXISTS hc_clips
  ALTER COLUMN clip_logical_id SET NOT NULL,
  ALTER COLUMN clip_version SET NOT NULL;

-- Ensure version uniqueness
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'hc_clips_logical_version_uniq'
  ) THEN
    ALTER TABLE hc_clips
      ADD CONSTRAINT hc_clips_logical_version_uniq UNIQUE (clip_logical_id, clip_version);
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS hc_clips_logical_idx ON hc_clips (clip_logical_id, clip_version DESC);

COMMIT;
