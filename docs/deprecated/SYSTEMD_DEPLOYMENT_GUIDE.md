# Systemd Deployment Guide: Distributed Analysis Worker

This guide covers deploying the distributed analysis worker as a systemd service for production use.

## Quick Start

### 1. Copy Service File

```bash
sudo cp /home/billie/tools/vidops/analysis-distributed-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
```

### 2. Start Single Worker Instance

```bash
sudo systemctl start analysis-distributed-worker
sudo systemctl status analysis-distributed-worker
sudo journalctl -u analysis-distributed-worker -f
```

### 3. Enable Auto-Start

```bash
sudo systemctl enable analysis-distributed-worker
```

## Configuration

### Environment Variables

You can customize worker behavior via environment file:

**Option A: System-wide (requires sudo)**
```bash
sudo mkdir -p /etc/vidops
sudo tee /etc/vidops/analysis-worker.env << EOF
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b-instruct
ANALYSIS_CAPABILITIES=qwen2.5:7b-instruct,gpu_8gb
ANALYSIS_LEASE_MINUTES=60
EOF
sudo systemctl restart analysis-distributed-worker
```

**Option B: Per-user (no sudo)**
```bash
mkdir -p ~/.vidops
cat > ~/.vidops/analysis-worker.env << EOF
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b-instruct
ANALYSIS_LEASE_MINUTES=60
EOF

# Edit service file to point to user config
sudo systemctl edit analysis-distributed-worker
# Add: EnvironmentFile=/home/billie/.vidops/analysis-worker.env
```

### CLI Parameters

To customize the worker further, edit the ExecStart line in the service file:

```bash
sudo systemctl edit analysis-distributed-worker
```

Key options:
- `--machine-alias`: Worker identifier (default: hostname + '-analysis-0')
- `--model-url`: Ollama API endpoint
- `--model-name`: Model to use for analysis
- `--capabilities`: Worker capabilities (repeatable, e.g., `--capabilities gpu_8gb`)
- `--lease-minutes`: Task lease duration (default: 60)

Example configuration for CPU worker:
```ini
ExecStart=/usr/bin/python3 /home/billie/tools/vidops/vo_cli.py worker start analysis-distributed \
    --machine-alias %H-analysis-cpu-0 \
    --model-url http://localhost:11434 \
    --model-name phi:2.2b \
    --capabilities cpu \
    --capabilities phi:2.2b \
    --lease-minutes 120
```

## Multiple Worker Instances

### Template-Based Approach

Create templated service files for different worker types:

**GPU Worker (high-capability)**
```bash
sudo tee /etc/systemd/system/analysis-worker@gpu.service << 'EOF'
[Unit]
Description=Analysis Worker %i (GPU)
After=network.target

[Service]
User=billie
WorkingDirectory=/home/billie/tools/vidops
Environment="VIDOPS_PROJECT_ROOT=/home/billie/tools/vidops"
ExecStart=/usr/bin/python3 /home/billie/tools/vidops/vo_cli.py worker start analysis-distributed \
    --machine-alias %H-analysis-gpu-%i \
    --capabilities qwen2.5:7b-instruct \
    --capabilities gpu_8gb
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Enable multiple GPU workers
sudo systemctl enable analysis-worker@gpu@0.service
sudo systemctl enable analysis-worker@gpu@1.service
sudo systemctl start analysis-worker@gpu@{0,1}
```

**CPU Worker (lightweight tasks)**
```bash
sudo tee /etc/systemd/system/analysis-worker@cpu.service << 'EOF'
[Unit]
Description=Analysis Worker %i (CPU)
After=network.target

[Service]
User=billie
WorkingDirectory=/home/billie/tools/vidops
ExecStart=/usr/bin/python3 /home/billie/tools/vidops/vo_cli.py worker start analysis-distributed \
    --machine-alias %H-analysis-cpu-%i \
    --model-name phi:2.2b \
    --capabilities cpu \
    --capabilities phi:2.2b
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable analysis-worker@cpu@0.service
sudo systemctl start analysis-worker@cpu@0.service
```

## Monitoring

### Check Worker Status

```bash
# Overall service status
systemctl status analysis-distributed-worker

# Follow logs in real-time
journalctl -u analysis-distributed-worker -f

# Show last 50 lines
journalctl -u analysis-distributed-worker -n 50

# Show errors only
journalctl -u analysis-distributed-worker -p err
```

