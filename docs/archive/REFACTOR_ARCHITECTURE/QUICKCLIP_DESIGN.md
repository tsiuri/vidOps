# QuickClip System Design

**Status**: Proposal
**Created**: 2025-12-01
**Purpose**: Fast, interactive workflow for capturing clips from videos with timestamps and descriptions

---

## Overview

QuickClip is a streamlined end-to-end system for quickly saving video clips while watching content. It's designed for the common workflow: watching a video, spotting interesting moments, and wanting to preserve those moments with context and metadata.

### Core User Experience

```bash
# Basic usage: URL + timestamps + description
vo quickclip "https://youtube.com/watch?v=ABC123" \
  "120-145" "300-320" "450-(end)" \
  --description "Larry David compilation - best moments"

# Clips-only mode (skip downloading full video)
vo quickclip "https://youtube.com/watch?v=ABC123" \
  "60-90" "200-250" \
  --clips-only \
  --description "Specific segments for editing"

# With labels per clip
vo quickclip "https://youtube.com/watch?v=ABC123" \
  "120-145:intro" "300-320:main_point" "450-500:conclusion" \
  --description "Presentation breakdown"

# From local file (already downloaded)
vo quickclip --ytid ABC123 \
  "120-145" "300-320" \
  --description "Clips from local archive"
```

---

## Design Goals

1. **Speed**: Single command from idea to saved clips (< 30 seconds for most operations)
2. **Quality**: Always use highest available quality (no compromises)
3. **Persistence**: Store clips locally AND in central storage with full metadata
4. **Context**: Preserve why you clipped it (description, labels, timestamps)
5. **Flexibility**: Work with URLs, YTIDs, or local files
6. **Reusability**: Leverage existing download/clipping/storage infrastructure

---

## Architecture

### Command Flow

```
1. Parse input
   ↓
2. Validate URL/YTID
   ↓
3. Download video (if needed)
   ├─→ Full video (default)
   └─→ Skip if --clips-only or already exists
   ↓
4. Extract clips in parallel
   ↓
5. Store clips (local + central)
   ↓
6. Save metadata to DB
   ↓
7. Output summary with file paths
```

### Storage Layout

```
generated/quickclips/<session_id>/
├── metadata.json              # Session metadata
├── description.txt            # User's description
├── ABC123_full.mp4           # Full video (optional)
├── ABC123_clip1_120-145.mp4  # Clip 1
├── ABC123_clip2_300-320.mp4  # Clip 2
└── ABC123_clip3_450-500.mp4  # Clip 3
```

### Database Schema

New table: `quickclip_sessions`

```sql
CREATE TABLE quickclip_sessions (
    session_id TEXT PRIMARY KEY,
    ytid TEXT NOT NULL REFERENCES videos(ytid),
    url TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    clips_count INTEGER,
    full_video_saved BOOLEAN,
    quality_profile TEXT,
    total_duration_sec NUMERIC,
    session_dir TEXT  -- relative path to session directory
);
```

New table: `quickclip_clips`

