\set ON_ERROR_STOP on
-- Set videos.upload_type for either a supplied list of ytids (one per line) or all rows with NULL upload_type.
-- Variables (psql -v):
--   upload_type=...  (required)
--   ytid_file=...    (optional path to a file with one ytid per line)

\if :{?upload_type}
\else
\echo 'ERROR: upload_type not provided. Use: psql -v upload_type=vod -f scripts/db/set_upload_type.sql'
\quit 1
\endif

BEGIN;

-- Optional scope from ytid_file
DROP TABLE IF EXISTS _ytids_tmp;
-- Always create the temp table; fill only if a file is provided
CREATE TEMP TABLE _ytids_tmp(ytid text PRIMARY KEY);
\if :{?ytid_file}
\copy _ytids_tmp FROM :'ytid_file' WITH (FORMAT text)
\endif

WITH has_list AS (
  SELECT EXISTS(SELECT 1 FROM _ytids_tmp) AS has
)
UPDATE videos v
SET upload_type = :'upload_type'
FROM has_list h
LEFT JOIN _ytids_tmp t ON t.ytid = v.ytid
WHERE (v.upload_type IS NULL OR v.upload_type = '')
  AND (NOT h.has OR t.ytid IS NOT NULL);

COMMIT;
