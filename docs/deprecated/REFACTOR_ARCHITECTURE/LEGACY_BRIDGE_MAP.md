# Legacy Bridge Map — workspace.sh Commands

Canonical map of each legacy `workspace.sh` command to the DB→legacy→DB flow. Use these as the implementation targets for worker parity.

Pattern for all entries:
- Inputs: Rebuild legacy inputs (TSV/manifest/env/paths) under the legacy locations (`pull/`, `results/`, `media/...`, `logs/...`, `tmp/`).
- Execution: Invoke the legacy command with `jobs.config` args.
- Completion: Rely on the legacy completion marker; do not invent new logic.
- Ingestion: Treat outputs as cache; push to central storage, register assets, and write DB rows.

## download
- Legacy path: `workspace.sh download` → `clips.sh pull`.
- Inputs: URLs/args from `jobs.config` → legacy pacing/cookie flags.
- Outputs: Media + sidecars in `pull/`; upload to storage (broker/direct), register `media` asset, update job result.
- Worker: Download worker follows this path today; use as reference.

## clips (hits / cut-local / cut-net / refine)
- Legacy path: `workspace.sh clips ...` → `clips.sh` subcommands.
- Inputs: Rebuild hits/cut TSVs/manifests under `results/` and `media/clips/`.
- Outputs: TSVs, raw/refined clips under `media/clips/...`; register `clip` assets; push to storage.
- Worker: TODO align to download/transcription pattern. Hits search is DAL-backed already (`vo clips hits`). The cut worker must: pull media via `FilesystemCache` to legacy `pull/`, regenerate the TSV manifest under `results/`, run the legacy `clips.sh cut-…` subcommand, then register/push outputs.

## dl-subs
- Legacy path: `workspace.sh dl-subs ...` → `clips.sh transcripts`.
- Inputs: URL lists/ytids reconstructed into `pull/<job>_urls.txt`, format flags forwarded; ensure `pull/` present.
- Outputs: subtitle files in `pull/`; register transcript/subtitle assets with `rel_path`; push to central storage via FilesystemCache.
- Worker: Implemented (`job_type=dl_subs` via `vo dl-subs enqueue`, handled by SubtitleWorker/GenericWorker).

## voice
- Legacy path: `workspace.sh voice ...` → scripts under `scripts/voice_filtering/`.
- Inputs: Target clips pulled from storage to `media/clips/voice/<job_id>`; reference clips staged under `generated/voice_reference/<ytid>` via FilesystemCache; threshold/method preserved from `jobs.config`.
- Outputs: `voice_analysis.json` + `hasan_clips.txt` under `results/voice_filter/<ytid>/`; register `voice_match` assets with `rel_path`, include match counts and paths in `job.result`.
- Worker: ✅ bridged (`vo voice enqueue` → VoiceFilterService/VoiceFilterWorker or GenericWorker); shells into `workspace.sh voice filter-{chunked|parallel|simple}` with staged inputs.

## diarize
- Legacy path: `workspace.sh diarize ...` → `scripts/diarization/run_resemblyzer_diarization.py`.
- Inputs: Media pulled to `pull/`, words TSV staged under `generated/diarization_inputs/<job_id>`, reference dir reconstructed at `generated/diary_reference/<ytid>/`; device/chunk/threshold params captured in `jobs.config`.
- Outputs: `generated/diarization_resemblyzer/<ytid>/diarized_timestamps.tsv`, `speaker_words.tsv`, `diarization.json`; register `diarization` assets with `rel_path`, record segment/word counts + metadata in `job.result`.
- Worker: ✅ bridged (`vo diarize enqueue` → DiarizationService/DiarizeWorker or GenericWorker); shells into `workspace.sh diarize --ytid ... --audio ... --words ...` with staged inputs.

## transcribe
- Legacy path: `workspace.sh transcribe ...` → `scripts/transcription/dual_gpu_transcribe.sh`.
- Inputs: Media in `pull/`; filelist in `tmp/`; model/language from `jobs.config`.
- Outputs: VTT + words TSV in `generated/`; register transcript assets; ingest words/transcripts.
- Worker: Implemented as legacy-bridge reference.

## analyze
- Legacy path: `workspace.sh analyze ...` → `scripts/analysis/analyze_transcript.py`.
- Inputs: transcripts/words staged in legacy `generated/`; job.config carries transcript kind/path.
- Outputs: analysis JSON/TSV; register `analysis` assets with `rel_path`; ingest into DB as needed.
- Worker: ✅ bridged. AnalysisService copies transcripts to legacy paths, invokes workspace.sh, registers `analysis/<ytid>/<job_id>_<model>.json`, fails loudly on empty output, and stores stdout/stderr tails in `job.result`.

## convert-captions
- Legacy path: `workspace.sh convert-captions`.
- Inputs: captions staged in legacy locations (pull/) from storage/cache.
- Outputs: words TSV in `generated/`; register subtitle + words assets (with `rel_path`); ingest words + transcript rows.
- Worker: Implemented (`job_type=convert_captions` via `vo convert-captions enqueue`, handled by SubtitleWorker/GenericWorker).

## stitch
- Legacy path: `workspace.sh stitch ...` → `scripts/video_processing/stitch_*`.
- Inputs: manifests/lists rebuilt under legacy `media/clips/`; clip assets pulled from storage/cache.
- Outputs: stitched media under `media/final/` (persisted to `storage/stitch/`); register `stitched` asset with `rel_path`.
- Worker: ✅ bridged. StitchingService stages clip assets, calls `workspace.sh stitch <method>`, captures stdout/stderr tails, registers stitched output, and fails if the legacy script produces no file.

## dates
- Legacy path: `workspace.sh dates ...` → date management helpers.
- Inputs/Outputs: manifests/lists treated as cache; register `dates_manifest` assets when files are produced.
- Worker: ✅ bridged via `vo dates enqueue`; workers rebuild date lists under `data/`, run the legacy helper, push manifests to storage with `rel_path`, and include stdout/stderr tails + empty-output flags in `job.result`.

## extra-utils
- Legacy path: `workspace.sh extra-utils ...`.
- Inputs/Outputs: wrap only if explicitly queued; otherwise local-only.
- Worker: ✅ bridged per-tool when queued. Inputs materialized under legacy paths, legacy tool invoked, outputs registered as `utility_output` with `rel_path`, and `job.result` records stdout/stderr/empty-output states.

## gpu/dbupdate/info/help
- Operator/local-only today; if queued later, must still follow the same bridge pattern.
