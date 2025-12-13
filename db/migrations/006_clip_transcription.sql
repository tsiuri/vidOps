-- Migration 006: Clip Transcription Support
-- Add columns to support storing clip transcripts separately from video transcripts

-- Add transcripts JSONB column to quickclip_clips table
ALTER TABLE quickclip_clips ADD COLUMN IF NOT EXISTS transcripts JSONB DEFAULT '{}';

-- Add clip_id column to assets table to link clip transcripts to specific clips
ALTER TABLE assets ADD COLUMN IF NOT EXISTS clip_id TEXT NULL;

-- Add index on clip_id for efficient lookups
CREATE INDEX IF NOT EXISTS idx_assets_clip_id ON assets(clip_id);

-- Add composite index for (ytid, clip_id) to efficiently find clip-level assets
CREATE INDEX IF NOT EXISTS idx_assets_ytid_clip_id ON assets(ytid, clip_id);
