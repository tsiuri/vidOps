# Claude Starter — Asset Pipelines & Legacy CLI Parity

Read before working:
1. `SOURCE_OF_TRUTH.md` for current system status.
2. `AGENT_ROLES.md` for scopes (Codex/docs, Gemini/transcription, Atlas/tests).
3. Your last log (`logs/changelog/2025-11-30_claude_overlord.txt`) so you stay consistent with prior decisions.
4. **Legacy bridge rule:** All work must honor the DB→legacy→DB path—read `jobs.config`, recreate legacy inputs, run the legacy `workspace.sh` command, rely on a minimal completion signal, then ingest outputs into the DB and push artifacts to central storage before marking the job complete. Do not introduce new server-side logic paths.

## Tasks (exclusive to you)

### 1. Output Asset Write-Back (non-transcription services)
- Extend `FilesystemCache` usage so services beyond download register their outputs in central storage:
  - Wire `ClippingService`, `StitchingService`, and `AnalysisService` to write finished artifacts under `central_storage_root` (e.g., `clips/`, `stitch/`, `analysis/`) and register them in the `assets` table.
  - Ensure CLI commands (`vo clips`, `vo worker start clipping`, `vo worker start stitching`, etc.) still function with the new storage paths.
  - Do **not** modify `vidops/services/transcription.py` (Gemini’s territory); leave TODO notes if you need alignment.
- Update `docs/STORAGE_INTERFACE.md` with the new directories/asset kinds plus any helper methods you add.

### 2. Legacy CLI Parity (clips hits/cut + stitch)
- Bring the `vo clips` and `vo stitch` commands closer to `workspace.sh` parity:
  - Implement a `clips hits` query path that reads from the `words` table via DAL (no shell scripts).
  - Ensure `clip enqueue` stores enough metadata for workers to pull inputs from storage and emit outputs registered above.
  - Flesh out `stitch` commands to accept clip lists from the database or stored manifests.
- Update quick-reference docs (`START_HERE.md`, `QUICK_REFERENCE.md`) with any new flags/flows introduced.

## Deliverables & Logging
- Log all work in `logs/changelog/2025-12-01_claude_asset_cli.txt` (summary, commands, files touched, schema/doc updates).
- Update `SOURCE_OF_TRUTH.md` once both tasks are complete, noting the new asset pipeline + CLI parity progress.

## Rules
- Coordinate with Gemini if you need to alter transcription internals; otherwise leave TODOs.
- Tests touching DAL/storage are Atlas’s area; you may add targeted unit tests for new logic but avoid editing his suites without a note.
- Keep `.venv_cli/` or similar environments out of git (add to `.gitignore` if needed) unless the user requests otherwise.
