#!/usr/bin/env bash
# Wrapper script for backward compatibility
# Ensures clips.sh runs in project directory

# Tool installation directory
TOOL_ROOT="$(cd "$(dirname "$0")" && pwd)"

# Project directory (where data lives) - current working directory
export PROJECT_ROOT="${PROJECT_ROOT:-$(pwd)}"
export TOOL_ROOT

# Don't change directory - stay in project directory
exec "$TOOL_ROOT/scripts/utilities/clips.sh" "$@"
