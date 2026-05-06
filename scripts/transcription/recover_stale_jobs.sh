#!/bin/bash
# Cron job to recover stale jobs (runs every 5 minutes)
# Add to crontab: */5 * * * * /home/billie/bq_netservices/vidops/scripts/transcription/recover_stale_jobs.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOL_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_FILE="$TOOL_ROOT/logs/recovery.log"

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

{
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Running stale job recovery..."

    # Run recovery using the Python CLI tool
    RECOVERED=$("$SCRIPT_DIR/queue_cli.py" recover-stale 2>&1)

    if echo "$RECOVERED" | grep -q "Recovered"; then
        echo "$RECOVERED"
    else
        echo "  No stale jobs found"
    fi

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Recovery complete"
    echo ""
} >> "$LOG_FILE" 2>&1

# Keep log file from growing too large (keep last 1000 lines)
if [ -f "$LOG_FILE" ]; then
    tail -n 1000 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi
