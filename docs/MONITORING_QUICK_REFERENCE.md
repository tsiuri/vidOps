# Monitoring Quick Reference

**Essential commands and URLs for operating the monitoring stack**

---

## URLs

| Service | URL | Purpose |
|---------|-----|---------|
| **Prometheus** | http://localhost:9090 | Time series database & query engine |
| **Prometheus Targets** | http://localhost:9090/targets | Health of scraped endpoints |
| **Prometheus Alerts** | http://localhost:9090/alerts | Current active/pending alerts |
| **Grafana** | http://localhost:3000 | Dashboards & visualization |
| **Grafana Admin** | http://localhost:3000/admin | Settings & data sources |
| **AlertManager** | http://localhost:9093 | Alert routing & notifications |
| **AlertManager Alerts** | http://localhost:9093/api/v1/alerts | Alerting API |

---

## Starting Services

### Quick Start (All in Docker)
```bash
cd /home/billie/bq_netservices/vidops
docker-compose -f docker-compose.monitoring.yml up -d
```

### Individual Services
```bash
# Prometheus
docker run -d --name prometheus -p 9090:9090 \
  -v $(pwd)/config/prometheus.yml:/etc/prometheus/prometheus.yml:ro \
  prom/prometheus

# Grafana
docker run -d --name grafana -p 3000:3000 \
  -e GF_SECURITY_ADMIN_PASSWORD=admin \
  grafana/grafana:latest

# AlertManager
docker run -d --name alertmanager -p 9093:9093 \
  -v $(pwd)/config/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro \
  prom/alertmanager
```

---

## Starting Worker with Metrics

### Default (Metrics on 8888)
```bash
python3 vo_cli.py worker start analysis-distributed \
    --machine-alias local-gpu-0 \
    --metrics-port 8888
```

### Custom Port
```bash
python3 vo_cli.py worker start analysis-distributed \
    --machine-alias local-gpu-0 \
    --metrics-port 8889
```

### Multiple Workers
```bash
# Terminal 1
python3 vo_cli.py worker start analysis-distributed \
    --machine-alias gpu-0 --metrics-port 8888

# Terminal 2
python3 vo_cli.py worker start analysis-distributed \
    --machine-alias gpu-1 --metrics-port 8889

# Terminal 3
python3 vo_cli.py worker start analysis-distributed \
    --machine-alias cpu-0 --metrics-port 8890
```

---

## Docker Commands

### View Logs
```bash
# All services
docker-compose -f docker-compose.monitoring.yml logs -f

# Single service
docker logs -f prometheus
docker logs -f grafana
docker logs -f alertmanager
```

### Stop Services
```bash
# Stop all
docker-compose -f docker-compose.monitoring.yml down

# Stop single
docker stop prometheus
docker stop grafana
docker stop alertmanager
```

### Restart Services
```bash
# Restart all
docker-compose -f docker-compose.monitoring.yml restart

# Restart single
docker restart prometheus
```

### Check Service Status
```bash
# All services
docker-compose -f docker-compose.monitoring.yml ps

# Single service
docker ps -f name=prometheus
```

---

## Health Checks

### Prometheus
```bash
# API endpoint test
curl -s http://localhost:9090/api/v1/query?query=up

# Target status
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets'

# Alert rules
curl -s http://localhost:9090/api/v1/rules
```

### Grafana
```bash
# Health endpoint
curl -s http://localhost:3000/api/health

# Data source test
curl -s -H "Authorization: Bearer YOUR_TOKEN" \
  http://localhost:3000/api/datasources
```

### Worker Metrics
```bash
# Verify metrics export
curl -s http://localhost:8888/metrics | head -20

# Check specific metric
curl -s http://localhost:8888/metrics | grep task_processing
```

---

## Common Queries

### Task Processing

```promql
# Success rate (%)
(sum(rate(analysis_worker_tasks_completed_total{status="success"}[5m]))
 / sum(rate(analysis_worker_tasks_completed_total[5m]))) * 100

# Tasks per second
sum(rate(analysis_worker_tasks_completed_total[5m])) by (worker_id)

# Failed tasks per second
sum(rate(analysis_worker_tasks_failed_total[5m])) by (error_type)

# p95 latency
histogram_quantile(0.95,
  sum(rate(analysis_worker_task_processing_duration_seconds_bucket[5m]))
  by (pass_id, le))
```

### Queue Status

```promql
# Pending tasks
analysis_worker_pending_tasks

# Total backlog (pending + claimed)
analysis_worker_pending_tasks + analysis_worker_claimed_not_completed
```

### Worker Health

```promql
# Worker uptime
analysis_worker_uptime_seconds

# Memory usage (GB)
analysis_worker_memory_usage_bytes / 1024 / 1024 / 1024

# CPU usage
analysis_worker_cpu_usage_percent
```

### LLM Metrics

```promql
# Model availability
analysis_worker_llm_model_available

# Inference latency p95
histogram_quantile(0.95,
  sum(rate(analysis_worker_llm_inference_duration_seconds_bucket[5m]))
  by (model_name, le))

# LLM error rate
sum(rate(analysis_worker_llm_inference_total{status="failed"}[5m])) by (model_name)
```

---

## Grafana Operations

### Login
- URL: http://localhost:3000
- Username: `admin`
- Password: `admin`
- **Change immediately in Settings**

### Import Dashboard
1. Click **+** (top menu)
2. Select **Import**
3. Choose file: `vidops/monitoring/dashboards/analysis_worker_dashboard.json`
4. Select Prometheus data source
5. Click **Import**

### Add Data Source
1. Settings (gear icon) → Data Sources
2. Click **Add data source**
3. Select **Prometheus**
4. URL: `http://prometheus:9090`
5. Click **Save & Test**

