# Database Maintenance Guide

Last updated: 2025-11-29

This document explains how to maintain the Overlord job/worker tables, apply migrations, and validate the queue state safely.

## 1. Migrations

The generic queue schema lives under `scripts/db/migrations/002_create_new_job_queue_tables.sql`.

### Apply (forward)
```bash
psql -h <db-host> -d transcripts -U billie \
  -f scripts/db/migrations/002_create_new_job_queue_tables.sql
```
This script:
- Drops legacy `transcribe_jobs` / `transcribe_workers`
- Creates `jobs` and `workers` tables plus helper views/triggers

### Recreate Test Tables
Development tests use `jobs_test` / `workers_test`, which mirror the real schema:
```sql
CREATE TABLE IF NOT EXISTS jobs_test (LIKE jobs INCLUDING ALL);
CREATE TABLE IF NOT EXISTS workers_test (LIKE workers INCLUDING ALL);
TRUNCATE jobs_test;
TRUNCATE workers_test;
```
The DAL tests run these commands automatically (see `tests/dal/conftest.py`).

## 2. Inspecting the Queue

Helpful queries (psql or any SQL client):
```sql
-- Overall status counts
SELECT job_type, status, COUNT(*) FROM jobs GROUP BY job_type, status ORDER BY job_type, status;

-- Most recent jobs of a type
SELECT job_id, status, priority, ytid, created_at
FROM jobs
WHERE job_type = 'transcription'
ORDER BY created_at DESC LIMIT 10;

-- Workers / heartbeats
SELECT worker_id, worker_type, status, last_heartbeat
FROM workers
ORDER BY last_heartbeat DESC;
```

CLI equivalents:
```bash
python3 vo_cli.py status jobs --job-type transcription
python3 vo_cli.py status workers
```

## 3. Resetting the Queue (Test Environments Only)

**Danger:** Only do this on disposable databases.
```sql
TRUNCATE jobs;
TRUNCATE workers;
```
If you need to clear assets/transcripts tied to test runs, coordinate with the team before truncating other tables.

## 4. Health Checks & Validation

Run the targeted DAL/storage tests to ensure the queue + filesystem helpers behave correctly:
```bash
pytest tests/dal/test_jobs_repo.py tests/dal/test_worker_repo.py tests/dal/test_filesystem_cache.py
```

These cover:
- JobRepository CRUD + claim/release logic on the generic `jobs` table (`jobs_test` copy)
- WorkerRepository registration/heartbeat/stale purge on `workers_test`
- FilesystemCache pull/write/cleanup operations using temporary storage paths

For end-to-end smoke tests, see `scripts/smoke/run_smoke_suite.sh` and `tests/smoke/`.

## 5. Quick Reference

| Task | Command / File |
|------|----------------|
| Apply queue migration | `scripts/db/migrations/002_create_new_job_queue_tables.sql` |
| Inspect jobs | `SELECT * FROM jobs WHERE ...` or `vo_cli.py status jobs --job-type ...` |
| Clear test tables | `TRUNCATE jobs_test; TRUNCATE workers_test;` |
| Run DAL/storage tests | `pytest tests/dal/test_jobs_repo.py tests/dal/test_worker_repo.py tests/dal/test_filesystem_cache.py` |
