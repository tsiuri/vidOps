\set ON_ERROR_STOP on
-- Load/merge videos from logs/videos_from_info.tsv
BEGIN;

DROP TABLE IF EXISTS videos_info_tmp;
CREATE TEMP TABLE videos_info_tmp (
  ytid           TEXT,
  url            TEXT,
  title          TEXT,
  upload_date    DATE,
  duration_sec   INTEGER,
  channel        TEXT,
  channel_id     TEXT,
  extractor_key  TEXT,
  tags_json      JSONB,
  categories_json JSONB
);

-- Use a non-printable QUOTE to avoid treating JSON double-quotes as CSV quotes
\copy videos_info_tmp FROM 'logs/db/videos_from_info.tsv' WITH (FORMAT csv, DELIMITER E'\t', HEADER true, QUOTE E'\b')

-- Deduplicate by ytid within this statement to avoid ON CONFLICT hitting the same row twice
INSERT INTO videos (ytid, url, title, upload_date, duration_sec, channel, channel_id, extractor_key, tags, categories)
SELECT ytid, url, title, upload_date, duration_sec, channel, channel_id, extractor_key, tags_json, categories_json
FROM (
  SELECT DISTINCT ON (ytid)
    ytid, url, title, upload_date, duration_sec, channel, channel_id, extractor_key, tags_json, categories_json
  FROM videos_info_tmp
  WHERE ytid IS NOT NULL AND ytid <> ''
  ORDER BY ytid, upload_date DESC NULLS LAST, length(coalesce(url,'')) DESC
) d
ON CONFLICT (ytid) DO UPDATE SET
  url = EXCLUDED.url,
  title = EXCLUDED.title,
  upload_date = EXCLUDED.upload_date,
  duration_sec = EXCLUDED.duration_sec,
  channel = EXCLUDED.channel,
  channel_id = EXCLUDED.channel_id,
  extractor_key = EXCLUDED.extractor_key,
  tags = EXCLUDED.tags,
  categories = EXCLUDED.categories;

COMMIT;
