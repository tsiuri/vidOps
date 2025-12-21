-- Migration: Add timestamp columns to videos table
-- Created: 2025-11-29
-- Author: Claude Code
-- Purpose: Add created_at and updated_at columns to support the new Overlord System

-- Add columns with defaults
ALTER TABLE videos
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();

-- Create trigger function to auto-update updated_at on row changes
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
   NEW.updated_at = NOW();
   RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop trigger if it exists (for idempotent re-runs)
DROP TRIGGER IF EXISTS update_videos_updated_at ON videos;

-- Create trigger to automatically update updated_at on UPDATE
CREATE TRIGGER update_videos_updated_at
  BEFORE UPDATE ON videos
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

-- Verification
-- Expected: videos table now has created_at and updated_at columns
-- Run: \d videos
