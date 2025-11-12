#!/usr/bin/env bash
# shellcheck shell=bash

cmd_cut_net(){
  # Modes:
  #  1) Default: cut-net <wanted.tsv> [outdir]
  #  2) Exact-from-words mode: cut-net --from-words-exact <file|dir> -q "term,phrase" [outdir]

  local WORDS_EXACT_SRC=""; local TERMS=""; local OUTDIR=""; local TSV="";
  # Allow per-invocation padding overrides (seconds)
  local PAD_START_LOCAL=""; local PAD_END_LOCAL="";
  # Format/metadata knobs (defaults: best MP4; no section metadata/infojson)
  local ALLOW_ANY_FORMAT=0; local WANT_INFOJSON=0; local WANT_EMBED_METADATA=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --from-words-exact)
        WORDS_EXACT_SRC="${2:-}"; shift 2;;
      -q|--query)
        TERMS="${2:-}"; shift 2;;
      --pad-start)
        PAD_START_LOCAL="${2:-}"; shift 2;;
      --pad-end)
        PAD_END_LOCAL="${2:-}"; shift 2;;
      --allow-any-format|--negotiate-format)
        ALLOW_ANY_FORMAT=1; shift;;
      --write-info-json)
        WANT_INFOJSON=1; shift;;
      --embed-metadata)
        WANT_EMBED_METADATA=1; shift;;
      -o|--outdir)
        OUTDIR="${2:-}"; shift 2;;
      -h|--help)
        cat <<H
Usage:
  clips.sh cut-net <wanted.tsv> [outdir]
  clips.sh cut-net --from-words-exact <file|dir> -q "term,phrase" [--pad-start S] [--pad-end S] [outdir]

Notes:
  --from-words-exact scans words.tsv logs and builds exact [start,end] per match.
  Padding for --from-words-exact can be set via --pad-start/--pad-end (in seconds).
  If not set, it uses env PAD_START/PAD_END (default 0).

  TSV mode: you can also pass --pad-start/--pad-end to shift the start/end
  from the TSV by those amounts before downloading. This is additive to any
  padding that may have been baked into the TSV upstream.

 Formats:
   Default is best possible MP4 (AVC1 video + M4A audio) to minimize probing
   and yield MP4 outputs consistently. Use --allow-any-format to let yt-dlp
   negotiate any best format (may download WebM/Opus when better).

 Metadata:
   By default, section downloads do not save per-section info.json and do not
   embed metadata. Use --write-info-json and/or --embed-metadata to enable.
