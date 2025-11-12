#!/usr/bin/env bash
# batch_faster_whisper.sh
# Recursively transcribe media to .txt + (.vtt/.srt) and a timestamped log (.tslog.txt).
# CUDA/NVIDIA: auto-detects GPU; if CUDA 13 is installed but CTranslate2 wants CUDA 12
# (libcublas.so.12 / libcublasLt.so.12), it will temporarily SHIM those sonames and undo on exit.
#
# Usage:
#   ./batch_faster_whisper.sh
#   ./batch_faster_whisper.sh --model small          # tiny|base|small|medium|large-v3
#   ./batch_faster_whisper.sh --lang en              # force language (omit to autodetect)
#   ./batch_faster_whisper.sh --force                # re-transcribe even if .txt/.vtt exist
#   ./batch_faster_whisper.sh --ext 'mp4 mkv wav'    # custom extensions
#   ./batch_faster_whisper.sh --outfmt vtt           # vtt|srt|both (default: vtt)
#
# Env knobs:
#   MIN_TS_INTERVAL=10     # minimum seconds between timestamps in .tslog.txt (default 10)
#   SHIM_CUDA12=1          # create libcublas.so.12/libcublasLt.so.12 symlinks for this run (default 1)
#   FW_DEVICE=cuda         # force device (otherwise auto-detects)
#   FW_COMPUTE=float16     # compute type on CUDA: float16 | int8_float16 | int8 etc.

set -euo pipefail

MODEL="medium"
LANGUAGE=""
FORCE=0
OUTFMT="vtt"   # vtt|srt|both
EXTENSIONS=("mp4" "mkv" "mov" "avi" "mp3" "wav" "m4a")
VENV_DIR="$HOME/transcription-env"
LOG="${PWD}/batch_faster_whisper.log"

MIN_TS_INTERVAL="${MIN_TS_INTERVAL:-10}"
SHIM_CUDA12="${SHIM_CUDA12:-1}"

# --- Helpers: CUDA12 shim/unshim -------------------------------------------------
CUDA_LIBDIR="/opt/cuda/lib64"
CUBLAS12="/usr/lib/libcublas.so.12"
CUBLASLT12="/usr/lib/libcublasLt.so.12"
declare -a _SHIMS_MADE=()

need_root_or_sudo() {
  if [[ $EUID -eq 0 ]]; then return 0; fi
  if command -v sudo >/dev/null 2>&1; then return 0; fi
  echo "This action requires root (or sudo not found)." >&2
  return 1
}

make_cuda12_shims_if_needed() {
  [[ "${SHIM_CUDA12}" == "1" ]] || return 0

  # Only bother if we're going to use CUDA
  if [[ "${DEVICE:-}" != "cuda" ]]; then return 0; fi

  # If libcudnn not present, warn (user likely needs: pacman -S cudnn)
  if ! ls /usr/lib/libcudnn*.so* >/dev/null 2>&1; then
    echo "Warning: cuDNN libraries not found in /usr/lib (install 'cudnn')." >&2
  fi

  # If required .12 sonames already exist, nothing to do
  if [[ -e "$CUBLAS12" && -e "$CUBLASLT12" ]]; then
    return 0
  fi

  # Ensure CUDA libdir exists
  if [[ ! -e "$CUDA_LIBDIR/libcublas.so" || ! -e "$CUDA_LIBDIR/libcublasLt.so" ]]; then
    echo "CUDA libs not found at $CUDA_LIBDIR. Install 'cuda' or adjust CUDA_LIBDIR." >&2
    return 0
  fi

  need_root_or_sudo || return 0

  # Create shims that point CUDA-12 sonames to your installed CUDA (often v13) libs
  if [[ ! -e "$CUBLAS12" ]]; then
    if [[ $EUID -eq 0 ]]; then
      ln -sfn "$CUDA_LIBDIR/libcublas.so" "$CUBLAS12"
    else
      sudo ln -sfn "$CUDA_LIBDIR/libcublas.so" "$CUBLAS12"
    fi
    _SHIMS_MADE+=("$CUBLAS12")
  fi
  if [[ ! -e "$CUBLASLT12" ]]; then
    if [[ $EUID -eq 0 ]]; then
      ln -sfn "$CUDA_LIBDIR/libcublasLt.so" "$CUBLASLT12"
    else
      sudo ln -sfn "$CUDA_LIBDIR/libcublasLt.so" "$CUBLASLT12"
    fi
    _SHIMS_MADE+=("$CUBLASLT12")
  fi

  # Make sure loader sees CUDA dir too
  if [[ -d "$CUDA_LIBDIR" ]]; then
    export LD_LIBRARY_PATH="${CUDA_LIBDIR}:${LD_LIBRARY_PATH-}"
  fi

  # Refresh linker cache (best effort)
  if [[ $EUID -eq 0 ]]; then
    ldconfig || true
  else
    command -v sudo >/dev/null 2>&1 && sudo ldconfig || true
  fi
}

