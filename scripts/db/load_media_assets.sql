\set ON_ERROR_STOP on

-- Allow overriding the media TSV path via -v media_tsv=/abs/path.tsv
\if :{?media_tsv}
  \set media_file :media_tsv
\else
  \set media_file 'logs/db/media_assets.tsv'
\endif

BEGIN;

DROP TABLE IF EXISTS media_assets_tmp;
CREATE TEMP TABLE media_assets_tmp (
  ytid      TEXT,
  kind      TEXT,
  path      TEXT,
  bytes     BIGINT,
  rel_path  TEXT
);

\copy media_assets_tmp FROM :'media_file' WITH (FORMAT csv, DELIMITER E'\t', HEADER true, QUOTE E'\b')

INSERT INTO assets (ytid, kind, path, bytes, rel_path)
SELECT
  ytid,
  COALESCE(kind, 'media'),
  path,
  bytes,
  NULLIF(rel_path, '')
FROM media_assets_tmp
WHERE ytid IS NOT NULL AND ytid <> '' AND path IS NOT NULL AND path <> ''
ON CONFLICT (path) DO UPDATE SET
  ytid = EXCLUDED.ytid,
  kind = EXCLUDED.kind,
  bytes = EXCLUDED.bytes,
  rel_path = EXCLUDED.rel_path;

COMMIT;
