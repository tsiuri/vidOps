# Legacy pull.sh Behaviors vs. DB Download Service (Gap List)

Legacy `scripts/utilities/clips_templates/pull.sh` provided the following behaviors that are **not** currently mirrored in the DB-backed Python DownloadService (unless noted as partially added):

- Download archive support (`--download-archive`) to skip already-downloaded entries.
- Cookie discovery and cookies-first mode (browser order: firefox/chrome/chromium/edge/brave) with multiple client profiles (`android, ios, web_safari, web, web_embedded, default`).
- Pacing controls (`YT_PULL_SLEEP_REQUESTS`, `YT_PULL_SLEEP_INTERVAL`, `YT_PULL_MAX_SLEEP_INTERVAL`, retries, fragment/extractor retries, concurrent fragment tuning).
- Flat playlist discovery + fallback to direct fetch when discovery fails.
- Completeness checks (video/transcript/info) to skip work; scan existing media extensions in `pull/` before downloading.
- Exec hook to mark success per id (`scripts/utilities/mark_success.sh`) and per-run pull logs.
- Auto-sub download (`--write-auto-subs --sub-langs en`) and subtitle rename to `.transcript.en.vtt`.
- Tracking videos without transcripts (`logs/no_transcripts_available.txt`) and post-run detection of missing transcripts.
- Failed URL reporting (requested vs succeeded; failed_urls/failed_tsv outputs).
- Archive sync placeholder (not used now).

Items partially present in Python download:
- Info JSON writing is present.
- Audio-only defaults (opus) and metadata embedding can be configured via `download` config (applied at enqueue).
- Configurable yt-dlp args now exist in `download` config, but the above behaviors (archive, cookies-first, completeness checks, autosubs, mark_success hooks, no-transcript tracking, pacing knobs) are only partially implemented and need parity work.
