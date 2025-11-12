#!/usr/bin/env bash
set -euo pipefail

# find_hits.sh — wrapper around find_hits.py with sane defaults + docs
# Requires: find_hits.py in PATH or CWD. Expects it prints TSV:
#   url \t start \t end \t label \t source_caption
# Tip: You can pass multiple queries separated by commas: "dog,like,prong collar"

usage() {
  cat <<'EOF'
Usage:
  find_hits.sh "query" [window_seconds] [output.tsv]
  find_hits.sh -q "dog,like" -w 7 -o wanted_clips.tsv

Options:
  -q, --query STRING          Search terms; comma-separated for OR.
  -w, --window SECONDS        Window seconds around a hit (default: 7)
  -o, --out FILE              Output TSV (default: wanted_clips.tsv)
  -h, --help                  Show help

Examples:
  find_hits.sh "like" 7
  find_hits.sh -q "shock collar,prong" -w 6 -o prong.tsv
EOF
}

Q=""; W=""; OUT=""
# quick arg parse
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0;;
    -q|--query) Q="${2:-}"; shift 2;;
    -w|--window) W="${2:-}"; shift 2;;
    -o|--out) OUT="${2:-}"; shift 2;;
    *) # positional fallback
       if [[ -z "$Q" ]]; then Q="$1"
       elif [[ -z "$W" ]]; then W="$1"
       elif [[ -z "$OUT" ]]; then OUT="$1"
       else echo "Too many args"; usage; exit 1; fi
       shift;;
  esac
done

Q="${Q:-like}"
W="${W:-7}"
OUT="${OUT:-wanted_clips.tsv}"

# Normalize commas to OR calls on find_hits.py (call once per term, then merge)
tmp="$(mktemp)"
echo -e "url\tstart\tend\tlabel\tsource_caption" > "$tmp"

IFS=',' read -r -a TERMS <<< "$Q"
for term in "${TERMS[@]}"; do
  t="$(echo "$term" | sed 's/^ *//;s/ *$//')"
  [[ -z "$t" ]] && continue
  # Your original interface:
  # python3 find_hits.py 'like' 7 > wanted_clips.tsv
  python3 find_hits.py "$t" "$W" | awk 'NR==1 {next} {print}' >> "$tmp"
done

# De-dupe by url+start+end
awk -F'\t' 'BEGIN{print "url\tstart\tend\tlabel\tsource_caption"}
NR>1{key=$1 FS $2 FS $3;if(!seen[key]++){print}}' "$tmp" > "$OUT"
rm -f "$tmp"
echo "Wrote $OUT"
