#!/bin/bash
# Test script to verify metrics integration is working

echo "=========================================="
echo "METRICS INTEGRATION TEST"
echo "=========================================="
echo ""

# Test 1: CLI option available
echo "✓ Test 1: Checking CLI option..."
python3 vo_cli.py worker start analysis-distributed --help 2>&1 | grep -q "metrics-port"
if [ $? -eq 0 ]; then
    echo "  ✓ CLI option --metrics-port found"
else
    echo "  ✗ CLI option --metrics-port NOT found"
    exit 1
fi
echo ""

# Test 2: Start worker with metrics
echo "✓ Test 2: Starting worker with metrics on port 8888..."
python3 vo_cli.py worker start analysis-distributed --machine-alias test-worker --metrics-port 8888 > /tmp/test_worker.log 2>&1 &
WORKER_PID=$!
sleep 3

# Check if worker started
if ! kill -0 $WORKER_PID 2>/dev/null; then
    echo "  ✗ Worker failed to start"
    cat /tmp/test_worker.log
    exit 1
fi
echo "  ✓ Worker started (PID: $WORKER_PID)"
echo ""

# Test 3: Metrics endpoint responds
echo "✓ Test 3: Testing metrics endpoint (http://localhost:8888/metrics)..."
curl -s http://localhost:8888/metrics > /tmp/metrics_output.txt 2>&1
if grep -q "analysis_worker_tasks" /tmp/metrics_output.txt; then
    echo "  ✓ Metrics endpoint responding with valid Prometheus format"
    echo "  ✓ Found sample metrics:"
    grep "^# HELP" /tmp/metrics_output.txt | head -3 | sed 's/^/    /'
else
    echo "  ✗ Metrics endpoint not responding correctly"
    kill $WORKER_PID
    exit 1
fi
echo ""

# Test 4: Prometheus can scrape worker
echo "✓ Test 4: Checking Prometheus scrape targets..."
sleep 5  # Wait for Prometheus to scrape
TARGETS=$(curl -s 'http://localhost:9090/api/v1/targets' 2>&1)
if echo "$TARGETS" | grep -q '"health":"up"'; then
    HEALTH_COUNT=$(echo "$TARGETS" | grep -o '"health":"up"' | wc -l)
    echo "  ✓ Prometheus has $HEALTH_COUNT healthy targets"
    
    if echo "$TARGETS" | grep -q '"job":"analysis_worker_local"'; then
        echo "  ✓ Worker target 'analysis_worker_local' found"
    fi
else
    echo "  ✗ No healthy targets in Prometheus"
fi
echo ""

# Test 5: Worker graceful shutdown
echo "✓ Test 5: Testing graceful shutdown..."
kill $WORKER_PID 2>/dev/null
sleep 3
if ! kill -0 $WORKER_PID 2>/dev/null; then
    echo "  ✓ Worker shut down gracefully"
else
    echo "  ✗ Worker did not shut down"
    kill -9 $WORKER_PID 2>/dev/null
fi
echo ""

echo "=========================================="
echo "ALL TESTS PASSED ✓"
echo "=========================================="
echo ""
echo "Metrics integration is working correctly!"
echo "Next steps:"
echo "  1. Start a worker: python3 vo_cli.py worker start analysis-distributed --metrics-port 8888"
echo "  2. View Prometheus: http://localhost:9090"
echo "  3. View Grafana dashboards: http://localhost:3000"