remove_cuda12_shims() {
  # Remove only the symlinks we created in this run
  ((${#_SHIMS_MADE[@]})) || return 0
  need_root_or_sudo || return 0
  for s in "${_SHIMS_MADE[@]}"; do
    if [[ -L "$s" ]]; then
      if [[ $EUID -eq 0 ]]; then rm -f "$s"; else sudo rm -f "$s"; fi
    fi
  done
  if [[ $EUID -eq 0 ]]; then ldconfig || true; else command -v sudo >/dev/null 2>&1 && sudo ldconfig || true; fi
}
trap remove_cuda12_shims EXIT

# --- Timestamped log builder ------------------------------------------------------
# make_timestamped_log INPUT_(.vtt|.srt) OUTPUT_.tslog.txt [interval]
make_timestamped_log() {
  local in="$1"
  local out="$2"
  local interval="${3:-$MIN_TS_INTERVAL}"

  awk -v interval="$interval" '
    function tosec(hms,   a,h,m,s) {
      gsub(",", ".", hms)
      split(hms, a, ":")
      h = a[1] + 0
      m = a[2] + 0
      s = a[3] + 0
      return h*3600 + m*60 + s
    }
    function fmt_from_seconds(t,   h,m,s) {
      h = int(t/3600); t -= h*3600
      m = int(t/60);   t -= m*60
      s = int(t + 0.5)
      return sprintf("[%02d:%02d:%02d]", h,m,s)
    }
    BEGIN { last_ts = -1e9; in_text = 0 }
    /^[0-9]{2}:[0-9]{2}:[0-9]{2}[.,][0-9]{3}[ \t]*-->[ \t]*[0-9]{2}:[0-9]{2}:[0-9]{2}[.,][0-9]{3}/ {
      split($0, parts, /[ \t]*-->[ \t]*/)
      cue_start = tosec(parts[1]); in_text = 1; next
    }
    /^$/ { in_text = 0; next }
    /^[0-9]+$/ && !in_text { next }  # SRT indices
    in_text {
      gsub(/<\/?i>/, "", $0)
      if ((cue_start - last_ts) >= interval) {
        printf("%s %s\n", fmt_from_seconds(cue_start), $0)
        last_ts = cue_start
      } else {
        print $0
      }
      next
    }
    { next }
  ' "$in" > "$out"
}

# --- Arg parsing ------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="${2:-$MODEL}"; shift 2;;
    --lang|--language) LANGUAGE="${2:-}"; shift 2;;
    --force) FORCE=1; shift;;
    --ext) IFS=' ' read -r -a EXTENSIONS <<< "${2:-}"; shift 2;;
    --outfmt) OUTFMT="${2:-$OUTFMT}"; shift 2;;
    -h|--help)
      echo "Usage: $0 [--model MODEL] [--lang CODE] [--force] [--ext 'mp4 mkv ...'] [--outfmt vtt|srt|both]"
      exit 0;;
    *) echo "Unknown option: $1"; exit 1;;
  esac
