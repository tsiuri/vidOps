# Development Workflow: Running Workers from CLI

**Document Purpose:** Guide for developers testing/running workers during development
**Status:** This is the current recommended approach
**Last Updated:** 2025-12-09

## Quick Start (Development)

```bash
cd ~/tools/vidops
python3 vo_cli.py worker start analysis-distributed
```

That's it. Worker starts and runs until you press Ctrl+C.

---

## Why CLI for Development?

✅ **Immediate feedback** - Output visible in terminal in real-time
✅ **Easy debugging** - Add print statements, see them immediately
✅ **Easy to kill** - Ctrl+C stops cleanly
✅ **No sudo required** - Just run the command
✅ **Perfect for testing** - Iterate fast on changes

❌ **Not suitable for production** - Dies when terminal closes, no auto-restart, etc.

---

## Running with Different Configurations

### Default Configuration

```bash
# Uses config from vidops/config.yml
python3 vo_cli.py worker start analysis-distributed
```

Worker gets:
- machine-alias: (your hostname)-analysis-0
- model: qwen2.5:7b-instruct (from config)
- capabilities: [qwen2.5:7b-instruct, gpu_8gb]
- lease-minutes: 60

### Custom Machine Alias

```bash
python3 vo_cli.py worker start analysis-distributed \
    --machine-alias dev-gpu-0
```

Worker ID will be: `dev-gpu-0:analysis_gpu:qwen2.5:7b-instruct:12345`

### Different Model

```bash
python3 vo_cli.py worker start analysis-distributed \
    --model-name phi:2.2b
```

Uses lighter model for testing.

### Different Ollama Server

```bash
python3 vo_cli.py worker start analysis-distributed \
    --model-url http://gpu-server:11434
```

Point to different machine's Ollama.

### Short Lease Duration (for testing)

```bash
python3 vo_cli.py worker start analysis-distributed \
    --lease-minutes 5
```

Tasks release back quickly if worker crashes (good for testing failure scenarios).

### All Options

```bash
python3 vo_cli.py worker start analysis-distributed --help
```

Shows all available options with descriptions.

---

## Multiple Workers (Development)

Run in separate terminals:

**Terminal 1:**
```bash
cd ~/tools/vidops
python3 vo_cli.py worker start analysis-distributed --machine-alias dev-gpu-0
```

**Terminal 2:**
```bash
cd ~/tools/vidops
python3 vo_cli.py worker start analysis-distributed --machine-alias dev-gpu-1
```

**Terminal 3:**
```bash
cd ~/tools/vidops
python3 vo_cli.py worker start analysis-distributed --machine-alias dev-cpu-0 --model-name phi:2.2b
```

Now you have 3 workers running concurrently, claiming tasks independently.

Check progress:
```bash
psql -U transcripts_user transcripts << SQL
SELECT COUNT(*) FROM analysis_tasks GROUP BY status;
SQL
```

---

## Creating Test Jobs

### From Python Script

```bash
cd ~/tools/db-and-analysis
python3 test_phase4_worker.py
```

Creates a test job with 20 tasks, workers claim them.

### Manual Job Creation

```bash
python3 << 'EOF'
from scripts.analysis_system.analyze_to_db import create_analysis_job
from scripts.analysis_system.config_loader import load_local_config
from scripts.analysis_system.db_storage import AnalysisDatabase
from scripts.analysis_system.analysis_config import default_config

# Connect to database
cfg = load_local_config()
db = AnalysisDatabase(
    host=cfg.get('db_host', 'localhost'),
    dbname=cfg.get('db_name', 'transcripts'),
    user=cfg.get('db_user'),
    password=cfg.get('db_password')
)
db.connect()

# Create test config
config = default_config(config_id="dev-test-001")

# Create synthetic chunks
chunks = []
for i in range(3):
    chunks.append({
        "chunk_id": i,
        "text": f"Test chunk {i} - development test",
        "word_count": 10,
        "start_sec": float(i * 10),
        "end_sec": float(i * 10 + 8),
        "speaker": None,
    })

# Create job (use real video ID)
job_id = create_analysis_job(
    ytid="AxtuJ-IVOGA",  # Real video from database
    config_id=config.id,
    config=config,
    chunks=chunks,
    db=db,
)

print(f"Created job: {job_id}")
print(f"Total tasks: {len(chunks)} chunks × 10 passes = 30 tasks")

db.disconnect()
EOF
```