```sql
CREATE TABLE quickclip_clips (
    clip_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES quickclip_sessions(session_id),
    ytid TEXT NOT NULL REFERENCES videos(ytid),
    start_sec NUMERIC NOT NULL,
    end_sec NUMERIC NOT NULL,
    duration_sec NUMERIC NOT NULL,
    label TEXT,  -- optional user label for this clip
    clip_index INTEGER,  -- 1, 2, 3... order in session
    asset_path TEXT,  -- relative path in storage
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## CLI Interface

### Command Structure

```bash
vo quickclip <URL_OR_YTID> <SPAN>... [OPTIONS]
```

### Positional Arguments

- `URL_OR_YTID`: YouTube URL or video ID
- `SPAN`: Timestamp range(s) in format:
  - `"START-END"` - Simple range (e.g., `"120-145"`)
  - `"START-END:LABEL"` - Range with label (e.g., `"120-145:intro"`)
  - `"(start)-60"` - From beginning to 60 seconds
  - `"300-(end)"` - From 5 minutes to end
  - `"(start)-(end)"` - Entire video as clip

### Options

**Core Options:**
- `--description TEXT` / `-d TEXT` - Session description (what this collection is about)
- `--clips-only` - Skip downloading full video, only extract clips
- `--keep-full` - Force keeping full video even if --clips-only (override)

**Quality Options:**
- `--quality PROFILE` - Quality profile (default: `best`)
  - `best` - Highest available (default)
  - `1080p` - 1080p max
  - `720p` - 720p max
  - `audio-only` - Audio clips only

**Organization Options:**
- `--name NAME` - Custom session name (default: auto-generated from timestamp)
- `--tags TAG,TAG,...` - Tags for searchability
- `--output DIR` - Custom output directory (default: `generated/quickclips/<session_id>`)

**Behavior Options:**
- `--force` - Re-download even if video exists
- `--skip-db` - Don't save to database (local only)
- `--priority INT` - Job priority (default: 10, higher than normal jobs)

### Examples

**Quick capture while watching:**
```bash
vo quickclip "https://youtube.com/watch?v=ABC123" \
  "120-145" "300-320" \
  -d "Interesting points from interview"
```

**Clips-only (don't need full video):**
```bash
vo quickclip "https://youtube.com/watch?v=ABC123" \
  "60-90" "200-250" "400-450" \
  --clips-only \
  -d "Just the highlights"
```

**Labeled clips for editing project:**
```bash
vo quickclip "https://youtube.com/watch?v=ABC123" \
  "0-30:intro" "120-180:main_argument" "500-530:conclusion" \
  --name "debate_breakdown" \
  --tags "politics,debate,analysis" \
  -d "Debate structure analysis"
```

**From already-downloaded video:**
```bash
vo quickclip --ytid ABC123 \
  "300-400" "500-600" \
  -d "Additional clips from archive"
```

**Audio-only clips (podcast highlights):**
```bash
vo quickclip "https://youtube.com/watch?v=ABC123" \
  "600-720" "1200-1350" \
  --quality audio-only \
  -d "Podcast highlights"
```

**Entire video as single clip (for archival with metadata):**
```bash
vo quickclip "https://youtube.com/watch?v=ABC123" \
  "(start)-(end)" \
  -d "Full video archive - important lecture"
