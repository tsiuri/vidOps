#!/usr/bin/env bash
# dual_gpu_transcribe.sh — multi-GPU (+optional CPU) batch transcriber with dynamic queue, hotwords priming, percent progress, and clean quit
# Supports multiple NVIDIA GPUs with automatic detection and per-GPU workers
# REFACTORED: Worker scripts externalized to transcribe_worker_*.py (2025-11-26)
set -euo pipefail

# Script directory (for locating worker scripts)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

########### user options (safe defaults) ###########
: "${MODEL:=small}"
: "${LANGUAGE:=en}"                       # en=English (use empty string for auto-detect)
: "${FORCE:=0}"
: "${OUTFMT:=vtt}"                        # vtt|srt|both
: "${INPUT_FILELIST:=}"                   # path to file containing list of media files (one per line)
EXTENSIONS=("mp4" "mkv" "mov" "avi" "mp3" "wav" "m4a" "opus")

: "${MIN_TS_INTERVAL:=10}"                # seconds between tslog timestamps

# NVIDIA (faster-whisper) settings
: "${NV_COMPUTE:=float16}"                # float16|int8_float16|int8
: "${NV_VAD_FILTER:=1}"                   # 1=enable VAD (2min startup); 0=disable (instant start)
: "${NV_CONFIDENCE_THRESHOLD:=-0.7}"      # avg_logprob threshold for retry; lower values = more selective

# AMD (OpenAI whisper) settings
: "${AMD_THREADS:=1}"                     # (kept for parity; Whisper (torch) uses GPU)
: "${AMD_NO_SPEECH_THRESHOLD:=0.4}"       # 0.0-1.0; higher=skip more silence (lowered for noisy audio)
: "${AMD_COMPRESSION_RATIO_THRESHOLD:=3.0}" # Skip hallucinations; raised to handle dense/noisy speech
: "${AMD_LOGPROB_THRESHOLD:=-1.5}"        # Skip low-confidence segments; lowered for noisy conditions
: "${AMD_FP16:=1}"                        # 1=FP16 precision (faster); 0=FP32 (slower, more accurate)

: "${ENABLE_AMD:=0}"                      # 1=enable legacy AMD/ROCm worker (deprecated)

# Multi-GPU NVIDIA worker controls
: "${NUM_GPU_WORKERS:=auto}"              # auto=detect all GPUs, or specify number (1, 2, 3, etc.)
: "${GPU_DEVICES:=}"                      # comma-separated GPU indices to use (e.g., "0,1"), empty=use all detected

: "${SHIM_CUDA12:=1}"

: "${ANTIHALLUC:=1}"                     # 1=enable thresholds; 0=disable (compat)

: "${FOLLOW:=1}"                          # set 0 or --no-follow to disable inline logs
: "${LOG_DIR:=${PROJECT_ROOT}/logs}"      # where nv.log/amd.log/cpu.log live
: "${KILL_STALE_TAILS:=1}"                # try to kill leftover tails on startup
: "${LOG_TS_FORMAT:=%Y-%m-%d %H:%M:%S}"   # strftime format for log timestamps

: "${SETUP_VENVS:=0}"                      # 1 to auto-create/update venvs; 0=skip (faster startup)
: "${NV_VENV:=${HOME}/transcribe-nv}"
: "${AMD_VENV:=${HOME}/transcribe-amd}"   # --system-site-packages to see ROCm torch

# CPU worker controls
: "${ENABLE_CPU:=0}"
: "${CPU_WORKERS:=1}"                     # number of CPU workers to launch
: "${CPU_THREADS:=8}"
: "${CPU_COMPUTE:=int8_float32}"          # int8|int8_float32|float32 — CHANGED from int8 for better accuracy
: "${CPU_AFFINITY:=}"                     # e.g. "0-7" to pin cores; empty = no pin
: "${CPU_NICE:=10}"                       # lower priority to keep desktop snappy
: "${CPU_IONICE_CLASS:=2}"                # 2=best-effort
: "${CPU_IONICE_PRIO:=7}"                 # 0..7 (7=lowest)

# Hotword priming + optional corrections
: "${HOTWORDS_FILE:=./hotwords.txt}"      # one term per line (optional)
: "${PROMPT_PREFIX:=}"                     # prefix for initial_prompt (empty = no prefix)
: "${CORRECTIONS_TSV:=./corrections.tsv}" # tab-separated (miss \t fix), optional

# Thread counts for GPU workers (for audio preprocessing/I-O)
# 24 cores - 1 reserved = 23 available for GPU workers
: "${GPU_THREADS:=23}"

# Retry behavior: 0=defer retries (write manifest), 1=inline retries (slower)
: "${INLINE_RETRY:=0}"

# Thread counts for CPU worker (for inference)
# When running WITH GPU workers, reduce to avoid starving GPU preprocessing
: "${CPU_THREADS_WITH_GPU:=4}"     # Reduced when GPUs active
: "${CPU_THREADS_SOLO:=8}"         # Full power when CPU only

