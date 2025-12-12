# Download Configuration — Job Config JSON + Local Enqueue Config

## Objective

Match the robustness of `workspace.sh download` (see `scripts/utilities/clips_templates/pull.sh` for pacing/cookies logic) while keeping the queue/worker architecture simple. Every download job carries its yt-dlp arguments directly inside `jobs.config`, so retries remain deterministic and auditable without extra tables. A repo-local config file (`config/download-enqueue-config.yaml`) drives how the CLI fills those JSON fields.

## Job Config Schema

When `vo_cli.py download enqueue` runs (playlist URLs explode into one job per video), the inserted job contains:

```json
{
  "url": "https://www.youtube.com/watch?v=VIDEO",
  "ytid": "VIDEO",
  "playlist_id": "PLAYLIST123",
  "playlist_title": "Sample Playlist",
  "enqueue_batch_id": "batch_20241205T101500Z",
  "yt_retries": 15,
  "yt_frag_retries": 20,
  "yt_extractor_retries": 10,
  "sleep_requests": 0.35,
  "sleep_interval": 0.35,
  "sleep_max": 1.5,
  "pull_sleep_requests": 0.5,
  "pull_sleep_interval": 0.5,
  "pull_sleep_max": 2.0,
  "concurrent_fragments": 1,
  "cookies_first": false,
  "cookie_browser_order": "firefox,chrome,chromium,edge,brave",
  "download_archive": true,
  "remove_from_archive": true,
  "embed_metadata": true,
  "write_info_json": true,
  "write_auto_subs": true,
  "sub_langs": "en",
  "audio_format": "opus",
  "audio_quality": "0",
  "extractor_args": "youtube:player_client=android,web_embedded,default,-tv",
  "yt_exec_template": "bash scripts/utilities/mark_success.sh '%(id)s' '{log_prefix}'",
  "output_template": "pull/%(id)s__%(upload_date>%Y-%m-%d)s - %(title).120B.%(ext)s"
}
```

Workers read `job.config` and build the yt-dlp command verbatim. If mandatory keys are missing or malformed, the worker fails the job immediately with a clear error so operators can re-enqueue. There is no fallback to global defaults beyond what the CLI writes.

## Local `download-enqueue-config`

`config/download-enqueue-config.yaml` tells the CLI how to populate `job.config`:

```yaml
defaults:
  force: false
  cookies_first: false
  log_prefix_dir: logs/pull
  pacing:
    sleep_requests: 0.35
    sleep_interval: 0.35
    sleep_max: 1.5

from_list:
  cookies_first: true
  log_prefix_dir: logs/pull

retry_failed:
  force: true
  cookies_first: true

per_channel_overrides:
  UC1234567890:
    force: true
    cookies_first: true
```

- CLI merges the appropriate block (defaults + scenario + per-channel) into the JSON.
- Derived fields map to yt-dlp flags (e.g., `force: true` ⇒ `download_archive: false`).
- Rate-limiting values (`sleep_*`, retries, cookies-first toggles) come straight from this file so enqueue-time playlist resolution uses the same pacing as worker-time downloads.

## Batch Handling & Rate Limiting

- **Playlist explosion:** resolve once, write one job per video with identical pacing values in `job.config`.
- **Batch inserts:** enqueue operations must use `executemany`/COPY so thousands of jobs land in a single transaction instead of thousands of individual inserts.
- **Batch IDs:** every enqueue command assigns an `enqueue_batch_id`. Add CLI helpers:
  - `python3 vo_cli.py download status-batch <batch_id>`
  - `python3 vo_cli.py download cancel-batch <batch_id>`
  These commands (documented in START_HERE / QUICK_REFERENCE / OPERATIONS_CHECKLIST) let operators inspect or abort huge batches without SQL.
- **Rate limiting:** playlist metadata fetches and the actual downloads must honor the legacy timing knobs (`YT_SLEEP_*`, `YT_PULL_*`, retries, cookies-first fallbacks). Store the resolved values in `job.config` so the worker doesn’t guess.

## Worker Behavior

1. Claim job from `jobs`.
2. Read `job.config`; if required keys are missing, fail the job immediately and log a high-severity error.
3. Build yt-dlp arguments from the JSON and run the download by invoking the lightly patched legacy download script. That script must check its existing “success” marker and exit success only when all downloads are finished.
4. After the legacy script reports success, push outputs from local cache to central storage using the existing broker/cert pipeline (sequential is acceptable), register assets, and mark the DB job complete; otherwise, fail fast with logs.
5. Include `enqueue_batch_id` and pacing info in `jobs.result` for auditing.

## Documentation Touchpoints

- `SOURCE_OF_TRUTH.md` references this plan (playlist explosion, config JSON, batch commands, rate limits).
- `START_HERE.md` and `QUICK_REFERENCE.md` highlight that download jobs carry their own yt-dlp settings via `job.config` and mention the batch helper commands.
- `REMOTE_WORKER_BOOTSTRAP.md` reminds operators to copy `config/download-enqueue-config.yaml` if they enqueue jobs from that machine.
- `OPERATIONS_CHECKLIST.md` explains how to monitor/cancel `enqueue_batch_id`s and what to do when a download job fails because of malformed config.

## Summary

- Every download job owns its yt-dlp arguments inside `job.config`.
- The repo-local YAML drives defaults and scenario-specific overrides (force, cookies-first, pacing).
- Batch inserts plus batch-management commands keep large playlist enqueues manageable.
- Workers fail fast if config JSON is missing/invalid, preserving determinism and auditability.