```

---

## Implementation Plan

### Phase 1: Core Functionality
- [ ] Create `vo quickclip` CLI command in `vidops/cli/quickclip.py`
- [ ] Implement session ID generation (timestamp-based + short hash)
- [ ] Add database schema (migration script)
- [ ] Create `QuickClipService` in `vidops/services/quickclip.py`
- [ ] Implement basic flow: URL → download → clip → store

### Phase 2: Smart Reuse
- [ ] Check if video already exists (in DB or local storage)
- [ ] Reuse existing media assets when possible
- [ ] Implement `--clips-only` mode (yt-dlp timestamp download)
- [ ] Add quality profile handling

### Phase 3: Metadata & Search
- [ ] Save session metadata to DB
- [ ] Save individual clip metadata
- [ ] Add `vo quickclip list` - list recent sessions
- [ ] Add `vo quickclip show <session_id>` - show session details
- [ ] Add `vo quickclip search <query>` - search descriptions/tags

### Phase 4: Nice-to-Haves
- [ ] Thumbnail extraction for clips
- [ ] Auto-generate clip labels from transcripts (if available)
- [ ] Merge mode: combine clips into single video
- [ ] Export session as JSON/TSV
- [ ] Integration with existing hits/clips workflow

---

## Technical Details

### Reused Components

**Download:**
- Use existing `DownloadService.enqueue_download()`
- Quality profiles map to yt-dlp format strings:
  - `best` → `"bestvideo+bestaudio/best"`
  - `1080p` → `"bestvideo[height<=1080]+bestaudio/best[height<=1080]"`
  - `audio-only` → `"bestaudio/best"` with FFmpegExtractAudio

**Clipping:**
- Use existing clipping infrastructure (`ClippingService`)
- Generate hits TSV manifest internally
- Use `cut-net` mode for quick network extraction
- Fall back to `cut-local` if media is already staged

**Storage:**
- Use `FilesystemCache.persist_local_artifact()`
- Store in both local project dir and central storage
- Register as assets in DB with kind `quickclip`

### Session ID Format

```
quickclip_<YYYYMMDD>_<HHMMSS>_<SHORT_HASH>
```

Example: `quickclip_20251201_143022_a4f9b`

### Metadata JSON Schema

```json
{
  "session_id": "quickclip_20251201_143022_a4f9b",
  "ytid": "ABC123",
  "url": "https://youtube.com/watch?v=ABC123",
  "title": "Video Title Here",
  "description": "User's description",
  "tags": ["tag1", "tag2"],
  "created_at": "2025-12-01T14:30:22Z",
  "quality_profile": "best",
  "clips_only": false,
  "clips": [
    {
      "clip_id": "clip_1",
      "start_sec": 120.0,
      "end_sec": 145.0,
      "duration_sec": 25.0,
      "label": "intro",
      "filename": "ABC123_clip1_120-145.mp4",
      "asset_path": "generated/quickclips/quickclip_20251201_143022_a4f9b/ABC123_clip1_120-145.mp4"
    }
  ],
  "full_video": {
    "saved": true,
    "filename": "ABC123_full.mp4",
    "asset_path": "generated/quickclips/quickclip_20251201_143022_a4f9b/ABC123_full.mp4"
  }
}
```

---

## Edge Cases & Error Handling

### Already Downloaded Video
- Check DB for existing media asset
- Check local `pull/` directory
- Reuse if exists, skip download

### Clips-Only Mode with Unavailable Timestamps
- yt-dlp may not support timestamp downloads for all sources
- Fallback: download full video, extract clips, optionally delete full video

### Overlapping Clips
- Allow it - user may want multiple versions
- Warn if clips are identical

### Invalid Timestamps
- Validate against video duration (from metadata)
- Error clearly: "Timestamp 600s exceeds video duration (500s)"

### Network Failures
- Retry downloads with exponential backoff
- Save partial progress (clips already extracted)
- Allow resume with `--resume <session_id>`

### Storage Full
- Check available space before download
- Warn if local storage < 2x estimated video size
- Proceed with central storage only if user confirms

---

## Future Enhancements

### Interactive Mode
```bash
vo quickclip --interactive "https://youtube.com/watch?v=ABC123"
# Opens interactive prompt:
# "Enter timestamps (or 'done'): "
# "Clip 1 timestamp: 120-145"
# "Clip 1 label (optional): intro"
# "Clip 2 timestamp: done"
# "Session description: Interview highlights"
```

### Web UI Integration
- Simple web form: paste URL, select ranges on timeline
- Drag-to-select on video timeline
- Preview clips before committing

### Smart Suggestions
- If video has transcript, suggest "interesting" segments
- Based on keyword density, speaker changes, applause/laughter detection

### Export Formats
- Create compilation video (all clips stitched)
- Create YouTube chapters file
- Export to Premiere/DaVinci Resolve XML
- Create Anki flashcard deck with video clips

### Sharing
- Generate shareable link to session
- Export as portable archive (ZIP with videos + metadata)

### Auto-Tagging
- Extract tags from video title/description
- Suggest tags based on transcript content
- Learn from user's tagging patterns

---

## Example Workflow

**User's Perspective:**

```bash
# 1. Watching a video, spots interesting moments
vo quickclip "https://youtube.com/watch?v=dQw4w9WgXcQ" \
  "45-75:rick_dance" "120-145:never_gonna" "200-(end):full_chorus" \
  -d "Rick Astley highlights for meme compilation" \
  --tags "rickroll,meme,80s"

