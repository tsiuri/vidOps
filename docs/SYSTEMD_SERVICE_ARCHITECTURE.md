# Systemd Service Architecture: Distributed Analysis Worker

**Document Purpose:** Technical reference for systemd service implementation
**Audience:** Operators, DevOps, System Administrators
**Last Updated:** 2025-12-09

## Overview

The distributed analysis worker can run as a systemd service for production deployment. This document explains the architecture, how it works, configuration options, and when to use it.

## Quick Reference

**Service File:** `/home/billie/tools/vidops/analysis-distributed-worker.service`

**For Development:** Continue using CLI
```bash
cd ~/tools/vidops
python3 vo_cli.py worker start analysis-distributed
```

**For Production:** Use systemd service
```bash
sudo cp analysis-distributed-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start analysis-distributed-worker
sudo journalctl -u analysis-distributed-worker -f
```

---

## Execution Chain

### What Systemd Executes

```
systemd service starts
  ↓
ExecStart=/usr/bin/python3 /home/billie/tools/vidops/vo_cli.py worker start analysis-distributed [options]
  ↓
vo_cli.py (entry point)
  ↓
vidops.cli.worker.start_worker() (Click CLI handler)
  ↓
AnalysisWorker.__init__() (worker initialization)
  ↓
worker.run_forever() (main event loop)
  ↓
Infinite loop:
  - claim_next() from analysis_tasks table
  - _execute_pass() (LLM inference)
  - mark_completed() or mark_failed()
  - Aggregate results when job complete
```

### Code Flow

**Entry Point:** `/home/billie/tools/vidops/vo_cli.py`
- Click CLI framework entry point
- Routes to: `from vidops.cli.worker import worker`

**CLI Handler:** `/home/billie/tools/vidops/cli/worker.py`
- Parses CLI arguments (machine-alias, model-url, capabilities, etc.)
- Loads vidops config via `load_config()`
- Instantiates `AnalysisWorker` class
- Calls `worker.run_forever()`

**Worker Implementation:** `/home/billie/tools/vidops/workers/analysis_distributed.py`
- `__init__()` - Initialize with configuration, connect to DB
- `run_forever()` - Main event loop (lines 107-187)
  - Sets up signal handlers for graceful shutdown
  - Enters infinite while loop
  - Calls `claim_next()` to get task from database
  - Executes pass with `_execute_pass()`
  - Marks task completed/failed
  - Checks if job is complete, aggregates results
  - Sleeps 5 seconds if no tasks available
- `_execute_pass()` - Execute LLM pass (lines 193-259)
  - Routes to appropriate pass handler (chunk_analysis, sentiment_pass, etc.)
  - For chunk_analysis: calls real LLM via OllamaAnalyzer
  - For others: returns stub implementation
- `_aggregate_job_results()` - Aggregate when job complete (lines 323-392)
  - Groups results by pass type
  - Writes to analysis_results table
  - Updates job status
- `_handle_signal()` - Graceful shutdown on SIGTERM/SIGINT (lines 398-406)
  - Sets `should_exit` flag
  - Completes current task before exiting

**Data Access:** `/home/billie/tools/vidops/dal/analysis_task_repository.py`
- `claim_next()` - Atomic task claiming via PostgreSQL SELECT FOR UPDATE
- `mark_completed()` - Update task with results
- `mark_failed()` - Mark task as failed
- `is_job_complete()` - Check if all tasks done
- `get_job_progress()` - Get current progress counts
- `get_job_tasks()` - Retrieve all tasks for aggregation

**Configuration:** `/home/billie/tools/vidops/configuration.py`
- Loads from `vidops/config.yml` (YAML format)
- Provides: database credentials, Ollama URL/model, analysis settings
- Environment variable overrides supported

---

## Service File Breakdown

### Unit Section

```ini
[Unit]
Description=VidOps Distributed Analysis Worker
Documentation=file:///home/billie/tools/vidops/docs/PHASE_3_COMPLETE.md
After=network.target postgresql.service
Wants=ollama.service
```

**What each line does:**

- `Description` - Human-readable name (shows in `systemctl status`)
- `Documentation` - Link to documentation (referenced in `systemctl help`)
- `After=network.target postgresql.service` - Start only after these services
  - Ensures network is available
  - Ensures PostgreSQL is running (wait for it to be ready)