### Create Custom Dashboard
1. Click **+** → **Dashboard**
2. Click **Add panel**
3. Select Prometheus data source
4. Enter PromQL query
5. Configure visualization
6. Save dashboard

### Share Dashboard
1. Dashboard → Share (top right)
2. Copy link or generate snapshot
3. Select time range to include

---

## Alert Management

### View Active Alerts
```bash
# Prometheus UI
http://localhost:9090/alerts

# API
curl -s http://localhost:9090/api/v1/alerts | jq '.data.alerts'
```

### Silence Alert
```bash
# Via AlertManager UI
http://localhost:9093

# Via API (30 min silence)
curl -X POST http://localhost:9093/api/v1/silences \
  -H 'Content-Type: application/json' \
  -d '{
    "matchers": [{"name": "alertname", "value": "HighTaskFailureRate"}],
    "duration": "30m"
  }'
```

### Check Alert History
1. AlertManager UI: http://localhost:9093
2. Look for recent alerts in history
3. View status and routing

---

## Troubleshooting

### Prometheus Not Collecting Metrics

**Check target status:**
```bash
curl -s http://localhost:9090/api/v1/targets | jq '.data'
```

**Verify worker is exporting:**
```bash
curl -s http://localhost:8888/metrics | head -5
```

**Check Prometheus logs:**
```bash
docker logs prometheus | grep -i error
```

### Grafana Not Showing Data

**Verify data source:**
1. Settings → Data Sources
2. Click Prometheus
3. Click **Save & Test**

**Check time range:**
- Top right of dashboard
- Ensure it includes data collection time

**Query directly in Prometheus:**
- http://localhost:9090/graph
- Type metric name

### Alerts Not Firing

**Check alert rules loaded:**
```bash
curl -s http://localhost:9090/api/v1/rules | jq '.data'
```

**Verify PromQL expression:**
```bash
# In Prometheus Graph tab
# Paste expression and execute
```

**Check AlertManager configuration:**
```bash
curl -s http://localhost:9093/api/v1/alerts
```

---

## Performance Tuning

### Reduce Metric Storage
```bash
# Shorter retention (default: 30d)
docker run -d --name prometheus ... \
  prom/prometheus \
  --storage.tsdb.retention.time=7d
```

### Increase Scrape Interval
Edit `config/prometheus.yml`:
```yaml
global:
  scrape_interval: 30s  # Increased from 15s
```

### Disable Unnecessary Metrics
Comment out in `prometheus.yml`:
```yaml
scrape_configs:
  # - job_name: 'node'  # Disabled if not needed
```

---

## Database Cleanup

### Delete Old Data
```bash
# Via API
curl -X POST http://localhost:9090/api/v1/admin/tsdb/delete_series \
  -H 'Content-Type: application/json' \
  -d '{"matchers":["analysis_worker_"]}'
```

### Compact Database
```bash
# Via API
curl -X POST http://localhost:9090/api/v1/admin/tsdb/clean_tombstones
```

---

## Integration Testing

### Generate Test Metrics
```bash
# Create test task load
python3 test_distributed_analysis.py \
  --ytid AxtuJ-IVOGA \
  --num-chunks 100
```

### Monitor in Real-Time
```bash
# Grafana dashboard
# Open: http://localhost:3000
# Select dashboard: "Analysis Worker Monitoring"
# Adjust time range to "Last 5 minutes"
```

---

## Useful Shortcuts

| Action | Command |
|--------|---------|
| Check all containers | `docker ps` |
| Follow all logs | `docker-compose -f docker-compose.monitoring.yml logs -f` |
| Restart stack | `docker-compose -f docker-compose.monitoring.yml restart` |
| View Prometheus targets | `curl http://localhost:9090/api/v1/targets` |
| Query top metrics | `curl http://localhost:8888/metrics \| head -30` |
| Check worker process | `ps aux \| grep analysis-distributed` |

---

## Emergency Procedures

### Worker Not Responding

1. Check if process running: `ps aux | grep vo_cli`
2. View logs: Last 100 lines of worker output
3. Check metrics: `curl http://localhost:8888/metrics`
4. Restart: Kill process, restart

### Prometheus Storage Full

1. Check disk: `df -h /prometheus`
2. Reduce retention: Edit `prometheus.yml`
3. Compact database: `curl -X POST http://localhost:9090/api/v1/admin/tsdb/clean_tombstones`
4. Restart: `docker restart prometheus`

### Grafana Locked Out

1. Reset password: `docker exec grafana grafana-cli admin reset-admin-password newpassword`
2. Or restart: `docker restart grafana`

### Remove All Monitoring

```bash
# Stop containers
docker-compose -f docker-compose.monitoring.yml down

# Remove volumes
docker volume rm vidops_prometheus_data vidops_grafana_data vidops_alertmanager_data

# Delete containers
docker rm prometheus grafana alertmanager
```

---

## Key Files

| Path | Purpose |
|------|---------|
| `vidops/monitoring/metrics.py` | Metric definitions |
| `vidops/monitoring/exporter.py` | HTTP metrics server |
| `config/prometheus.yml` | Scrape configuration |
| `config/alert_rules.yml` | Alert definitions |
| `docker-compose.monitoring.yml` | Full stack compose |
| `docs/MONITORING_SETUP.md` | Full documentation |

---

## Support Resources

- **Prometheus Docs:** https://prometheus.io/docs/
- **Grafana Docs:** https://grafana.com/docs/
- **prometheus-client:** https://github.com/prometheus/client_python
- **Dashboard Examples:** https://grafana.com/grafana/dashboards/
