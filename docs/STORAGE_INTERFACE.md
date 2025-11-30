# Storage Manager Interface Guide

**Last Updated:** 2025-11-29

This document describes how services should interact with the FilesystemCache storage manager.

---

## Overview

The `FilesystemCache` class manages file storage across two locations:
1. **Central Storage** (`/mnt/mainroot/mnt/13tb_sas/vidops/storage/`) - Shared, persistent storage
2. **Local Cache** (`~/vidops_cache` or `./tmp`) - Worker-local temporary files

All services receive a `FilesystemCache` instance via dependency injection.

---

## Core Methods

### 1. Get Central Storage Path

```python
# Get absolute path in central storage
central_path = fs_cache.get_central_path("raw/video.mp4")
# Returns: /mnt/mainroot/mnt/13tb_sas/vidops/storage/raw/video.mp4
```

### 2. Get Local Cache Path

```python
# Get absolute path in local cache
local_path = fs_cache.get_local_path("temp/processing.mp4")
# Returns: ~/vidops_cache/temp/processing.mp4
```

### 3. Pull File to Local Cache

```python
# Pull from central storage to local cache
local_path = fs_cache.pull_to_cache("raw/video.mp4")
# Copies file and returns local path
```

### 4. Find Media File

```python
# Locate video media file (searches central storage)
media_path = fs_cache.get_media_path(video)
# Returns path to media file or None

# Optionally pull to local cache for processing
media_path = fs_cache.get_media_path(video, pull_to_local=True)
```

### 5. Register Asset in Database

```python
# Register a file as an asset
asset = fs_cache.register_asset(
    video_repo=self.video_repo,
    relative_path="raw/eKDI2rxQ-fA__20241009.mp4",
    ytid="eKDI2rxQ-fA",
    kind="media"  # or "transcript", "clip", etc.
)
```

### 6. Write Transcript

```python
# Write transcript to local cache
transcript_path = fs_cache.write_transcript(
    transcript=transcript_obj,
    content=vtt_text,
    extension="vtt"
)
# Returns: ~/vidops_cache/transcripts/ytid_kind.vtt
```

### 7. Cleanup Local Cache

```python
# Remove file from local cache
removed = fs_cache.cleanup_local("temp/processing.mp4")
# Returns True if removed, False if didn't exist
```

### 8. Prepare Local Output Paths

```python
local_clip_path = fs_cache.prepare_local_path("clips/tmp/job_123.mp4")
# Ensures parent directories exist under the cache before writing
```

### 9. Push Local Artifact to Central Storage

```python
# Copy a processed file from cache -> central storage
fs_cache.push_local_to_central(local_clip_path, "clips/ytid/foo.mp4")
```

### 10. Persist + Register an Asset

```python
fs_cache.persist_local_artifact(
    local_path=local_clip_path,
    relative_path="clips/ytid/foo.mp4",
    video_repo=video_repo,
    ytid="eKDI2rxQ-fA",
    kind="clip",
)
```

---

## Usage Patterns

### Pattern 1: Download Service (Write to Central)

```python
def process_job(self, job: Job):
    # Get central storage path
    download_dir = self.fs_cache.get_central_path("raw")
    download_dir.mkdir(parents=True, exist_ok=True)

    # Download directly to central storage
    downloaded_file = download_to(download_dir / "video.mp4")

    # Register as asset
    relative_path = downloaded_file.relative_to(self.fs_cache.central_storage_root)
    self.fs_cache.register_asset(
        video_repo=self.video_repo,
        relative_path=str(relative_path),
        ytid=job.ytid,
        kind="media"
    )
```

### Pattern 2: Transcription Service (Read from Central, Process Locally)

```python
def process_job(self, job: Job):
    # Get video object
    video = self.video_repo.get(job.ytid)

    # Pull media to local cache for faster processing
    media_path = self.fs_cache.get_media_path(video, pull_to_local=True)

    if not media_path:
        raise FileNotFoundError(f"Media not found for {job.ytid}")

    # Process locally
    segments = transcribe_file(media_path)

    # Write transcript to local cache
    vtt_path = self.fs_cache.write_transcript(transcript, vtt_content, "vtt")

    # Optional: Copy transcript to central storage for persistence
    # ... implementation here ...
```

### Pattern 3: Clipping Service (Mixed Access)

```python
def process_job(self, job: Job):
    # Read source from central storage
    source_path = self.fs_cache.get_central_path(job.config['source_path'])

    # Create clip in local cache
    clip_path = self.fs_cache.get_local_path("clips/temp_clip.mp4")
    clip_path.parent.mkdir(parents=True, exist_ok=True)

    # Process locally
    create_clip(source_path, clip_path, start=10.0, end=20.0)

    # Copy to central storage for persistence
    central_clip_path = self.fs_cache.get_central_path(f"clips/{job.ytid}_clip.mp4")
    shutil.copy2(clip_path, central_clip_path)

    # Register asset
    relative_path = central_clip_path.relative_to(self.fs_cache.central_storage_root)
    self.fs_cache.register_asset(
        video_repo=self.video_repo,
        relative_path=str(relative_path),
        ytid=job.ytid,
        kind="clip"
    )

    # Cleanup local temp file
    self.fs_cache.cleanup_local("clips/temp_clip.mp4")
```

