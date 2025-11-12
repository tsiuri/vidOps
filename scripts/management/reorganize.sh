#!/usr/bin/env bash
# Reorganization script - moves files to new structure
# Creates manifest for tracking and potential rollback

set -euo pipefail

MANIFEST="reorganization_manifest.txt"
echo "# Reorganization Manifest - $(date)" > "$MANIFEST"
echo "# Format: OLD_PATH -> NEW_PATH" >> "$MANIFEST"

# Function to move and log
move_file() {
    local src="$1"
    local dest="$2"
    if [[ -e "$src" ]]; then
        mv "$src" "$dest"
        echo "$src -> $dest" >> "$MANIFEST"
        echo "Moved: $(basename "$src") -> $dest"
    fi
}

echo "==> Moving voice filtering scripts..."
move_file "filter_hasan_voice.py" "scripts/voice_filtering/"
move_file "filter_hasan_voice_parallel.py" "scripts/voice_filtering/"
move_file "filter_hasan_voice_parallel_chunked.py" "scripts/voice_filtering/"
move_file "filter_hasan_voice_batched.py" "scripts/voice_filtering/"

echo "==> Moving video processing scripts..."
move_file "stitch_videos.sh" "scripts/video_processing/"
move_file "stitch_videos_cfr.sh" "scripts/video_processing/"
move_file "stitch_videos_batched.sh" "scripts/video_processing/"
move_file "stitch_videos_batched_filter.sh" "scripts/video_processing/"
move_file "stitch_videos_concat_filter.sh" "scripts/video_processing/"
move_file "concat_filter_from_list.sh" "scripts/video_processing/"

echo "==> Moving date management scripts..."
move_file "extract_and_compare_dates.py" "scripts/date_management/"
move_file "find_missing_dates.py" "scripts/date_management/"
move_file "move_files_by_date.py" "scripts/date_management/"
move_file "create_download_list.py" "scripts/date_management/"

echo "==> Moving transcription scripts..."
move_file "dual_gpu_transcribe.sh" "scripts/transcription/"
move_file "dual_gpu_transcribe.sh_bakshort" "backups/"

echo "==> Moving utility scripts..."
move_file "clips.sh" "scripts/utilities/"
move_file "pull.sh" "scripts/utilities/"
move_file "sort_clips.py" "scripts/utilities/"
move_file "quality_report.py" "scripts/utilities/"
move_file "find_word_hits.py" "scripts/utilities/"
move_file "migrate_to_new_structure.sh" "backups/"

echo "==> Moving GPU tools..."
move_file "gpu-bind-status.sh" "scripts/gpu_tools/"
move_file "gpu-to-nvidia.sh" "scripts/gpu_tools/"

echo "==> Moving data files..."
move_file "dates_to_pull_from_2024and5.txt" "data/"
move_file "missing_dates.txt" "data/"
move_file "hotwords.txt" "data/"
move_file ".clips-download-archive.txt" "data/"
move_file "wanted_exact.tsv" "data/"
move_file "wanted_midway.tsv" "data/"
move_file "downloaded.txt" "data/"

echo "==> Moving result files..."
move_file "date_comparison.txt" "results/"
move_file "temp.txt" "results/"
if [[ -d "voice_filtered" ]]; then
    mv voice_filtered/* results/voice_filtered/ 2>/dev/null || true
    rmdir voice_filtered
    echo "voice_filtered/ -> results/voice_filtered/" >> "$MANIFEST"
fi

echo "==> Moving log files..."
move_file "stage1.log" "logs/stitching/"
move_file "stitch_filter_run.log" "logs/stitching/"
move_file "stitch_filter_run2.log" "logs/stitching/"
if [[ -d "logs/old_logs" ]]; then
    # Move existing logs directory contents
    [[ -d "logs" ]] && mv logs logs_old && mv logs_old/* logs/ 2>/dev/null || true
fi

echo "==> Moving backup/archive directories..."
if [[ -d "original_scripts" ]]; then
    rm -rf backups/original_scripts 2>/dev/null || true
    mv original_scripts backups/
    echo "original_scripts/ -> backups/original_scripts/" >> "$MANIFEST"
fi

echo "==> Moving config files..."
move_file "clips.env" "config/"

echo "==> Moving media directories..."
if [[ -d "cuts" ]]; then
    mv cuts media/clips/
    echo "cuts/ -> media/clips/cuts/" >> "$MANIFEST"
fi

echo "==> Moving clips directory to utilities..."
if [[ -d "clips" ]]; then
    mv clips scripts/utilities/clips_templates
    echo "clips/ -> scripts/utilities/clips_templates/" >> "$MANIFEST"
fi

echo "==> Cleanup..."
# Keep voice_filter_env in root for now (Python venv should stay at root)
echo ""
echo "==> Reorganization complete!"
echo "==> Manifest saved to: $MANIFEST"
echo ""
echo "Files not moved (intentionally kept in place):"
echo "  - voice_filter_env/ (Python virtual environment)"
echo "  - .claude/ (Claude configuration)"
