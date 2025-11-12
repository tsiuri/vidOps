#!/usr/bin/env bash
set -euo pipefail
# fetch_clips_net.sh wanted_clips.tsv [outdir]
# Downloads only the requested time windows via yt-dlp --download-sections.

here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
[[ -f "$here/clips.env" ]] && source "$here/clips.env" || true

tsv="${1:-wanted_clips.tsv}"
outdir="${2:-${CLIPS_OUT:-clips}}"

mkdir -p "$outdir"
bash "$(dirname "$0")/validate_tsv.sh" "$tsv"

# We batch by URL to let yt-dlp group sections.
# Build a temp file per URL that contains the sections.
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

# map[url] -> sections file
awk -F'\t' 'NR>1{print $1 "\t" $2 "\t" $3 "\t" $4}' "$tsv" \
| python3 - "$tmpdir" <<'PY'
import sys, os, collections, re
tmpdir=sys.argv[1]
by_url=collections.defaultdict(list)
for line in sys.stdin:
    url,s,e,label=line.rstrip('\n').split('\t',3)
    by_url[url].append((float(s), float(e), label))
for url, items in by_url.items():
    # yt-dlp expects sections like: "*start-end"
    # Keep a stable order
    items.sort(key=lambda x:(x[0],x[1]))
    path=os.path.join(tmpdir, re.sub(r'[^A-Za-z0-9._-]+','_',url)+".sections.txt")
    with open(path,'w') as f:
        for s,e,label in items:
            f.write(f"*{s}-{e}\n")
    print(url+"\t"+path)
PY \
| while IFS=$'\t' read -r url secfile; do
    echo "==> Downloading sections for: $url"
    yt-dlp \
      --download-sections "$(tr '\n' ',' < "$secfile" | sed 's/,$//')" \
      --sleep-requests "${YT_SLEEP_REQUESTS:-2}" \
      --sleep-interval "${YT_SLEEP_INTERVAL:-1}" \
      --max-sleep-interval "${YT_MAX_SLEEP_INTERVAL:-5}" \
      --retries "${YT_RETRIES:-15}" \
      --fragment-retries "${YT_FRAG_RETRIES:-20}" \
      --extractor-retries "${YT_EXTRACTOR_RETRIES:-10}" \
      --concurrent-fragments 1 \
      --no-cookies \
      --extractor-args "youtube:player_client=android,web_embedded,default,-tv" \
      --write-info-json \
      --embed-metadata \
      --ignore-no-formats-error -ciw --no-overwrites \
      -o "${outdir}/%(id)s_%(title).${NAME_MAX_TITLE:-70}B_%(section_number)02d.%(ext)s" \
      "$url"
  done

echo "All requested clip windows fetched into: $outdir"
