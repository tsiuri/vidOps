#!/usr/bin/env bash
# Wrapper script for backward compatibility
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/scripts/date_management/extract_and_compare_dates.py" "$@"
