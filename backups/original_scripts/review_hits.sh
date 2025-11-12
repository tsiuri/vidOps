#!/usr/bin/env bash
set -euo pipefail
# review_hits.sh wanted_clips.tsv — prints counts, first/last, and a sample table

tsv="${1:-wanted_clips.tsv}"
[[ -r "$tsv" ]] || { echo "Not readable: $tsv" >&2; exit 1; }

echo "File: $tsv"
echo
echo "Counts by URL:"
awk -F'\t' 'NR>1{c[$1]++} END{for(k in c) printf "%5d  %s\n", c[k], k}' "$tsv" \
| sort -nr | head -n 20

echo
echo "First 10:"
awk -F'\t' 'NR==1{print; next} NR<=11{print}' OFS='\t' "$tsv" | column -ts $'\t'

echo
echo "Last 10:"
tac "$tsv" | awk -F'\t' 'NR==1{print; next} NR<=11{print}' OFS='\t' | tac | column -ts $'\t'
