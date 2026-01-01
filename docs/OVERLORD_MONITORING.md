# Overlord System - Monitoring & Automation

**Last Updated:** 2025-11-30

This document explains the Overlord service automation, CLI status commands, and operational expectations for the VidOps system.

---

## Overview

The **Overlord Service** is a background supervisor that monitors the job queue and worker registry, automatically performing:

1. **Job Chaining**: Detects completed transcription jobs and enqueues follow-up analysis jobs (**currently disabled**)
2. **Stale Job Recovery**: Releases jobs claimed by dead/crashed workers back to pending
3. **Worker Housekeeping**: Marks workers without heartbeats as stale

The Overlord operates entirely on the generic `jobs` and `workers` tables, maintaining system health without manual intervention.

---

## Overlord Service Details

### Responsibilities

**1. Job Chaining (transcription → analysis)**
- **Currently disabled** in `services/overlord.py` (implementation kept, not invoked)
- When enabled: polls `job_type='transcription'` jobs with `status='completed'`
- Checks `result.analysis_enqueued` to avoid duplicates
- Enqueues analysis jobs via `AnalysisService`
- Records linkage in transcription job's `result.analysis_job_id`

**2. Stale Job Recovery**
- Detects jobs with `status IN ('claimed', 'running')` not updated recently
- Default threshold: 12 hours since last `updated_at`
- Workers now run a background heartbeat that touches active jobs to keep `updated_at` fresh
- Releases jobs back to `status='pending'` for re-claiming
- Marks associated workers as `STALE`

**3. Worker Housekeeping**
- Detects workers without heartbeat within threshold
- Default threshold: 3x heartbeat interval (3 minutes if heartbeat=1min)
- Marks workers as `STALE` status
- Prevents stale workers from being considered active

### Configuration

Defined in `vidops/services/overlord.py`:

```python
# Polling frequency
cycle_interval_sec = 10  # Check every 10 seconds

# Stale thresholds
job_stale_threshold = timedelta(hours=12)  # Jobs not updated in 12 hours
worker_stale_threshold = timedelta(
    minutes=config.workers.heartbeat_interval * 3
)  # 3x heartbeat interval
```

### Running the Overlord

**Option 1: Via CLI (future enhancement)**
```bash
python3 vo_cli.py overlord start
```

**Option 2: Standalone**
```bash
python3 -m vidops.services.overlord
```

**Option 3: As systemd service (production)**
Create `/etc/systemd/system/vidops-overlord.service`:
```ini
[Unit]
Description=VidOps Overlord Service
After=network.target postgresql.service

[Service]
Type=simple
User=billie
WorkingDirectory=/home/billie/tools/vidops
ExecStart=/usr/bin/python3 -m vidops.services.overlord
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable vidops-overlord
sudo systemctl start vidops-overlord
sudo systemctl status vidops-overlord
```

---

## Monitoring CLI Commands

### 1. Check Database Connection

```bash
python3 vo_cli.py status db
```

**Output:**
```
✓ Database connection successful.
```

### 2. View Worker Status

**Basic (active workers only):**
```bash
python3 vo_cli.py status workers
```

**All workers (including stale):**
```bash
python3 vo_cli.py status workers --all
```

**Custom stale threshold:**
```bash
python3 vo_cli.py status workers --stale-minutes 10
```

**Example Output:**
```
--- Active Workers (last heartbeat within 5 minutes) ---
Total: 3 workers (2 active, 1 stale, 0 errored)

  ID: worker_mothership_download_12345
    Alias: mothership-arch (Type: download)
    Status: BUSY
    Last Heartbeat: 2025-11-30 10:15:23 UTC (1m ago)
    Current Job: job_abc123...
    Capabilities: gpu_0, cuda
  --------------------
  ID: worker_mothership_transcription_67890
    Alias: mothership-arch (Type: transcription)
    Status: STALE
    Last Heartbeat: 2025-11-30 09:45:12 UTC (31m ago)
  --------------------
```

**Interpretation:**
- **GREEN** (IDLE/BUSY) = Healthy worker
- **YELLOW** (BUSY) = Worker processing job
- **RED** (STALE/ERRORED) = Problem requiring attention
- **Time ago** = Minutes since last heartbeat

### 3. View Job Queue Status

**All jobs (summary):**
```bash
python3 vo_cli.py status jobs
```

**Filter by job type:**
```bash
python3 vo_cli.py status jobs --job-type transcription
```

**Detailed breakdown by job type:**
```bash
python3 vo_cli.py status jobs --detail
```

**Example Output (all jobs):**
```
--- Job Summary ---
Total Jobs: 127

  Pending: 15
  Claimed: 3
  Running: 5
  Completed: 98
  Failed: 6
  Cancelled: 0
```

**Example Output (detailed):**
```
--- Breakdown by Job Type ---

download: 45 total
  completed: 43
  failed: 2

transcription: 62 total
  pending: 10
  running: 3
  completed: 45
  failed: 4

analysis: 20 total
  pending: 5
  running: 2
  completed: 13
```

---

## Operational Expectations

### Healthy System Indicators

✅ **Workers:**
- Active workers show heartbeats within 1-2 minutes
- No workers in STALE or ERRORED status
- BUSY workers have `current_job_id` populated

✅ **Jobs:**
- Pending jobs are being claimed (count decreasing)
- Running jobs complete in reasonable time
- Low failed job count (<5%)
- No jobs stuck in CLAIMED status (indicates claim without processing)