### Pattern 4: Stitching Service (concat)

```python
def process_job(self, job: Job):
    local_inputs = [self.fs_cache.pull_to_cache(path) for path in job.config["input_assets"]]
    manifest = self.fs_cache.prepare_local_path(f"stitch/manifests/{job.job_id}.txt")
    manifest.write_text("\n".join(f"file '{p}'" for p in local_inputs))
    output_local = self.fs_cache.prepare_local_path(f"stitch/tmp/{job.job_id}.mp4")
    run_ffmpeg_concat(manifest, output_local)
    self.fs_cache.persist_local_artifact(output_local, job.config["output_relative_path"], video_repo, ytid, "stitched")
```

### Pattern 5: Analysis Service (read transcript, write report)

```python
def process_job(self, job: Job):
    transcript = transcript_repo.get(job.ytid, job.config["transcript_kind"])
    transcript_path = self.fs_cache.pull_to_cache(transcript.path)
    summary = analyze(transcript_path.read_text())
    local_report = self.fs_cache.prepare_local_path(f"analysis/tmp/{job.job_id}.json")
    local_report.write_text(summary_json, encoding="utf-8")
    self.fs_cache.persist_local_artifact(local_report, f"analysis/{job.ytid}/{job.job_id}.json", video_repo, job.ytid, "analysis")
```

---

## File Naming Conventions

### Media Files (raw/)
```
{ytid}__{upload_date} - {title}.{ext}
Example: eKDI2rxQ-fA__20241009 - Video Title.mp4
```

### Transcripts (transcripts/)
```
{ytid}_{kind}.{ext}
Example: eKDI2rxQ-fA_words_whisper_medium.vtt
```

### Clips (clips/)
```
{ytid}_{label}_{start}-{end}.{ext}
Example: eKDI2rxQ-fA_highlight_123.45-145.67.mp4
```

### Stitched Videos (stitch/)
```
{custom-output-name}.mp4
Example: highlight_showcase.mp4
```

### Analysis Reports (analysis/)
```
{ytid}/{job_id}_{model}.json
Example: analysis/eKDI2rxQ-fA/job_abc_llama3.json
```

---

## Asset Kinds

Common asset `kind` values:
- `media` - Original downloaded video
- `transcript` - VTT, SRT, or TSV transcript
- `clip` - Extracted video clip
- `analysis` - Analysis output (JSON, etc.)
- `diarization` - Speaker diarization output
- `subtitle` - Generated subtitle file
- `stitched` - Final stitched compilations

---

## Best Practices

1. **Always use relative paths for asset registration**
   ```python
   # Good
   relative_path = file.relative_to(fs_cache.central_storage_root)

   # Bad
   relative_path = str(file)  # Absolute path, not portable
   ```

2. **Pull to local cache for intensive processing**
   ```python
   # Good for transcription (CPU/GPU intensive)
   media_path = fs_cache.get_media_path(video, pull_to_local=True)

   # Acceptable for quick operations (metadata extraction)
   media_path = fs_cache.get_media_path(video, pull_to_local=False)
   ```

3. **Register all persistent outputs as assets**
   ```python
   # Any file that should be tracked in the database
   fs_cache.register_asset(...)
   ```

4. **Cleanup temporary files**
   ```python
   # After processing is complete
   fs_cache.cleanup_local("temp/file.mp4")
   ```

5. **Use storage manager methods, not direct Path operations**
   ```python
   # Good
   path = fs_cache.get_central_path("raw/video.mp4")

   # Bad
   path = Path(config.paths.central_storage_root) / "raw" / "video.mp4"
   ```

---

## Remote Worker Considerations

For remote workers accessing central storage over a network:

1. **Pull files to local cache first** for better performance
2. **Write outputs to local cache**, then copy to central storage
3. **Use cleanup_local()** to free space after job completion
4. **Handle network failures** when accessing central storage

Example:
```python
try:
    # Try to pull file to local cache
    local_path = fs_cache.pull_to_cache(relative_path)
except FileNotFoundError:
    # Handle missing file
    logger.error(f"File not found in central storage: {relative_path}")
    raise
```

---

## Future Enhancements

Planned features (not yet implemented):
- Asset database query methods (find assets by ytid, kind, etc.)
- Automatic cache expiry/cleanup policies
- Transfer progress tracking for large files
- Checksum validation for cache integrity
- S3/cloud storage backend support

---

## See Also

- `vidops/dal/cache.py` - FilesystemCache implementation
- `vidops/services/download.py` - Example usage in DownloadService
- `config.yaml` - Storage path configuration
