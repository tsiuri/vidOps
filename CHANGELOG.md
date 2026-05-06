# VidOps Changelog & Agent Pickup Notes

## Session: 2026-05-01

### What was done this session

#### Infrastructure fixes
- **Python venv** was broken (system upgraded Python 3.13→3.14, venv symlinks pointed at wrong version). Fixed by installing `python313` from AUR and relinking `.venv/bin/python3 → /usr/bin/python3.13`.
- **yt-dlp** was outdated (2025.11.12 → 2026.3.17), causing HTTP 403 failures on downloads. Updated via pip.
- **PostgreSQL collation mismatch** warning fixed: `ALTER DATABASE transcripts REFRESH COLLATION VERSION;`
- **Storage broker** was enabled in `config.yaml` but the broker service is not deployed and `/etc/vidops/certs/broker-ca.pem` does not exist. This caused harmless `[Errno 2]` warnings on every asset persist. Fixed by setting `storage_broker.enabled: false` in `config.yaml`. Direct copy fallback to `/mnt/13tb_sas/vidops/storage/` continues to work.

#### Workers
- Download workers were not running. Restarted 3 parallel download workers in tmux session `vidops-workers`, window `downloads` (3 panes).
- GPU analysis workers also running in `vidops-workers:gpu0-3060` and `vidops-workers:gpu1-3090-`.
- Attach with: `tmux attach -t vidops-workers`
- Cleared ~120 queued download jobs; 95 completed, a few failed (see below).

#### Download dedup
- Added `find_active_download(url)` to `services/download.py` — checks for an active/pending job with the same URL before enqueuing a new one.
- `enqueue_download()` now accepts `allow_duplicate=False` param; returns the existing job if found.
- CLI `download enqueue` passes `allow_duplicate=force`.
- `pipeline.py` also updated.

#### Concurrent UniqueViolation fix (`dal/videos.py`)
- Two workers racing on the same channel URL could both INSERT simultaneously. The `ON CONFLICT (ytid)` clause doesn't protect the separate `url` unique constraint.
- Fixed: wrapped the INSERT in try/except for `psycopg2.errors.UniqueViolation`, falls back to a plain `UPDATE WHERE ytid=%(ytid)s OR url=%(url)s`.

#### Web UI — Video overview page (`/video/<ytid>`)
- Replaced the old analysis-only view with a full overview page (`web/templates/video_overview.html`).
- Shows: video metadata, HTML5 media player, jobs table with color-coded badges and expandable error details, retry buttons for failed/cancelled jobs, links to analysis.
- Old analysis page moved to `/video/<ytid>/analysis`, accessible via breadcrumb.
- Videos list (`/videos`) now links ytid and title to `/video/<ytid>`.

#### Video streaming (`/video/<ytid>/stream`)
- Added Flask route that resolves the asset path (tries absolute first, falls back to `FilesystemCache.pull_to_cache()`), then serves with `send_file(conditional=True)` for range request / seek support.

#### Retry button
- `POST /api/job/<job_id>/retry` — resets a failed/cancelled job to pending (clears error, claimed_by, claimed_at).
- Retry buttons on jobs page (`jobs_browser.html`) and video overview page.

#### AV1 playback problem & download solution
- Videos downloaded from YouTube arrive as AV1. NVDEC on the RTX 3060 Ti (192.168.0.100) cannot decode AV1 properly (black in VLC, glitchy in mpv). Software decode (libdav1d/ffmpeg) is fine.
- **Policy decision**: store AV1 as-is (best quality, smallest size). Transcode to H.264 on demand at download time.
- Two download links in the UI:
  - **⬇ Original (AV1)** — `GET /video/<ytid>/stream` with `download` attribute, serves the stored file directly.
  - **⬇ Download H.264** — triggers SSE progress flow then serves transcoded file.

#### On-demand H.264 transcode with SSE progress bar
- `GET /video/<ytid>/transcode-progress` — SSE endpoint. Resolves asset, checks codec (skips transcode if already H.264), runs `ffmpeg -progress pipe:1` to a temp file `/tmp/vidops_h264_<ytid>.mp4`, streams `{pct, elapsed, total, frame}` events ~1/sec.
- `GET /video/<ytid>/download` — serves the pre-transcoded temp file as an attachment.
- Per-ytid threading lock (`_transcode_locks`) prevents multiple simultaneous transcodes of the same file (discovered the hard way: 11 concurrent ffmpeg processes eating all RAM).
- UI: clicking "Download H.264" shows a progress bar + `"Transcoding… 0.1% (0:04 / 67:12)"` label; auto-triggers download on completion.
- ffmpeg args: `-c:v libx264 -crf 18 -preset fast -c:a aac -b:a 192k -movflags +faststart`

### Known issues / pending work

- **Secondary machine (192.168.0.180)** still has the broken Python 3.14 venv — not fixed this session. Same fix applies: install `python313` from AUR, relink venv.
- **Diarization jobs**: ~2,158 pending, many failing with `reference.json missing`. Root cause not investigated this session.
- **Analysis failures**:
  - `HotTargetRunner() takes no arguments` — code bug in analysis service, not fixed.
  - `current transaction is aborted` — DB transaction not rolled back on error somewhere upstream.
- **Model profile 22** references `gpt-oss:latest` which doesn't exist — stale config, should be cleaned up.
- **Transcode temp files** (`/tmp/vidops_h264_<ytid>.mp4`) are never cleaned up automatically. They'll grow over time. Consider a TTL cleanup job or deleting on next request.
- **`--no-transcode` CLI flag** was added to `cli/download.py` (stores raw AV1 without transcoding) but was not tested end-to-end.
- **sse-test route** (`/sse-test`) left in `web/web_app.py` — debugging artifact, remove it.

### Files changed this session

| File | Change |
|------|--------|
| `config.yaml` | `storage_broker.enabled: false` |
| `configuration.py` | Added `transcode_to_h264: bool = False` to `DownloadConfig` (unused, can remove) |
| `dal/videos.py` | UniqueViolation catch + UPDATE fallback in `upsert()` |
| `services/download.py` | Dedup guard, `allow_duplicate` param, `_transcode_to_h264` helper (unused — remove), `subprocess` import |
| `cli/download.py` | `--no-transcode` flag, `allow_duplicate=force` |
| `cli/pipeline.py` | `allow_duplicate=force` |
| `web/web_app.py` | `/video/<ytid>` overview, `/video/<ytid>/analysis`, `/video/<ytid>/stream`, `/video/<ytid>/transcode-progress`, `/video/<ytid>/download`, `/api/video/<ytid>/overview`, `/api/job/<job_id>/retry`, transcode lock, `_resolve_media_path()`, `/sse-test` (remove this) |
| `web/templates/video_overview.html` | New file — full video overview page |
| `web/templates/video_detail.html` | Added breadcrumb |
| `web/templates/videos.html` | ytid links to `/video/<ytid>` |
| `web/templates/jobs_browser.html` | Error labels, expandable error details, retry buttons |

### Cleanup to do next session

1. Remove `/sse-test` route from `web/web_app.py`
2. Remove `_transcode_to_h264()` function and `transcode_to_h264` config field from `configuration.py` — these were added and then reversed; dead code now
3. Fix 192.168.0.180 venv
4. Investigate diarization `reference.json missing` failures
5. Temp file cleanup for `/tmp/vidops_h264_*.mp4`
