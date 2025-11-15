#!/usr/bin/env bash
set -euo pipefail

# Load a hits TSV into PostgreSQL (videos + hits tables)
# Usage: scripts/db/load_hits.sh logs/israel_hits_including_ytts_debug_convertedytV4.tsv [database]

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <hits.tsv> [database]" >&2
  exit 1
fi

if command -v readlink >/dev/null 2>&1; then
  HITS_FILE="$(readlink -f "$1")"
else
  HITS_FILE="$1"
fi
DB_NAME="${2:-transcripts}"

if [[ ! -f "$HITS_FILE" ]]; then
  echo "File not found: $HITS_FILE" >&2
  exit 1
fi

psql "${DB_NAME}" -v ON_ERROR_STOP=1 <<'SQL'
-- Stage table persists across psql sessions so we can \copy via STDIN
DROP TABLE IF EXISTS stage_hits;
CREATE TABLE stage_hits (
  url            TEXT,
  start          TEXT,
  "end"          TEXT,
  label          TEXT,
  source_caption TEXT,
  source_type    TEXT
);
SQL

# Load data via STDIN to avoid any path quoting issues inside psql
cat -- "$HITS_FILE" | psql "${DB_NAME}" -v ON_ERROR_STOP=1 -c "\\copy stage_hits FROM STDIN WITH (FORMAT csv, DELIMITER E'\t', HEADER true, QUOTE E'\b')"

psql "${DB_NAME}" -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;

-- Basic URL → ytid extraction (youtu.be or v=)
WITH m AS (
  SELECT DISTINCT
    COALESCE(
      NULLIF((regexp_match(url, 'v=([A-Za-z0-9_-]{11})'))[1], ''),
      NULLIF((regexp_match(url, 'youtu\\.be/([A-Za-z0-9_-]{11})'))[1], '')
    ) AS ytid,
    url
  FROM stage_hits
)
INSERT INTO videos (ytid, url)
SELECT ytid, url FROM m WHERE ytid IS NOT NULL
ON CONFLICT (ytid) DO UPDATE SET url = EXCLUDED.url;

-- Load hits
INSERT INTO hits (ytid, start_sec, end_sec, label, source_caption, source_type)
SELECT
  COALESCE(
    NULLIF((regexp_match(url, 'v=([A-Za-z0-9_-]{11})'))[1], ''),
    NULLIF((regexp_match(url, 'youtu\\.be/([A-Za-z0-9_-]{11})'))[1], '')
  ) AS ytid,
  (start)::numeric,
  ("end")::numeric,
  NULLIF(label, ''),
  NULLIF(source_caption, ''),
  NULLIF(source_type, '')
FROM stage_hits
WHERE ("end")::numeric >= (start)::numeric
  AND COALESCE(
        NULLIF((regexp_match(url, 'v=([A-Za-z0-9_-]{11})'))[1], ''),
        NULLIF((regexp_match(url, 'youtu\\.be/([A-Za-z0-9_-]{11})'))[1], '')
      ) IS NOT NULL
ON CONFLICT DO NOTHING;

-- Report
DO $$
DECLARE n_hits bigint; n_vids bigint;
BEGIN
  SELECT count(*) INTO n_hits FROM hits;
  SELECT count(*) INTO n_vids FROM videos;
  RAISE NOTICE 'videos=% hits=%', n_vids, n_hits;
END$$;

COMMIT;

DROP TABLE IF EXISTS stage_hits;
SQL

echo "Loaded hits from: $HITS_FILE into DB: $DB_NAME"