✅ **Overlord:**
- Completed transcriptions trigger analysis within 10s
- Stale jobs released and re-claimed quickly
- Logs show regular cycling without errors

### Problem Indicators

⚠️ **Stale Workers:**
- Workers showing heartbeat >5 minutes ago
- Workers stuck in BUSY with same job for hours

**Action:** Check worker logs, restart worker, investigate crash

⚠️ **Stale Jobs:**
- Jobs in CLAIMED/RUNNING for >12 hours without progress
- Jobs with same `claimed_by` as STALE worker

**Action:** Overlord will auto-release after 12 hours. Check worker logs to identify failure cause.

⚠️ **Failed Jobs:**
- High failure rate (>10%)
- Same job failing repeatedly

**Action:** Check job `error_message`, investigate tool failures (yt-dlp, faster-whisper), check storage access

⚠️ **Job Backlog:**
- Pending count growing faster than processing
- No BUSY workers despite pending jobs

**Action:** Start more workers, check worker crashes, investigate claim failures

---

## Troubleshooting

### Overlord Not Running

**Symptoms:**
- Completed transcriptions don't trigger analysis
- Stale jobs not being released

**Check:**
```bash
ps aux | grep overlord
systemctl status vidops-overlord
```

**Fix:**
```bash
# Restart overlord
systemctl restart vidops-overlord

# Or run manually for debugging
python3 -m vidops.services.overlord
```

### Workers Marked STALE Incorrectly

**Cause:** Worker heartbeat failing (network issue, database connection)

**Check worker logs:**
```bash
tail -f /var/log/vidops/worker_transcription.log
```

**Fix:**
- Verify database connection from worker machine
- Check network latency to database server
- Increase `heartbeat_interval` in config.yaml if network is slow

### Jobs Stuck in CLAIMED

**Cause:** Worker crashed after claiming but before starting

**Check:**
```bash
# Find stale jobs
python3 vo_cli.py status jobs --detail

# Check which worker claimed them
psql -h 192.168.0.187 -d transcripts -U billie -c "
  SELECT job_id, job_type, claimed_by, claimed_at, updated_at
  FROM jobs
  WHERE status = 'claimed' AND updated_at < NOW() - INTERVAL '1 hour'
"
```

**Fix:**
- Overlord will auto-release after 12 hours
- Manually release if urgent:
```python
from vidops.dal import JobRepository
repo = JobRepository()
repo.release('job_id_here')
```

### Analysis Not Auto-Enqueuing

**Cause:**
- Overlord not running
- `result.analysis_enqueued` already set
- AnalysisService error

**Check overlord logs:**
```bash
journalctl -u vidops-overlord -f
```

**Debug:**
```python
# Check if transcription has follow-up flag
psql -c "SELECT job_id, result FROM jobs WHERE job_type='transcription' AND status='completed' LIMIT 5"

# Manually trigger analysis
from vidops.services import get_analysis_service
service = get_analysis_service()
service.enqueue_analysis_job(ytid='...', transcript_kind='words_whisper_medium')
```

---

## Monitoring Best Practices

### Regular Checks

**Every Hour:**
- `vo status workers` - Verify active workers
- `vo status jobs` - Check job counts

**Daily:**
- `vo status jobs --detail` - Review job type distribution
- Check failed jobs: `SELECT * FROM jobs WHERE status='failed' LIMIT 10`
- Review overlord logs: `journalctl -u vidops-overlord --since "1 day ago"`

**Weekly:**
- Purge old completed jobs (cleanup script - future enhancement)
- Review long-running jobs (>24 hours in completed)
- Audit stale worker patterns

### Alerting (Future Enhancement)

Recommended alerts:
- Worker heartbeat >10 minutes
- Job failure rate >10% over 1 hour
- Pending jobs >100 with no active workers
- Overlord service down
- Database connection failures

---

## Database Queries for Monitoring

**Find jobs by worker:**
```sql
SELECT job_id, job_type, status, updated_at
FROM jobs
WHERE claimed_by = 'worker_id'
ORDER BY updated_at DESC;
```

**Find stale jobs manually:**
```sql
SELECT job_id, job_type, status, claimed_by, updated_at,
       NOW() - updated_at AS age
FROM jobs
WHERE status IN ('claimed', 'running')
  AND updated_at < NOW() - INTERVAL '12 hours'
ORDER BY updated_at ASC;
```

**Worker activity summary:**
```sql
SELECT
    worker_type,
    status,
    COUNT(*) as count,
    MAX(last_heartbeat) as latest_heartbeat,
    NOW() - MAX(last_heartbeat) AS time_since_last
FROM workers
GROUP BY worker_type, status
ORDER BY worker_type, status;
```

**Job chaining verification:**
```sql
-- Transcriptions with analysis
SELECT
    t.job_id as transcription_job_id,
    t.result->>'analysis_job_id' as analysis_job_id,
    a.status as analysis_status
FROM jobs t
LEFT JOIN jobs a ON t.result->>'analysis_job_id' = a.job_id
WHERE t.job_type = 'transcription'
  AND t.status = 'completed'
ORDER BY t.completed_at DESC
LIMIT 20;
```

---

## See Also

- `vidops/services/overlord.py` - Overlord implementation
- `vidops/dal/jobs.py` - Job repository with helper methods
- `vidops/cli/status.py` - CLI status commands
- `SOURCE_OF_TRUTH.md` - Current system status
- `STORAGE_INTERFACE.md` - Storage manager guide
