#!/usr/bin/env bash
set -euo pipefail

# Full ingest pipeline with prompts
# Steps:
#  1) Export videos (.info.json → logs/db/videos_from_info.tsv)
#  2) Load videos
#  3) Prompt upload_type for this batch
#  4) Derive title dates from titles
#  5) Build transcripts manifest from generated/ and load
#  6) Prompt: optional hits TSV load
#  7) Prompt: optional words load
#  8) ANALYZE
# Usage: scripts/db/import_videos.sh [database]

DB_NAME="${1:-transcripts}"

ts() { date +%s; }
human() { printf "%02d:%02d" "$(( $1/60 ))" "$(( $1%60 ))"; }
section() { echo; echo "==> $1"; }

start_all=$(ts)

# 1) Export videos
section "[1/8] Exporting videos from pull/*__*.info.json → logs/db/videos_from_info.tsv"
start=$(ts)
python3 scripts/db/export_videos_from_info.py
dur=$(( $(ts) - start ))
echo "   done in $(human $dur)"

# 2) Load videos
section "[2/8] Loading videos into DB: ${DB_NAME}"
start=$(ts)
psql -v ON_ERROR_STOP=1 -d "$DB_NAME" -f scripts/db/load_videos_from_info.sql
psql -d "$DB_NAME" -c "SELECT COUNT(*) AS videos FROM videos;" || true
dur=$(( $(ts) - start ))
echo "   done in $(human $dur)"

# 3) Prompt upload_type
section "[3/8] Annotating upload_type for this batch (prompt)…"
start=$(ts)
bash scripts/db/annotate_upload_type.sh "$DB_NAME"
psql -d "$DB_NAME" -c "SELECT upload_type, COUNT(*) FROM videos GROUP BY 1 ORDER BY 2 DESC;" || true
dur=$(( $(ts) - start ))
echo "   done in $(human $dur)"

# 4) Derive title dates
section "[4/8] Deriving title dates from videos.title"
start=$(ts)
psql -v ON_ERROR_STOP=1 -d "$DB_NAME" -f scripts/db/derive_title_dates.sql
psql -d "$DB_NAME" -c "SELECT COUNT(*) AS filled_title_date FROM videos WHERE title_date IS NOT NULL;" || true
dur=$(( $(ts) - start ))
echo "   done in $(human $dur)"

# 5) Transcripts manifest from generated/
section "[5/8] Scanning generated/ for transcripts (prefer Whisper words)"
start=$(ts)
python3 scripts/db/export_transcripts_from_lists_fast.py
wc -l logs/db/transcripts.tsv 2>/dev/null || true
psql -v ON_ERROR_STOP=1 -d "$DB_NAME" -f scripts/db/load_transcripts.sql
psql -d "$DB_NAME" -c "SELECT COUNT(*) AS transcripts FROM transcripts; SELECT COUNT(*) AS assets FROM assets;" || true
dur=$(( $(ts) - start ))
echo "   done in $(human $dur)"

# 6) Optional hits load
section "[6/8] Optional: load hits TSV"
read -r -p "Path to hits TSV (leave empty to skip): " HITS_FILE
if [[ -n "${HITS_FILE:-}" ]]; then
  if [[ -f "$HITS_FILE" ]]; then
    start=$(ts)
    echo "   loading: $HITS_FILE"
    bash scripts/db/load_hits.sh "$HITS_FILE" "$DB_NAME"
    psql -d "$DB_NAME" -c "SELECT COUNT(*) AS hits FROM hits; SELECT ROUND(SUM(duration_sec),3) AS total_seconds FROM hits;" || true
    dur=$(( $(ts) - start ))
    echo "   done in $(human $dur)"
  else
    echo "   not found: $HITS_FILE (skipping)"
  fi
else
  echo "   skipped"
fi

# 7) Optional words load
section "[7/8] Optional: load words (per-token timestamps, large)"
read -r -p "Load words now? (y/N): " DO_WORDS
if [[ "${DO_WORDS,,}" =~ ^y ]]; then
  start=$(ts)
  echo "   exporting per-token rows from generated/ (one words file per video)"
  python3 scripts/db/export_transcripts_and_words_from_lists.py
  wc -l logs/db/words_export.tsv 2>/dev/null || true
  psql -v ON_ERROR_STOP=1 -d "$DB_NAME" -f scripts/db/load_words.sql
  psql -d "$DB_NAME" -c "SELECT COUNT(*) AS words FROM words;" || true
  dur=$(( $(ts) - start ))
  echo "   done in $(human $dur)"

  # Offer to delete the interim TSV to reclaim space
  if [[ -f logs/db/words_export.tsv ]]; then
    read -r -p "Delete interim logs/db/words_export.tsv? (Y/n): " DEL_WORDS
    DEL_WORDS=${DEL_WORDS:-Y}
    if [[ "${DEL_WORDS,,}" =~ ^(y|yes)$ ]]; then
      rm -f logs/db/words_export.tsv && echo "   removed logs/db/words_export.tsv"
    else
      echo "   kept logs/db/words_export.tsv"
    fi
  fi
else
  echo "   skipped"
fi

# 8) ANALYZE
section "[8/8] ANALYZE planner stats"
start=$(ts)
psql -d "$DB_NAME" -c "ANALYZE;" || true
dur=$(( $(ts) - start ))
echo "   done in $(human $dur)"

all_dur=$(( $(ts) - start_all ))
echo
echo "All done in $(human $all_dur)."
