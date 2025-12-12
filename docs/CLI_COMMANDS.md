# CLI Commands & Entry Points

Structured list of Click commands exposed by `vo_cli.py`, with file references.

## Top-Level Groups (vo_cli.py)
- `status` — `cli/status.py`
- `worker` — `cli/worker.py`
- `download` — `cli/download.py`
- `transcribe` — `cli/transcribe.py`
- `clip` — `cli/clipping.py`
- `clips` — `cli/clips.py`
- `stitch` — `cli/stitch.py`
- `dl_subs` — `cli/dl_subs.py`
- `convert_captions` — `cli/convert_captions.py`
- `analysis` — `cli/analysis.py`
- `diarize` — `cli/diarization.py`
- `voice` — `cli/voice.py`
- `query_ids` — `cli/query_ids.py`
- `dates` — `cli/dates.py`
- `extra_utils` — `cli/extra_utils.py`
- `overlord` — `cli/overlord.py`
- `quickclip` — `cli/quickclip.py`
- Monitoring/ops scripts (non-Click)
  - `scripts/management/watch_jobs.py` — curses TUI to watch `jobs` table (q to quit)
  - Prometheus/Grafana: `docker-compose.monitoring.yml`; metrics emitted from workers via `monitoring/metrics.py` and exposed by `monitoring/exporter.py` (see `docs/MONITORING_QUICK_REFERENCE.md`)
  - Deployment helpers: `scripts/deploy/*.sh` (storage broker certs/nginx, config setup)
  - Analysis TUI: `scripts/analysis/analysis_tui.py` (curses config browser)
  - Web drill helpers: `scripts/web_app/*.py` (drill APIs/views)
  - Job reset: `scripts/utilities/reset_stuck_jobs.py`

## Groups and Subcommands
- `status` (`cli/status.py`)
  - `db`
  - `workers`
  - `jobs`
- `worker` (`cli/worker.py`)
  - `start` (start a worker for a given role/model/caps)
- `download` (`cli/download.py`)
  - `enqueue` (enqueue download job)
- `transcribe` (`cli/transcribe.py`)
  - `enqueue` (enqueue transcription job)
  - `enqueue-pending` (enqueue missing transcripts)
  - `from-download` (transcribe from existing downloads)
- `clip` (`cli/clipping.py`)
  - `enqueue` (enqueue single clip)
- `clips` (`cli/clips.py`)
  - `hits` (search hits and write manifest)
  - `cut` (cut clips from manifest; modes: net/local)
  - `enqueue` (generic clip command enqueue)
- `stitch` (`cli/stitch.py`)
  - `enqueue` (stitch manifest enqueue)
- `dl_subs` (`cli/dl_subs.py`)
  - `enqueue` (subtitle download)
  - `enqueue-from-list` (batch from list)
- `convert_captions` (`cli/convert_captions.py`)
  - `enqueue` (caption conversion)
- `analysis` (`cli/analysis.py`)
  - `enqueue` (legacy single analysis)
  - `enqueue-distributed` (distributed analysis job)
- `diarize` (`cli/diarization.py`)
  - `enqueue` (ytid-based diarization)
  - `enqueue-file` (ad-hoc file diarization)
- `voice` (`cli/voice.py`)
  - `enqueue` (voice filter job)
- `query_ids` (`cli/query_ids.py`)
  - `run` (batch query IDs and write manifests)
- `dates` (`cli/dates.py`)
  - `enqueue` (dates helper job)
- `extra_utils` (`cli/extra_utils.py`)
  - `enqueue` (generic utility runner)
- `overlord` (`cli/overlord.py`)
  - `start-overlord` (start orchestrator)
- `quickclip` (`cli/quickclip.py`)
  - `create`
  - `list`
  - `show`
  - `search`
  - `browse-web`

## Monitoring & Utilities (manual)
- `scripts/management/watch_jobs.py` — Live DB queue TUI; run with `PYTHONPATH=. python scripts/management/watch_jobs.py`
- Metrics: start Prometheus/Grafana via `docker-compose -f docker-compose.monitoring.yml up -d`; workers expose metrics on `--metrics-port` (default 8888) using `monitoring/exporter.py` + `monitoring/metrics.py`.
- Deploy/ops: storage broker & worker trust scripts under `scripts/deploy/` (nginx TLS, cert reissue, broker diagnostics).
- Analysis/UI: `scripts/analysis/analysis_tui.py`, `scripts/web_app/` (drill APIs/views)
- Job maintenance: `scripts/utilities/reset_stuck_jobs.py`
- Smoke harness: `scripts/smoke/run_smoke_suite.sh`
- Broker test tool: `scripts/test_broker.py`

## Domain Utilities & Scripts (non-Click, by area)
- **Diarization (scripts/diarization/ + scripts/examples/)**: `scripts/examples/diarize_batch_best.sh`, `batch_diarize.py`, `diarize_inference.py`, `chunk_audio.py`, `parallel_vad_preprocess.py`, `postprocess_and_map.py`, `validate_diarization.py`, `build_reference.py` (curses picker), `run_resemblyzer_diarization.py`, `combine_chunks.py`, `match_reference.py`, `labels_to_rttm.py`
- **Transcription (scripts/transcription/)**: `transcribe_worker_*` (cpu/nvidia/db), `db_queue.py`, `queue_cli.py`, `recover_stale_jobs.sh`, `fragmented_transcribe.py`, `fragment_runner.py`, `dual_gpu_transcribe.sh`, `batch_retry.sh` / `batch_retry_worker.py`, `detect_dupe_hallu.py`, `watch_cuda_error.sh`
- **Voice Filtering (scripts/voice_filtering/)**: `filter_voice.py`, `filter_voice_parallel*.py`, `voicefil_w_venv.sh`
- **Video processing (scripts/video_processing/)**: `stitch_videos*.sh`, `concat_filter_from_list.sh`
- **DB tools (scripts/db/)**: export/import helpers (`export_transcripts_and_words*.py`, `export_videos_from_info.py`, `export_media_assets.py`, `export_ytids_by_upload_type.sh`, `annotate_upload_type.sh`, `import_videos.sh`, `load_hits.sh`)
- **Dates (scripts/date_management/)**: `extract_and_compare_dates.py`, `move_files_by_date.py`, `create_download_list.py`, `find_missing_dates.py`
- **GPU helpers (scripts/gpu_tools/)**: `gpu-bind-status.sh`, `gpu-to-nvidia.sh`
- **Management/maintenance (scripts/management/)**: `watch_jobs.py`, path fix/test scripts (`set_paths.sh`, `fix_paths.sh`, `reorganize.sh`, `find_path_references.sh`, `test_scripts.sh`, `test_clips_wrapper.sh`)
- **Utilities (scripts/utilities/)**: `clips.sh`, `convert-captions.sh`, `quality_report.py`, `sort_clips.py`, `map_ids_to_files.py`, `monitor_transcription_memory.sh`, `mark_success.sh`, `repair_archive.sh`, `detailed_proc_watch.sh`, `filter_tsv_by_existing_segments.py`, `list_overlaps_and_filter_tsv.py`

- DB schema snapshot: db/schema_dump.sql (pg_dump --schema-only)