# If user sets ENABLE_CPU to a numeric >1 and CPU_WORKERS is default 1, use ENABLE_CPU as worker count
if [[ "$ENABLE_CPU" =~ ^[0-9]+$ && "$ENABLE_CPU" -gt 1 && "${CPU_WORKERS}" == "1" ]]; then
  CPU_WORKERS="$ENABLE_CPU"
fi


########### helpers ###########
log(){ printf '%s ==> %s\n' "$(date +"${LOG_TS_FORMAT}")" "$*"; }
warn(){ printf '%s !!  %s\n' "$(date +"${LOG_TS_FORMAT}")" "$*" >&2; }

# prefix each stdout/stderr line with a timestamp (used for worker logs)
ts_prefix_awk(){ awk 'BEGIN{fmt=ENVIRON["LOG_TS_FORMAT"]; if(!fmt) fmt="%Y-%m-%d %H:%M:%S"} { print strftime(fmt), $0; fflush(); }'; }
export -f ts_prefix_awk

# CUDA-12 shim (for CT2 wheels needing libcublas.so.12)
CUDA_LIBDIR="/opt/cuda/lib64"
CUBLAS12="/usr/lib/libcublas.so.12"
CUBLASLT12="/usr/lib/libcublasLt.so.12"
declare -a _SHIMS=()
add_cuda12_shims(){
  [[ "${SHIM_CUDA12}" == "1" ]] || return 0
  command -v nvidia-smi >/dev/null 2>&1 || return 0
  nvidia-smi >/dev/null 2>&1 || return 0
  [[ -e "$CUBLAS12" && -e "$CUBLASLT12" ]] && return 0
  if [[ -e "$CUDA_LIBDIR/libcublas.so" && -e "$CUDA_LIBDIR/libcublasLt.so" ]]; then
    log "Shimming CUDA-12 sonames -> CUDA libs"
    if [[ $EUID -ne 0 ]]; then command -v sudo >/dev/null || { warn "sudo missing; cannot create CUDA12 shims"; return 0; }; fi
    [[ $EUID -eq 0 ]] && ln -sfn "$CUDA_LIBDIR/libcublas.so" "$CUBLAS12" || sudo ln -sfn "$CUDA_LIBDIR/libcublas.so" "$CUBLAS12"
    [[ $EUID -eq 0 ]] && ln -sfn "$CUDA_LIBDIR/libcublasLt.so" "$CUBLASLT12" || sudo ln -sfn "$CUDA_LIBDIR/libcublasLt.so" "$CUBLASLT12"
    _SHIMS+=("$CUBLAS12" "$CUBLASLT12")
    export LD_LIBRARY_PATH="${CUDA_LIBDIR}:${LD_LIBRARY_PATH-}"
    ([[ $EUID -eq 0 ]] && ldconfig || sudo ldconfig) || true
  fi
}
remove_cuda12_shims(){
  ((${#_SHIMS[@]})) || return 0
  log "Removing CUDA-12 shims"
  for s in "${_SHIMS[@]}"; do [[ -L "$s" ]] && { [[ $EUID -eq 0 ]] && rm -f "$s" || sudo rm -f "$s"; }; done
  ([[ $EUID -eq 0 ]] && ldconfig || sudo ldconfig) || true
}



# Write a provenance sidecar for the current media file if we can infer it
write_provenance_sidecar() {
  local media="$1"
  local base="${media%.*}"
  local src="${base}.src.json"

  # If already exists, don't overwrite
  [[ -f "$src" ]] && return 0

  # Try to infer YT ID from filename prefix "VIDEOID__"
  local bn="$(basename "$media")"
  local id=""
  if [[ "$bn" =~ ^([A-Za-z0-9_-]{11})__ ]]; then
    id="${BASH_REMATCH[1]}"
  fi

  # If there's a matching .info.json, prefer it
  local info="${base%.*}.info.json"
  if [[ -f "$info" ]]; then
    # Minimal extract (URL, duration, title, uploader)
    python - "$info" "$src" <<'PY'
import json, sys, re
info, out = sys.argv[1], sys.argv[2]
with open(info, 'r', encoding='utf-8', errors='ignore') as f:
    d=json.load(f)
vid = {
  "platform": d.get("extractor_key") or "YouTube",
  "id": d.get("id"),
  "url": d.get("webpage_url") or (f"https://www.youtube.com/watch?v={d.get('id')}" if d.get("id") else None),
  "title": d.get("title"),
  "uploader": d.get("uploader"),
  "upload_date": d.get("upload_date"),
  "duration": d.get("duration"),
  "base_offset": 0.0
}
with open(out, 'w', encoding='utf-8') as o: json.dump(vid, o, ensure_ascii=False, indent=2)
PY
    return 0
  fi

  # If no .info.json, but filename looked like a YT ID
  if [[ -n "$id" ]]; then
    python - "$id" "$src" <<'PY'
import json, sys
id,out=sys.argv[1],sys.argv[2]
with open(out,'w',encoding='utf-8') as o:
    json.dump({
      "platform":"YouTube","id":id,
      "url":f"https://www.youtube.com/watch?v={id}",
      "title":None,"uploader":None,"upload_date":None,"duration":None,
      "base_offset":0.0
    }, o, ensure_ascii=False, indent=2)
PY
    return 0
  fi

  # Last resort: probe container tags for a URL/ID (works surprisingly often)
  if command -v ffprobe >/dev/null 2>&1; then
    local purl
    purl="$(ffprobe -v error -show_entries format_tags=purl -of default=nk=1:nw=1 -- "$media" 2>/dev/null || true)"
    if [[ -n "$purl" ]]; then
      python - "$purl" "$src" <<'PY'
import json, sys, re
url,out=sys.argv[1],sys.argv[2]
ytid=None
m=re.search(r'v=([A-Za-z0-9_-]{11})', url)
if m: ytid=m.group(1)
with open(out,'w',encoding='utf-8') as o:
    json.dump({
      "platform":"YouTube","id":ytid,"url":url,
      "title":None,"uploader":None,"upload_date":None,"duration":None,
      "base_offset":0.0
    }, o, ensure_ascii=False, indent=2)
PY
    fi
  fi
}
export -f write_provenance_sidecar


# venvs
ensure_nv_venv(){
  if [[ ! -d "$NV_VENV" ]]; then log "Creating NVIDIA venv: $NV_VENV"; python -m venv "$NV_VENV"; fi
  "$NV_VENV/bin/python" - <<'PY'
import sys, subprocess
def pipi(*pkgs): subprocess.check_call([sys.executable,"-m","pip","install","-U",*pkgs])
pipi("pip","setuptools","wheel")
pipi("faster-whisper","ffmpeg-python")
PY
}
ensure_amd_venv(){
  if [[ ! -d "$AMD_VENV" ]]; then log "Creating AMD venv (system-site-packages): $AMD_VENV"; python -m venv --system-site-packages "$AMD_VENV"; fi
  "$AMD_VENV/bin/python" - <<'PY'
import sys, subprocess, importlib
def pipi(*pkgs): subprocess.check_call([sys.executable,"-m","pip","install","-U",*pkgs])
pipi("pip","setuptools","wheel")
pipi("git+https://github.com/openai/whisper.git")
pipi("ffmpeg-python")
assert importlib.util.find_spec("torch"), "PyTorch (ROCm) not visible; install python-pytorch-opt-rocm"
PY
}

# cleanup
cleanup_queue(){
  find . -type f -name '*.transcribing.lock' -print0 2>/dev/null | xargs -0r rm -f -- 2>/dev/null || true
  [[ -n "${QUEUE_DIR:-}" && -d "$QUEUE_DIR" ]] && rm -rf -- "$QUEUE_DIR" || true
}

# inline log tailers (NV0..NVn/AMD/CPU)
declare -a tail_pids=()
declare -a tail_pidfiles=()
stop_follow(){
  for tpid in "${tail_pids[@]:-}"; do
    [[ -n "$tpid" ]] && kill "$tpid" 2>/dev/null || true
  done
  tail_pids=()
  for tpf in "${tail_pidfiles[@]:-}"; do
    [[ -n "$tpf" && -f "$tpf" ]] && rm -f -- "$tpf" || true
  done
  tail_pidfiles=()
}
kill_stale_tailers(){
  (( ${KILL_STALE_TAILS:-1} )) || return 0
  # Best-effort kill of old tails on these exact files
  for p in "${NV_LOGS[@]:-}" "${AMD_LOG:-}" "${CPU_LOGS[@]:-}"; do
    [[ -n "${p:-}" ]] || continue
    pkill -f "tail -n \+1 -F $(printf %q "$p")" 2>/dev/null || true
  done
}
start_follow(){
  (( FOLLOW )) || return 0
  kill_stale_tailers
  # Tail all NV logs
  for i in "${!NV_LOGS[@]}"; do
    local nvlog="${NV_LOGS[$i]}"
    [[ -n "$nvlog" && -f "$nvlog" ]] || continue
    ( stdbuf -oL -eL tail -n +1 -F "$nvlog" 2>/dev/null ) &
    local tpid=$!
    tail_pids+=("$tpid")
    local pidfile="$QUEUE_DIR/tail_nv${i}.pid"
    echo "$tpid" > "$pidfile"
    tail_pidfiles+=("$pidfile")
  done
  # Tail AMD log if exists
  if [[ -n "${AMD_LOG:-}" && -f "$AMD_LOG" ]]; then
    ( stdbuf -oL -eL tail -n +1 -F "$AMD_LOG" 2>/dev/null ) &
    local tpid=$!
    tail_pids+=("$tpid")
    local pidfile="$QUEUE_DIR/tail_amd.pid"
    echo "$tpid" > "$pidfile"
    tail_pidfiles+=("$pidfile")
  fi
  # Tail CPU logs if exist
  for i in "${!CPU_LOGS[@]}"; do
    local cplog="${CPU_LOGS[$i]}"
    [[ -n "$cplog" && -f "$cplog" ]] || continue
    ( stdbuf -oL -eL tail -n +1 -F "$cplog" 2>/dev/null ) &
    local tpid=$!
    tail_pids+=("$tpid")
    local pidfile="$QUEUE_DIR/tail_cpu${i}.pid"
    echo "$tpid" > "$pidfile"
    tail_pidfiles+=("$pidfile")
  done
}

# quit/traps (define BEFORE anything that could trap)
pids=(); PGIDS=(); watcher_pid=""; quit_requested=0
on_quit(){
  # prevent re-entry and repeated INT/TERM handling
  trap - INT TERM
  [[ ${quit_requested:-0} -eq 1 ]] && return 0
  quit_requested=1
  warn "Stopping workers…"
  stop_follow
  # terminate background workers; prefer process groups if available
  if ((${#PGIDS[@]})); then
    kill -TERM "${PGIDS[@]}" 2>/dev/null || true
  elif ((${#pids[@]})); then
    kill -TERM "${pids[@]}" 2>/dev/null || true
  fi
  [[ -n "${watcher_pid:-}" ]] && kill "$watcher_pid" 2>/dev/null || true
  # give them a moment to exit cleanly
  for i in 1 2 3 4 5; do
    alive=0; for pid in "${pids[@]:-}"; do kill -0 "$pid" 2>/dev/null && alive=1; done
    (( alive==0 )) && break
    sleep 0.5
  done
  # force kill any stragglers
  if ((${#PGIDS[@]})); then
    kill -KILL "${PGIDS[@]}" 2>/dev/null || true
  else
    for pid in "${pids[@]:-}"; do kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true; done
  fi
  cleanup_queue || true
  exit 130
}
trap on_quit INT TERM
trap '{ on_quit; remove_cuda12_shims; }' EXIT
trap 'on_quit' USR1   # emergency: kill -USR1 <pid>

########### args ###########
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="${2:-$MODEL}"; shift 2;;
    --lang|--language) LANGUAGE="${2:-}"; shift 2;;
    --force) FORCE=1; shift;;
    --outfmt) OUTFMT="${2:-$OUTFMT}"; shift 2;;
    --ext) IFS=' ' read -r -a EXTENSIONS <<< "${2:-}"; shift 2;;
    --filelist) INPUT_FILELIST="${2:-}"; shift 2;;
    --follow) FOLLOW=1; shift;;
    --no-follow) FOLLOW=0; shift;;
    --setup-venvs) SETUP_VENVS=1; shift;;
    -h|--help)
      cat <<EOF
dual_gpu_transcribe.sh — multi-GPU batch transcriber with automatic NVIDIA GPU detection (plus optional CPU/AMD), hotwords, provenance sidecars, and clean quit.
Supports multiple NVIDIA GPUs with per-GPU workers. AMD support is deprecated.

USAGE:
  ./dual_gpu_transcribe.sh [options]

BASIC OPTIONS:
  --model M                  Whisper model name/size (default: medium)
  --lang, --language CODE    Force language (default: en). Empty = auto-detect
  --force                    Re-transcribe even if .txt exists
  --outfmt vtt|srt|both      Subtitle output format (default: vtt)
  --ext 'e1 e2 ...'          Space-separated extensions to scan (default:
                             mp4 mkv mov avi mp3 wav m4a opus)
  --filelist FILE            Read media file paths from FILE (one per line)
                             Bypasses directory search; supports # comments
  --follow | --no-follow     Live-tail logs to terminal (default: --follow)
  --setup-venvs              Create/update venvs and install packages (slow)
  -h, --help                 Show this help and exit

ENV VARS (set before running):
  MODEL=medium               Same as --model
  LANGUAGE=en                Same as --language (en=English; empty=auto)
  FORCE=0                    1 to force re-transcribe
  OUTFMT=vtt                 vtt|srt|both
  MIN_TS_INTERVAL=10         Seconds between entries in *.tslog.txt

GPU / CPU:
  # Multi-GPU NVIDIA support
  NUM_GPU_WORKERS=auto       auto=use all detected GPUs, or specify number (1, 2, 3, etc.)
  GPU_DEVICES=               Comma-separated GPU indices (e.g., "0,1"), empty=use all detected
  NV_COMPUTE=float16         NVIDIA faster-whisper: float16|int8_float16|int8
  NV_VAD_FILTER=1            1=enable VAD pre-scan (2min delay); 0=disable (instant)
  NV_VENV=$HOME/transcribe-nv

  # Legacy AMD/ROCm worker (deprecated, opt-in)
  ENABLE_AMD=0               1 to enable legacy AMD/ROCm worker
  AMD_VENV=$HOME/transcribe-amd
  AMD_NO_SPEECH_THRESHOLD=0.6       0.0-1.0; higher=skip more silence; 1.0=disable VAD
  AMD_COMPRESSION_RATIO_THRESHOLD=2.4  Hallucination detection; set high to disable
  AMD_LOGPROB_THRESHOLD=-1.0        Low-confidence filter; set very low to disable
  AMD_FP16=1                        1=FP16 (faster); 0=FP32 (slower, more accurate)

  INLINE_RETRY=0             0=defer retries (write manifest, faster); 1=inline retries (slower)
                             Use 0 for two-pass: transcribe all files first, then run ./batch_retry.sh
  GPU_THREADS=23             CPU threads for GPU workers (default: 24 cores - 1 reserved)
  ENABLE_CPU=0               1 to enable CPU worker (disabled by default)
  CPU_THREADS_WITH_GPU=4     CPU worker threads when GPUs active (reduced)
  CPU_THREADS_SOLO=8         CPU worker threads when no GPUs (full power)
  CPU_COMPUTE=int8_float32   int8|int8_float32|float32 (default: int8_float32)
  CPU_AFFINITY=              e.g. "0-7" to pin cores (taskset)
  CPU_NICE=10                Lower CPU worker priority
  CPU_IONICE_CLASS=2         2=best-effort (see ionice)
  CPU_IONICE_PRIO=7          0..7, 7=lowest

HOTWORDS & CORRECTIONS:
  HOTWORDS_FILE=./hotwords.txt     One term per line (optional)
  PROMPT_PREFIX=""                 Prefix for initial_prompt (empty by default)
  CORRECTIONS_TSV=./corrections.tsv  TSV lines:  miss<TAB>fix

LOGGING / UI:
  LOG_DIR=logs               Where nv0.log, nv1.log, amd.log, cpu.log live (multi-GPU: nv<idx>.log)
  KILL_STALE_TAILS=1         Best-effort kill of leftover tails on startup
  SHIM_CUDA12=1              Create CUDA-12 soname shims if needed
  ANTIHALLUC=1               0 disables anti-hallucination thresholds (compat)
  SETUP_VENVS=0              1 to auto-create/update venvs (same as --setup-venvs)

THREAD CONTROL:
  GPU workers: GPU_THREADS=23 (for audio preprocessing I/O; 24 cores - 1 reserved)
  CPU worker with GPUs: CPU_THREADS_WITH_GPU=4 (reduced to avoid starvation)
  CPU worker solo: CPU_THREADS_SOLO=8 (full power when no GPU competition)
  CPU worker disabled by default (ENABLE_CPU=0); use ENABLE_CPU=1 to enable

CONTROLS:
  Press 'q' or Ctrl-C to stop; or send SIGUSR1 to the master PID.

NOTES:
  • The script writes <file>.src.json to record provenance using any existing
    *.info.json, container tags, or an ID prefix in the filename.
  • Logs are tailed live if --follow (default). Set --no-follow to disable.
  • First time setup: Run with --setup-venvs to create/update virtual envs
  • Quality tracking: logs/transcription_confidence.tsv tracks avg confidence per file
    Format: timestamp, worker, filename, avg/min/max confidence, segments, retries, duration
EXAMPLES:
  # First-time setup (creates venvs and installs packages):
  ./dual_gpu_transcribe.sh --setup-venvs

  # Regular runs (GPUs only, default):
  ./dual_gpu_transcribe.sh

  # Transcribe specific files from a list:
  ./dual_gpu_transcribe.sh --filelist my_videos.txt

  # Enable CPU worker alongside GPUs:
  ENABLE_CPU=1 ./dual_gpu_transcribe.sh

  # Other options:
  OUTFMT=both ./dual_gpu_transcribe.sh
  ./dual_gpu_transcribe.sh --ext 'opus m4a' --no-follow --force
EOF
      exit 0;;
    *) echo "Unknown option: $1"; exit 1;;
  esac
done

log "Model: ${MODEL} | Language: ${LANGUAGE:-auto} | Force: ${FORCE} | Outfmt: ${OUTFMT}"
log "Extensions: ${EXTENSIONS[*]}"
log "MIN_TS_INTERVAL: ${MIN_TS_INTERVAL}s"
echo

########### discover + de-dupe ###########
RAW_FILELIST="$(mktemp)"

if [[ -n "$INPUT_FILELIST" ]]; then
  # Use provided filelist
  [[ -f "$INPUT_FILELIST" ]] || { warn "Filelist not found: $INPUT_FILELIST"; exit 1; }
  log "Using filelist: $INPUT_FILELIST"

  # Read newline-delimited paths and convert to null-delimited
  while IFS= read -r line; do
    # Skip empty lines and comments
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    # Expand path and validate
    line="${line/#\~/$HOME}"  # Expand ~
    if [[ -f "$line" ]]; then
      printf '%s\0' "$line" >> "$RAW_FILELIST"
    else
      warn "File not found (skipping): $line"
    fi
  done < "$INPUT_FILELIST"
else
  # Search for media in pull/ directory; fall back to current dir if pull/ doesn't exist
  SEARCH_DIR="${PROJECT_ROOT}/pull"
  [[ -d "$SEARCH_DIR" ]] || SEARCH_DIR="${PROJECT_ROOT}"
  log "Searching for media in: $SEARCH_DIR"

  ARGS=( -type f "(" ); for ext in "${EXTENSIONS[@]}"; do ARGS+=( -iname "*.${ext}" -o ); done
  unset 'ARGS[${#ARGS[@]}-1]'; ARGS+=( ")" -print0 )
  find "$SEARCH_DIR" "${ARGS[@]}" > "$RAW_FILELIST"
fi

# De-duplicate by inode
FILELIST="$(mktemp)"
declare -A SEEN
while IFS= read -r -d '' f; do
  read -r dev ino size < <(stat -Lc '%d %i %s' -- "$f" 2>/dev/null || echo "x x x")
  [[ "$dev" == "x" ]] && continue
  key="${dev}:${ino}:${size}"
  if [[ -z "${SEEN[$key]:-}" ]]; then SEEN[$key]=1; printf '%s\0' "$f" >> "$FILELIST"; fi
done < "$RAW_FILELIST"
TOTAL=$(tr -cd '\000' < "$FILELIST" | wc -c)
(( TOTAL > 0 )) || { warn "No media files found."; exit 0; }

########### queue with background validation ###########
QUEUE_DIR="$(mktemp -d -p . dualq.XXXXXX)"
mkdir -p "$QUEUE_DIR"/{pending,inprogress,done}

# Background file validator (runs in parallel with worker startup)
validate_and_queue() {
  local filelist="$1"
  local qdir="$2"
  local total="$3"
  local count=0
  while IFS= read -r -d '' f; do
    # Quick validation: file exists and is readable
    if [[ ! -r "$f" ]]; then
      warn "Skipping unreadable file: $f"
      continue
    fi
    # Write task (just the filename - workers will probe duration themselves)
    printf '%s' "$f" > "$qdir/pending/task.$(printf '%08d' "$count")"
    count=$((count+1))
  done < "$filelist"
  log "Validated and queued $count/$total files"
}
export -f validate_and_queue
export -f log
export -f warn

# Start validation in background while workers initialize
validate_and_queue "$FILELIST" "$QUEUE_DIR" "$TOTAL" &
VALIDATOR_PID=$!

log "Started background file validation (PID: $VALIDATOR_PID)"
log "Queue directory: $QUEUE_DIR"
echo

########### runners ###########
########### output directory ###########
mkdir -p "${PROJECT_ROOT}/generated"







########### start ###########
add_cuda12_shims || true

if (( SETUP_VENVS )); then
  log "Setting up virtual environments..."
  ensure_nv_venv
  if (( ENABLE_AMD )); then
    ensure_amd_venv
  else
    log "Skipping AMD venv setup (ENABLE_AMD=0; legacy/ROCm path disabled)"
  fi
fi

# Archive existing logs before starting
archive_log() {
  local logfile="$1"
  [[ -f "$logfile" && -s "$logfile" ]] || return 0  # Skip if doesn't exist or empty

  local logdir="$(dirname "$logfile")"
  local basename="$(basename "$logfile" .log)"
  local old_logs_dir="${logdir}/old_logs"
  mkdir -p "$old_logs_dir"

  # Find next available number
  local num=1
  while [[ -f "${old_logs_dir}/${basename}_${num}.log" ]]; do
    num=$((num + 1))
  done

  mv "$logfile" "${old_logs_dir}/${basename}_${num}.log"
  log "Archived old log: ${basename}.log -> old_logs/${basename}_${num}.log"
}

mkdir -p "$LOG_DIR"

# Detect NVIDIA GPUs
declare -a DETECTED_GPUS=()
declare -a GPU_NAMES=()
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  # Get list of GPU indices and names
  while IFS=',' read -r idx name; do
    DETECTED_GPUS+=("$idx")
    GPU_NAMES+=("$name")
  done < <(nvidia-smi --query-gpu=index,name --format=csv,noheader)
fi

NUM_DETECTED_GPUS=${#DETECTED_GPUS[@]}
log "Detected $NUM_DETECTED_GPUS NVIDIA GPU(s)"

# Determine which GPUs to use
declare -a USE_GPUS=()
if [[ -n "$GPU_DEVICES" ]]; then
  # User specified GPU indices
  IFS=',' read -ra USE_GPUS <<< "$GPU_DEVICES"
  log "Using user-specified GPUs: ${USE_GPUS[*]}"
elif [[ "$NUM_GPU_WORKERS" == "auto" ]]; then
  # Auto: use all detected GPUs
  USE_GPUS=("${DETECTED_GPUS[@]}")
  log "Auto mode: using all $NUM_DETECTED_GPUS detected GPU(s)"
elif [[ "$NUM_GPU_WORKERS" =~ ^[0-9]+$ ]]; then
  # User specified number of workers
  requested=$NUM_GPU_WORKERS
  use_count=$((requested < NUM_DETECTED_GPUS ? requested : NUM_DETECTED_GPUS))
  for ((i=0; i<use_count; i++)); do
    USE_GPUS+=("${DETECTED_GPUS[$i]}")
  done
  log "Using first $use_count GPU(s) (requested: $requested, detected: $NUM_DETECTED_GPUS)"
else
  warn "Invalid NUM_GPU_WORKERS='$NUM_GPU_WORKERS'; using auto mode"
  USE_GPUS=("${DETECTED_GPUS[@]}")
fi

# Setup log files for all workers
declare -a NV_LOGS=()
for i in "${!USE_GPUS[@]}"; do
  worker_num=$((i+1))
  NV_LOGS+=("${LOG_DIR%/}/NV$(printf '%02d' "$worker_num").log")
done

declare -a CPU_LOGS=()
cpu_workers_sanitized="$CPU_WORKERS"
[[ "$cpu_workers_sanitized" =~ ^[0-9]+$ ]] || cpu_workers_sanitized=1
for ((i=0; i<cpu_workers_sanitized; i++)); do
  CPU_LOGS+=("${LOG_DIR%/}/CP$(printf '%02d' "$((i+1))").log")
done

AMD_LOG="${LOG_DIR%/}/AMD.log"

# Archive old logs instead of truncating
for nvlog in "${NV_LOGS[@]}"; do
  archive_log "$nvlog"
done
for cplog in "${CPU_LOGS[@]}"; do
  archive_log "$cplog"
done
archive_log "$AMD_LOG"

# Create new empty logs
for nvlog in "${NV_LOGS[@]}"; do
  : >"$nvlog"
done
for cplog in "${CPU_LOGS[@]}"; do
  : >"$cplog"
done
: >"$AMD_LOG"

# Wait for background file validation to complete
if [[ -n "${VALIDATOR_PID:-}" ]]; then
  log "Waiting for file validation to complete..."
  wait "$VALIDATOR_PID" || warn "File validation had errors (some files may be skipped)"
fi

AMD_OK=0
NV_OK=0
if (( ENABLE_AMD )); then
  AMD_OK=1
fi

# ----- NVIDIA workers (one per GPU) -----
if (( ${#USE_GPUS[@]} > 0 )); then
  NV_OK=1
  for i in "${!USE_GPUS[@]}"; do
    gpu_idx="${USE_GPUS[$i]}"
    gpu_log="${NV_LOGS[$i]}"
    gpu_name="${GPU_NAMES[$gpu_idx]:-GPU$gpu_idx}"
    worker_num=$((i+1))
    worker_label="$(printf 'NV%02d' "$worker_num")"
    log "Starting NVIDIA worker #$worker_num on GPU $gpu_idx ($gpu_name) (log: $gpu_log) label=$worker_label"
    setsid bash -c "env GPU_IDX=\"$gpu_idx\" WORKER_NUM=\"$worker_num\" WORKER_LABEL=\"$worker_label\" CUDA_VISIBLE_DEVICES=\"$gpu_idx\" MODEL=\"$MODEL\" LANG=\"$LANGUAGE\" FORCE=\"$FORCE\" OUTFMT=\"$OUTFMT\" NV_COMPUTE=\"$NV_COMPUTE\" NV_VAD_FILTER=\"$NV_VAD_FILTER\" NV_CONFIDENCE_THRESHOLD=\"$NV_CONFIDENCE_THRESHOLD\" INLINE_RETRY=\"$INLINE_RETRY\" MIN_TS_INTERVAL=\"$MIN_TS_INTERVAL\" QUEUE_DIR=\"$QUEUE_DIR\" HOTWORDS_FILE=\"$HOTWORDS_FILE\" PROMPT_PREFIX=\"$PROMPT_PREFIX\" CORRECTIONS_TSV=\"$CORRECTIONS_TSV\" ANTIHALLUC=\"$ANTIHALLUC\" LOG_TS_FORMAT=\"$LOG_TS_FORMAT\" PROJECT_ROOT=\"$PROJECT_ROOT\" OMP_NUM_THREADS=\"$GPU_THREADS\" MKL_NUM_THREADS=\"$GPU_THREADS\" OPENBLAS_NUM_THREADS=\"$GPU_THREADS\" NUMEXPR_NUM_THREADS=\"$GPU_THREADS\" RAYON_NUM_THREADS=\"$GPU_THREADS\" stdbuf -oL -eL \"$NV_VENV/bin/python\" \"$SCRIPT_DIR/transcribe_worker_nvidia.py\" --queue-dir \"$QUEUE_DIR\" --model \"$MODEL\" --language \"$LANGUAGE\" --gpu-idx \"$gpu_idx\" 2>&1 | ts_prefix_awk >\"$gpu_log\"" & pids+=($!); PGIDS+=(-$!)
  done
else
  warn "No NVIDIA GPUs available; skipping NV workers."
fi

# ----- AMD worker -----
if (( AMD_OK )); then
  log "Starting AMD worker (log: $AMD_LOG)"
  setsid bash -c "env ENABLE_AMD=\"$ENABLE_AMD\" MODEL=\"$MODEL\" LANG=\"$LANGUAGE\" FORCE=\"$FORCE\" OUTFMT=\"$OUTFMT\" MIN_TS_INTERVAL=\"$MIN_TS_INTERVAL\" AMD_NO_SPEECH_THRESHOLD=\"$AMD_NO_SPEECH_THRESHOLD\" AMD_COMPRESSION_RATIO_THRESHOLD=\"$AMD_COMPRESSION_RATIO_THRESHOLD\" AMD_LOGPROB_THRESHOLD=\"$AMD_LOGPROB_THRESHOLD\" AMD_FP16=\"$AMD_FP16\" QUEUE_DIR=\"$QUEUE_DIR\" HOTWORDS_FILE=\"$HOTWORDS_FILE\" PROMPT_PREFIX=\"$PROMPT_PREFIX\" CORRECTIONS_TSV=\"$CORRECTIONS_TSV\" ANTIHALLUC=\"$ANTIHALLUC\" LOG_TS_FORMAT=\"$LOG_TS_FORMAT\" PROJECT_ROOT=\"$PROJECT_ROOT\" OMP_NUM_THREADS=\"$GPU_THREADS\" MKL_NUM_THREADS=\"$GPU_THREADS\" OPENBLAS_NUM_THREADS=\"$GPU_THREADS\" NUMEXPR_NUM_THREADS=\"$GPU_THREADS\" RAYON_NUM_THREADS=\"$GPU_THREADS\" stdbuf -oL -eL \"$AMD_VENV/bin/python\" \"$SCRIPT_DIR/transcribe_worker_amd.py\" --queue-dir \"$QUEUE_DIR\" --model \"$MODEL\" --language \"$LANGUAGE\" 2>&1 | ts_prefix_awk >\"$AMD_LOG\"" & pids+=($!); PGIDS+=(-$!)
else
  warn "ROCm unavailable; skipping AMD worker."
fi

# ----- CPU worker (optional) -----
if (( ENABLE_CPU )); then
  # Adjust CPU threads based on whether GPU workers are active
  # If GPUs active, reduce CPU threads to avoid starving GPU preprocessing
  if (( NV_OK || AMD_OK )); then
    CPU_THREADS="$CPU_THREADS_WITH_GPU"
    log "Starting CPU worker(s) with reduced threads ($CPU_THREADS) to avoid GPU contention"
  else
    CPU_THREADS="$CPU_THREADS_SOLO"
    log "Starting CPU worker(s) with full threads ($CPU_THREADS) - no GPU competition"
  fi

  # Build CPU launch prefix string
  CPU_PREFIX=""
  [[ -n "$CPU_AFFINITY" ]] && CPU_PREFIX+="taskset -c \"$CPU_AFFINITY\" "
  CPU_PREFIX+="ionice -c$CPU_IONICE_CLASS -n$CPU_IONICE_PRIO nice -n $CPU_NICE "

  for i in "${!CPU_LOGS[@]}"; do
    worker_num=$((i+1))
    worker_label="$(printf 'CP%02d' "$worker_num")"
    cplog="${CPU_LOGS[$i]}"
    log "Starting CPU worker #$worker_num with threads $CPU_THREADS (log: $cplog) label=$worker_label"
    setsid bash -c "env WORKER_NUM=\"$worker_num\" WORKER_LABEL=\"$worker_label\" MODEL=\"$MODEL\" LANG=\"$LANGUAGE\" FORCE=\"$FORCE\" OUTFMT=\"$OUTFMT\" CPU_COMPUTE=\"$CPU_COMPUTE\" CPU_THREADS=\"$CPU_THREADS\" NV_CONFIDENCE_THRESHOLD=\"$NV_CONFIDENCE_THRESHOLD\" INLINE_RETRY=\"$INLINE_RETRY\" NV_VAD_FILTER=\"$NV_VAD_FILTER\" MIN_TS_INTERVAL=\"$MIN_TS_INTERVAL\" QUEUE_DIR=\"$QUEUE_DIR\" HOTWORDS_FILE=\"$HOTWORDS_FILE\" PROMPT_PREFIX=\"$PROMPT_PREFIX\" CORRECTIONS_TSV=\"$CORRECTIONS_TSV\" ANTIHALLUC=\"$ANTIHALLUC\" LOG_TS_FORMAT=\"$LOG_TS_FORMAT\" PROJECT_ROOT=\"$PROJECT_ROOT\" OMP_NUM_THREADS=\"$CPU_THREADS\" MKL_NUM_THREADS=\"$CPU_THREADS\" OPENBLAS_NUM_THREADS=\"$CPU_THREADS\" NUMEXPR_NUM_THREADS=\"$CPU_THREADS\" RAYON_NUM_THREADS=\"$CPU_THREADS\" ${CPU_PREFIX}stdbuf -oL -eL \"$NV_VENV/bin/python\" \"$SCRIPT_DIR/transcribe_worker_cpu.py\" --queue-dir \"$QUEUE_DIR\" --model \"$MODEL\" --language \"$LANGUAGE\" 2>&1 | ts_prefix_awk >\"$cplog\"" & pids+=($!); PGIDS+=(-$!)
  done
fi

# Save main PID for keywatch to signal
MAIN_PID=$$

# key watcher (also supports SIGUSR1)
keywatch(){
  printf "\n[controls] Press 'q' to stop all workers…\n\n"
  while :; do
    alive=0; for pid in "${pids[@]}"; do kill -0 "$pid" 2>/dev/null && alive=1; done
    [[ $alive -eq 0 ]] && return 0
    if [[ -r /dev/tty ]] && IFS= read -rsn1 -t 1 k < /dev/tty; then
      if [[ "$k" == $'\x03' || "$k" == "q" || "$k" == "Q" ]]; then
        warn "Quit requested (key: ${k@Q})"
        kill -TERM "$MAIN_PID" 2>/dev/null || true
        return
      fi
    else
      sleep 1
    fi
  done
}
keywatch & watcher_pid=$!

# start single inline tailers (NV/AMD/CPU)
start_follow

# wait for workers
fail=0; for pid in "${pids[@]}"; do wait "$pid" || fail=1; done
stop_follow
cleanup_queue || true

if [[ $fail -eq 0 && $quit_requested -eq 0 ]]; then
  log "All workers finished."
elif [[ $quit_requested -eq 1 ]]; then
  warn "Stopped by user."
else
  warn "Some failures occurred (see logs)."
fi
