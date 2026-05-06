# Phase 4 – Testing & Validation (2025-12-10)

The integration is not production-ready yet, but the full db-and-analysis test harness now lives inside this repository. All commands below assume you are in `/home/billie/tools/vidops` with access to a PostgreSQL database that already contains the `videos` table and valid ytids.

## Test Matrix
| Test | Location | Purpose |
| ---- | -------- | ------- |
| Repository operations | `tests/analysis_system/test_distributed_analysis.py` | Creates synthetic chunks, inserts tasks, and exercises claim/complete/progress logic |
| Worker integration | `tests/analysis_system/test_phase4_worker.py` | Spins up the in-repo `AnalysisWorker` and ensures aggregation flows to `analysis_results` |
| Config/pipeline coverage | `tests/analysis_system/test_analysis_system.py` | Runs the default config through multiple pass combinations |
| Pass behavior toggles | `tests/analysis_system/test_pipeline_behavior.py` | Verifies pass enable/disable + override scenarios |

## Prerequisites
1. Apply the new migrations: `psql -f scripts/db/migrations/005_add_analysis_tables.sql <connection args>`.
2. Provide database credentials via `config/config.local.cfg`, `config.local.json`, or environment variables consumed by `scripts.analysis.config_loader.load_local_config()`.
3. Seed at least one valid `videos.ytid` referenced by the tests (see `tests/analysis_system/test_analysis_system.py` for defaults).

## Running the Suite
```bash
# Repository + worker smoke tests
pytest tests/analysis_system/test_distributed_analysis.py \
       tests/analysis_system/test_phase4_worker.py

# Full pipeline tests (slower, will run LLM passes)
pytest tests/analysis_system/test_analysis_system.py \
       tests/analysis_system/test_pipeline_behavior.py
```

Each test prints detailed progress to STDOUT. Use a non-production database: the harness creates jobs under ids beginning with `test_` and inserts rows into `analysis_tasks`, `analysis_results`, `analysis_configs`, and `drills`.

## Expected Outcomes
- Pending/claimed/completed counts should move monotonically when running the repository test.
- `test_phase4_worker` should create ~5 tasks, mark them completed, and write a single row into `analysis_results`.
- `test_analysis_system` exercises pass toggles—watch for failures indicating a missing pass implementation in `workers/analysis_distributed.py`.
- `test_pipeline_behavior` validates config overrides; failures typically mean the CLI/worker still points at the legacy stub code.

Record pass/fail results and any database artifacts before re-running the tests. Clean up with:
```sql
DELETE FROM analysis_tasks WHERE job_id LIKE 'test_%';
DELETE FROM analysis_results WHERE job_id LIKE 'test_%';
DELETE FROM analysis_configs WHERE id LIKE 'test_%';
DELETE FROM drills WHERE config_id LIKE 'test_%';
```
