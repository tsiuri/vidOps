#!/usr/bin/env bash
set -euo pipefail

# Export YTIDs (with titles + URLs) for a given upload_type.
# Usage:
#   scripts/db/export_ytids_by_upload_type.sh <upload_type> [output_file] [db_name]
#   scripts/db/export_ytids_by_upload_type.sh --like <pattern> [output_file] [db_name]

# Resolve repo root (prefers VIDOPS_PROJECT_ROOT, otherwise git root, otherwise CWD)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${VIDOPS_PROJECT_ROOT:-$(git -C "$SCRIPT_DIR/.." rev-parse --show-toplevel 2>/dev/null || pwd)}"
CONFIG_PATH="${CONFIG_PATH:-${ROOT_DIR}/config.yaml}"

hydrate_pg_from_config() {
  # Only hydrate if not already set
  if [[ -n "${PGHOST:-}" && -n "${PGPORT:-}" && -n "${PGUSER:-}" && -n "${PGDATABASE:-}" ]]; then
    return 0
  fi
  if [[ ! -f "$CONFIG_PATH" ]]; then
    return 0
  fi
  read -r PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE <<<"$(python - "$CONFIG_PATH" <<'PY'
import sys, yaml
cfg_path = sys.argv[1]
try:
    with open(cfg_path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
except FileNotFoundError:
    sys.exit(0)
db = data.get("database") or {}
# Emit space-separated fields (empty allowed)
print(
    (db.get("host") or ""),
    (db.get("port") or ""),
    (db.get("user") or ""),
    (db.get("password") or ""),
    (db.get("name") or ""),
)
PY
)"
  export PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE
}

hydrate_pg_from_config

USE_LIKE="false"
if [[ "${1:-}" == "--like" ]]; then
  USE_LIKE="true"
  shift
fi

if [[ $# -lt 1 || $# -gt 3 ]]; then
  echo "Usage: $0 [--like] <upload_type_or_pattern> [output_file] [db_name]" >&2
  echo "Examples:" >&2
  echo "  $0 pop_trigger generated/query_ids/pop_trigger_ytids.tsv" >&2
  echo "  $0 --like 'pop_trigger%'" >&2
  exit 1
fi

UPLOAD_FILTER="$1"
OUTPUT_FILE="${2:-generated/query_ids/${UPLOAD_FILTER}_ytids.tsv}"
DB_NAME="${3:-${VIDOPS_DB_NAME:-${DB_NAME:-${PGDATABASE:-vidops}}}}"

mkdir -p "$(dirname "$OUTPUT_FILE")"

# If using ILIKE and no SQL wildcard present, wrap with %...% for substring match
if [[ "$USE_LIKE" == "true" && "$UPLOAD_FILTER" != *"%"* && "$UPLOAD_FILTER" != *"_"* ]]; then
  UPLOAD_FILTER="%${UPLOAD_FILTER}%"
fi

SAFE_FILTER=${UPLOAD_FILTER//\'/\'\'}
if [[ "$USE_LIKE" == "true" ]]; then
  SQL_FILTER="upload_type ILIKE '${SAFE_FILTER}'"
else
  SQL_FILTER="upload_type = '${SAFE_FILTER}'"
fi

psql -v ON_ERROR_STOP=1 -d "$DB_NAME" -c "\
COPY (
  SELECT
    ytid,
    COALESCE(title, '') AS title,
    COALESCE(url, '')   AS url
  FROM videos
  WHERE ${SQL_FILTER}
  ORDER BY upload_date DESC NULLS LAST, created_at DESC
) TO STDOUT WITH (FORMAT csv, HEADER true, DELIMITER E'\t');
" > "$OUTPUT_FILE"

echo "Wrote $(wc -l < "$OUTPUT_FILE") rows to $OUTPUT_FILE"
