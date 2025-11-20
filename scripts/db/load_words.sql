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
  segment_id INTEGER,
  path TEXT
);

-- Respect quotes from the exporter and treat empty strings as NULLs
\copy words_tmp FROM 'logs/db/words_export.tsv' WITH (FORMAT csv, DELIMITER E'\t', HEADER true, NULL '')

-- Lowercase word on insert; enforce bounds; upsert on (ytid, source, idx)
INSERT INTO words (ytid, source, idx, word, start_sec, end_sec, confidence, segment_id)
SELECT ytid,
       CASE WHEN lower(source) IN ('yt','whisper') THEN lower(source) ELSE 'whisper' END,
       idx,
       lower(coalesce(word, '')),
       start_sec,
       end_sec,
       confidence,
       NULLIF(segment_id, 0)
FROM words_tmp
WHERE ytid IS NOT NULL AND ytid <> ''
  AND start_sec IS NOT NULL AND end_sec IS NOT NULL
  AND end_sec >= start_sec
ON CONFLICT (ytid, source, idx) DO UPDATE SET
  word = EXCLUDED.word,
  start_sec = EXCLUDED.start_sec,
  end_sec = EXCLUDED.end_sec,
  confidence = EXCLUDED.confidence,
  segment_id = EXCLUDED.segment_id;

COMMIT;