Then watch workers claim these tasks.

---

## Monitoring While Running

### Check Task Progress

**In another terminal:**
```bash
# Watch task progress update in real-time
watch -n 1 'psql -U transcripts_user transcripts -c "SELECT status, COUNT(*) FROM analysis_tasks WHERE created_at > now() - interval 1 hour GROUP BY status;"'
```

Shows:
- pending: 25 tasks waiting
- claimed: 2 tasks currently running
- completed: 3 tasks finished
- failed: 0 tasks failed

### View Database Results

```bash
psql -U transcripts_user transcripts << SQL
-- Show latest jobs
SELECT job_id, ytid, status, total_tasks, completed_tasks, failed_tasks
FROM analysis_results
ORDER BY created_at DESC
LIMIT 5;
SQL
```

### Check Worker Logs in Real-Time

Workers output logs to stdout (visible in their terminals):
```
[2025-12-09 05:20:00,500] [AnalysisWorker] INFO: Executing pass chunk_analysis
[2025-12-09 05:20:05,750] [AnalysisWorker] INFO: ✓ Task 196 completed
```

Each line shows what the worker is doing.

---

## Debugging Issues

### Worker Won't Start

Check error message in terminal:

```bash
# Try to start and see error
python3 vo_cli.py worker start analysis-distributed

# If it fails, you'll see the error immediately
# Common issues:
# - "ModuleNotFoundError: No module named..." → Missing dependency
# - "psycopg2.OperationalError: could not connect..." → DB not accessible
# - "Connection refused" → Ollama not running
```

### Worker Claims No Tasks

```bash
# Check if tasks exist
psql -U transcripts_user transcripts -c "SELECT COUNT(*) FROM analysis_tasks WHERE status='pending';"

# If 0, create a test job first (see above)

# Check worker capabilities vs task requirements
python3 << 'EOF'
from vidops.config import load_config
config = load_config()
print(f"DB: {config.database.host}:{config.database.port}/{config.database.name}")
print(f"Ollama: {config.analysis.ollama.url}")
print(f"Model: {config.analysis.ollama.model}")
EOF
```

### Worker Crashes

Terminal output shows the error:
```
[2025-12-09 05:20:10] Traceback (most recent call last):
  File "vidops/workers/analysis_distributed.py", line 119, in run_forever
    task = self.task_repo.claim_next(...)
...
```

Fix the issue (check logs, fix code, etc.) and restart:
```bash
Ctrl+C

# Fix code / check logs / etc.

python3 vo_cli.py worker start analysis-distributed
```

### Ollama Not Responding

```bash
# Test Ollama connectivity
curl http://localhost:11434/api/tags

# If fails, start Ollama on your GPU machine
ollama serve

# Verify model is available
ollama list | grep qwen2.5
```

---

## Development Patterns

### Iterate on Pass Implementation

**File:** `vidops/workers/analysis_distributed.py` (method `_execute_pass`)

1. **Modify pass logic**
   ```python
   def _pass_chunk_analysis(self, task, chunk_text, metadata):
       # Your changes here
       analysis = self.analyzer.analyze_chunk(chunk_text, int(task.chunk_id))
       return {...}
   ```

2. **Kill running worker**
   ```
   Ctrl+C in terminal
   ```

3. **Restart worker**
   ```bash
   python3 vo_cli.py worker start analysis-distributed
   ```

4. **Create new test job**
   ```bash
   # Create test job with new chunks
   python3 test_phase4_worker.py
   ```

5. **Watch worker process new tasks**
   - See output in terminal
   - Check results in database
   - Iterate until working

### Test Multi-Worker Scenarios

1. **Start 2 workers in parallel terminals**
   ```
   Terminal 1: python3 vo_cli.py worker start analysis-distributed --machine-alias w0
   Terminal 2: python3 vo_cli.py worker start analysis-distributed --machine-alias w1
   ```

2. **Create job with many tasks**
   ```bash
   python3 << 'EOF'
   # Create job with 20 chunks (200 tasks with 10 passes each)
   # See manual job creation above
   EOF
   ```

