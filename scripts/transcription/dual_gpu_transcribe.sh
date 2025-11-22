#!/usr/bin/env bash
# dual_gpu_transcribe.sh — dual-GPU (+optional CPU) batch transcriber with dynamic queue, hotwords priming, percent progress, and clean quit
# PATCHED VERSION: Fixed hallucination bug and improved CPU accuracy
set -euo pipefail

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

# AMD (OpenAI whisper) settings
: "${AMD_THREADS:=1}"                     # (kept for parity; Whisper (torch) uses GPU)
: "${AMD_NO_SPEECH_THRESHOLD:=0.4}"       # 0.0-1.0; higher=skip more silence (lowered for noisy audio)
: "${AMD_COMPRESSION_RATIO_THRESHOLD:=3.0}" # Skip hallucinations; raised to handle dense/noisy speech
: "${AMD_LOGPROB_THRESHOLD:=-1.5}"        # Skip low-confidence segments; lowered for noisy conditions
: "${AMD_FP16:=1}"                        # 1=FP16 precision (faster); 0=FP32 (slower, more accurate)

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

# inline log tailers (NV/AMD/CPU) — single instance each
tl1=""; tl2=""; tl3=""
TAIL_PIDFILE_NV=""; TAIL_PIDFILE_AMD=""; TAIL_PIDFILE_CPU=""
stop_follow(){
  [[ -n "${tl1:-}" ]] && kill "$tl1" 2>/dev/null || true; tl1=""
  [[ -n "${tl2:-}" ]] && kill "$tl2" 2>/dev/null || true; tl2=""
  [[ -n "${tl3:-}" ]] && kill "$tl3" 2>/dev/null || true; tl3=""
  [[ -n "${TAIL_PIDFILE_NV:-}" && -f "$TAIL_PIDFILE_NV" ]] && rm -f -- "$TAIL_PIDFILE_NV" || true
  [[ -n "${TAIL_PIDFILE_AMD:-}" && -f "$TAIL_PIDFILE_AMD" ]] && rm -f -- "$TAIL_PIDFILE_AMD" || true
  [[ -n "${TAIL_PIDFILE_CPU:-}" && -f "$TAIL_PIDFILE_CPU" ]] && rm -f -- "$TAIL_PIDFILE_CPU" || true
}
kill_stale_tailers(){
  (( ${KILL_STALE_TAILS:-1} )) || return 0
  # Best-effort kill of old tails on these exact files
  for p in "${NV_LOG:-}" "${AMD_LOG:-}" "${CPU_LOG:-}"; do
    [[ -n "${p:-}" ]] || continue
    pkill -f "tail -n \+1 -F $(printf %q "$p")" 2>/dev/null || true
  done
}
start_follow(){
  (( FOLLOW )) || return 0
  kill_stale_tailers
  if [[ -n "${NV_LOG:-}" ]]; then ( stdbuf -oL -eL tail -n +1 -F "$NV_LOG" 2>/dev/null ) & tl1=$!; TAIL_PIDFILE_NV="$QUEUE_DIR/tail_nv.pid"; echo "$tl1" > "$TAIL_PIDFILE_NV"; fi
  if [[ -n "${AMD_LOG:-}" ]]; then ( stdbuf -oL -eL tail -n +1 -F "$AMD_LOG" 2>/dev/null ) & tl2=$!; TAIL_PIDFILE_AMD="$QUEUE_DIR/tail_amd.pid"; echo "$tl2" > "$TAIL_PIDFILE_AMD"; fi
  if [[ -n "${CPU_LOG:-}" && -f "$CPU_LOG" ]]; then ( stdbuf -oL -eL tail -n +1 -F "$CPU_LOG" 2>/dev/null ) & tl3=$!; TAIL_PIDFILE_CPU="$QUEUE_DIR/tail_cpu.pid"; echo "$tl3" > "$TAIL_PIDFILE_CPU"; fi
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
dual_gpu_transcribe.sh — batch transcriber with NVIDIA + AMD GPU workers (plus optional CPU), hotwords, provenance sidecars, and clean quit.

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
  NV_COMPUTE=float16         NVIDIA faster-whisper: float16|int8_float16|int8
  NV_VAD_FILTER=1            1=enable VAD pre-scan (2min delay); 0=disable (instant)
  NV_VENV=$HOME/transcribe-nv

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
  LOG_DIR=logs               Where nv.log / amd.log / cpu.log live
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

NV_RUNNER="$(mktemp --suffix=.py)"
cat > "$NV_RUNNER" <<'PY'
import os, time, json, re, subprocess
from pathlib import Path

# Limit CPU thread usage for GPU worker (GPU does the heavy lifting)
GPU_THREADS = int(os.environ.get("OMP_NUM_THREADS", "16"))  # Default to 16 if not set
try:
    import torch
    torch.set_num_threads(GPU_THREADS)
    torch.set_num_interop_threads(GPU_THREADS)
except ImportError:
    pass

from faster_whisper import WhisperModel

# Helper to get output path in generated/ directory
def get_output_base(media_path: Path) -> Path:
    """Convert media path to output base in generated/ directory"""
    project_root = Path(os.environ.get("PROJECT_ROOT", "."))
    output_dir = project_root / "generated"
    output_dir.mkdir(exist_ok=True)
    # Keep just the filename, drop the pull/ prefix
    return output_dir / media_path.stem

def probe_duration_seconds(path: Path):
    try:
        out = subprocess.check_output(
            ["ffprobe","-v","error","-show_entries","format=duration",
             "-of","default=nk=1:nw=1", str(path)],
            text=True
        ).strip()
        return float(out) if out else None
    except Exception:
        return None

def emit_progress(prefix, done_sec, total_sec, next_mark):
    # next_mark is a 1-element list holding the next threshold (e.g., [0.10])
    if not total_sec or total_sec <= 0:
        return
    frac = max(0.0, min(1.0, done_sec / total_sec))
    while frac + 1e-9 >= next_mark[0] and next_mark[0] < 1.0:
        pct = int(next_mark[0] * 100)
        print(f"{prefix}[{pct:3d}%] {done_sec:,.1f}s / {total_sec:,.1f}s", flush=True)
        next_mark[0] += 0.10


MODEL=os.environ.get("MODEL","medium")
LANG=os.environ.get("LANG") or None
FORCE=os.environ.get("FORCE")=="1"
OUTFMT=os.environ.get("OUTFMT","vtt")
COMPUTE=os.environ.get("NV_COMPUTE","float16")
VAD_FILTER=os.environ.get("NV_VAD_FILTER","1")=="1"
MIN_TS=int(os.environ.get("MIN_TS_INTERVAL","10"))
QDIR=Path(os.environ["QUEUE_DIR"])
PENDING=QDIR/"pending"; INPROG=QDIR/"inprogress"; DONE=QDIR/"done"

# --- provenance: always write <base>.src.json first time, using info.json if present ---
def ensure_src_json(media_path: Path, base_path: Path):
    src_path = base_path.with_suffix(".src.json")
    if src_path.exists():
        return

    info = None
    info_path = base_path.with_suffix(".info.json")
    if info_path.exists():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            info = None

    url = None; vid = None; title = None; uploader = None; upload_date = None; duration = None
    platform = "YouTube"

    if info:
        url = info.get("webpage_url") or info.get("original_url")
        vid = info.get("id")
        title = info.get("title")
        uploader = info.get("uploader")
        upload_date = info.get("upload_date")
        duration = info.get("duration")
        platform = info.get("extractor_key") or platform
    else:
        # Fallback: purl tag
        try:
            out = subprocess.check_output(
                ["ffprobe","-v","error","-show_entries","format_tags=purl",
                 "-of","default=nk=1:nw=1", str(media_path)],
                text=True
            ).strip()
            if out:
                url = out
                m = re.search(r"v=([A-Za-z0-9_-]{11})", url)
                if m: vid = m.group(1)
        except Exception:
            pass
        # Extra fallback: filename prefix "ID__"
        if not vid:
            bn = media_path.name
            m = re.match(r"^([A-Za-z0-9_-]{11})__", bn)
            if m:
                vid = m.group(1)
                url = url or f"https://www.youtube.com/watch?v={vid}"

    src = {
        "platform": platform,
        "id": vid,
        "url": url or (f"https://www.youtube.com/watch?v={vid}" if vid else None),
        "title": title,
        "uploader": uploader,
        "upload_date": upload_date,   # YYYYMMDD if present
        "duration": duration,         # seconds if present
        "base_offset": 0.0
    }
    src_path.write_text(json.dumps(src, ensure_ascii=False, indent=2), encoding="utf-8")

# --- hotwords / corrections helpers ---
def build_initial_prompt():
    hp=os.environ.get("HOTWORDS_FILE")
    pref=os.environ.get("PROMPT_PREFIX","")
    if not hp or not os.path.isfile(hp): return None
    seen=set(); terms=[]
    with open(hp,encoding="utf-8",errors="ignore") as f:
        for line in f:
            t=line.strip()
            if t and t not in seen:
                seen.add(t); terms.append(t)
    if not terms: return None
    if pref:
        return f"{pref} " + ", ".join(terms)
    else:
        return ", ".join(terms)

def load_corrections():
    path=os.environ.get("CORRECTIONS_TSV")
    if not path or not os.path.isfile(path): return []
    pairs=[]
    with open(path,encoding="utf-8",errors="ignore") as f:
        for line in f:
            line=line.strip()
            if not line or line.startswith("#") or "\t" not in line: continue
            miss,fix=line.split("\t",1)
            miss=miss.strip(); fix=fix.strip()
            if miss and fix: pairs.append((miss,fix))
    return pairs

def apply_corrections(text, pairs):
    for miss,fix in pairs:
        text=text.replace(miss,fix).replace(miss.lower(),fix).replace(miss.title(),fix)
    return text

INITIAL_PROMPT = build_initial_prompt()
CORR = load_corrections()

def hms(t,sep=".",ms=3):
    t=max(0,float(t));h=int(t//3600);m=int((t%3600)//60);s=int(t%60);msv=int(round((t-int(t))*10**ms))
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{msv:03d}"

def write_retry_manifest_amd(base_path: Path, media_path: Path, segs, low_conf_indices, threshold):
    """Write a manifest of segments needing retry for batch post-processing"""
    manifest_path = base_path.with_suffix(".retry_manifest.tsv")
    with open(manifest_path, "w", encoding="utf-8") as f:
        # NEW: include zero_length flag
        f.write("media_file\tsegment_idx\tstart_time\tend_time\tconfidence\tzero_length\ttext\n")
        for idx in low_conf_indices:
            seg = segs[idx]
            # Handle both dict and object formats
            if isinstance(seg, dict):
                start = float(seg.get("start", 0.0))
                end = float(seg.get("end", 0.0))
                conf = float(seg.get("avg_logprob", 0.0))
                text = (seg.get("text", "") or "").strip()
            else:
                start = float(getattr(seg, "start", 0.0))
                end = float(getattr(seg, "end", 0.0))
                conf = float(getattr(seg, "avg_logprob", 0.0))
                text = (getattr(seg, "text", "") or "").strip()

            # Mark and expand zero-length spans
            zero_length = 1 if end <= start else 0
            if zero_length:
                end = start + 1.0  # 1-second window for retry

            text = text.replace("\t", " ").replace("\n", " ")
            f.write(
                f"{media_path}\t{idx}\t{start:.3f}\t{end:.3f}\t{conf:.3f}\t{zero_length}\t{text}\n"
            )
    print(f"[AMD][MANIFEST] wrote retry manifest for {len(low_conf_indices)} segments to {manifest_path.name}", flush=True)

def write_vtt(segs,path,retried_indices=None):
    retried_indices = retried_indices or set()
    with open(path,"w",encoding="utf-8") as f:
        f.write("WEBVTT\n\n")
        for i,s in enumerate(segs):
            txt=s.text.strip()
            if not txt: continue
            conf = float(getattr(s, "avg_logprob", 0.0))
            if i in retried_indices:
                f.write(f"NOTE Confidence: {conf:.3f} [RETRIED]\n\n")
            else:
                f.write(f"NOTE Confidence: {conf:.3f}\n\n")
            f.write(f"{hms(s.start)} --> {hms(s.end)}\n{txt}\n\n")

def write_srt(segs,path):
    with open(path,"w",encoding="utf-8") as f:
        for i,s in enumerate(segs,1):
            txt=s.text.strip()
            if not txt: continue
            a=hms(s.start,sep=","); b=hms(s.end,sep=",")
            f.write(f"{i}\n{a} --> {b}\n{txt}\n\n")

def write_retry_manifest(base_path: Path, media_path: Path, segs, low_conf_indices, threshold):
    """Write a manifest of segments needing retry for batch post-processing"""
    manifest_path = base_path.with_suffix(".retry_manifest.tsv")
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("media_file\tsegment_idx\tstart_time\tend_time\tconfidence\tzero_length\ttext\n")
        for idx in low_conf_indices:
            seg = segs[idx]
            start = float(getattr(seg, "start", 0.0))
            end = float(getattr(seg, "end", 0.0))
            conf = float(getattr(seg, "avg_logprob", 0.0))
            text = (getattr(seg, "text", "") or "").strip()

            # Mark and expand zero-length segments
            zero_length = 1 if end <= start else 0
            if zero_length:
                end = start + 1.0  # give a 1-second span

            text = text.replace("\t", " ").replace("\n", " ")
            f.write(
                f"{media_path}\t{idx}\t{start:.3f}\t{end:.3f}\t{conf:.3f}\t{zero_length}\t{text}\n"
            )
    print(f"[NV][MANIFEST] wrote retry manifest for {len(low_conf_indices)} segments to {manifest_path.name}", flush=True)


def write_words_tsv_faster(base_path: Path, segs, retried_indices=None):
    retried_indices = retried_indices or set()
    words_tsv = base_path.with_suffix(".words.tsv")
    try:
        with open(words_tsv, "w", encoding="utf-8") as wf:
            wf.write("start\tend\tword\tseg\tconfidence\tretried\n")
            for si, s in enumerate(segs):
                ws = getattr(s, "words", None)
                if not ws: continue
                conf = float(getattr(s, "avg_logprob", 0.0))
                retried = 1 if si in retried_indices else 0
                for w in ws:
                    word = (getattr(w, "word", "") or "").strip()
                    if not word: continue
                    wf.write(f"{float(getattr(w,'start',0.0)):.3f}\t{float(getattr(w,'end',0.0)):.3f}\t{word}\t{si}\t{conf:.3f}\t{retried}\n")
    except Exception as e:
        print(f"[NV][WARN] failed to write words.tsv: {e}", flush=True)

def make_tslog(caption,tslog,interval):
    import re
    rx=re.compile(r'^(\d\d):(\d\d):(\d\d)')
    last=-1e9; start=None
    with open(caption,encoding="utf-8",errors="ignore") as i, open(tslog,"w",encoding="utf-8") as o:
        for line in i:
            if "-->" in line and rx.match(line):
                h,m,s=map(int,line[:8].split(":")); start=h*3600+m*60+s; continue
            if not line.strip(): continue
            if start is None: continue
            if start-last>=interval:
                o.write(f"[{start//3600:02d}:{(start%3600)//60:02d}:{start%60:02d}] {line}"); last=start
            else: o.write(line)

def claim():
    try:
        import random
        # NVIDIA worker: prefer LARGE files (faster on big work once started)
        tasks = []
        for entry in PENDING.iterdir():
            if not entry.is_file(): continue
            try:
                media_path = Path(entry.read_text(encoding="utf-8", errors="ignore"))
                if media_path.exists():
                    size = media_path.stat().st_size
                else:
                    size = 0  # missing file = deprioritize
                # Add small random offset to break ties
                sort_key = size + random.randint(0, max(1, size // 1000))
                tasks.append((sort_key, entry, media_path))
            except Exception:
                continue
        if not tasks:
            return None
        # Sort by size DESC - largest files first
        tasks.sort(key=lambda x: x[0], reverse=True)
        for _, entry, media_path in tasks:
            dest = INPROG / entry.name
            try:
                entry.replace(dest)
                return dest, media_path
            except (FileNotFoundError, PermissionError, OSError):
                continue
    except FileNotFoundError:
        # Queue directory was cleaned up
        return None
    return None

print(f"[NV] Worker starting at {time.strftime('%H:%M:%S')}", flush=True)
print(f"[NV] Worker starting with {GPU_THREADS} CPU threads (GPU worker)", flush=True)
print(f"[NV] loading faster-whisper model={MODEL} device=cuda compute=float16", flush=True)
start_load = time.time()
model=WhisperModel(MODEL,device="cuda",compute_type="float16")
load_time = time.time() - start_load
print(f"[NV] Model loaded successfully in {load_time:.1f}s at {time.strftime('%H:%M:%S')}, entering main loop", flush=True)

while True:
    task = claim()
    if not task:
        try:
            # Quick check: if PENDING is empty and INPROG is empty, we're done
            # Use next() with default to avoid iterating all files
            if next(PENDING.iterdir(), None) is None and next(INPROG.iterdir(), None) is None:
                break
        except FileNotFoundError:
            # Queue directory was cleaned up, exit gracefully
            break
        time.sleep(0.1); continue  # Reduced sleep for faster task pickup

    task_file, media = task
    # Original base for source files (in pull/)
    media_base = media.with_suffix("")
    ensure_src_json(media, media_base)

    # Output base for generated files (in generated/)
    base = get_output_base(media)

    out_txt = base.with_suffix(".txt")
    do_vtt = OUTFMT in ("vtt","both"); do_srt = OUTFMT in ("srt","both")
    out_vtt = base.with_suffix(".vtt") if do_vtt else None
    out_srt = base.with_suffix(".srt") if do_srt else None
    lock = base.with_suffix(".transcribing.lock")
    success_marker = base.with_suffix(".transcribed")

    try:
        # Check success marker first (faster than checking txt existence)
        if success_marker.exists() and not FORCE:
            print(f"[NV][SKIP]{media} (already transcribed)", flush=True)
            continue
        if out_txt.exists() and not FORCE:
            print(f"[NV][SKIP]{media} (txt exists)", flush=True)
            continue  # Immediately try next task
        else:
            try:
                fd=os.open(lock, os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o644); os.close(fd)
                got_lock=True
            except FileExistsError:
                got_lock=False
            if not got_lock:
                print(f"[NV][LOCK]{media} (held elsewhere) — skipping", flush=True)
                continue  # Immediately try next task without sleeping
            else:
                print(f"[NV][RUN ]{media}", flush=True)

                total = probe_duration_seconds(media) or 0.0
                print(f"[NV][INFO]{media} duration={total:.1f}s", flush=True)

                next_mark = [0.10]  # 10%, 20%, … 90%
                last_end = 0.0

                # Fast first pass: beam_size=1 (greedy decoding)
                vad_status = "enabled" if VAD_FILTER else "DISABLED"
                print(f"[NV][PASS1] fast transcribe (float16, greedy, VAD {vad_status}) starting at {time.strftime('%H:%M:%S')}", flush=True)
                pass1_start = time.time()
                kwargs_fast = dict(
                    language=LANG,
                    vad_filter=VAD_FILTER,
                    initial_prompt=INITIAL_PROMPT,
                    condition_on_previous_text=False,
                    temperature=0.0,  # Greedy only
                    beam_size=1,      # Greedy search
                    word_timestamps=True,
                )
                # Skip anti-hallucination thresholds on fast pass
                segments, info = model.transcribe(str(media), **kwargs_fast)

                segs = []
                for s in segments:  # streaming
                    segs.append(s)
                    # Print segment text in real-time (similar to AMD verbose mode)
                    text = getattr(s, 'text', '').strip()
                    if text:
                        print(f"[NV][TEXT] {text}", flush=True)
                    last_end = max(last_end, float(getattr(s, "end", 0.0)))
                    emit_progress("[NV][PROG]", last_end, total, next_mark)

                if total > 0:
                    print(f"[NV][100%] {total:.1f}s / {total:.1f}s", flush=True)

                pass1_elapsed = time.time() - pass1_start
                print(f"[NV][PASS1] completed in {pass1_elapsed:.1f}s at {time.strftime('%H:%M:%S')}", flush=True)

                # Check confidence scores and identify low-confidence segments
                CONFIDENCE_THRESHOLD = float(os.environ.get("NV_CONFIDENCE_THRESHOLD", "-0.7"))
                INLINE_RETRY = int(os.environ.get("INLINE_RETRY", "0"))
                low_conf_indices = []
                for i, s in enumerate(segs):
                    avg_logprob = float(getattr(s, "avg_logprob", 0.0))
                    if avg_logprob < CONFIDENCE_THRESHOLD:
                        low_conf_indices.append(i)

                # Handle low-confidence segments based on INLINE_RETRY setting
                if low_conf_indices and INLINE_RETRY == 0:
                    # Deferred retry: write manifest for batch post-processing
                    print(f"[NV][DEFER] found {len(low_conf_indices)}/{len(segs)} low-confidence segments (avg_logprob < {CONFIDENCE_THRESHOLD})", flush=True)
                    write_retry_manifest(base, media, segs, low_conf_indices, CONFIDENCE_THRESHOLD)
                    retried_set = set()  # No inline retries performed
                elif low_conf_indices and INLINE_RETRY == 1:
                    # Inline retry (old behavior) for low-confidence segments
                    retry_start_time = time.time()
                    print(f"[NV][RETRY] found {len(low_conf_indices)}/{len(segs)} low-confidence segments (avg_logprob < {CONFIDENCE_THRESHOLD})", flush=True)
                    print(f"[NV][RETRY] re-transcribing with quality settings (beam_size=5) at {time.strftime('%H:%M:%S')}", flush=True)

                    # Extract and retry each low-confidence segment (reusing same model)
                    for idx in low_conf_indices:
                        seg = segs[idx]
                        start_time = float(getattr(seg, "start", 0.0))
                        end_time = float(getattr(seg, "end", 0.0))

                        # Extract audio clip for this segment using ffmpeg
                        clip_path = base.with_suffix(f".clip_{idx}.wav")
                        try:
                            subprocess.run(
                                ["ffmpeg", "-y", "-v", "error",
                                 "-i", str(media),
                                 "-ss", str(start_time),
                                 "-to", str(end_time),
                                 "-ac", "1", "-ar", "16000",
                                 str(clip_path)],
                                check=True
                            )

                            # Re-transcribe with quality settings (using same model, different params)
                            kwargs_quality = dict(
                                language=LANG,
                                vad_filter=VAD_FILTER,
                                initial_prompt=INITIAL_PROMPT,
                                condition_on_previous_text=False,
                                temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
                                beam_size=5,
                                word_timestamps=True,
                                compression_ratio_threshold=2.4,
                                log_prob_threshold=-1.0,
                            )
                            retry_segs, _ = model.transcribe(str(clip_path), **kwargs_quality)
                            retry_list = list(retry_segs)

                            if retry_list:
                                # Use first segment from retry (adjust timestamps)
                                retry_seg = retry_list[0]
                                # Reconstruct segment with corrected timestamps
                                class RetriedSegment:
                                    def __init__(self, orig_seg, retry_seg, base_time):
                                        self.text = retry_seg.text
                                        self.start = base_time + float(getattr(retry_seg, "start", 0.0))
                                        self.end = base_time + float(getattr(retry_seg, "end", 0.0))
                                        self.avg_logprob = float(getattr(retry_seg, "avg_logprob", 0.0))
                                        self.no_speech_prob = float(getattr(retry_seg, "no_speech_prob", 0.0))
                                        self.compression_ratio = float(getattr(retry_seg, "compression_ratio", 0.0))
                                        # Copy words if available, adjusting timestamps
                                        retry_words = getattr(retry_seg, "words", None)
                                        if retry_words:
                                            class Word:
                                                def __init__(self, word, start, end):
                                                    self.word = word
                                                    self.start = start
                                                    self.end = end
                                            self.words = [
                                                Word(
                                                    getattr(w, "word", ""),
                                                    base_time + float(getattr(w, "start", 0.0)),
                                                    base_time + float(getattr(w, "end", 0.0))
                                                )
                                                for w in retry_words
                                            ]
                                        else:
                                            self.words = getattr(orig_seg, "words", None)

                                segs[idx] = RetriedSegment(seg, retry_seg, start_time)

                            # Clean up clip
                            os.unlink(clip_path)
                        except Exception as e:
                            print(f"[NV][RETRY] segment {idx} failed: {e}", flush=True)

                    retry_elapsed = time.time() - retry_start_time
                    print(f"[NV][RETRY] completed retry for {len(low_conf_indices)} segments in {retry_elapsed:.1f}s at {time.strftime('%H:%M:%S')}", flush=True)
                    # Track retried segments for metadata
                    retried_set = set(low_conf_indices)
                else:
                    # No low-confidence segments found
                    retried_set = set()

                # Write plain text
                with open(out_txt,"w",encoding="utf-8") as f:
                    for s in segs:
                        t=s.text.strip()
                        if t: f.write(apply_corrections(t, CORR) + "\n")

                # NEW: write words.tsv with confidence scores
                write_words_tsv_faster(base, segs, retried_set)

                # Captions + tslog
                cap=None
                if do_vtt: write_vtt(segs,out_vtt,retried_set); cap=out_vtt
                if do_srt: write_srt(segs,out_srt); cap=cap or out_srt
                if cap: make_tslog(cap, base.with_suffix(".tslog.txt"), MIN_TS)

                # Mark successful completion
                success_marker.touch()
                print(f"[NV][DONE]{media}", flush=True)
    except Exception as e:
        print(f"[NV][FAIL]{media}: {e}", flush=True)
        # Clean up partial success marker if exists
        try: success_marker.unlink()
        except: pass
    finally:
        try: os.unlink(lock)
        except Exception: pass
        try: task_file.replace(DONE / task_file.name)
        except Exception: pass
PY


AMD_RUNNER="$(mktemp --suffix=.py)"
cat > "$AMD_RUNNER" <<'PY'
import os, time, json, re, subprocess
from pathlib import Path

# Limit CPU thread usage for GPU worker (GPU does the heavy lifting)
GPU_THREADS = int(os.environ.get("OMP_NUM_THREADS", "16"))  # Default to 16 if not set
try:
    import torch
    torch.set_num_threads(GPU_THREADS)
    torch.set_num_interop_threads(GPU_THREADS)
except ImportError:
    pass

import whisper

# Helper to get output path in generated/ directory
def get_output_base(media_path: Path) -> Path:
    """Convert media path to output base in generated/ directory"""
    project_root = Path(os.environ.get("PROJECT_ROOT", "."))
    output_dir = project_root / "generated"
    output_dir.mkdir(exist_ok=True)
    # Keep just the filename, drop the pull/ prefix
    return output_dir / media_path.stem

def probe_duration_seconds(path: Path):
    try:
        out = subprocess.check_output(
            ["ffprobe","-v","error","-show_entries","format=duration",
             "-of","default=nk=1:nw=1", str(path)],
            text=True
        ).strip()
        return float(out) if out else None
    except Exception:
        return None

MODEL=os.environ.get("MODEL","medium")
LANG=os.environ.get("LANG") or None
FORCE=os.environ.get("FORCE")=="1"
OUTFMT=os.environ.get("OUTFMT","vtt")
MIN_TS=int(os.environ.get("MIN_TS_INTERVAL","10"))

# AMD-specific tuning parameters
NO_SPEECH_THRESHOLD=float(os.environ.get("AMD_NO_SPEECH_THRESHOLD","0.6"))
COMPRESSION_RATIO_THRESHOLD=float(os.environ.get("AMD_COMPRESSION_RATIO_THRESHOLD","2.4"))
LOGPROB_THRESHOLD=float(os.environ.get("AMD_LOGPROB_THRESHOLD","-1.0"))
FP16=os.environ.get("AMD_FP16","1")=="1"

QDIR=Path(os.environ["QUEUE_DIR"])
PENDING=QDIR/"pending"; INPROG=QDIR/"inprogress"; DONE=QDIR/"done"

def ensure_src_json(media_path: Path, base_path: Path):
    src_path = base_path.with_suffix(".src.json")
    if src_path.exists():
        return

    info = None
    info_path = base_path.with_suffix(".info.json")
    if info_path.exists():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            info = None

    url = None; vid = None; title = None; uploader = None; upload_date = None; duration = None
    platform = "YouTube"

    if info:
        url = info.get("webpage_url") or info.get("original_url")
        vid = info.get("id")
        title = info.get("title")
        uploader = info.get("uploader")
        upload_date = info.get("upload_date")
        duration = info.get("duration")
        platform = info.get("extractor_key") or platform
    else:
        try:
            out = subprocess.check_output(
                ["ffprobe","-v","error","-show_entries","format_tags=purl",
                 "-of","default=nk=1:nw=1", str(media_path)],
                text=True
            ).strip()
            if out:
                url = out
                m = re.search(r"v=([A-Za-z0-9_-]{11})", url)
                if m: vid = m.group(1)
        except Exception:
            pass
        if not vid:
            bn = media_path.name
            m = re.match(r"^([A-Za-z0-9_-]{11})__", bn)
            if m:
                vid = m.group(1)
                url = url or f"https://www.youtube.com/watch?v={vid}"

    src = {
        "platform": platform,
        "id": vid,
        "url": url or (f"https://www.youtube.com/watch?v={vid}" if vid else None),
        "title": title,
        "uploader": uploader,
        "upload_date": upload_date,
        "duration": duration,
        "base_offset": 0.0
    }
    src_path.write_text(json.dumps(src, ensure_ascii=False, indent=2), encoding="utf-8")

# --- hotwords / corrections helpers ---
def build_initial_prompt():
    hp=os.environ.get("HOTWORDS_FILE")
    pref=os.environ.get("PROMPT_PREFIX","")
    if not hp or not os.path.isfile(hp): return None
    seen=set(); terms=[]
    with open(hp,encoding="utf-8",errors="ignore") as f:
        for line in f:
            t=line.strip()
            if t and t not in seen:
                seen.add(t); terms.append(t)
    if not terms: return None
    if pref:
        return f"{pref} " + ", ".join(terms)
    else:
        return ", ".join(terms)

def load_corrections():
    path=os.environ.get("CORRECTIONS_TSV")
    if not path or not os.path.isfile(path): return []
    pairs=[]
    with open(path,encoding="utf-8",errors="ignore") as f:
        for line in f:
            line=line.strip()
            if not line or line.startswith("#") or "\t" not in line: continue
            miss,fix=line.split("\t",1)
            miss=miss.strip(); fix=fix.strip()
            if miss and fix: pairs.append((miss,fix))
    return pairs

def apply_corrections(text, pairs):
    for miss,fix in pairs:
        text=text.replace(miss,fix).replace(miss.lower(),fix).replace(miss.title(),fix)
    return text

INITIAL_PROMPT = build_initial_prompt()
CORR = load_corrections()

def hms_dot(t): t=max(0.0,float(t)); h=int(t//3600); m=int((t%3600)//60); s=int(t%60); ms=int(round((t-int(t))*1000)); return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
def hms_comma(t): t=max(0.0,float(t)); h=int(t//3600); m=int((t%3600)//60); s=int(t%60); ms=int(round((t-int(t))*1000)); return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def write_words_tsv_whisper(base_path: Path, segs, retried_indices=None):
    retried_indices = retried_indices or set()
    words_tsv = base_path.with_suffix(".words.tsv")
    try:
        with open(words_tsv, "w", encoding="utf-8") as wf:
            wf.write("start\tend\tword\tseg\tconfidence\tretried\n")
            for si, s in enumerate(segs):
                ws = s.get("words", []) if isinstance(s, dict) else getattr(s, "words", None)
                if not ws: continue
                # Get confidence from segment (OpenAI whisper doesn't provide per-word confidence)
                conf = float(s.get("avg_logprob", 0.0)) if isinstance(s, dict) else 0.0
                retried = 1 if si in retried_indices else 0
                for w in ws:
                    # OpenAI whisper words may be dicts
                    if isinstance(w, dict):
                        start = float(w.get("start", 0.0))
                        end   = float(w.get("end", 0.0))
                        word  = (w.get("word") or "").strip()
                    else:
                        start = float(getattr(w, "start", 0.0))
                        end   = float(getattr(w, "end", 0.0))
                        word  = (getattr(w, "word", "") or "").strip()
                    if not word: continue
                    wf.write(f"{start:.3f}\t{end:.3f}\t{word}\t{si}\t{conf:.3f}\t{retried}\n")
    except Exception as e:
        print(f"[AMD][WARN] failed to write words.tsv: {e}", flush=True)

def claim():
    try:
        import random
        # AMD worker: prefer SMALL files (starts instantly, handle quick work)
        tasks = []
        for entry in PENDING.iterdir():
            if not entry.is_file(): continue
            try:
                media_path = Path(entry.read_text(encoding="utf-8", errors="ignore"))
                if media_path.exists():
                    size = media_path.stat().st_size
                else:
                    size = 9e18  # missing file = deprioritize (put at end)
                # Add small random offset to break ties
                sort_key = size + random.randint(0, max(1, size // 1000))
                tasks.append((sort_key, entry, media_path))
            except Exception:
                continue
        if not tasks:
            return None
        # Sort by size ASC - smallest files first
        tasks.sort(key=lambda x: x[0])
        for _, entry, media_path in tasks:
            dest = INPROG / entry.name
            try:
                entry.replace(dest)
                return dest, media_path
            except (FileNotFoundError, PermissionError, OSError):
                continue
    except FileNotFoundError:
        # Queue directory was cleaned up
        return None
    return None

def hms(t,sep=".",ms=3):
    t=max(0,float(t));h=int(t//3600);m=int((t%3600)//60);s=int(t%60);msv=int(round((t-int(t))*10**ms))
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{msv:03d}"

def write_retry_manifest_amd(base_path: Path, media_path: Path, segs, low_conf_indices, threshold):
    """Write a manifest of segments needing retry for batch post-processing"""
    manifest_path = base_path.with_suffix(".retry_manifest.tsv")
    with open(manifest_path, "w", encoding="utf-8") as f:
        # NEW: include zero_length flag
        f.write("media_file\tsegment_idx\tstart_time\tend_time\tconfidence\tzero_length\ttext\n")
        for idx in low_conf_indices:
            seg = segs[idx]
            # Handle both dict and object formats
            if isinstance(seg, dict):
                start = float(seg.get("start", 0.0))
                end = float(seg.get("end", 0.0))
                conf = float(seg.get("avg_logprob", 0.0))
                text = (seg.get("text", "") or "").strip()
            else:
                start = float(getattr(seg, "start", 0.0))
                end = float(getattr(seg, "end", 0.0))
                conf = float(getattr(seg, "avg_logprob", 0.0))
                text = (getattr(seg, "text", "") or "").strip()

            # Mark and expand zero-length spans
            zero_length = 1 if end <= start else 0
            if zero_length:
                end = start + 1.0  # 1-second window for retry

            text = text.replace("\t", " ").replace("\n", " ")
            f.write(
                f"{media_path}\t{idx}\t{start:.3f}\t{end:.3f}\t{conf:.3f}\t{zero_length}\t{text}\n"
            )
    print(f"[AMD][MANIFEST] wrote retry manifest for {len(low_conf_indices)} segments to {manifest_path.name}", flush=True)

def write_vtt(segs,path,retried_indices=None):
    retried_indices = retried_indices or set()
    with open(path,"w",encoding="utf-8") as f:
        f.write("WEBVTT\n\n")
        for i,s in enumerate(segs):
            txt=s.text.strip()
            if not txt: continue
            conf = float(getattr(s, "avg_logprob", 0.0))
            if i in retried_indices:
                f.write(f"NOTE Confidence: {conf:.3f} [RETRIED]\n\n")
            else:
                f.write(f"NOTE Confidence: {conf:.3f}\n\n")
            f.write(f"{hms(s.start)} --> {hms(s.end)}\n{txt}\n\n")

def write_srt(segs,path):
    with open(path,"w",encoding="utf-8") as f:
        for i,s in enumerate(segs,1):
            txt=s.text.strip()
            if not txt: continue
            a=hms(s.start,sep=","); b=hms(s.end,sep=",")
            f.write(f"{i}\n{a} --> {b}\n{txt}\n\n")

def write_words_tsv_faster(base_path: Path, segs, retried_indices=None):
    retried_indices = retried_indices or set()
    words_tsv = base_path.with_suffix(".words.tsv")
    try:
        with open(words_tsv, "w", encoding="utf-8") as wf:
            wf.write("start\tend\tword\tseg\tconf\tretried\n")
            for si, s in enumerate(segs):
                ws = getattr(s, "words", None)
                if not ws: continue
                conf = float(getattr(s, "avg_logprob", 0.0))
                retried = "1" if si in retried_indices else "0"
                for w in ws:
                    # OpenAI whisper words may be dicts
                    if isinstance(w, dict):
                        start = float(w.get("start", 0.0))
                        end   = float(w.get("end", 0.0))
                        word  = (w.get("word") or "").strip()
                    else:
                        start = float(getattr(w, "start", 0.0))
                        end   = float(getattr(w, "end", 0.0))
                        word  = (getattr(w, "word", "") or "").strip()
                    if not word: continue
                    wf.write(f"{start:.3f}\t{end:.3f}\t{word}\t{si}\t{conf:.3f}\t{retried}\n")
    except Exception as e:
        print(f"[AMD][WARN] failed to write words.tsv: {e}", flush=True)

print(f"[AMD] Worker starting with {GPU_THREADS} CPU threads (GPU worker)", flush=True)
print(f"[AMD] Tuning: no_speech_threshold={NO_SPEECH_THRESHOLD}, compression_ratio_threshold={COMPRESSION_RATIO_THRESHOLD}, logprob_threshold={LOGPROB_THRESHOLD}, fp16={FP16}", flush=True)
print(f"[AMD] loading whisper model={MODEL} device=cuda", flush=True)
model=whisper.load_model(MODEL,device="cuda")

while True:
    task = claim()
    if not task:
        try:
            # Quick check: if PENDING is empty and INPROG is empty, we're done
            # Use next() with default to avoid iterating all files
            if next(PENDING.iterdir(), None) is None and next(INPROG.iterdir(), None) is None:
                break
        except FileNotFoundError:
            # Queue directory was cleaned up, exit gracefully
            break
        time.sleep(0.1); continue  # Reduced sleep for faster task pickup

    task_file, media = task
    # Original base for source files (in pull/)
    media_base = media.with_suffix("")
    ensure_src_json(media, media_base)

    # Output base for generated files (in generated/)
    base = get_output_base(media)

    out_txt = base.with_suffix(".txt")
    lock = base.with_suffix(".transcribing.lock")
    success_marker = base.with_suffix(".transcribed")

    try:
        # Check success marker first (faster than checking txt existence)
        if success_marker.exists() and not FORCE:
            print(f"[AMD][SKIP]{media} (already transcribed)", flush=True)
            continue
        if out_txt.exists() and not FORCE:
            print(f"[AMD][SKIP]{media} (txt exists)", flush=True)
            continue  # Immediately try next task
        else:
            try:
                fd=os.open(lock, os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o644); os.close(fd)
                got_lock=True
            except FileExistsError:
                got_lock=False
            if not got_lock:
                print(f"[AMD][LOCK]{media} (held elsewhere) — skipping", flush=True)
                continue  # Immediately try next task without sleeping
            else:
                print(f"[AMD][RUN ]{media}", flush=True)

                total = probe_duration_seconds(media) or 0.0
                print(f"[AMD][INFO]{media} duration={total:.1f}s", flush=True)

                # Build transcribe kwargs for OpenAI whisper with tunable parameters
                import os as _os
                wkwargs = dict(
                    language=LANG,
                    verbose=True,  # Enable progress bar (shows frames processed)
                    initial_prompt=INITIAL_PROMPT,
                    condition_on_previous_text=False,
                    temperature=0.0,  # Greedy decoding (fastest)
                    beam_size=1,      # Greedy search (fastest)
                    word_timestamps=True,  # Keep word timestamps
                    fp16=FP16,        # FP16 precision control
                    no_speech_threshold=NO_SPEECH_THRESHOLD,  # VAD-like silence detection
                    compression_ratio_threshold=COMPRESSION_RATIO_THRESHOLD,  # Hallucination detection
                    logprob_threshold=LOGPROB_THRESHOLD,  # Confidence threshold
                )

                try:
                    res=model.transcribe(str(media), **wkwargs)
                except TypeError:
                    # Retry without thresholds for older versions
                    wkwargs.pop('compression_ratio_threshold', None)
                    wkwargs.pop('logprob_threshold', None)
                    wkwargs.pop('no_speech_threshold', None)
                    res=model.transcribe(str(media), **wkwargs)

                segs=res.get("segments",[])

                # Detect and retry hallucinated segments with higher quality settings
                def is_hallucination(seg):
                    text = (seg.get("text","") or "").strip()
                    if not text:
                        return True
                    # Check for zero duration
                    start = seg.get("start", 0.0)
                    end = seg.get("end", 0.0)
                    if end - start < 0.01:  # Less than 10ms
                        return True
                    # Check for character repetition (e.g., "eeeeee")
                    if len(text) > 20:
                        for i in range(len(text) - 10):
                            if len(set(text[i:i+10])) <= 2:  # 10+ chars, max 2 unique
                                return True
                    # Check for phrase repetition
                    words = text.lower().split()
                    if len(words) >= 6:
                        # Check if same 3-word phrase repeats
                        trigrams = [' '.join(words[i:i+3]) for i in range(len(words)-2)]
                        if len(trigrams) != len(set(trigrams)):  # Duplicates found
                            return True
                    return False

                # Find hallucinated segments and retry them
                INLINE_RETRY = int(os.environ.get("INLINE_RETRY", "0"))
                halluc_indices = [i for i, seg in enumerate(segs) if is_hallucination(seg)]

                if halluc_indices and INLINE_RETRY == 0:
                    # Deferred retry: write manifest for batch post-processing
                    print(f"[AMD][DEFER] found {len(halluc_indices)} hallucinated segments (will defer retries)", flush=True)
                    write_retry_manifest_amd(base, media, segs, halluc_indices, 0.0)  # Threshold N/A for hallucinations
                    retried_set = set()  # No inline retries performed
                elif halluc_indices and INLINE_RETRY == 1:
                    print(f"[AMD][RETRY] found {len(halluc_indices)} hallucinated segments, retrying with beam_size=3...", flush=True)
                    retry_wkwargs = dict(
                        language=LANG,
                        initial_prompt=INITIAL_PROMPT,
                        condition_on_previous_text=False,
                        beam_size=3,
                        temperature=(0.0, 0.4, 0.8),
                        word_timestamps=True,
                        fp16=FP16,
                        no_speech_threshold=NO_SPEECH_THRESHOLD,
                        compression_ratio_threshold=COMPRESSION_RATIO_THRESHOLD,
                        logprob_threshold=LOGPROB_THRESHOLD,
                        verbose=False,  # Don't spam progress on retries
                    )

                    for idx in halluc_indices:
                        seg = segs[idx]
                        start_time = seg.get("start", 0.0)
                        end_time = seg.get("end", 0.0)
                        # Add padding to capture full context
                        retry_start = max(0.0, start_time - 1.0)
                        retry_end = min(total, end_time + 1.0)

                        # Re-transcribe just this segment
                        try:
                            import ffmpeg
                            # Extract audio segment
                            segment_audio, _ = (
                                ffmpeg.input(str(media), ss=retry_start, t=retry_end-retry_start)
                                .output('pipe:', format='s16le', acodec='pcm_s16le', ac=1, ar='16k')
                                .run(capture_stdout=True, capture_stderr=True, quiet=True)
                            )
                            import numpy as np
                            audio_np = np.frombuffer(segment_audio, np.int16).flatten().astype(np.float32) / 32768.0

                            # Retry transcription with higher quality
                            retry_result = model.transcribe(audio_np, **retry_wkwargs)
                            retry_segs = retry_result.get("segments", [])

                            # Find best matching segment and replace
                            if retry_segs:
                                # Adjust timestamps back to original position
                                for rs in retry_segs:
                                    rs['start'] = rs.get('start', 0) + retry_start
                                    rs['end'] = rs.get('end', 0) + retry_start
                                # Replace with first non-hallucinating segment
                                for rs in retry_segs:
                                    if not is_hallucination(rs):
                                        segs[idx] = rs
                                        break
                        except Exception as e:
                            print(f"[AMD][WARN] retry failed for segment at {start_time:.1f}s: {e}", flush=True)

                # Final filter to remove any remaining hallucinations
                if INLINE_RETRY == 1:
                    segs = [s for s in segs if not is_hallucination(s)]
                    # Track retried segments for metadata
                    retried_set = set(halluc_indices) if halluc_indices else set()
                else:
                    # INLINE_RETRY == 0
                    # retried_set already set earlier if halluc_indices found
                    # If no hallucinations, still need to set it
                    if not halluc_indices:
                        retried_set = set()

                # Plain text
                with open(out_txt,"w",encoding="utf-8") as f:
                    for s in segs:
                        t=(s.get("text","") or "").strip()
                        if not t: continue
                        f.write(apply_corrections(t, CORR) + "\n")

                # Words.tsv with word timestamps and confidence
                write_words_tsv_whisper(base, segs, retried_set)

                # Captions + tslog
                if os.environ.get("OUTFMT","vtt") in ("vtt","both"):
                    out_vtt=base.with_suffix(".vtt")
                    with open(out_vtt,"w",encoding="utf-8") as f:
                        f.write("WEBVTT\n\n")
                        for i,s in enumerate(segs):
                            t=(s.get("text","") or "").strip()
                            if not t: continue
                            conf = float(s.get("avg_logprob", 0.0))
                            if i in retried_set:
                                f.write(f"NOTE Confidence: {conf:.3f} [RETRIED]\n\n")
                            else:
                                f.write(f"NOTE Confidence: {conf:.3f}\n\n")
                            f.write(f"{hms_dot(s['start'])} --> {hms_dot(s['end'])}\n{t}\n\n")
                    cap=out_vtt
                else:
                    out_srt=base.with_suffix(".srt")
                    with open(out_srt,"w",encoding="utf-8") as f:
                        for i,s in enumerate(segs,1):
                            t=(s.get("text","") or "").strip()
                            if not t: continue
                            f.write(f"{i}\n{hms_comma(s['start'])} --> {hms_comma(s['end'])}\n{t}\n\n")
                    cap=out_srt
                # tslog
                last=-1e9; start=None
                with open(cap,encoding="utf-8",errors="ignore") as i, open(base.with_suffix(".tslog.txt"),"w",encoding="utf-8") as o:
                    for line in i:
                        if '-->' in line and line[2]==':':
                            h,m,s=map(int,line[:8].split(':')); start=h*3600+m*60+s; continue
                        if not line.strip(): continue
                        if start is None: continue
                        if start-last>=MIN_TS: o.write(f"[{h:02d}:{m:02d}:{s:02d}] {line}"); last=start
                        else: o.write(line)

                if total > 0:
                    print(f"[AMD][100%] {total:.1f}s / {total:.1f}s", flush=True)

                # Mark successful completion
                success_marker.touch()
                print(f"[AMD][DONE]{media}", flush=True)
    except Exception as e:
        print(f"[AMD][FAIL]{media}: {e}", flush=True)
        # Clean up partial success marker if exists
        try: success_marker.unlink()
        except: pass
    finally:
        try: os.unlink(lock)
        except Exception: pass
        try: task_file.replace(DONE / task_file.name)
        except Exception: pass
PY


CPU_RUNNER="$(mktemp --suffix=.py)"
cat > "$CPU_RUNNER" <<'PY'
import os, time, json, re, subprocess
from pathlib import Path

# Set CPU thread usage for CPU worker (needs many threads for inference)
CPU_THREADS_VAL = int(os.environ.get("CPU_THREADS", "8"))
try:
    import torch
    torch.set_num_threads(CPU_THREADS_VAL)
    torch.set_num_interop_threads(CPU_THREADS_VAL)
except ImportError:
    pass

from faster_whisper import WhisperModel

# Helper to get output path in generated/ directory
def get_output_base(media_path: Path) -> Path:
    """Convert media path to output base in generated/ directory"""
    project_root = Path(os.environ.get("PROJECT_ROOT", "."))
    output_dir = project_root / "generated"
    output_dir.mkdir(exist_ok=True)
    # Keep just the filename, drop the pull/ prefix
    return output_dir / media_path.stem

def probe_duration_seconds(path: Path):
    try:
        out = subprocess.check_output(
            ["ffprobe","-v","error","-show_entries","format=duration",
             "-of","default=nk=1:nw=1", str(path)],
            text=True
        ).strip()
        return float(out) if out else None
    except Exception:
        return None

def emit_progress(prefix, done_sec, total_sec, next_mark):
    if not total_sec or total_sec <= 0:
        return
    frac = max(0.0, min(1.0, done_sec / total_sec))
    while frac + 1e-9 >= next_mark[0] and next_mark[0] < 1.0:
        pct = int(next_mark[0] * 100)
        print(f"{prefix}[{pct:3d}%] {done_sec:,.1f}s / {total_sec:,.1f}s", flush=True)
        next_mark[0] += 0.10

MODEL=os.environ.get("MODEL","medium")
LANG=os.environ.get("LANG") or None
FORCE=os.environ.get("FORCE")=="1"
OUTFMT=os.environ.get("OUTFMT","vtt")
COMPUTE=os.environ.get("CPU_COMPUTE","int8_float32")  # CHANGED default
CPU_THREADS=int(os.environ.get("CPU_THREADS","8"))
MIN_TS=int(os.environ.get("MIN_TS_INTERVAL","10"))
QDIR=Path(os.environ["QUEUE_DIR"])
PENDING=QDIR/"pending"; INPROG=QDIR/"inprogress"; DONE=QDIR/"done"

def ensure_src_json(media_path: Path, base_path: Path):
    src_path = base_path.with_suffix(".src.json")
    if src_path.exists():
        return

    info = None
    info_path = base_path.with_suffix(".info.json")
    if info_path.exists():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            info = None

    url = None; vid = None; title = None; uploader = None; upload_date = None; duration = None
    platform = "YouTube"

    if info:
        url = info.get("webpage_url") or info.get("original_url")
        vid = info.get("id")
        title = info.get("title")
        uploader = info.get("uploader")
        upload_date = info.get("upload_date")
        duration = info.get("duration")
        platform = info.get("extractor_key") or platform
    else:
        try:
            out = subprocess.check_output(
                ["ffprobe","-v","error","-show_entries","format_tags=purl",
                 "-of","default=nk=1:nw=1", str(media_path)],
                text=True
            ).strip()
            if out:
                url = out
                m = re.search(r"v=([A-Za-z0-9_-]{11})", url)
                if m: vid = m.group(1)
        except Exception:
            pass
        if not vid:
            bn = media_path.name
            m = re.match(r"^([A-Za-z0-9_-]{11})__", bn)
            if m:
                vid = m.group(1)
                url = url or f"https://www.youtube.com/watch?v={vid}"

    src = {
        "platform": platform,
        "id": vid,
        "url": url or (f"https://www.youtube.com/watch?v={vid}" if vid else None),
        "title": title,
        "uploader": uploader,
        "upload_date": upload_date,
        "duration": duration,
        "base_offset": 0.0
    }
    src_path.write_text(json.dumps(src, ensure_ascii=False, indent=2), encoding="utf-8")

# --- hotwords / corrections helpers ---
def build_initial_prompt():
    hp=os.environ.get("HOTWORDS_FILE")
    pref=os.environ.get("PROMPT_PREFIX","")
    if not hp or not os.path.isfile(hp): return None
    seen=set(); terms=[]
    with open(hp,encoding="utf-8",errors="ignore") as f:
        for line in f:
            t=line.strip()
            if t and t not in seen:
                seen.add(t); terms.append(t)
    if not terms: return None
    if pref:
        return f"{pref} " + ", ".join(terms)
    else:
        return ", ".join(terms)

def load_corrections():
    path=os.environ.get("CORRECTIONS_TSV")
    if not path or not os.path.isfile(path): return []
    pairs=[]
    with open(path,encoding="utf-8",errors="ignore") as f:
        for line in f:
            line=line.strip()
            if not line or line.startswith("#") or "\t" not in line: continue
            miss,fix=line.split("\t",1)
            miss=miss.strip(); fix=fix.strip()
            if miss and fix: pairs.append((miss,fix))
    return pairs

def apply_corrections(text, pairs):
    for miss,fix in pairs:
        text=text.replace(miss,fix).replace(miss.lower(),fix).replace(miss.title(),fix)
    return text

INITIAL_PROMPT = build_initial_prompt()
CORR = load_corrections()

def hms(t,sep=".",ms=3):
    t=max(0,float(t));h=int(t//3600);m=int((t%3600)//60);s=int(t%60);msv=int(round((t-int(t))*10**ms))
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{msv:03d}"

def write_retry_manifest_amd(base_path: Path, media_path: Path, segs, low_conf_indices, threshold):
    """Write a manifest of segments needing retry for batch post-processing"""
    manifest_path = base_path.with_suffix(".retry_manifest.tsv")
    with open(manifest_path, "w", encoding="utf-8") as f:
        # NEW: include zero_length flag
        f.write("media_file\tsegment_idx\tstart_time\tend_time\tconfidence\tzero_length\ttext\n")
        for idx in low_conf_indices:
            seg = segs[idx]
            # Handle both dict and object formats
            if isinstance(seg, dict):
                start = float(seg.get("start", 0.0))
                end = float(seg.get("end", 0.0))
                conf = float(seg.get("avg_logprob", 0.0))
                text = (seg.get("text", "") or "").strip()
            else:
                start = float(getattr(seg, "start", 0.0))
                end = float(getattr(seg, "end", 0.0))
                conf = float(getattr(seg, "avg_logprob", 0.0))
                text = (getattr(seg, "text", "") or "").strip()

            # Mark and expand zero-length spans
            zero_length = 1 if end <= start else 0
            if zero_length:
                end = start + 1.0  # 1-second window for retry

            text = text.replace("\t", " ").replace("\n", " ")
            f.write(
                f"{media_path}\t{idx}\t{start:.3f}\t{end:.3f}\t{conf:.3f}\t{zero_length}\t{text}\n"
            )
    print(f"[AMD][MANIFEST] wrote retry manifest for {len(low_conf_indices)} segments to {manifest_path.name}", flush=True)


def write_vtt(segs,path,retried_indices=None):
    retried_indices = retried_indices or set()
    with open(path,"w",encoding="utf-8") as f:
        f.write("WEBVTT\n\n")
        for i,s in enumerate(segs):
            txt=s.text.strip()
            if not txt: continue
            conf = float(getattr(s, "avg_logprob", 0.0))
            if i in retried_indices:
                f.write(f"NOTE Confidence: {conf:.3f} [RETRIED]\n\n")
            else:
                f.write(f"NOTE Confidence: {conf:.3f}\n\n")
            f.write(f"{hms(s.start)} --> {hms(s.end)}\n{txt}\n\n")

def write_srt(segs,path):
    with open(path,"w",encoding="utf-8") as f:
        for i,s in enumerate(segs,1):
            txt=s.text.strip()
            if not txt: continue
            a=hms(s.start,sep=","); b=hms(s.end,sep=",")
            f.write(f"{i}\n{a} --> {b}\n{txt}\n\n")

def write_words_tsv_faster(base_path: Path, segs):
    words_tsv = base_path.with_suffix(".words.tsv")
    try:
        with open(words_tsv, "w", encoding="utf-8") as wf:
            wf.write("start\tend\tword\tseg\n")
            for si, s in enumerate(segs):
                ws = getattr(s, "words", None)
                if not ws: continue
                for w in ws:
                    word = (getattr(w, "word", "") or "").strip()
                    if not word: continue
                    wf.write(f"{float(getattr(w,'start',0.0)):.3f}\t{float(getattr(w,'end',0.0)):.3f}\t{word}\t{si}\n")
    except Exception as e:
        print(f"[CPU][WARN] failed to write words.tsv: {e}", flush=True)

def make_tslog(caption,tslog,interval):
    import re
    rx=re.compile(r'^(\d\d):(\d\d):(\d\d)')
    last=-1e9; start=None
    with open(caption,encoding="utf-8",errors="ignore") as i, open(tslog,"w",encoding="utf-8") as o:
        for line in i:
            if "-->" in line and rx.match(line):
                h,m,s=map(int,line[:8].split(":")); start=h*3600+m*60+s; continue
            if not line.strip(): continue
            if start is None: continue
            if start-last>=interval:
                o.write(f"[{start//3600:02d}:{(start%3600)//60:02d}:{start%60:02d}] {line}"); last=start
            else: o.write(line)

def claim():
    # Pick the smallest media file first (with randomization to avoid contention)
    try:
        import random
        tasks = []
        for entry in PENDING.iterdir():
            if not entry.is_file():
                continue
            try:
                media_path = Path(entry.read_text(encoding="utf-8", errors="ignore"))
                if media_path.exists():
                    size = media_path.stat().st_size
                else:
                    size = 9e18  # missing file = deprioritize
                # Add small random offset to break ties and avoid worker contention
                sort_key = size + random.randint(0, size // 100 + 1)
                tasks.append((sort_key, entry, media_path))
            except Exception:
                continue
        if not tasks:
            return None
        tasks.sort(key=lambda x: x[0])
        for _, entry, media_path in tasks:
            dest = INPROG / entry.name
            try:
                entry.replace(dest)
                return dest, media_path
            except (FileNotFoundError, PermissionError, OSError):
                continue
    except FileNotFoundError:
        # Queue directory was cleaned up
        return None
    return None

print(f"[CPU] CPU threads set to {CPU_THREADS_VAL} (CPU worker needs many threads)", flush=True)
print(f"[CPU] loading faster-whisper model={MODEL} device=cpu compute={COMPUTE} threads={CPU_THREADS}", flush=True)
model=WhisperModel(MODEL, device="cpu", compute_type=COMPUTE, cpu_threads=CPU_THREADS)

while True:
    task = claim()
    if not task:
        try:
            # Quick check: if PENDING is empty and INPROG is empty, we're done
            # Use next() with default to avoid iterating all files
            if next(PENDING.iterdir(), None) is None and next(INPROG.iterdir(), None) is None:
                break
        except FileNotFoundError:
            # Queue directory was cleaned up, exit gracefully
            break
        time.sleep(0.1); continue  # Reduced sleep for faster task pickup

    task_file, media = task
    # Original base for source files (in pull/)
    media_base = media.with_suffix("")
    ensure_src_json(media, media_base)

    # Output base for generated files (in generated/)
    base = get_output_base(media)

    out_txt = base.with_suffix(".txt")
    do_vtt = OUTFMT in ("vtt","both"); do_srt = OUTFMT in ("srt","both")
    out_vtt = base.with_suffix(".vtt") if do_vtt else None
    out_srt = base.with_suffix(".srt") if do_srt else None
    lock = base.with_suffix(".transcribing.lock")
    success_marker = base.with_suffix(".transcribed")

    try:
        # Check success marker first (faster than checking txt existence)
        if success_marker.exists() and not FORCE:
            print(f"[CPU][SKIP]{media} (already transcribed)", flush=True)
            continue
        if out_txt.exists() and not FORCE:
            print(f"[CPU][SKIP]{media} (txt exists)", flush=True)
            continue  # Immediately try next task
        else:
            try:
                import os as _os
                fd=_os.open(lock, _os.O_CREAT|_os.O_EXCL|_os.O_WRONLY, 0o644); _os.close(fd)
                got_lock=True
            except FileExistsError:
                got_lock=False
            if not got_lock:
                print(f"[CPU][LOCK]{media} (held elsewhere) — skipping", flush=True)
                continue  # Immediately try next task without sleeping
            else:
                print(f"[CPU][RUN ]{media}", flush=True)

                total = probe_duration_seconds(media) or 0.0
                print(f"[CPU][INFO]{media} duration={total:.1f}s", flush=True)

                next_mark = [0.10]
                last_end = 0.0
                # Build transcribe kwargs with optional anti-hallucination knobs
                kwargs = dict(
                    language=LANG,
                    vad_filter=True,
                    initial_prompt=INITIAL_PROMPT,
                    condition_on_previous_text=False,
                    temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
                    beam_size=5,
                    word_timestamps=True,
                )
                import os as _os
                if _os.environ.get("ANTIHALLUC","1") != "0":
                    kwargs.update(dict(compression_ratio_threshold=2.4, log_prob_threshold=-1.0))
                try:
                    segments, info = model.transcribe(str(media), **kwargs)
                except TypeError:
                    kwargs.pop('compression_ratio_threshold', None)
                    kwargs.pop('log_prob_threshold', None)
                    segments, info = model.transcribe(str(media), **kwargs)

                segs = []
                for s in segments:
                    segs.append(s)
                    last_end = max(last_end, float(getattr(s, "end", 0.0)))
                    emit_progress("[CPU][PROG]", last_end, total, next_mark)

                if total > 0:
                    print(f"[CPU][100%] {total:.1f}s / {total:.1f}s", flush=True)

                # CPU worker uses single high-quality pass (no retries needed)
                retried_set = set()

                # Plain text
                with open(out_txt,"w",encoding="utf-8") as f:
                    for s in segs:
                        t=s.text.strip()
                        if t: f.write(apply_corrections(t, CORR) + "\n")

                # NEW: words.tsv with confidence scores
                write_words_tsv_faster(base, segs, retried_set)

                # Captions + tslog
                cap=None
                if do_vtt: write_vtt(segs,out_vtt,retried_set); cap=out_vtt
                if do_srt: write_srt(segs,out_srt); cap=cap or out_srt
                if cap: make_tslog(cap, base.with_suffix(".tslog.txt"), MIN_TS)

                # Mark successful completion
                success_marker.touch()
                print(f"[CPU][DONE]{media}", flush=True)
    except Exception as e:
        print(f"[CPU][FAIL]{media}: {e}", flush=True)
        # Clean up partial success marker if exists
        try: success_marker.unlink()
        except: pass
    finally:
        try: os.unlink(lock)
        except Exception: pass
        try: task_file.replace(DONE / task_file.name)
        except Exception: pass
PY


########### start ###########
add_cuda12_shims || true

if (( SETUP_VENVS )); then
  log "Setting up virtual environments..."
  ensure_nv_venv
  ensure_amd_venv
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
NV_LOG="${LOG_DIR%/}/nv.log"
AMD_LOG="${LOG_DIR%/}/amd.log"
CPU_LOG="${LOG_DIR%/}/cpu.log"

# Archive old logs instead of truncating
archive_log "$NV_LOG"
archive_log "$AMD_LOG"
archive_log "$CPU_LOG"

# Create new empty logs
: >"$NV_LOG"; : >"$AMD_LOG"; : >"$CPU_LOG"

# Wait for background file validation to complete
if [[ -n "${VALIDATOR_PID:-}" ]]; then
  log "Waiting for file validation to complete..."
  wait "$VALIDATOR_PID" || warn "File validation had errors (some files may be skipped)"
fi

NV_OK=0; command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1 && NV_OK=1
AMD_OK=1

# ----- NVIDIA worker -----
if (( NV_OK )); then
  log "Starting NVIDIA worker (log: $NV_LOG)"
  setsid bash -c "env MODEL=\"$MODEL\" LANG=\"$LANGUAGE\" FORCE=\"$FORCE\" OUTFMT=\"$OUTFMT\" NV_COMPUTE=\"$NV_COMPUTE\" NV_VAD_FILTER=\"$NV_VAD_FILTER\" MIN_TS_INTERVAL=\"$MIN_TS_INTERVAL\" QUEUE_DIR=\"$QUEUE_DIR\" HOTWORDS_FILE=\"$HOTWORDS_FILE\" PROMPT_PREFIX=\"$PROMPT_PREFIX\" CORRECTIONS_TSV=\"$CORRECTIONS_TSV\" ANTIHALLUC=\"$ANTIHALLUC\" LOG_TS_FORMAT=\"$LOG_TS_FORMAT\" OMP_NUM_THREADS=\"$GPU_THREADS\" MKL_NUM_THREADS=\"$GPU_THREADS\" OPENBLAS_NUM_THREADS=\"$GPU_THREADS\" NUMEXPR_NUM_THREADS=\"$GPU_THREADS\" RAYON_NUM_THREADS=\"$GPU_THREADS\" stdbuf -oL -eL \"$NV_VENV/bin/python\" \"$NV_RUNNER\" 2>&1 | ts_prefix_awk >\"$NV_LOG\"" & pids+=($!); PGIDS+=(-$!)
else
  warn "NVIDIA unavailable; skipping NV worker."
fi

# ----- AMD worker -----
if (( AMD_OK )); then
  log "Starting AMD worker (log: $AMD_LOG)"
  setsid bash -c "env MODEL=\"$MODEL\" LANG=\"$LANGUAGE\" FORCE=\"$FORCE\" OUTFMT=\"$OUTFMT\" MIN_TS_INTERVAL=\"$MIN_TS_INTERVAL\" AMD_NO_SPEECH_THRESHOLD=\"$AMD_NO_SPEECH_THRESHOLD\" AMD_COMPRESSION_RATIO_THRESHOLD=\"$AMD_COMPRESSION_RATIO_THRESHOLD\" AMD_LOGPROB_THRESHOLD=\"$AMD_LOGPROB_THRESHOLD\" AMD_FP16=\"$AMD_FP16\" QUEUE_DIR=\"$QUEUE_DIR\" HOTWORDS_FILE=\"$HOTWORDS_FILE\" PROMPT_PREFIX=\"$PROMPT_PREFIX\" CORRECTIONS_TSV=\"$CORRECTIONS_TSV\" ANTIHALLUC=\"$ANTIHALLUC\" LOG_TS_FORMAT=\"$LOG_TS_FORMAT\" OMP_NUM_THREADS=\"$GPU_THREADS\" MKL_NUM_THREADS=\"$GPU_THREADS\" OPENBLAS_NUM_THREADS=\"$GPU_THREADS\" NUMEXPR_NUM_THREADS=\"$GPU_THREADS\" RAYON_NUM_THREADS=\"$GPU_THREADS\" stdbuf -oL -eL \"$AMD_VENV/bin/python\" \"$AMD_RUNNER\" 2>&1 | ts_prefix_awk >\"$AMD_LOG\"" & pids+=($!); PGIDS+=(-$!)
else
  warn "ROCm unavailable; skipping AMD worker."
fi

# ----- CPU worker (optional) -----
if (( ENABLE_CPU )); then
  # Adjust CPU threads based on whether GPU workers are active
  # If GPUs active, reduce CPU threads to avoid starving GPU preprocessing
  if (( NV_OK || AMD_OK )); then
    CPU_THREADS="$CPU_THREADS_WITH_GPU"
    log "Starting CPU worker with reduced threads ($CPU_THREADS) to avoid GPU contention (log: $CPU_LOG)"
  else
    CPU_THREADS="$CPU_THREADS_SOLO"
    log "Starting CPU worker with full threads ($CPU_THREADS) - no GPU competition (log: $CPU_LOG)"
  fi

  # Build CPU launch prefix string
  CPU_PREFIX=""
  [[ -n "$CPU_AFFINITY" ]] && CPU_PREFIX+="taskset -c \"$CPU_AFFINITY\" "
  CPU_PREFIX+="ionice -c$CPU_IONICE_CLASS -n$CPU_IONICE_PRIO nice -n $CPU_NICE "

  setsid bash -c "env MODEL=\"$MODEL\" LANG=\"$LANGUAGE\" FORCE=\"$FORCE\" OUTFMT=\"$OUTFMT\" CPU_COMPUTE=\"$CPU_COMPUTE\" CPU_THREADS=\"$CPU_THREADS\" MIN_TS_INTERVAL=\"$MIN_TS_INTERVAL\" QUEUE_DIR=\"$QUEUE_DIR\" HOTWORDS_FILE=\"$HOTWORDS_FILE\" PROMPT_PREFIX=\"$PROMPT_PREFIX\" CORRECTIONS_TSV=\"$CORRECTIONS_TSV\" ANTIHALLUC=\"$ANTIHALLUC\" LOG_TS_FORMAT=\"$LOG_TS_FORMAT\" OMP_NUM_THREADS=\"$CPU_THREADS\" MKL_NUM_THREADS=\"$CPU_THREADS\" OPENBLAS_NUM_THREADS=\"$CPU_THREADS\" NUMEXPR_NUM_THREADS=\"$CPU_THREADS\" RAYON_NUM_THREADS=\"$CPU_THREADS\" ${CPU_PREFIX}stdbuf -oL -eL \"$NV_VENV/bin/python\" \"$CPU_RUNNER\" 2>&1 | ts_prefix_awk >\"$CPU_LOG\"" & pids+=($!); PGIDS+=(-$!)
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
