# Reference Handling Flows (Current Collision)

Two independent reference build/use paths are colliding in the diarization flow. They need to be unified or explicitly ordered to avoid double prompts and mismatched paths.

## Path 1: CLI/Service Shared Reference (DB enqueue)
- Entry: `vo_cli diarize enqueue-file` (pyannote path).
- Behavior: If `--reference <name>` is provided and build is requested, it calls `service.build_shared_reference(...)` before enqueuing jobs.
- Output location: `generated/diary_reference/<name>/` in central storage (via FilesystemCache).
- Prompts: One prompt at CLI to build if missing; then auto-builds non-interactively (BATCH_DIARIZE_SKIP_PROMPT set during build).
- Job config: Stores `reference_name` only; jobs expect reference to exist at `data/references/<name>` (pyannote worker path).

## Path 2: Worker-time Reference Build (pyannote worker)
- Entry: `DiarizationService.enqueue_diarization_job` → `_ensure_reference_at_enqueue`.
- Behavior: If the reference does not exist at `data/references/<name>`, it will:
  - If interactive and allowed: prompt once to build from media/words.
  - If non-interactive and build_reference is false: fail.
  - If build_reference is true: build non-interactively.
- Build implementation: `ReferenceBuilder.build/build_shared` (uses BATCH_DIARIZE_SKIP_PROMPT to suppress UI).
- Output location: Copies to `data/references/<name>/` in central storage (and builds under workspace then copies).
- Prompts: Can still occur if build_reference is false and stdin is TTY; suppressed in worker mode via BATCH_DIARIZE_SKIP_PROMPT.

## Collision Symptoms
- CLI builds a shared reference into `generated/diary_reference/<name>/`, but worker expects `data/references/<name>/`. After CLI build, the worker still thinks the reference is missing and prompts/fails.
- Dual prompting: one at CLI (shared build) and another at worker if the reference isn’t found where it expects.

## Fix Plan
1) **Single source of truth path**: Decide on `data/references/<name>/` as the authoritative location for both CLI builds and worker reads/writes.
2) **Unify build logic**:
   - CLI shared build writes directly to `data/references/<name>/` (use FilesystemCache to central storage) and sets `build_reference=False` in job config.
   - Worker build only triggers if `build_reference=True` AND the reference is missing; otherwise it must not prompt.
3) **Disable legacy path**: Remove or gate the old `generated/diary_reference/<name>/` output; ensure all copy/build steps target `data/references/<name>/`.
4) **Prompt policy**:
   - CLI: one prompt max; after build, mark reference as present.
   - Worker: no prompts in non-interactive mode; fail fast if build not requested and reference missing.
5) **Config/paths audit**: Align config defaults and service code to `data/references`, and ensure `batch_diarize.py` uses the same path without prompting when BATCH_DIARIZE_SKIP_PROMPT is set.

## Immediate Patch Steps
- Change `build_shared_reference` to write into `data/references/<name>/` and return that rel path.
- After CLI build, set `build_reference=False` when enqueuing jobs so the worker doesn’t try again.
- In `_ensure_reference_at_enqueue`, if the reference exists at `data/references/<name>/`, skip any prompts/builds.

## Acceptance Criteria
- Running `vo_cli diarize enqueue-file … --reference <name>` prompts at most once (CLI). After building, all jobs enqueue without further prompts.
- Worker finds `data/references/<name>/` and never prompts in worker mode.
- No references written to `generated/diary_reference/` remain in the pyannote flow (unless explicitly migrated).
