#!/usr/bin/env bash
set -euo pipefail

# Prompt for an upload_type and apply it to videos from the last export batch
# Usage: scripts/db/annotate_upload_type.sh [database]

DB_NAME="${1:-transcripts}"
LIST_FILE="logs/db/_last_exported_ytids.txt"

if [[ ! -f "$LIST_FILE" ]]; then
  echo "List not found: $LIST_FILE. Run export_videos_from_info.py first." >&2
  exit 1
fi

read -r -p "Upload type for this batch (e.g., vod, clip, highlight, stream, other): " UPLOAD_TYPE
UPLOAD_TYPE=${UPLOAD_TYPE:-}
if [[ -z "$UPLOAD_TYPE" ]]; then
  echo "No upload_type provided; aborting." >&2
  exit 1
fi

ABS_LIST_FILE=$(readlink -f "$LIST_FILE")
echo "Setting upload_type='$UPLOAD_TYPE' for $(wc -l < "$LIST_FILE") videos from last export..."
psql -v ON_ERROR_STOP=1 -v upload_type="$UPLOAD_TYPE" -d "$DB_NAME" <<SQL
BEGIN;
CREATE TEMP TABLE _ytids_tmp(ytid text PRIMARY KEY);
\copy _ytids_tmp FROM '${ABS_LIST_FILE}' WITH (FORMAT text)
UPDATE videos v
SET upload_type = :'upload_type'
WHERE (v.upload_type IS NULL OR v.upload_type = '')
  AND v.ytid IN (SELECT ytid FROM _ytids_tmp);
COMMIT;
SQL
echo "Done."
