# VidOps Project Status – Distributed Analysis Migration

**Last Updated:** 2025-12-10
**State:** Migration in progress (functional code moved, integration/tests still pending)

## Highlights
- Renamed `config.py` → `configuration.py` and updated every import to resolve the module/directory collision that blocked pytest.
- Relocated the entire analysis system (configs, pipelines, worker, scheduler, drills, hot targets) under `scripts/analysis/` and deleted the stale `analysis/` copy.
- Introduced `scripts/__init__.py` so anything can import `scripts.analysis.*` without manual `sys.path` surgery.
- Ported the Postgres schema (`analysis_configs`, `drills`, `analysis_tasks`, `analysis_results`) into `scripts/db/migrations/005_add_analysis_tables.sql`.
- Copied the standalone test harnesses (`test_distributed_analysis.py`, `test_phase4_worker.py`, `test_analysis_system.py`, `test_pipeline_behavior.py`) into `tests/analysis_system/`.
- Added `analyze enqueue-distributed` CLI command so operators can chunk transcripts and enqueue distributed jobs without leaving VidOps.

## What’s Left
1. **Hook up the worker:** Replace the stub passes in `workers/analysis_distributed.py` with the real implementations from `scripts/analysis/analysis_worker.py` (sentiment, categories, subchunks, drills, hot targets, persistence).
2. **Expose job creation:** Provide a CLI/service command that calls `scripts.analysis.analyze_to_db.create_analysis_job()` so that real videos can be queued from VidOps.
3. **Run and document tests:** Update `PHASE_4_TESTING_GUIDE.md`/`PHASE_4_TEST_RESULTS.md` to describe running the ported tests locally, and execute them once database access is available.
4. **Doc sweep:** The old “Phase Complete” files described a finished project; they now need to outline the incremental plan (this file + `MERGE_COMPLETION_INDEX.md` are the source of truth until that happens).
5. **Operational hardening:** Once the code works end-to-end, refresh the systemd template, CLI help text, and monitoring docs to match the new module paths.

## Quick Reference
| Area | New Location |
| ---- | ------------ |
| Analysis configs + helpers | `scripts/analysis/*.py` |
| Worker CLI entry | `cli/worker.py` (`analysis-distributed` command) |
| Worker implementation | `workers/analysis_distributed.py` |
| Migrations | `scripts/db/migrations/005_add_analysis_tables.sql` |
| Tests | `tests/analysis_system/` |
| Config loader | `scripts/analysis/config_loader.py`, `configuration.py` |

## Open Questions
- Which environment will host the database used for local tests? (`load_local_config()` still looks for `config.local.*` files.)
- Do we keep the lightweight `legacy_simple_analyze.py` script, or should `workspace.sh analyze` call the new pipeline directly?
- What is the desired deployment story for the Flask analysis browser now that its dependencies live under `scripts/analysis`?

Until those answers arrive, continue treating the migrated code as experimental. All future work should land under `scripts/analysis/` (for implementation) or `tests/analysis_system/` (for validation).