done

echo "==> Starting batch transcription"
echo "    Model:        ${MODEL}"
echo "    Language:     ${LANGUAGE:-auto}"
echo "    Force:        ${FORCE}"
echo "    Extensions:   ${EXTENSIONS[*]}"
echo "    Outfmt:       ${OUTFMT}"
echo "    Log:          ${LOG}"
echo "    MIN_TS_INTERVAL: ${MIN_TS_INTERVAL}s"
echo

# --- prereqs ----------------------------------------------------------------------
command -v python >/dev/null 2>&1 || { echo "python not found"; exit 1; }
command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg not found (pacman -S ffmpeg)"; exit 1; }

# --- venv bootstrap ---------------------------------------------------------------
if [[ ! -d "$VENV_DIR" ]]; then
  echo "==> Creating venv at ${VENV_DIR}"
  python -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# Install/upgrade libs quietly
python - <<'PYSETUP'
import sys, subprocess
def pip_install(pkg):
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-U", pkg], stdout=subprocess.DEVNULL)
for pkg in ("pip", "setuptools", "wheel"):
    pip_install(pkg)
for pkg in ("faster-whisper", "ffmpeg-python"):
    pip_install(pkg)
PYSETUP

# --- device detection & compute type ---------------------------------------------
DEVICE="${FW_DEVICE:-cpu}"
COMPUTE_TYPE="${FW_COMPUTE:-int8}"  # good CPU default

if [[ "${FW_DEVICE:-}" == "" ]]; then
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    DEVICE="cuda"
    COMPUTE_TYPE="float16"
  else
    DEVICE="cpu"
    COMPUTE_TYPE="int8"
  fi
fi

echo "==> Inference device: ${DEVICE}  (compute_type=${COMPUTE_TYPE})"
echo

# If using CUDA, ensure CUDA12 sonames are satisfied for CTranslate2
make_cuda12_shims_if_needed || true

# --- gather files -----------------------------------------------------------------
ARGS=( -type f "(" )
for ext in "${EXTENSIONS[@]}"; do
  ARGS+=( -iname "*.${ext}" -o )
done
unset 'ARGS[${#ARGS[@]}-1]'
ARGS+=( ")" -print0 )

FILELIST="$(mktemp)"
find . "${ARGS[@]}" > "${FILELIST}"

TOTAL=$(tr -cd '\000' < "${FILELIST}" | wc -c)
if [[ "$TOTAL" -eq 0 ]]; then
  echo "No media files found. Extensions searched: ${EXTENSIONS[*]}"
  rm -f "${FILELIST}"
  exit 0
fi

echo "==> Found ${TOTAL} files to consider"

# --- python runner: NUL-separated list; writes .txt + .vtt/.srt -------------------
PYTHON_RUNNER="$(mktemp --suffix=.py)"
cat > "${PYTHON_RUNNER}" <<'PY'
import sys, os, time
from pathlib import Path
from faster_whisper import WhisperModel

MODEL   = os.environ.get("FW_MODEL", "medium")
DEVICE  = os.environ.get("FW_DEVICE", "cpu")
COMPUTE = os.environ.get("FW_COMPUTE", "int8")
LANG    = os.environ.get("FW_LANG") or None
FORCE   = os.environ.get("FW_FORCE", "0") == "1"
LOG     = os.environ.get("FW_LOG", "batch_faster_whisper.log")
OUTFMT  = os.environ.get("FW_OUTFMT", "vtt")  # vtt|srt|both

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")
    print(msg, flush=True)

