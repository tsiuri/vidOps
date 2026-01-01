# Distributed Analysis Worker - Monitoring Setup Guide

This guide walks you through setting up comprehensive monitoring for the distributed analysis worker using Prometheus, Grafana, and AlertManager.

**Status:** Ready to deploy | **Last Updated:** 2025-12-09

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Architecture Overview](#architecture-overview)
3. [Metrics Explained](#metrics-explained)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [Running the Stack](#running-the-stack)
7. [Dashboard Access](#dashboard-access)
8. [Alert Rules](#alert-rules)
9. [Troubleshooting](#troubleshooting)
10. [Docker Deployment](#docker-deployment)

---

## Quick Start

**5-minute setup (single machine):**

```bash
# 1. Start Prometheus
docker run -d \
  --name prometheus \
  -p 9090:9090 \
  -v $(pwd)/config/prometheus.yml:/etc/prometheus/prometheus.yml \
  -v $(pwd)/config/alert_rules.yml:/etc/prometheus/alert_rules.yml \
  prom/prometheus

# 2. Start Grafana
docker run -d \
  --name grafana \
  -p 3000:3000 \
  -e GF_SECURITY_ADMIN_PASSWORD=admin \
  grafana/grafana:latest

# 3. Start your worker with metrics enabled
cd /home/billie/tools/vidops
python3 vo_cli.py worker start analysis-distributed \
    --machine-alias local-gpu-0 \
    --metrics-port 8888

# 4. Import dashboard
# - Open http://localhost:3000
# - Login with admin/admin
# - Import JSON from: vidops/monitoring/dashboards/analysis_worker_dashboard.json
```

---

## Architecture Overview

```
┌─────────────────────────────────────────┐
│   AnalysisWorker Process                │
│  ┌───────────────────────────────────┐  │
│  │ Metrics Collection (prometheus-cl │  │
│  │ • task_processing_duration        │  │
│  │ • tasks_completed_total           │  │
│  │ • worker_uptime                   │  │
│  │ • llm_inference_latency           │  │
│  │ • db_query_latency                │  │
│  └───────────────────────────────────┘  │
│                  ▲                       │
│                  │ HTTP /metrics         │
└──────────────────┼──────────────────────┘
                   │
        ┌──────────┴───────────┐
        │                      │
┌───────▼────────┐  ┌──────────▼──────────┐
│  Prometheus    │  │  AlertManager       │
│  (Time Series  │  │  (Alert Routing)    │
│   Database)    │  │                     │
└───────┬────────┘  └──────────┬──────────┘
        │                      │
        │ PromQL Queries       │ Alerts (email, Slack, etc)
        │                      │
┌───────▼──────────────────────▼──────────┐
│         Grafana Dashboards              │
│   (Visualization & Analysis)            │
└─────────────────────────────────────────┘
```

---

## Metrics Explained

### Task Processing Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `analysis_worker_tasks_claimed_total` | Counter | Total tasks claimed by worker |
| `analysis_worker_tasks_completed_total` | Counter | Completed tasks (success or failure) |
| `analysis_worker_tasks_failed_total` | Counter | Failed tasks by error type |
| `analysis_worker_task_processing_duration_seconds` | Histogram | Task execution time (p50, p95, p99) |
| `analysis_worker_task_processing_duration_summary` | Summary | Detailed latency percentiles |

**Key Queries:**
```promql
# Task success rate (%)
(sum(rate(analysis_worker_tasks_completed_total{status="success"}[5m]))
 / sum(rate(analysis_worker_tasks_completed_total[5m]))) * 100

# Tasks per second
sum(rate(analysis_worker_tasks_completed_total[5m])) by (worker_id)

# p95 task latency
histogram_quantile(0.95,
  sum(rate(analysis_worker_task_processing_duration_seconds_bucket[5m]))
  by (pass_id, le))
```

### Queue/Backlog Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `analysis_worker_pending_tasks` | Gauge | Tasks waiting to be claimed |
| `analysis_worker_claimed_not_completed` | Gauge | Tasks in progress |
| `analysis_worker_job_progress` | Gauge | Job completion percentage |

**Alerts:**
- Warning if pending > 100 for 15 minutes
- Critical if pending > 500 for 5 minutes

### Worker Health Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `analysis_worker_uptime_seconds` | Gauge | Worker running time |
| `analysis_worker_current_task` | Gauge | Current task ID (0 if idle) |
| `analysis_worker_memory_usage_bytes` | Gauge | RSS memory usage |
| `analysis_worker_cpu_usage_percent` | Gauge | CPU utilization |

**Alerts:**
- Warning if memory > 2GB or CPU > 80%
- Critical if memory > 4GB or worker down > 5 min

### LLM/Inference Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `analysis_worker_llm_inference_duration_seconds` | Histogram | Model inference latency |
| `analysis_worker_llm_inference_total` | Counter | Total inferences (success/failed) |
| `analysis_worker_llm_model_available` | Gauge | Model availability (1/0) |
| `analysis_worker_llm_tokens_processed_total` | Counter | Tokens sent/received by model |

**Alerts:**
- Critical if model unavailable
- Warning if inference latency p95 > 15 seconds
- Warning if error rate > 5%

### Database Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `analysis_worker_db_query_duration_seconds` | Histogram | Query execution time by type |
| `analysis_worker_db_connection_pool_size` | Gauge | Active connections |
| `analysis_worker_db_connection_errors_total` | Counter | Connection failures |

**Alerts:**
- Warning if p95 query latency > 1 second
- Warning if connection errors > 0.1/sec

### Error Tracking Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `analysis_worker_errors_total` | Counter | Total errors by category |
| `analysis_worker_last_error_timestamp` | Gauge | When last error occurred |

**Alert Categories:**
- `llm`: LLM inference failures
- `database`: Database connection/query errors
- `task_execution`: Task processing errors
- `timeout`: Operation timeouts

---

## Installation

### Prerequisites

- **Python 3.8+**
- **Docker** (for containerized Prometheus/Grafana)
- **prometheus-client** library (automatically installed with vidops dependencies)

### Step 1: Install prometheus-client

```bash
cd /home/billie/tools/vidops
pip install prometheus-client
```

### Step 2: Install Prometheus (Local)

**Option A: Docker (Recommended)**

```bash
docker run -d \
  --name prometheus \
  -p 9090:9090 \
  --volume $(pwd)/config/prometheus.yml:/etc/prometheus/prometheus.yml:ro \
  --volume $(pwd)/config/alert_rules.yml:/etc/prometheus/alert_rules.yml:ro \
  --volume prometheus_data:/prometheus \
  prom/prometheus \
  --config.file=/etc/prometheus/prometheus.yml \
  --storage.tsdb.path=/prometheus \
  --web.console.libraries=/usr/share/prometheus/console_libraries \
  --web.console.templates=/usr/share/prometheus/consoles
```

**Option B: Binary (macOS/Linux)**

```bash
# Download
wget https://github.com/prometheus/prometheus/releases/download/v2.48.1/prometheus-2.48.1.linux-amd64.tar.gz
tar xzf prometheus-2.48.1.linux-amd64.tar.gz
cd prometheus-2.48.1.linux-amd64

# Run
./prometheus --config.file=../vidops/config/prometheus.yml
```

### Step 3: Install Grafana (Local)

```bash
docker run -d \
  --name grafana \
  -p 3000:3000 \
  -e GF_SECURITY_ADMIN_PASSWORD=admin \
  -e GF_SECURITY_ADMIN_USER=admin \
  -v grafana_data:/var/lib/grafana \
  grafana/grafana:latest
```

### Step 4: Install AlertManager (Optional)

```bash
docker run -d \
  --name alertmanager \
  -p 9093:9093 \
  --volume $(pwd)/config/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro \
  prom/alertmanager \
  --config.file=/etc/alertmanager/alertmanager.yml
```

---

## Configuration

### Worker Configuration

Edit `/home/billie/tools/vidops/configuration.py` (if needed) to adjust metrics:

```python
# Metrics collection is enabled by default
# To disable metrics collection, set in worker CLI:
# python3 vo_cli.py worker start analysis-distributed --no-metrics
```

### Prometheus Configuration

Edit `/home/billie/tools/vidops/config/prometheus.yml`:

```yaml
# Change scrape interval (default: 15 seconds)
global:
  scrape_interval: 10s
  evaluation_interval: 10s

# Add remote workers
scrape_configs:
  - job_name: 'analysis_workers'
    static_configs:
      - targets:
          - 'localhost:8888'
          - 'worker1.example.com:8888'
          - 'worker2.example.com:8888'
```

### AlertManager Configuration

Create `/home/billie/tools/vidops/config/alertmanager.yml`:

```yaml
global:
  resolve_timeout: 5m
  slack_api_url: 'https://hooks.slack.com/services/YOUR/WEBHOOK/URL'

route:
  receiver: 'default'
  group_by: ['job', 'instance']
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 4h

  routes:
    - match:
        severity: critical
      receiver: 'critical'
      continue: true

receivers:
  - name: 'default'
    slack_configs:
      - channel: '#monitoring'
        title: 'Analysis Worker Alert'
        text: '{{ range .Alerts }}{{ .Annotations.description }}{{ end }}'

  - name: 'critical'
    slack_configs:
      - channel: '#critical-alerts'
        title: 'CRITICAL: {{ .Alerts.Firing | len }} alerts'
```

---

## Running the Stack

### Single Machine (Development)

```bash
# Terminal 1: Start Prometheus
docker start prometheus
# or: ./prometheus --config.file=config/prometheus.yml

# Terminal 2: Start Grafana
docker start grafana
# or: Access http://localhost:3000

# Terminal 3: Start worker with metrics
cd /home/billie/tools/vidops
python3 vo_cli.py worker start analysis-distributed \
    --machine-alias local-gpu-0 \
    --model-url http://localhost:11434 \
    --model-name qwen2.5:7b-instruct \
    --capabilities gpu_8gb \
    --capabilities qwen2.5:7b-instruct
```
Note: `--capabilities` are legacy tags only; VRAM/profile ids drive analysis scheduling.

### Multiple Workers

```bash
# Terminal 1: Prometheus
docker start prometheus

# Terminal 2: Grafana
docker start grafana

# Terminal 3: Worker 1 (metrics on 8888)
python3 vo_cli.py worker start analysis-distributed \
    --machine-alias local-gpu-0 \
    --metrics-port 8888

# Terminal 4: Worker 2 (metrics on 8889)
python3 vo_cli.py worker start analysis-distributed \
    --machine-alias local-gpu-1 \
    --metrics-port 8889

# Prometheus will scrape both automatically (see config/prometheus.yml)
```

---

## Dashboard Access

### Initial Setup

1. Open http://localhost:3000
2. Login with credentials:
   - Username: `admin`
   - Password: `admin` (or your configured password)
3. Change password (recommended)

### Import Grafana Dashboard

**Method 1: Direct Import**

1. Click **+** → **Import**
2. Upload JSON file: `vidops/monitoring/dashboards/analysis_worker_dashboard.json`
3. Select Prometheus data source
4. Click **Import**

**Method 2: Via Code**

```bash
curl -X POST \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer YOUR_API_TOKEN' \
  -d @vidops/monitoring/dashboards/analysis_worker_dashboard.json \
  http://localhost:3000/api/dashboards/db
```

### Dashboard Panels

The analysis worker dashboard includes:

1. **Task Completion Rate** - Pie chart of tasks by pass type
2. **Task Success Rate** - Gauge showing % success
3. **Task Latency** - p95 and p99 latencies over time
4. **Task Throughput** - Tasks completed per second by worker
5. **Pending Tasks** - Queue depth over time
6. **Worker Uptime** - How long each worker has been running
7. **Memory Usage** - RSS memory by worker
8. **CPU Usage** - CPU utilization with thresholds
9. **LLM Inference Latency** - Model inference times (p95, p99)
10. **LLM Model Availability** - Model status (1=up, 0=down)
11. **Error Rate** - Errors per second by category

---

## Alert Rules

### Alert Severity Levels

**Warning** (yellow)
- High task failure rate (>10% for 5 min)
- High latency (p95 > thresholds)
- Large queue backlog (>100 tasks)
- Memory usage > 2GB

**Critical** (red)
- Task failure rate >25%
- Worker down >5 min
- Memory > 4GB (OOM risk)
- LLM model unavailable
- Queue backlog >500 tasks

### Viewing Active Alerts

```bash
# Via Prometheus UI
curl http://localhost:9090/api/v1/alerts

# Via Grafana
# Dashboard → Alerts tab shows all active/pending alerts
```

### Testing Alerts

```bash
# Trigger high failure rate alert (kill workers to cause failures)
# Kill worker: Ctrl+C in worker terminal
# Prometheus will mark worker down after 5 minutes

# Trigger queue backlog alert
# Create many tasks without running workers:
python3 test_distributed_analysis.py --ytid AxtuJ-IVOGA --num-chunks 1000
```

---

## Troubleshooting

### Metrics Not Appearing in Prometheus

**Problem:** Prometheus shows "no data" for worker metrics

**Solution:**
1. Verify worker is running with metrics enabled:
   ```bash
   curl http://localhost:8888/metrics
   ```
   Should return Prometheus format output

2. Check Prometheus scrape status:
   - Go to http://localhost:9090/targets
   - Look for `analysis_worker_local` job
   - Should show "UP" in green

3. Verify worker IP is reachable:
   ```bash
   ping localhost  # or worker hostname
   ```

### Grafana Dashboard Not Showing Data

**Problem:** Dashboard panels show "No data"

**Solution:**
1. Verify Prometheus data source is configured:
   - Settings → Data Sources → Prometheus
   - Test Connection button should succeed

2. Check dashboard time range:
   - Click time picker (top right)
   - Select "Last 1 hour" or similar
   - Ensure data has been collected

3. Verify metrics exist:
   - Go to Prometheus → Graph
   - Type metric name: `analysis_worker_tasks_claimed_total`
   - Should show results if data exists

### Prometheus Storage Growing Too Fast

**Problem:** `/prometheus` directory consuming lots of disk space

**Solution:**
1. Reduce retention in docker run command:
   ```bash
   docker run -d \
     --name prometheus \
     ... \
     prom/prometheus \
     --storage.tsdb.retention.time=7d  # Default: 15d
   ```

2. Or reduce scrape interval:
   ```yaml
   global:
     scrape_interval: 30s  # Increased from 15s
   ```

### Alerts Not Firing

**Problem:** You know there's an issue but no alert

**Solution:**
1. Check alert rules syntax:
   ```bash
   curl -X POST \
     -F 'files=@config/alert_rules.yml' \
     http://localhost:9090/api/v1/rules
   ```

2. Verify rule is loaded:
   - http://localhost:9090/alerts
   - Look for rule name in "Alerts" section

3. Check evaluation result:
   - Query the PromQL expression directly in Graph
   - Verify it returns values above threshold

### AlertManager Not Receiving Alerts

**Problem:** Prometheus has alerts but AlertManager shows none

**Solution:**
1. Verify AlertManager is running:
   ```bash
   curl http://localhost:9093/api/v1/alerts
   ```

2. Check Prometheus AlertManager config:
   ```yaml
   alerting:
     alertmanagers:
       - static_configs:
           - targets: ['localhost:9093']
   ```

3. Verify alert routing rules:
   - http://localhost:9093 (AlertManager web UI)
   - Check routing tree and receivers

---

## Docker Deployment

### Docker Compose Stack

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./config/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./config/alert_rules.yml:/etc/prometheus/alert_rules.yml:ro
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--storage.tsdb.retention.time=30d'
    networks:
      - monitoring

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_SECURITY_ADMIN_USER=admin
      - GF_INSTALL_PLUGINS=grafana-piechart-panel
    volumes:
      - grafana_data:/var/lib/grafana
    networks:
      - monitoring

  alertmanager:
    image: prom/alertmanager:latest
    ports:
      - "9093:9093"
    volumes:
      - ./config/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro
    command:
      - '--config.file=/etc/alertmanager/alertmanager.yml'
    networks:
      - monitoring

volumes:
  prometheus_data:
  grafana_data:

networks:
  monitoring:
    driver: bridge
```

**Start the stack:**
```bash
docker-compose up -d
```

**Stop the stack:**
```bash
docker-compose down
```

---

## Performance Tips

### Optimize Prometheus

1. **Reduce metric cardinality** (avoid high-cardinality labels):
   ```promql
   # BAD: High cardinality per task
   analysis_worker_tasks_by_task_id

   # GOOD: Grouped by pass
   analysis_worker_tasks_total{pass_id="chunk_analysis"}
   ```

2. **Use recording rules** for frequent queries:
   ```yaml
   # Add to alert_rules.yml
   - record: 'worker:task_success_rate:5m'
     expr: |
       (sum(rate(analysis_worker_tasks_completed_total{status="success"}[5m])) by (worker_id)
        / sum(rate(analysis_worker_tasks_completed_total[5m])) by (worker_id)) * 100
   ```

3. **Increase scrape interval** for less critical metrics:
   ```yaml
   scrape_configs:
     - job_name: 'analysis_workers'
       scrape_interval: 30s  # Increased from 15s
   ```

### Optimize Grafana

1. **Use query caching** in dashboard settings
2. **Set appropriate refresh intervals** (e.g., 30s instead of 5s for non-critical dashboards)
3. **Use recording rules** for complex queries

---

## Resources

- [Prometheus Best Practices](https://prometheus.io/docs/practices/naming/)
- [Grafana Documentation](https://grafana.com/docs/)
- [AlertManager Routing](https://prometheus.io/docs/alerting/latest/routing/)
- [prometheus-client Python Library](https://github.com/prometheus/client_python)

---

**Next Steps:**
- Set up AlertManager for Slack/email notifications
- Configure recording rules for common queries
- Add Prometheus to systemd service (monitoring the monitor!)
- Explore Grafana plugins for advanced visualizations
