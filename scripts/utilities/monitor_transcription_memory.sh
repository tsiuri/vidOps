#!/usr/bin/env bash
# Monitor memory usage during transcription

echo "=== Transcription Memory Monitor ==="
echo "Timestamp | Total RAM Used | Python Procs | FFmpeg Procs | Top Process"
echo "=========================================================================="

while true; do
    timestamp=$(date '+%H:%M:%S')

    # Total memory used (GB)
    mem_used=$(free -g | awk '/^Mem:/ {print $3}')
    mem_total=$(free -g | awk '/^Mem:/ {print $2}')

    # Count Python processes
    python_count=$(pgrep -f "python.*dual_gpu_transcribe" | wc -l)

    # Count FFmpeg processes
    ffmpeg_count=$(pgrep ffmpeg | wc -l)

    # Find top memory consumer
    top_proc=$(ps aux --sort=-%mem | head -2 | tail -1 | awk '{printf "%s (PID:%s) %.1fGB", $11, $2, $6/1024/1024}')

    printf "%s | %sG/%sG | Python:%d | FFmpeg:%d | %s\n" \
        "$timestamp" "$mem_used" "$mem_total" "$python_count" "$ffmpeg_count" "$top_proc"

    sleep 2
done
