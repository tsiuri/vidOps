# Distributed Analysis Migration Tracker

**Status:** Migration in progress (2025-12-10)
**Scope:** Move the db-and-analysis system into VidOps, normalize imports, and finish the distributed worker stack under `scripts/analysis/`.

## Completed Today
- ✅ Flattened the duplicate `vidops/vidops` package and renamed the root module to `configuration.py` to remove the `config.py` vs `config/` collision.
- ✅ Copied the full analysis system from `/home/billie/tools/db-and-analysis/scripts/analysis_system/` into `scripts/analysis/` (plus subpackages like `llm/`, `scheduler/`, and `persistence/`).
- ✅ Removed the stale `analysis/` package, updated the worker/CLI/web app to import from `scripts.analysis`, and added `scripts/__init__.py` so the new package resolves normally.
- ✅ Ported migrations for `analysis_configs`, `drills`, `analysis_tasks`, and `analysis_results` into `scripts/db/migrations/005_add_analysis_tables.sql`.
- ✅ Brought over the analysis test harnesses into `tests/analysis_system/` so they can be executed from this repo.
- ✅ Added `vo_cli.py analyze enqueue-distributed` to chunk transcripts locally, load configs from `analysis_configs`, and enqueue distributed jobs directly from VidOps.

## Still Outstanding
1. **End-to-end validation:** Wire the new tests into CI/pytest and run them against a development database once credentials are available.
2. **Worker feature parity:** `workers/analysis_distributed.py` still contains placeholder passes (sentiment/categories/subchunks). The full implementations live under `scripts/analysis/` and need to be hooked up.
3. **Job creation path:** Surface `scripts.analysis.analyze_to_db.create_analysis_job()` through the VidOps CLI/service so we can seed tasks without leaving the repo.
4. **Documentation sweep:** Many docs still describe the five phase plan as “complete”. This tracker plus `PROJECT_STATUS_SUMMARY.md` now reflect the real state, but individual guides (Phase docs, monitoring summaries, etc.) still need edits as the code stabilizes.
5. **Operational polish:** Update deployment scripts/systemd templates after the worker changes settle, ensure requirements/pip packages are minimal, and document any new environment variables.

## Current Layout
```
scripts/
  analysis/
    analysis_config.py
    analysis_pipeline.py
    analysis_task*.py
    analyze_to_db.py
    analyze_transcript.py
    ... (llm/, scheduler/, persistence/)
  __init__.py
  ... other tool directories ...
configuration.py
workers/analysis_distributed.py
cli/worker.py
web/web_app.py
web/scripts/web_app/drill_api.py
```

## Tests
| File | Purpose |
| ---- | ------- |
| `tests/analysis_system/test_distributed_analysis.py` | Low-level repository + job lifecycle checks |
| `tests/analysis_system/test_phase4_worker.py` | Smoke test for the in-repo `AnalysisWorker` implementation |
| `tests/analysis_system/test_analysis_system.py` | Configuration + pass-behavior validation |
| `tests/analysis_system/test_pipeline_behavior.py` | Ensures pass enable/disable and overrides behave |

Run them with:
```bash
pytest tests/analysis_system -m "not slow"  # database must contain videos referenced by config loader
```
(Use a throw-away database; the harness inserts and updates `analysis_tasks`/`analysis_results`.)

## References
- Legacy project (read-only): `/home/billie/tools/db-and-analysis`
- Migrations: `scripts/db/migrations/005_add_analysis_tables.sql`
- Config loader + overrides: `scripts/analysis/config_loader.py`, `configuration.py`
- Distributed worker entry point: `workers/analysis_distributed.py`
