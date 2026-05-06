#!/usr/bin/env bash
# Wrapper script for backward compatibility
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/scripts/video_processing/stitch_videos_concat_filter.sh" "$@"
