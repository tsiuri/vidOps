#!/usr/bin/env bash
# Wrapper script for backward compatibility
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/scripts/video_processing/concat_filter_from_list.sh" "$@"
