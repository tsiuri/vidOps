# Queue System Quick Start Guide

## What Was Built

A PostgreSQL-backed queue system for managing transcription jobs across multiple GPU/CPU workers.

## Quick Commands

### View Queue Status
```bash
~/tools/vidops/scripts/transcription/queue_cli.py status
```

### Enqueue Jobs
```bash
~/tools/vidops/scripts/transcription/queue_cli.py enqueue video1.mp4 video2.mp4 --model medium
```

### View Workers
```bash
~/tools/vidops/scripts/transcription/queue_cli.py workers
```

### View Statistics
```bash
~/tools/vidops/scripts/transcription/queue_cli.py stats
```

### Monitor Dashboard
```bash
psql -h 192.168.0.187 -U billie -d transcripts -f ~/tools/vidops/scripts/db/queue_dashboard.sql
```

### Continuous Monitoring
```bash
watch -n 5 'psql -h 192.168.0.187 -U billie -d transcripts -f ~/tools/vidops/scripts/db/queue_dashboard.sql'
```

## Database Tables Created

1. **transcribe_jobs** - Main job queue
2. **transcribe_workers** - Worker health tracking
3. **transcribe_fragments** - Fragment-level tracking
4. **transcribe_job_log** - Audit trail
5. **transcribe_retry_queue** - Retry job queue

## Python API

```python
from scripts.transcription.db_queue import TranscriptionQueue

queue = TranscriptionQueue()

# Enqueue a job
job_id = queue.enqueue("/path/to/video.mp4", model="medium", priority=0)

# Claim a job
job = queue.claim("worker-id", "nvidia")

# Complete a job
queue.complete_job(job_id, output_vtt="/path/to/output.vtt")

# Get stats
stats = queue.get_queue_stats()
```

## Files Created

- `scripts/db/migrations/001_create_transcribe_queue.up.sql` - Database schema
- `scripts/transcription/db_queue.py` - Python queue client
- `scripts/transcription/queue_worker_base.py` - Worker base class
- `scripts/transcription/queue_cli.py` - CLI tool
- `scripts/db/queue_dashboard.sql` - Monitoring dashboard
- `scripts/transcription/recover_stale_jobs.sh` - Auto-recovery script

## Setup Auto-Recovery (Optional)

Add to crontab to recover stale jobs every 5 minutes:
```bash
crontab -e
# Add this line:
*/5 * * * * /home/billie/tools/vidops/scripts/transcription/recover_stale_jobs.sh
```

## Next Steps

1. **Test the queue**: Enqueue some test jobs and verify they appear in the database
2. **Integrate workers**: Modify existing transcription workers to use the queue
3. **Setup monitoring**: Use the dashboard to track queue health

## Documentation

- `docs/QUEUE_IMPLEMENTATION_SUMMARY.md` - Complete implementation details
- `docs/DBQUEUE.md` - Original design specification
- `docs/DBQUEUE_IMPLEMENTATION_GUIDE.md` - Implementation guide
- `scripts/db/migrations/MIGRATION_LOG.md` - Migration history

## Testing

All core components tested and working:
- ✓ Database schema and functions
- ✓ Python queue client
- ✓ CLI tool (all commands)
- ✓ Monitoring dashboard
- ✓ Auto-recovery script

Worker integration pending (requires actual media files for testing).
