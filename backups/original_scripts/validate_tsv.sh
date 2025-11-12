#!/usr/bin/env bash
set -euo pipefail

# validate_tsv.sh file.tsv — ensures header & basic typing

if [[ $# -ne 1 ]]; then
  echo "Usage: validate_tsv.sh file.tsv" >&2
  exit 1
fi

tsv="$1"
[[ -r "$tsv" ]] || { echo "Not readable: $tsv" >&2; exit 1; }

# Must have header
read -r hdr < "$tsv" || { echo "Empty file: $tsv" >&2; exit 1; }
if [[ "$hdr" != $'url\tstart\tend\tlabel\tsource_caption' ]]; then
  echo "Fixing/adding header…" >&2
  tmp="$(mktemp)"
  echo -e "url\tstart\tend\tlabel\tsource_caption" > "$tmp"
  awk 'NR==1 && $1=="url"{next}{print}' OFS='\t' "$tsv" >> "$tmp"
  mv "$tmp" "$tsv"
fi

# Check numeric fields
awk -F'\t' 'NR==1{next}
{ok=1;
 if(!($2 ~ /^-?[0-9]*\.?[0-9]+$/)) {print "Non-numeric start @ line " NR ": " $0 > "/dev/stderr"; ok=0}
 if(!($3 ~ /^-?[0-9]*\.?[0-9]+$/)) {print "Non-numeric end   @ line " NR ": " $0 > "/dev/stderr"; ok=0}
 if(ok==1 && $3 <= $2) {print "end<=start @ line " NR ": " $0 > "/dev/stderr"}
}' "$tsv" || true

echo "Validated: $tsv"
