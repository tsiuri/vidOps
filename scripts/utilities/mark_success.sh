#!/usr/bin/env bash
# mark_success.sh — mark a single video ID as successfully downloaded in pull logs
# Usage: mark_success.sh <video_id> <base_prefix>
# Where base_prefix is logs/pull/<ts> or a provided prefix via PULL_LOG_PREFIX
set -euo pipefail
vid="${1:-}"
base="${2:-}"
[[ -n "$vid" && -n "$base" ]] || exit 0

planned="${base}.planned.tsv"
pending="${base}.pending.tsv"
succ="${base}.succeeded.tsv"

# If pending/planned do not exist, nothing to do
[[ -f "$planned" && -f "$pending" ]] || exit 0

# Find the canonical line for this id from planned
line="$(grep -m1 -F "${vid}	" "$planned" || true)"
[[ -n "$line" ]] || exit 0

# Append to succeeded if not already present
if ! grep -q -F "${vid}	" "$succ" 2>/dev/null; then
  printf '%s\n' "$line" >> "$succ"
fi

# Remove from pending (by id)
tmp="${pending}.tmp$$"
awk -v id="$vid" -F '\t' 'BEGIN{OFS="\t"} $1!=id { print $0 }' "$pending" > "$tmp" 2>/dev/null || cp "$pending" "$tmp" || true
mv "$tmp" "$pending" 2>/dev/null || true

exit 0