### Verify Worker is Claiming Tasks

```bash
# From psql
psql -U transcripts_user transcripts << SQL
SELECT
  COUNT(*) as pending_tasks,
  COUNT(*) FILTER (WHERE status = 'claimed') as claimed_tasks
FROM analysis_tasks
WHERE created_at > now() - interval '5 minutes';
SQL
```

### Check Worker Health

```bash
# Log into worker machine and run:
curl -s http://localhost:11434/api/status || echo "Ollama not running"

# Check PostgreSQL connectivity
python3 -c "
import psycopg2
conn = psycopg2.connect('dbname=transcripts user=transcripts_user host=192.168.0.187')
print('✓ Database connected')
conn.close()
"
```

## Troubleshooting

### Worker Won't Start

**Check logs:**
```bash
journalctl -u analysis-distributed-worker -n 100 -p err
```

**Common issues:**
- Database unreachable: Check PostgreSQL is running on 192.168.0.187:5432
- Ollama unreachable: Check `curl http://localhost:11434/api/tags`
- Permission denied: User may lack write access to log files

### Worker Claims No Tasks

**Verify tasks exist:**
```bash
psql -U transcripts_user transcripts << SQL
SELECT COUNT(*) FROM analysis_tasks WHERE status = 'pending';
SQL
```

**Check worker capabilities:**
```bash
journalctl -u analysis-distributed-worker | grep "Capabilities"
```

**Verify capability matching:**
```bash
psql -U transcripts_user transcripts << SQL
-- Check if any tasks require capabilities this worker has
SELECT DISTINCT required_capabilities
FROM analysis_tasks
WHERE status = 'pending'
LIMIT 5;
SQL
```

### High Task Lease Duration

If tasks are getting stuck, reduce lease duration:

```bash
sudo systemctl edit analysis-distributed-worker
# Change: --lease-minutes 60  →  --lease-minutes 30
sudo systemctl restart analysis-distributed-worker
```

## Performance Tuning

### CPU-Bound Tasks

For CPU-bound analysis work, reduce lease duration to allow faster reassignment on failure:

```ini
ExecStart=.../vo_cli.py worker start analysis-distributed \
    --lease-minutes 30 \
    --capabilities cpu
```

### GPU-Accelerated Tasks

For GPU workers, increase lease duration to allow longer-running analyses:

```ini
ExecStart=.../vo_cli.py worker start analysis-distributed \
    --lease-minutes 120 \
    --capabilities gpu_8gb \
    --capabilities qwen2.5:7b-instruct
```

### Multiple Model Support

Run multiple workers with different models:

```bash
# High-speed CPU model
ExecStart=.../vo_cli.py worker start analysis-distributed \
    --machine-alias %H-fast-cpu \
    --model-name phi:2.2b \
    --capabilities phi:2.2b,cpu

# High-quality GPU model
ExecStart=.../vo_cli.py worker start analysis-distributed \
    --machine-alias %H-quality-gpu \
    --model-name qwen2.5:7b-instruct \
    --capabilities qwen2.5:7b-instruct,gpu_8gb
```

## Log Rotation

Configure logrotate for persistent logs:

```bash
sudo tee /etc/logrotate.d/vidops-analysis << EOF
/var/log/vidops/analysis-worker.log {
    daily
    rotate 7
    compress
    delaycompress
    notifempty
    create 0640 billie billie
    sharedscripts
    postrotate
        systemctl reload analysis-distributed-worker > /dev/null 2>&1 || true
    endscript
}
EOF
```

## Cleanup & Removal

**Stop service:**
```bash
sudo systemctl stop analysis-distributed-worker
```

**Disable auto-start:**
```bash
sudo systemctl disable analysis-distributed-worker
```

**Remove service file:**
```bash
sudo rm /etc/systemd/system/analysis-distributed-worker.service
sudo systemctl daemon-reload
```

## References

- CLI Help: `python3 vo_cli.py worker start analysis-distributed --help`
- Worker Logs: `journalctl -u analysis-distributed-worker`
- Database Schema: `~/tools/db-and-analysis/schema.sql`
- Worker Code: `~/tools/vidops/vidops/workers/analysis_distributed.py`