- `Wants=ollama.service` - Weakly depends on Ollama (soft requirement)
  - If Ollama is available, start after it
  - If Ollama not found, still start (won't fail)

### Service Section

```ini
[Service]
Type=simple
User=billie
WorkingDirectory=/home/billie/tools/vidops
```

- `Type=simple` - Process runs in foreground (systemd monitors it directly)
- `User=billie` - Run as user 'billie' (not root)
- `WorkingDirectory` - CD to this directory before running

### Environment Variables

```ini
EnvironmentFile=-/etc/vidops/analysis-worker.env
EnvironmentFile=-/home/billie/.vidops/analysis-worker.env

Environment="VIDOPS_PROJECT_ROOT=/home/billio/tools/vidops"
Environment="PYTHONUNBUFFERED=1"
Environment="OLLAMA_URL=http://localhost:11434"
Environment="OLLAMA_MODEL=qwen2.5:7b-instruct"
Environment="ANALYSIS_LEASE_MINUTES=60"
```

- `EnvironmentFile=-/path` - Load from file (dash = optional, no error if missing)
- `Environment="KEY=value"` - Set environment variable
- These can be referenced in ExecStart via `${KEY}`

### Execution

```ini
ExecStart=/usr/bin/python3 /home/billie/tools/vidops/vo_cli.py worker start analysis-distributed \
    --machine-alias %H-analysis-0 \
    --model-url ${OLLAMA_URL} \
    --model-name ${OLLAMA_MODEL} \
    --capabilities gpu_8gb \
    --capabilities ${OLLAMA_MODEL} \
    --lease-minutes ${ANALYSIS_LEASE_MINUTES}
```

- `%H` - Expands to system hostname (e.g., 'my-gpu-server')
- `${OLLAMA_URL}` - Expands to environment variable value
- Result: Single worker with hostname-based identification

### Restart Policy

```ini
Restart=on-failure
RestartSec=10
StartLimitInterval=300
StartLimitBurst=3
```

- `Restart=on-failure` - Restart if process exits with non-zero code
- `RestartSec=10` - Wait 10 seconds between restarts
- `StartLimitInterval=300` - Time window (5 minutes)
- `StartLimitBurst=3` - Max 3 restart attempts in time window
- Effect: Auto-restart on crash, but stop if crashing repeatedly

### Logging

```ini
StandardOutput=journal
StandardError=journal
SyslogIdentifier=analysis-worker
```

- Send stdout/stderr to systemd journal (not lost when terminal closes)
- Can view with: `journalctl -u analysis-distributed-worker`
- Persists across reboots

### Process Management

```ini
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=30
```

- `KillMode=mixed` - Send SIGTERM to main process, SIGKILL to children
- `KillSignal=SIGTERM` - Use SIGTERM for graceful shutdown
- `TimeoutStopSec=30` - Wait 30 seconds, then force kill if still running
- Effect: Graceful shutdown with timeout fallback

### Security

```ini
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/home/billie/tools/vidops
```

- `NoNewPrivileges=true` - Can't escalate privileges
- `PrivateTmp=true` - Private /tmp (isolated filesystem)
- `ProtectSystem=strict` - System files read-only
- `ProtectHome=yes` - Home directory read-only
- `ReadWritePaths=/home/billie/tools/vidops` - Except this path (can write here)
- Effect: Sandboxed environment, limited blast radius

### Install Section

```ini
[Install]
WantedBy=multi-user.target
```

- When enabled with `systemctl enable`, symlink to this target
- Means: Start automatically on boot (in multi-user mode)

---

## CLI Arguments → Systemd

### Mapping CLI Arguments to Service

When you run from CLI:
```bash
python3 vo_cli.py worker start analysis-distributed \
    --machine-alias my-worker \
    --model-url http://localhost:11434 \
    --model-name qwen2.5:7b-instruct \
    --capabilities gpu_8gb \
    --lease-minutes 60
```

Systemd service does the same, but with:
- `--machine-alias %H-analysis-0` (hostname-based)
- `--model-url ${OLLAMA_URL}` (from environment variable)
- `--model-name ${OLLAMA_MODEL}` (from environment variable)
- `--capabilities gpu_8gb` and `--capabilities ${OLLAMA_MODEL}` (repeatable)
- `--lease-minutes ${ANALYSIS_LEASE_MINUTES}` (from environment variable)

### How Options Flow to Worker

```
CLI Argument: --machine-alias %H-analysis-0
  ↓
Systemd expands: my-gpu-server-analysis-0
  ↓
Passed to: vo_cli.py worker start analysis-distributed
  ↓
Handled by: vidops/cli/worker.py start_worker()
  ↓
Used in: AnalysisWorker(machine_alias="my-gpu-server-analysis-0", ...)
  ↓
Worker ID: my-gpu-server-analysis-0:analysis_gpu:qwen2.5:7b-instruct:12345
```

---

## Environment Variable Configuration

### Default Environment (in service file)

```
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b-instruct
ANALYSIS_LEASE_MINUTES=60
```

### Override with File

Create `/etc/vidops/analysis-worker.env`:
```bash
# System-wide configuration for all workers
OLLAMA_URL=http://192.168.1.100:11434
OLLAMA_MODEL=qwen2.5:7b-instruct
ANALYSIS_LEASE_MINUTES=120
```

Or per-user: `~/.vidops/analysis-worker.env`

### Override with systemctl edit

```bash
sudo systemctl edit analysis-distributed-worker
```

Adds:
```ini
[Service]
Environment="OLLAMA_URL=http://gpu-server:11434"
Environment="OLLAMA_MODEL=phi:2.2b"
```

Takes precedence over EnvironmentFile.

---

## Multi-Instance Scaling

### Templated Service (Recommended)

Create `/etc/systemd/system/analysis-worker@.service`:
```ini
[Unit]
Description=Analysis Worker %i
After=network.target postgresql.service

[Service]
Type=simple
User=billie
WorkingDirectory=/home/billie/tools/vidops
Environment="OLLAMA_URL=http://localhost:11434"
Environment="OLLAMA_MODEL=qwen2.5:7b-instruct"

ExecStart=/usr/bin/python3 /home/billie/tools/vidops/vo_cli.py worker start analysis-distributed \
    --machine-alias %H-analysis-%i \
    --model-url ${OLLAMA_URL} \
    --model-name ${OLLAMA_MODEL} \
    --capabilities gpu_8gb \
    --lease-minutes 60

Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Then:
```bash
# Start 3 instances with IDs 0, 1, 2
sudo systemctl start analysis-worker@0 analysis-worker@1 analysis-worker@2

# Status all
sudo systemctl status analysis-worker@*

# Restart all
sudo systemctl restart analysis-worker@*

# Stop all
sudo systemctl stop analysis-worker@*

# View logs for instance 0
sudo journalctl -u analysis-worker@0 -f
```

Result: 3 workers with IDs: `hostname-analysis-0`, `hostname-analysis-1`, `hostname-analysis-2`

---

## Logging & Monitoring

### View Live Logs

```bash
# Follow logs as they happen
sudo journalctl -u analysis-distributed-worker -f

# Last 50 lines
sudo journalctl -u analysis-distributed-worker -n 50

# Errors only
sudo journalctl -u analysis-distributed-worker -p err

# Last hour
sudo journalctl -u analysis-distributed-worker --since "1 hour ago"
```

### Check Service Status

```bash
# Overall status
sudo systemctl status analysis-distributed-worker

# Is it running?
sudo systemctl is-active analysis-distributed-worker

# Is it enabled at boot?
sudo systemctl is-enabled analysis-distributed-worker

# Show all instances
sudo systemctl status analysis-worker@*
```

### Monitor Performance

```bash
# Resource usage
ps aux | grep analysis-distributed-worker

# Connections to database
ps aux | grep python3 | grep analysis

# Check database task counts
psql -U transcripts_user transcripts << SQL
SELECT status, COUNT(*) FROM analysis_tasks GROUP BY status;
SQL
```

---

## Graceful Shutdown Sequence

When systemd stops the service:

1. **systemd sends SIGTERM** (signal 15)
   - Worker's `_handle_signal()` handler called
   - Sets `should_exit = True`
   - Worker completes current task

2. **Worker exits while loop**
   - Closes database connection
   - Finally block: `db.disconnect()`

3. **Process exits cleanly**
   - systemd considers it successful (exit code 0)
   - No forced kill needed

4. **If takes > 30 seconds**
   - systemd sends SIGKILL (signal 9)
   - Process forcefully terminated
   - Note: May leave database connections open

### Testing Graceful Shutdown

```bash
# In one terminal, start worker
python3 vo_cli.py worker start analysis-distributed

# In another terminal, stop gracefully
sudo systemctl stop analysis-distributed-worker

# Watch logs to see shutdown
sudo journalctl -u analysis-distributed-worker -f
```

Expected logs:
```
[timestamp] [AnalysisWorker] INFO: Worker started
[timestamp] [AnalysisWorker] INFO: Claimed task 123
[timestamp] [AnalysisWorker] INFO: Received signal 15
[timestamp] [AnalysisWorker] INFO: Exiting
```

---

## Troubleshooting

### Worker Won't Start

```bash
# Check service status
sudo systemctl status analysis-distributed-worker

# View full logs
sudo journalctl -u analysis-distributed-worker -n 100

# Verify executable exists
file /usr/bin/python3

# Test CLI directly
python3 /home/billie/tools/vidops/vo_cli.py worker start analysis-distributed --help
```

### Worker Keeps Restarting

Check logs for reason:
```bash
sudo journalctl -u analysis-distributed-worker -p err
```

Common issues:
- Database not reachable
- Ollama not running
- Insufficient permissions
- Port conflict

### Database Connection Error

```bash
# Verify PostgreSQL is running
sudo systemctl status postgresql

# Verify connectivity
psql -h 192.168.0.187 -U transcripts_user -d transcripts -c "SELECT 1;"

# Check worker environment
sudo systemctl show-environment analysis-distributed-worker
```

### No Tasks Being Claimed

```bash
# Check if tasks exist
psql -U transcripts_user transcripts << SQL
SELECT COUNT(*) FROM analysis_tasks WHERE status='pending';
SQL

# Check worker capabilities vs task requirements
sudo journalctl -u analysis-distributed-worker | grep -i capability

# Manually verify claim logic
python3 << 'EOF'
from vidops.dal.analysis_task_repository import AnalysisTaskRepository
from vidops.config import load_config

config = load_config()
# ... test claiming logic
EOF
```

---

## Development vs Production

### Development (Use CLI)

```bash
cd ~/tools/vidops
python3 vo_cli.py worker start analysis-distributed
```

**Advantages:**
- Direct output visible in terminal
- Easy to debug (add print statements)
- Easy to kill (Ctrl+C)
- No sudo required
- Perfect for testing

### Production (Use Systemd)

```bash
sudo systemctl start analysis-distributed-worker
```

**Advantages:**
- Auto-restart on crash
- Persistent logs
- Auto-start on reboot
- Remote-manageable
- Scalable (multi-instance)
- Better monitoring/alerting
- Security hardening

---

## Service Dependencies

### PostgreSQL Dependency

```ini
After=postgresql.service
```

- Service waits for PostgreSQL to be ready
- If PostgreSQL restarts, worker restarts
- Worker won't start if PostgreSQL is down

### Ollama Soft Dependency

```ini
Wants=ollama.service
```

- If Ollama is available, wait for it
- If Ollama not found, start anyway
- Worker can handle Ollama being down (will retry)

### Manual Dependency Check

```bash
# Before starting worker, verify:
sudo systemctl is-active postgresql  # Should be "active"
sudo systemctl is-active ollama      # May be "inactive" (OK)

# Test connectivity
psql -h 192.168.0.187 -U transcripts_user -d transcripts -c "SELECT 1;"
curl http://localhost:11434/api/tags
```

---

## Next Steps: Future Enhancements

### Monitoring Integration

```ini
# Future: Add to service file
ExecStartPost=/usr/local/bin/register-worker.sh
ExecStopPost=/usr/local/bin/deregister-worker.sh
```

Could integrate with Prometheus, Grafana, PagerDuty, etc.

### Resource Limits

```ini
# Future: Add to service file
MemoryLimit=4G
CPUQuota=80%
```

Limit resource consumption per worker.

### Health Checks

```bash
# Future: Periodic health check script
ExecStartPost=/usr/local/bin/healthcheck-worker.sh
```

Detect and restart unhealthy workers.

---

## References

- Service File: `/home/billie/tools/vidops/analysis-distributed-worker.service`
- Deployment Guide: `SYSTEMD_DEPLOYMENT_GUIDE.md`
- Worker Code: `vidops/workers/analysis_distributed.py`
- CLI Code: `vidops/cli/worker.py`
- Configuration: `configuration.py`

**Systemd Documentation:**
- `man systemd.service`
- `man systemctl`
- `man journalctl`

---

**Document Complete**

For development: Continue using CLI as shown above.
For production: See `SYSTEMD_DEPLOYMENT_GUIDE.md` for deployment procedures.
