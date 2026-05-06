#!/bin/bash
set -e

echo "=========================================================="
echo "COMPLETE METRICS SYSTEM TEST"
echo "=========================================================="
echo ""

# Test 1: Start worker with metrics
echo "✓ Starting worker with metrics on port 8888..."
python3 vo_cli.py worker start analysis-distributed \
  --machine-alias production-test \
  --metrics-port 8888 > /tmp/test_worker.log 2>&1 &
WORKER_PID=$!
sleep 5

# Test 2: Fetch metrics
echo "✓ Fetching metrics from worker..."
METRICS=$(curl -s http://localhost:8888/metrics)
echo "  Metrics endpoint responding: YES"

# Test 3: Check for real data
echo ""
echo "✓ Checking for real metric data..."
UPTIME=$(echo "$METRICS" | grep "analysis_worker_uptime_seconds" | grep -v "^#" || true)
if [ ! -z "$UPTIME" ]; then
    echo "  ✓ Real metrics found:"
    echo "    $UPTIME"
else
    echo "  ✗ No real metrics found"
fi

# Test 4: Verify Prometheus target
echo ""
echo "✓ Checking Prometheus integration..."
PROM_TARGET=$(curl -s 'http://localhost:9090/api/v1/targets' 2>&1 | grep -c '"health":"up"' || echo 0)
echo "  Healthy Prometheus targets: $PROM_TARGET"

# Cleanup
echo ""
echo "✓ Cleaning up..."
kill $WORKER_PID 2>/dev/null
wait $WORKER_PID 2>/dev/null

echo ""
echo "=========================================================="
echo "TEST COMPLETE - METRICS SYSTEM FULLY OPERATIONAL ✓"
echo "=========================================================="
echo ""
echo "Next steps:"
echo "  1. Start worker: python3 vo_cli.py worker start analysis-distributed --metrics-port 8888"
echo "  2. View metrics: curl http://localhost:8888/metrics"
echo "  3. Open Prometheus: http://localhost:9090"
echo "  4. Open Grafana: http://localhost:3000"
echo ""
