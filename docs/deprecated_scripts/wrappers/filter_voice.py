#!/usr/bin/env bash
# Wrapper script for backward compatibility
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/scripts/voice_filtering/filter_voice.py" "$@"
