# VidOps Changelog & Agent Pickup Notes

## Session: 2026-05-06 11:52 PDT — root-dir cleanup

### What was done this session

Goal: declutter the repo root by relocating files that don't belong there and verifying nothing breaks.

#### Deprecated to `docs/deprecated_root/` (7 files)
Inspected each root-dir file against grep + loader paths to confirm zero live callers.

- `6SJYw-wVXio_labels.txt~` — editor backup file (`*~`).
- `hasan_piker_poptrigger_ytids.txt` — orphan ytid list, no code references.
- `test_distributed_cli.py` — top-level test script not picked up by pytest (lives outside `tests/`); only mentioned in `docs/archive/`.
- `FINAL_METRICS_TEST.sh`, `TEST_METRICS_INTEGRATION.sh` — one-off shell smoke scripts, zero references.
- `vidops.code-workspace` — trivial editor workspace file.
- `workspace-completion.bash` — bash completion for `download`/`clips`/`voice`/`diarize`/`transcribe`/`stitch`/`dl-subs`/`dates` subcommands that were already removed from `workspace.sh`.

Tracked files moved via `git mv` (history preserved); the untracked backup with plain `mv`.

#### Monitoring compose file → `web/monitoring/`
- `git mv docker-compose.monitoring.yml web/monitoring/docker-compose.monitoring.yml`.
- Rewrote internal volume bind paths from `./config/...` to `../../config/...` (compose v2 resolves relative paths against the compose file's directory, not CWD).
- Updated comment header at top of the file with the new invocation path.
- Updated active references in `scripts/run_webui.py:13` (docstring example), `docs/MONITORING_QUICK_REFERENCE.md` (11 sites incl. command lines + table entry), and `docs/CLI_COMMANDS.md:27,122`.
- Left `docs/archive/*` historical mentions untouched.

#### Deprecated `db.cfg` (root-level legacy DB-creds file)
- Found that `db.cfg` was read by exactly one script: `scripts/diarization/run_resemblyzer_diarization.py:load_db_config()`. Rest of repo gets DB creds from `configuration.load_config().database` (i.e. `config.yaml` `database:` block).
- Refactored `load_db_config()` to load via `configuration.load_config()` and return a dict in the legacy `db_*` key shape so the call site below stays unchanged. Added repo-root sys.path bootstrap inside the function so the script keeps working as a standalone CLI.
- Dropped the 8-path candidate list at the call site (now `db_config = load_db_config()` with no args).
- Updated argparse `--db-*` help strings to reference `config.yaml database.*` instead of `db.cfg/db.local.json`.
- Updated `AGENTS.md:42`, `docs/JOB_CLEANUP.md:8,73-74`, `docs/WINDOWS_SETUP.md:10`.
- Moved `db.cfg` to `docs/deprecated_root/`.

**Behavior change to flag:** `db.cfg` had `db_path_prefix: "/mnt/mainroot"`; `config.yaml` has `paths.path_prefix: ""`. After this change, the diarization script reads `cfg.paths.path_prefix`. If you actually rely on the `/mnt/mainroot` prefix for DB-stored absolute paths, set it via `paths.path_prefix` in `config.yaml`, or `DB_PATH_PREFIX` env var, or `--db-path-prefix` on the CLI.

#### Moved `config.local.json` → `config/config.local.json`
- Relocated the local-overrides JSON. File is gitignored (the `.gitignore` pattern `config.local.json` matches at any depth, so still ignored under `config/`).
- Updated the loader at `scripts/analysis/config_loader.py`: `CONFIG_CANDIDATES` rewritten to four `config/`-prefixed entries; root-level fallbacks dropped (single-shot migration, no soft-landing).
- Updated docstring search-order list to match.
- String/comment touch-ups: `scripts/analysis/apply_schema.py:3`, `scripts/analysis/analyze_to_db.py:499`, `docs/ARCHITECTURE.md:156`.
- Verified runtime: `load_local_config()` returns `__source__='config/config.local.json'` with the expected keys.

#### Moved `exceptions.py` → `workers/exceptions.py`
- The file defines `WorkerLocalError` + 4 subclasses + `JobDataError` — entirely worker-tied semantics. Two live importers: `workers/general.py` and `utils/local_health.py`.
- `git mv exceptions.py workers/exceptions.py`. Updated header comment.
- `workers/general.py:25` → `from .exceptions import ...` (sibling, relative).
- `utils/local_health.py:15` → `from workers.exceptions import ...` (absolute, outside the package).
- Updated `docs/WORKER_ERROR_HANDLING.md` (5 sites: location header, file-list bullet, prose mention, 3 code-block imports) and `docs/ARCHITECTURE.md:46`.
- Verified: `vo_cli.py --help` runs cleanly (full worker-import chain resolves).
- `JobDataError` is dead code — moved as-is, flagged for separate cleanup.

### Verification across all moves
- `py_compile` passed on every edited Python file.
- Smoke imports / runtime checks passed for the loaders that were rewired (`load_db_config`, `load_local_config`, `load_config`).
- `vo_cli.py --help` confirms the CLI still wires up.
- Final greps confirmed no stale references outside `docs/archive/` and `docs/deprecated_scripts/` (intentionally left as historical record).

### Open items at session end

- **`analysis-distributed-worker.service`** still in repo root. Pending decision on whether the unit is actually deployed on motherbase (README implies workers run manually in tmux, "no auto-restart yet"). If unused, deprecate it together with `scripts/install-systemd-service.sh`.
- **`configuration.py:191-192`** has a stray duplicate `@dataclass` decorator on `DiarizationConfig`. Cosmetic, harmless, removable.
- **`exceptions.py`'s `JobDataError`** is defined but never imported or raised anywhere live. Candidate for removal.

### Files changed this session

| File | Change |
|------|--------|
| `docker-compose.monitoring.yml` | Moved to `web/monitoring/`; volume paths rewritten `./config/...` → `../../config/...` |
| `db.cfg` | Moved to `docs/deprecated_root/` (replaced by `config.yaml database.*`) |
| `config.local.json` | Moved to `config/config.local.json` |
| `exceptions.py` | Moved to `workers/exceptions.py` |
| `6SJYw-wVXio_labels.txt~`, `hasan_piker_poptrigger_ytids.txt`, `test_distributed_cli.py`, `FINAL_METRICS_TEST.sh`, `TEST_METRICS_INTEGRATION.sh`, `vidops.code-workspace`, `workspace-completion.bash` | Moved to `docs/deprecated_root/` |
| `scripts/diarization/run_resemblyzer_diarization.py` | `load_db_config()` rewritten to read from `configuration.load_config()`; argparse help strings updated |
| `scripts/analysis/config_loader.py` | `CONFIG_CANDIDATES` reduced to four `config/`-prefixed entries; docstring updated |
| `scripts/analysis/apply_schema.py` | Docstring path corrected |
| `scripts/analysis/analyze_to_db.py` | Comment path corrected |
| `scripts/run_webui.py` | Docstring example updated to new compose path |
| `workers/general.py` | `from exceptions ...` → `from .exceptions ...` |
| `utils/local_health.py` | `from exceptions ...` → `from workers.exceptions ...` |
| `AGENTS.md` | DB-defaults sentence rewritten; no longer references `db.cfg` |
| `docs/ARCHITECTURE.md` | Per-machine-overrides path updated; `exceptions.py` reference updated |
| `docs/CLI_COMMANDS.md` | Two compose-file references updated |
| `docs/JOB_CLEANUP.md` | Two `db.cfg` references replaced with `config.yaml database.*` |
| `docs/MONITORING_QUICK_REFERENCE.md` | 11 compose-file references updated |
| `docs/WINDOWS_SETUP.md` | DB-config sentence updated |
| `docs/WORKER_ERROR_HANDLING.md` | 5 sites updated for `exceptions.py` move |

---

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
