-- Registry for diarization references (metadata + manifests)
CREATE TABLE IF NOT EXISTS diarization_references (
    name TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    model TEXT,
    transcript_kind TEXT,
    clip_count INTEGER,
    manifest_path TEXT,
    aggregate_hash TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_diarization_references_hash ON diarization_references(aggregate_hash);
CREATE INDEX IF NOT EXISTS idx_diarization_references_model ON diarization_references(model);

CREATE OR REPLACE FUNCTION update_diarization_references_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_diarization_references_updated_at ON diarization_references;
CREATE TRIGGER trigger_update_diarization_references_updated_at
BEFORE UPDATE ON diarization_references
FOR EACH ROW
EXECUTE FUNCTION update_diarization_references_updated_at();
