\set ON_ERROR_STOP on
BEGIN;

DROP TABLE IF EXISTS words_tmp;
CREATE TEMP TABLE words_tmp (
  ytid TEXT,
  source TEXT,
  idx INTEGER,
  word TEXT,
  start_sec NUMERIC(10,3),
  end_sec NUMERIC(10,3),
  confidence REAL,
  segment_id INTEGER
);

-- Respect quotes from the exporter and treat empty strings as NULLs
-- Explicit column list allows files without a trailing path column.
\copy words_tmp (ytid, source, idx, word, start_sec, end_sec, confidence, segment_id) FROM 'logs/db/words_export.tsv' WITH (FORMAT csv, DELIMITER E'\t', HEADER true, NULL '');

-- Report rows whose videos are missing (will be skipped below)
SELECT count(*) AS missing_words_no_video
FROM words_tmp wt
LEFT JOIN videos v ON v.ytid = wt.ytid
WHERE v.ytid IS NULL;

-- Lowercase word on insert; enforce bounds; upsert on (ytid, source, idx)
INSERT INTO words (ytid, source, idx, word, start_sec, end_sec, confidence, segment_id)
SELECT wt.ytid,
       CASE WHEN lower(wt.source) IN ('yt','whisper') THEN lower(wt.source) ELSE lower(wt.source) END,
       wt.idx,
       lower(coalesce(wt.word, '')),
       wt.start_sec,
       wt.end_sec,
       wt.confidence,
       NULLIF(wt.segment_id, 0)
FROM words_tmp wt
JOIN videos v ON v.ytid = wt.ytid
WHERE wt.ytid IS NOT NULL AND wt.ytid <> ''
  AND wt.start_sec IS NOT NULL AND wt.end_sec IS NOT NULL
  AND wt.end_sec >= wt.start_sec
ON CONFLICT (ytid, source, idx) DO UPDATE SET
  word = EXCLUDED.word,
  start_sec = EXCLUDED.start_sec,
  end_sec = EXCLUDED.end_sec,
  confidence = EXCLUDED.confidence,
  segment_id = EXCLUDED.segment_id;

COMMIT;