def hms_ms(t: float, sep=",", ms_digits=3):
    t = max(0.0, float(t))
    h = int(t // 3600); t -= h*3600
    m = int(t // 60);   t -= m*60
    s = int(t)
    ms = int(round((t - s) * (10**ms_digits)))
    if ms == 1000 and ms_digits == 3:
        s += 1; ms = 0
        if s == 60:
            m += 1; s = 0
            if m == 60:
                h += 1; m = 0
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:0{ms_digits}d}"

def write_srt(segments, out_path: Path):
    with out_path.open("w", encoding="utf-8") as f:
        idx = 1
        for seg in segments:
            text = (seg.text or "").strip()
            if not text:
                continue
            start = hms_ms(seg.start, sep=",")
            end   = hms_ms(seg.end,   sep=",")
            f.write(f"{idx}\n{start} --> {end}\n{text}\n\n")
            idx += 1

def write_vtt(segments, out_path: Path):
    with out_path.open("w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n")
        for seg in segments:
            text = (seg.text or "").strip()
            if not text:
                continue
            start = hms_ms(seg.start, sep=".")
            end   = hms_ms(seg.end,   sep=".")
            f.write(f"{start} --> {end}\n{text}\n\n")

log(f"Loading model='{MODEL}' device='{DEVICE}' compute_type='{COMPUTE}' lang='{LANG or 'auto'}'")
model = WhisperModel(MODEL, device=DEVICE, compute_type=COMPUTE)

def transcribe(path: Path):
    out_txt = path.with_suffix(".txt")
    do_vtt  = OUTFMT in ("vtt", "both")
    do_srt  = OUTFMT in ("srt", "both")
    out_vtt = path.with_suffix(".vtt") if do_vtt else None
    out_srt = path.with_suffix(".srt") if do_srt else None

    if not FORCE:
        have_txt = out_txt.exists() and out_txt.stat().st_size > 0
        have_vtt = (not do_vtt) or (out_vtt and out_vtt.exists() and out_vtt.stat().st_size > 0)
        have_srt = (not do_srt) or (out_srt and out_srt.exists() and out_srt.stat().st_size > 0)
        if have_txt and have_vtt and have_srt:
            log(f"[SKIP] {path} (has .txt and requested captions)")
            return

    log(f"[RUN ] {path}")
    try:
        segments, info = model.transcribe(str(path), language=LANG, vad_filter=True)
        segs = list(segments)

        with out_txt.open("w", encoding="utf-8") as f:
            for seg in segs:
                text = (seg.text or "").strip()
                if text:
                    f.write(text + "\n")

        if do_vtt:
            write_vtt(segs, out_vtt)
        if do_srt:
            write_srt(segs, out_srt)

        made = [".txt"]
        if do_vtt: made.append(".vtt")
        if do_srt: made.append(".srt")
        log(f"[DONE] {path}  -> {' '.join(made)}")
    except Exception as e:
        log(f"[FAIL] {path}  error={e}")

data = sys.stdin.buffer.read().split(b"\x00")
for raw in data:
    if not raw:
        continue
    p = Path(raw.decode("utf-8", "ignore"))
    transcribe(p)
PY

# export settings for runner
export FW_MODEL="${MODEL}"
export FW_DEVICE="${DEVICE}"
export FW_COMPUTE="${COMPUTE_TYPE}"
export FW_LANG="${LANGUAGE}"
export FW_FORCE="${FORCE}"
export FW_LOG="${LOG}"
export FW_OUTFMT="${OUTFMT}"

echo "==> Transcribing…"
python "${PYTHON_RUNNER}" < "${FILELIST}"

# --- Build timestamped logs from generated captions --------------------------------
echo
echo "==> Building timestamped logs (.tslog.txt) with interval >= ${MIN_TS_INTERVAL}s"
while IFS= read -r -d '' f; do
  base="${f%.*}"
  tslog="${base}.tslog.txt"
  if [[ -f "${base}.vtt" ]]; then
    make_timestamped_log "${base}.vtt" "${tslog}" "${MIN_TS_INTERVAL}"
  elif [[ -f "${base}.srt" ]]; then
    make_timestamped_log "${base}.srt" "${tslog}" "${MIN_TS_INTERVAL}"
  fi
done < "${FILELIST}"

echo
echo "==> All done."
echo "    Log:   ${LOG}"
echo "    Venv:  ${VENV_DIR}"