# Output:
# ✓ Downloading video dQw4w9WgXcQ (best quality)...
# ✓ Video downloaded: Never Gonna Give You Up (3:32)
# ✓ Extracting clip 1/3: rick_dance (45.0s - 75.0s)
# ✓ Extracting clip 2/3: never_gonna (120.0s - 145.0s)
# ✓ Extracting clip 3/3: full_chorus (200.0s - 212.0s)
# ✓ Uploaded to central storage
# ✓ Saved locally to: generated/quickclips/quickclip_20251201_220045_x7k2p/
#
# Session: quickclip_20251201_220045_x7k2p
# Clips saved:
#   1. rick_dance (30.0s): generated/quickclips/quickclip_20251201_220045_x7k2p/dQw4w9WgXcQ_clip1_45-75.mp4
#   2. never_gonna (25.0s): generated/quickclips/quickclip_20251201_220045_x7k2p/dQw4w9WgXcQ_clip2_120-145.mp4
#   3. full_chorus (12.0s): generated/quickclips/quickclip_20251201_220045_x7k2p/dQw4w9WgXcQ_clip3_200-212.mp4

# 2. Later, list all sessions
vo quickclip list

# Output:
# Recent QuickClip sessions:
#   quickclip_20251201_220045_x7k2p - "Rick Astley highlights..." (3 clips)
#   quickclip_20251201_143022_a4f9b - "Interview highlights" (2 clips)

# 3. Show specific session
vo quickclip show quickclip_20251201_220045_x7k2p

# Output:
# Session: quickclip_20251201_220045_x7k2p
# Video: Never Gonna Give You Up (dQw4w9WgXcQ)
# Created: 2025-12-01 22:00:45
# Description: Rick Astley highlights for meme compilation
# Tags: rickroll, meme, 80s
#
# Clips (3):
#   1. rick_dance       45.0s - 75.0s  (30.0s)
#   2. never_gonna     120.0s - 145.0s (25.0s)
#   3. full_chorus     200.0s - 212.0s (12.0s)
#
# Directory: generated/quickclips/quickclip_20251201_220045_x7k2p/
```

---

## Questions to Resolve

1. **Default quality**: Always `best`, or allow config default?
   - Proposal: Always `best` for quickclip (it's for important moments)

2. **Auto-delete full video after clipping?**
   - Proposal: Keep by default, add `--delete-full` flag

3. **Parallel clip extraction vs sequential?**
   - Proposal: Parallel (faster), limit to 3 concurrent

4. **Integration with existing `clips` commands?**
   - Proposal: Separate namespace for now, may merge later

5. **What if user wants to add clips to existing session?**
   - Proposal: Add `--append <session_id>` flag (Phase 4)

6. **Thumbnail generation?**
   - Proposal: Optional, `--thumbnails` flag, extract mid-frame of each clip

---

## Success Metrics

- **Speed**: < 30s from command to local clips for typical 5-minute video with 3 clips
- **Quality**: 100% of clips use highest available quality
- **Reliability**: 99% success rate for common video sources (YouTube, Vimeo)
- **Adoption**: Used for 50%+ of ad-hoc clip extraction workflows
- **Data**: All clips tracked in DB with searchable metadata

---

## Related Documentation

- Main clipping system: `CLIPS_HITS_CUTS.md`
- Storage architecture: `STORAGE_BROKER_DESIGN.md`
- Download system: `DOWNLOAD_ENQUEUE_CONFIG.md`
- Legacy bridge: `LEGACY_BRIDGE_MAP.md`

---

## Notes

- This design prioritizes user experience and speed over complex features
- Reuses existing proven infrastructure (download, clip, storage services)
- Can be implemented incrementally (Phase 1 gives full basic functionality)
- Database schema allows rich future queries and analytics
- Local + central storage ensures clips are never lost