H
        return 0;;
      *)
        # First non-flag argument is TSV or OUTDIR depending on mode
        if [[ -z "$WORDS_EXACT_SRC" && -z "$TSV" ]]; then
          TSV="$1"; shift; continue
        fi
        if [[ -z "$OUTDIR" ]]; then OUTDIR="$1"; shift; continue; fi
        shift;;
    esac
  done

  if [[ -n "$WORDS_EXACT_SRC" ]]; then
    [[ -n "$TERMS" ]] || { c_er "cut-net: --from-words-exact requires -q TERMS"; exit 1; }
    OUTDIR="${OUTDIR:-$CLIP_NET_OUT}"
    mkdir -p "$OUTDIR"
    c_do "Fetching exact word spans from words.tsv (terms: $TERMS) → $OUTDIR"
    # Apply per-run padding overrides for exact mode
    if [[ -n "$PAD_START_LOCAL" ]]; then PAD_START="$PAD_START_LOCAL"; fi
    if [[ -n "$PAD_END_LOCAL" ]]; then PAD_END="$PAD_END_LOCAL"; fi
  else
    [[ -n "$TSV" ]] || { c_er "cut-net: need TSV"; exit 1; }
    [[ -r "$TSV" ]] || { c_er "not readable: $TSV"; exit 1; }
    OUTDIR="${OUTDIR:-${2:-$CLIP_NET_OUT}}"
    mkdir -p "$OUTDIR"
    c_do "Fetching exact windows from YouTube → $OUTDIR"
  fi

  # Cookie discovery prefs (Firefox first, only if needed)
  COOKIE_ARG=()
  : "${COOKIE_REQUIRE:=}"                 # empty → allow fallback; set to 'firefox' to require
  : "${COOKIE_BROWSER_ORDER:=firefox chrome chromium edge brave}"

  # Try to discover cookies early for metadata checks
  if [[ ${#COOKIE_ARG[@]} -eq 0 ]]; then
    c_do "Attempting to discover browser cookies..."
    if clips_discover_cookies 2>/dev/null; then
      c_ok "Found cookies from browser"
    else
      c_wr "No cookies found; YouTube may block formats or metadata access."
      c_wr "Tip: export cookies to ./cookies.txt or set YTDLP_COOKIES=/path/to/cookies.txt"
    fi
  fi

  # Build "URL<TAB>start-end,start-end,..." per URL (requires both start & end)
  if [[ -n "$WORDS_EXACT_SRC" ]]; then
    HITS_TERMS="$TERMS" PAD_START="$PAD_START" PAD_END="$PAD_END" python3 - <<'PY' "$WORDS_EXACT_SRC"
import sys, os, csv, re
csv.field_size_limit(10 * 1024 * 1024)
root = sys.argv[1]
phrases = [t.strip().lower() for t in os.environ.get('HITS_TERMS','').split(',') if t.strip()]
pad_pre = float(os.environ.get('PAD_START','0') or 0)
pad_post = float(os.environ.get('PAD_END','0') or 0)

def find_cols(header):
    h=[c.strip().lower() for c in header]
    def pick(*names):
        for n in names:
            if n in h:
                return h.index(n)
        return -1
    return {
        'word': pick('word','token','term'),
        'start': pick('start','start_sec','time','ts','offset'),
        'end': pick('end','end_sec','stop','to'),
        'id': pick('ytid','video_id','id'),
        'url': pick('url','webpage_url'),
    }

def guess_id_from_path(path):
    base=os.path.basename(path)
    m=re.match(r'([A-Za-z0-9_-]{11})__', base)
    if m:
        return m.group(1)
    m=re.match(r'([A-Za-z0-9_-]{11})__', os.path.basename(os.path.dirname(path)))
    if m:
        return m.group(1)
    return ''

def norm_token(w):
    return re.sub(r"[^a-z0-9']+", "", (w or '').strip().lower())

def iter_words_files(root):
    files=[]
    if os.path.isdir(root):
        for dp,_,fs in os.walk(root):
            for n in fs:
                if n=='words.tsv' or n.endswith('words.tsv'):
                    files.append(os.path.join(dp,n))
    else:
        files.append(root)
    return files

def matches_from_file(path):
    out=[]  # (url, start, end)
    with open(path,'r',encoding='utf-8',errors='ignore',newline='') as fh:
        reader = csv.reader(fh, delimiter='\t')
        try:
            header = next(reader)
        except StopIteration:
            return out
        cols = find_cols(header)
        rows=[row for row in reader if row]
    # Build normalized tokens list
    toks=[]
    for row in rows:
        def get(idx):
            return row[idx] if 0 <= idx < len(row) else ''
        w=get(cols['word']); s=get(cols['start']); e=get(cols['end'])
        try:
            s=float(s)
        except Exception:
            continue
        try:
            e=float(e)
        except Exception:
            e=None
        toks.append((norm_token(w), s, e))
    # Sequence match for each phrase across contiguous tokens
    url=''
    # Try to read url/id from first data row
    with open(path,'r',encoding='utf-8',errors='ignore',newline='') as fh:
        reader = csv.reader(fh, delimiter='\t')
        try:
            header = next(reader)
            cols = find_cols(header)
            first = next(reader)
            def get(idx):
                return first[idx] if 0 <= idx < len(first) else ''
            url = get(cols['url'])
            vid = get(cols['id'])
            if not url and vid:
                url=f"https://www.youtube.com/watch?v={vid}"
        except Exception:
            pass
    if not url:
        vid=guess_id_from_path(path)
        if vid:
            url=f"https://www.youtube.com/watch?v={vid}"
    if not url:
        return out

    n=len(toks)
    for phrase in phrases:
        target = [t for t in (norm_token(x) for x in phrase.split()) if t]
        if not target:
            continue
        m=len(target)
        for i in range(0, n-m+1):
            ok=True
            for k in range(m):
                if toks[i+k][0] != target[k]:
                    ok=False; break
            if not ok:
                continue
            s=toks[i][1]
            e=toks[i+m-1][2] if toks[i+m-1][2] is not None else toks[i+m-1][1]
            s=max(0.0, s - pad_pre)
            e=e + pad_post
            out.append((url, s, e))
    return out

by_url={}
for f in iter_words_files(root):
    for (u,s,e) in matches_from_file(f):
        by_url.setdefault(u, []).append((s,e))

for u, spans in by_url.items():
    spans = sorted(spans)
    joined = ",".join(f"{s:.3f}-{e:.3f}" for (s,e) in spans)
    sys.stdout.write(f"{u}\t{joined}\n")
PY
  else
    # Determine effective padding for TSV mode (flags override env; default 0)
    local _PAD_S="${PAD_START_LOCAL:-${PAD_START:-0}}"
    local _PAD_E="${PAD_END_LOCAL:-${PAD_END:-0}}"
    awk -F'\t' -v pad_start="${_PAD_S}" -v pad_end="${_PAD_E}" '
      NR==1 { next }                            # skip header
      $1=="" { next }                           # need URL
      {
        url=$1; s=$2; e=$3;
        if (s=="" || e=="") next;
        # apply padding; ensure non-negative start
        s = s + 0; e = e + 0;                   # force numeric
        s = s - pad_start; if (s < 0) s = 0;
        e = e + pad_end;
        sec = sprintf("%.3f-%.3f", s, e);
        if (list[url] == "") list[url] = sec;
        else list[url] = list[url] "," sec;
      }
      END { for (u in list) print u "\t" list[u]; }
    ' "$TSV"
  fi | while IFS=$'\t' read -r URL SECTIONS; do
        [[ -z "$URL" || -z "$SECTIONS" ]] && continue

        # Get video metadata to check for existing files
        c_do "Checking video metadata for: $URL"
        local metadata vid_id vid_title
        if ! metadata=$(get_video_metadata "$URL"); then
          c_er "Skipping $URL due to metadata failure"
          continue
        fi

        # Split on tab using bash parameter expansion (more reliable than IFS read in nested contexts)
        vid_id="${metadata%%$'\t'*}"
        vid_title="${metadata#*$'\t'}"

        if [[ -z "$vid_id" || -z "$vid_title" ]]; then
          c_er "Empty metadata fields for: $URL"
          c_er "  Got metadata: '$metadata'"
          c_er "  Skipping this URL"
          continue
        fi

        # Filter out sections that already exist
        c_do "Checking for existing clips of: $vid_title"
        local filtered_sections
        filtered_sections=$(filter_existing_sections "$vid_id" "$vid_title" "$SECTIONS" "$OUTDIR" "${NAME_MAX_TITLE:-80}")

        if [[ -z "$filtered_sections" ]]; then
          c_ok "All sections already downloaded for: $vid_title"
          continue
        fi

        # Update SECTIONS to only include missing sections
        SECTIONS="$filtered_sections"
        c_do "Downloading ${filtered_sections//,/ } for: $vid_title"

        # Base args (sleep/retry knobs from env if you set them)
        ybase=( yt-dlp --ignore-config -4 --no-playlist )
        ybase+=(
          --concurrent-fragments 1
          --force-keyframes-at-cuts
          --geo-bypass
          --sleep-requests "${YT_SLEEP_REQUESTS:-1}"
          --sleep-interval "${YT_SLEEP_INTERVAL:-2}"
          --max-sleep-interval "${YT_MAX_SLEEP_INTERVAL:-6}"
          --retries "${YT_RETRIES:-10}"
          --fragment-retries "${YT_FRAG_RETRIES:-10}"
          --extractor-retries "${YT_EXTRACTOR_RETRIES:-5}"
          --ignore-no-formats-error -ciw --no-overwrites
          -o "${OUTDIR}/%(id)s_%(title).${NAME_MAX_TITLE:-80}B_%(section_start)06.2f-%(section_end)06.2f.%(ext)s"
        )

        # Optional: re-enable info.json and/or metadata embedding
        if [[ $WANT_INFOJSON -eq 1 ]]; then ybase+=( --write-info-json ); fi
        if [[ $WANT_EMBED_METADATA -eq 1 ]]; then ybase+=( --embed-metadata ); fi

        # Build section args
        secargs=()

        # One --download-sections per time window
        IFS=',' read -r -a secs <<< "$SECTIONS"
        for sec in "${secs[@]}"; do
          sec="${sec//[[:space:]]/}"
          [[ -z "$sec" ]] && continue
          secargs+=( --download-sections "*${sec}" )
        done

        # Two-pass strategy: try preferred formats, then a permissive fallback
        # Add Referer/Origin to mimic browser context
        hdr=( --add-header "Referer: ${URL}" --add-header "Origin: https://www.youtube.com" )

        # Pass 1: no cookies; prefer stable clients
        pass1=( "${ybase[@]}" ${hdr[@]} --no-cookies --extractor-args "youtube:player_client=android,web_embedded,default,-tv" )
        if [[ $ALLOW_ANY_FORMAT -eq 1 ]]; then
          pass1+=( -f "b/bv*+ba" )
        else
          pass1+=( -f "bv*[ext=mp4][vcodec^=avc1]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b" --merge-output-format mp4 )
        fi
        pass1+=( ${secargs[@]} "$URL" )
        if ! "${pass1[@]}"; then
          c_wr "Primary fetch failed for $URL; trying fallback strategy"
          # Pass 2: discover cookies (Firefox first), then broaden clients
          if [[ ${#COOKIE_ARG[@]} -eq 0 ]]; then
            clips_discover_cookies || true
          fi
          pass2=( "${ybase[@]}" ${hdr[@]} ${COOKIE_ARG[@]:-} --extractor-args "youtube:player_client=android,ios,web_safari,web,web_embedded,default" )
          if [[ $ALLOW_ANY_FORMAT -eq 1 ]]; then
            pass2+=( -f "b/bv*+ba" )
          else
            pass2+=( -f "bv*[ext=mp4][vcodec^=avc1]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b" --merge-output-format mp4 )
          fi
          pass2+=( ${secargs[@]} "$URL" )
          if ! "${pass2[@]}"; then
            c_er "Failed to fetch sections for: $URL"
          fi
        fi
    done

  c_ok "Network section fetch complete."
}

# Extract YouTube video ID from URL
extract_video_id(){
  local url="$1"
  # Match various YouTube URL formats
  if [[ "$url" =~ (youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]{11}) ]]; then
    echo "${BASH_REMATCH[2]}"
  elif [[ "$url" =~ ^([A-Za-z0-9_-]{11})$ ]]; then
    echo "$1"
  else
    echo ""
  fi
}

# Get video metadata (title, id) without downloading
# Returns: video_id<TAB>video_title
get_video_metadata(){
  local url="$1"
  local tmpfile=$(mktemp)

  # Try with cookies if available
  local cookie_opts=()
  if [[ ${#COOKIE_ARG[@]} -gt 0 ]]; then
    cookie_opts=("${COOKIE_ARG[@]}")
  fi

  # Fetch metadata (errors go to tmpfile for debugging)
  # Use two separate --print calls to get id and title on separate lines
  local vid_id vid_title
  {
    read -r vid_id
    read -r vid_title
  } < <(yt-dlp --ignore-config -4 --no-playlist --skip-download \
    "${cookie_opts[@]}" \
    --print "%(id)s" --print "%(title)s" "$url" 2>"$tmpfile")

  if [[ -z "$vid_id" || -z "$vid_title" ]]; then
    # Show the actual error from yt-dlp
    if [[ -s "$tmpfile" ]]; then
      c_er "Metadata fetch failed for $url:"
      sed 's/^/    /' "$tmpfile" >&2
    fi
    rm -f "$tmpfile"
    return 1
  fi

  rm -f "$tmpfile"
  # Return with an actual tab separator
  printf '%s\t%s\n' "$vid_id" "$vid_title"
}

# Check which sections already exist in the output directory
# Args: video_id, video_title, sections_string, outdir, max_title_len
# Returns: filtered sections string (comma-separated) with missing sections only
filter_existing_sections(){
  local vid="$1"
  local title="$2"
  local sections="$3"
  local outdir="$4"
  local max_len="${5:-80}"

  # Truncate title to match yt-dlp's behavior (approximate)
  # yt-dlp uses bytes, but we'll use characters as an approximation
  local title_trunc="${title:0:$max_len}"

  # Build list of missing sections
  local missing_sections=()
  IFS=',' read -r -a sec_array <<< "$sections"

  for sec in "${sec_array[@]}"; do
    sec="${sec//[[:space:]]/}"
    [[ -z "$sec" ]] && continue

    # Parse start-end from section
    if [[ "$sec" =~ ^([0-9.]+)-([0-9.]+)$ ]]; then
      local start="${BASH_REMATCH[1]}"
      local end="${BASH_REMATCH[2]}"

      # Format times to match yt-dlp pattern: 06.2f means 6 digits total, 2 decimals
      # e.g., 123.45 -> 123.45, 12.3 -> 012.30
      local start_fmt=$(printf "%06.2f" "$start")
      local end_fmt=$(printf "%06.2f" "$end")

      # Build glob pattern: {vid}_*_{start}-{end}.{ext}
      # Use wildcard for title to avoid issues with spaces and special characters
      local pattern="${vid}_*_${start_fmt}-${end_fmt}.*"

      # Check if any file matches this pattern
      local found=0
      shopt -s nullglob
      local matches=("$outdir"/${pattern})
      shopt -u nullglob

      if [[ ${#matches[@]} -gt 0 ]]; then
        c_wr "  ✓ Section $sec already exists" >&2
        found=1
      else
        c_do "  → Section $sec will be downloaded" >&2
        missing_sections+=("$sec")
      fi
    fi
  done

  # Return comma-separated list of missing sections
  if [[ ${#missing_sections[@]} -eq 0 ]]; then
    echo ""
  else
    local result=$(IFS=,; echo "${missing_sections[*]}")
    echo "$result"
  fi
}
