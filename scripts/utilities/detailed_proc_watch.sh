#!/usr/bin/env bash
# Detailed process and memory tracking for transcription

echo "=== Detailed Process Memory Tracker ==="
echo ""

watch_interval=3

while true; do
    clear
    echo "=== $(date) ==="
    echo ""

    echo "--- Memory Overview ---"
    free -h
    echo ""

    echo "--- Python Workers (sorted by memory) ---"
    ps aux --sort=-%mem | grep -E "python.*dual_gpu_transcribe|python.*EXTRACT|python.*NV_RUNNER|python.*AMD_RUNNER" | grep -v grep | head -10 | awk '{printf "PID:%s USER:%s MEM:%s VSZ:%s RSS:%s CMD:%s\n", $2, $1, $4, $5, $6, substr($0, index($0,$11))}'
    echo ""

    echo "--- FFmpeg Processes ---"
    ps aux | grep ffmpeg | grep -v grep | awk '{printf "PID:%s MEM:%s CPU:%s CMD:%s\n", $2, $4, $3, substr($0, index($0,$11))}'
    echo ""

    echo "--- Process Counts ---"
    printf "Python workers: %d\n" $(pgrep -f "python.*dual_gpu_transcribe" | wc -l)
    printf "FFmpeg: %d\n" $(pgrep ffmpeg | wc -l)
    printf "Total processes: %d\n" $(ps aux | wc -l)
    echo ""

    echo "--- Top 5 Memory Consumers ---"
    ps aux --sort=-%mem | head -6 | tail -5 | awk '{printf "%s: %s MEM:%s%% RSS:%sMB\n", $2, $11, $4, $6/1024}'

    sleep $watch_interval
done
