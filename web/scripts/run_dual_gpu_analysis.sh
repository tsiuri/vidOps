#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Run dual‑GPU transcript analysis (parallel chunks + speaker pass).

Defaults assume:
  - NVIDIA on 11434 using granite3.3:8b
  - AMD on 11435 using qwen3-vl:8b
  - Source VTTs in:
      /mnt/13tb_sas/Hasan Project/VidOps/2019_2022_pikerpull/pull

Options:
  -s, --source DIR          Directory with *.transcript.en.vtt (default: see above)
  -n, --limit N             How many files (default: 5)
  -d, --db-name NAME        Postgres DB name (default: transcripts)
      --quality Q           fast|balanced|thorough (default: thorough)

  # Primary (A) – also used for political summaries
      --url-a URL           Ollama URL for A (default: http://localhost:11434)
      --model-a TAG         Model tag for A (default: granite3.3:8b)

  # Secondary (B) – used for even chunks
      --url-b URL           Ollama URL for B (default: http://localhost:11435)
      --model-b TAG         Model tag for B (default: qwen3-vl:8b)

  # Speaker pass (second pass)
      --speaker-url URL     Speaker pass URL (default: URL-B)
      --speaker-model TAG   Speaker pass model (default: MODEL-B)
      --alias NAME          Speaker third-person alias (default: Hasan)

      --parallel N          Chunk parallel workers (default: 8)
  -o, --output DIR          Output dir for JSON/MD (default: analyzed)
      --no-force            Do not overwrite existing analyses (default: overwrite)
  -h, --help                Show help

Examples:
  scripts/run_dual_gpu_analysis.sh -n 5 \
    --url-a http://localhost:11434 --model-a granite3.3:8b \
    --url-b http://localhost:11435 --model-b qwen3-vl:8b
USAGE
}

# Defaults
SOURCE_DIR="/mnt/13tb_sas/Hasan Project/VidOps/2019_2022_pikerpull/pull"
LIMIT=5
DB_NAME="transcripts"
QUALITY="thorough"
URL_A="http://localhost:11434"
MODEL_A="granite3.3:8b"
URL_B="http://localhost:11435"
MODEL_B="qwen3-vl:8b"
SPEAKER_URL=""
SPEAKER_MODEL=""
ALIAS="Hasan"
PARALLEL=8
OUTPUT_DIR="analyzed"
FORCE=1

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--source) SOURCE_DIR="$2"; shift 2;;
    -n|--limit) LIMIT="$2"; shift 2;;
    -d|--db-name) DB_NAME="$2"; shift 2;;
    --quality) QUALITY="$2"; shift 2;;
    --url-a) URL_A="$2"; shift 2;;
    --model-a) MODEL_A="$2"; shift 2;;
    --url-b) URL_B="$2"; shift 2;;
    --model-b) MODEL_B="$2"; shift 2;;
    --speaker-url) SPEAKER_URL="$2"; shift 2;;
    --speaker-model) SPEAKER_MODEL="$2"; shift 2;;
    --alias) ALIAS="$2"; shift 2;;
    --parallel) PARALLEL="$2"; shift 2;;
    -o|--output) OUTPUT_DIR="$2"; shift 2;;
    --no-force) FORCE=0; shift 1;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1"; usage; exit 1;;
  esac
done

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "Source directory not found: $SOURCE_DIR" >&2
  exit 1
fi

cd "$(dirname "$0")/.."  # repo root with analyze_to_db.py

mapfile -t files < <(find "$SOURCE_DIR" -maxdepth 1 -type f -name '*.transcript.en.vtt' | sort | head -n "$LIMIT")
if [[ ${#files[@]} -eq 0 ]]; then
  echo "No *.transcript.en.vtt files found in: $SOURCE_DIR" >&2
  exit 1
fi

args=(
  "${files[@]}"
  --db-name "$DB_NAME"
  --quality "$QUALITY"
  --model "$MODEL_A"
  --ollama-url "$URL_A"
  --chunk-url-a "$URL_A" --chunk-model-a "$MODEL_A"
  --chunk-url-b "$URL_B" --chunk-model-b "$MODEL_B"
  --speaker-url "${SPEAKER_URL:-$URL_B}" --speaker-model "${SPEAKER_MODEL:-$MODEL_B}"
  --speaker-alias "$ALIAS"
  --chunk-parallel "$PARALLEL"
  --continue-on-error
  --save-json --output "$OUTPUT_DIR"
)

if [[ "$FORCE" -eq 1 ]]; then
  args+=(--force)
fi

echo "Running analyze_to_db.py with ${#files[@]} files (limit=$LIMIT)"
echo "  A: $MODEL_A @ $URL_A"
echo "  B: $MODEL_B @ $URL_B"
echo "  Speaker: ${SPEAKER_MODEL:-$MODEL_B} @ ${SPEAKER_URL:-$URL_B} (alias: $ALIAS)"

./analyze_to_db.py "${args[@]}"

