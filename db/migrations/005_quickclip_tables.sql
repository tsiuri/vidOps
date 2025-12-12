-- Migration 005: QuickClip Tables
-- Add tables for QuickClip session tracking

CREATE TABLE IF NOT EXISTS quickclip_sessions (
    session_id TEXT PRIMARY KEY,
    ytid TEXT NOT NULL REFERENCES videos(ytid),
    url TEXT NOT NULL,
    description TEXT,
    tags TEXT,  -- comma-separated tags
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    clips_count INTEGER DEFAULT 0,
    full_video_saved BOOLEAN DEFAULT FALSE,
    quality_profile TEXT DEFAULT 'best',
    total_duration_sec NUMERIC,
    session_dir TEXT  -- relative path to session directory
);

CREATE INDEX IF NOT EXISTS idx_quickclip_sessions_ytid ON quickclip_sessions(ytid);
CREATE INDEX IF NOT EXISTS idx_quickclip_sessions_created_at ON quickclip_sessions(created_at DESC);

CREATE TABLE IF NOT EXISTS quickclip_clips (
    clip_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES quickclip_sessions(session_id) ON DELETE CASCADE,
    ytid TEXT NOT NULL REFERENCES videos(ytid),
    start_sec NUMERIC NOT NULL,
    end_sec NUMERIC NOT NULL,
    duration_sec NUMERIC NOT NULL,
    label TEXT,  -- optional user label for this clip
    clip_index INTEGER,  -- 1, 2, 3... order in session
    asset_path TEXT,  -- relative path in storage
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_quickclip_clips_session ON quickclip_clips(session_id);
CREATE INDEX IF NOT EXISTS idx_quickclip_clips_ytid ON quickclip_clips(ytid);
