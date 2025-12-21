# Job Cleanup: Reset Failed Diarization Jobs

Use this when diarization jobs are stuck in `failed` and you want to retry them
by returning them to `pending`. This is a direct DB operation on `public.jobs`.

## Prereqs
- DB access to the VidOps Postgres instance.
- Connection settings from `config.yaml` (preferred) or `db.cfg`.
- `psql` installed locally.

## Step 1: Inspect counts

Total failed diarization jobs:

```sh
PGPASSWORD="$VIDOPS_DB_PASSWORD" psql -h 127.0.0.1 -p 5432 -U "$VIDOPS_DB_USER" \
  -d "$VIDOPS_DB_NAME" -c \
  "SELECT count(*) AS failed_diarization_jobs
   FROM public.jobs
   WHERE job_type = 'diarization' AND status = 'failed';"
```

Breakdown by day (uses `updated_at` when present, otherwise `created_at`):

```sh
PGPASSWORD="$VIDOPS_DB_PASSWORD" psql -h 127.0.0.1 -p 5432 -U "$VIDOPS_DB_USER" \
  -d "$VIDOPS_DB_NAME" -c \
  "SELECT date_trunc('day', COALESCE(updated_at, created_at))::date AS day,
          count(*) AS failed_count
   FROM public.jobs
   WHERE job_type = 'diarization' AND status = 'failed'
   GROUP BY 1
   ORDER BY 1 DESC
   LIMIT 14;"
```

## Step 2: Reset a specific day (or range) to `pending`

This clears claim fields, timestamps, and `error_message` so the job can be
re-claimed cleanly.

```sh
PGPASSWORD="$VIDOPS_DB_PASSWORD" psql -h 127.0.0.1 -p 5432 -U "$VIDOPS_DB_USER" \
  -d "$VIDOPS_DB_NAME" -v ON_ERROR_STOP=1 -c \
  "WITH updated AS (
     UPDATE public.jobs
     SET status = 'pending',
         claimed_by = NULL,
         claimed_at = NULL,
         started_at = NULL,
         completed_at = NULL,
         updated_at = NOW(),
         error_message = NULL
     WHERE job_type = 'diarization'
       AND status = 'failed'
       AND date_trunc('day', COALESCE(updated_at, created_at))::date IN ('YYYY-MM-DD', 'YYYY-MM-DD')
     RETURNING 1
   )
   SELECT count(*) AS reset_count FROM updated;"
```

## Step 3: Verify

Re-run the count query from Step 1 to confirm the failed total dropped by the
expected amount.

## Notes for AI helpers
- The diarization queue uses `public.jobs` with `job_type = 'diarization'`.
- Status values are enforced by a CHECK constraint: `pending`, `claimed`,
  `running`, `completed`, `failed`, `cancelled`.
- Prefer filtering by day using `date_trunc('day', COALESCE(updated_at, created_at))`
  to match operator expectations.
- Local access may be via `127.0.0.1`; remote hosts are defined in `config.yaml`
  (`database.host`) or `db.cfg`.
