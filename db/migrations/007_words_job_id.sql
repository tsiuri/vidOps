-- Add provenance link from words rows back to the producing job.
ALTER TABLE words
    ADD COLUMN job_id TEXT NULL;

CREATE INDEX IF NOT EXISTS idx_words_job_id ON words(job_id);