3. **Watch workers race to claim tasks**
   - Both workers shown in logs
   - Both claiming and processing
   - Tasks getting distributed

4. **Verify aggregation works with both workers**
   - When job complete, check analysis_results table
   - Should have combined results from both

### Test Failure Handling

1. **Start worker with short lease**
   ```bash
   python3 vo_cli.py worker start analysis-distributed --lease-minutes 1
   ```

2. **Worker claims task**
   - Check: `SELECT * FROM analysis_tasks WHERE status='claimed';`

3. **Kill worker mid-task**
   ```
   Ctrl+C in worker terminal
   ```

4. **Wait > 1 minute (lease expiration)**
   - Task becomes "pending" again
   - `SELECT status FROM analysis_tasks WHERE task_id=XXX;` shows pending

5. **Start new worker**
   ```bash
   python3 vo_cli.py worker start analysis-distributed
   ```

6. **New worker reclaims expired task**
   - Task processed again
   - Results stored (may be duplicate if earlier worker partially executed)

---

## Common Development Tasks

### Add Logging

```python
# In worker code
import logging
logger = logging.getLogger(__name__)

logger.info(f"Debug info: {variable}")
logger.error(f"Error occurred: {error}")
```

Output visible immediately in terminal.

### Add Print Statements

```python
# Quick debug
print(f"DEBUG: task_id={task.task_id}, pass_id={task.pass_id}")
```

Appears in worker terminal output.

### Test Configuration Changes

```bash
# Edit vidops/config.yml
vim vidops/config.yml

# Restart worker to pick up new config
Ctrl+C in worker terminal
python3 vo_cli.py worker start analysis-distributed
```

### Test Database Queries

```bash
# In another terminal, test queries while worker runs
psql -U transcripts_user transcripts
```

Useful for:
- Checking task states
- Verifying aggregation
- Testing progress calculations
- Debugging business logic

---

## Tips & Tricks

### Run Worker in Background (Development)

```bash
# Start worker in background
python3 vo_cli.py worker start analysis-distributed &
WORKER_PID=$!

# Do other things...

# Kill when done
kill $WORKER_PID
```

### Capture Worker Output to File

```bash
# Redirect output to file
python3 vo_cli.py worker start analysis-distributed > worker.log 2>&1 &

# Monitor log in another terminal
tail -f worker.log
```

### Run Multiple Different Workers (Quick)

```bash
# Use GNU parallel (if installed)
parallel "python3 vo_cli.py worker start analysis-distributed --machine-alias dev-{}" ::: 0 1 2

# Or use shell loop
for i in {0..2}; do
  python3 vo_cli.py worker start analysis-distributed --machine-alias dev-$i &
done
wait
```

### Test with Small Dataset First

```bash
# Create job with just 1 chunk (10 tasks)
chunks = [
    {
        "chunk_id": 0,
        "text": "Test chunk",
        "word_count": 5,
        "start_sec": 0,
        "end_sec": 10,
        "speaker": None,
    }
]

# Create job
job_id = create_analysis_job(...chunks=chunks...)

# Watch 10 tasks get processed (fast iteration)
```

---

## Transition to Production

When ready to move from development to production:

1. **Stop running CLI workers**
   ```
   Ctrl+C in all terminals
   ```

2. **Copy service file**
   ```bash
   sudo cp ~/tools/vidops/analysis-distributed-worker.service /etc/systemd/system/
   sudo systemctl daemon-reload
   ```

3. **Start via systemd**
   ```bash
   sudo systemctl start analysis-distributed-worker
   sudo journalctl -u analysis-distributed-worker -f
   ```

4. **Monitor logs**
   ```bash
   sudo journalctl -u analysis-distributed-worker --since "5 minutes ago"
   ```

See `SYSTEMD_DEPLOYMENT_GUIDE.md` for full production setup.

---

## Summary

**Development:** Use CLI, restart frequently, test changes quickly
```bash
python3 vo_cli.py worker start analysis-distributed
```

**Production:** Use systemd, auto-restart, persistent logs
```bash
sudo systemctl start analysis-distributed-worker
```

For now, stick with CLI for development work. The systemd service is ready whenever you need it!

---

**Next Steps:**
- Create test jobs
- Run multiple workers
- Monitor task progress
- Test your changes
- Scale up when ready
