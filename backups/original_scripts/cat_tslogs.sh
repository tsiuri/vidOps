#!/usr/bin/env bash
# cat_tslogs.sh – concatenate all .tslog.txt files into one searchable log
# Usage: ./cat_tslogs.sh > all_tslogs.txt

set -euo pipefail

find . -type f -name "*.tslog.txt" -print0 | sort -z | while IFS= read -r -d '' f; do
  echo "===== $(realpath --relative-to=. "$f") ====="
  cat "$f"
  echo
done
