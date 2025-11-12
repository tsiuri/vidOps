#!/usr/bin/env bash
# Wrapper script for backward compatibility
# Ensures clips.sh runs from workspace root so pull/ goes in the right place

WRAPPER_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$WRAPPER_DIR" || exit 1
exec "$WRAPPER_DIR/scripts/utilities/clips.sh" "$@"
