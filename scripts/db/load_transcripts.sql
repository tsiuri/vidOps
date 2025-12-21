\set ON_ERROR_STOP on
BEGIN;

DROP TABLE IF EXISTS transcripts_tmp;
CREATE TEMP TABLE transcripts_tmp (
  ytid TEXT,
  kind TEXT,
  lang TEXT,
  path TEXT,
  bytes BIGINT,
  word_count INTEGER,
  segment_count INTEGER
);

\copy transcripts_tmp FROM 'logs/db/transcripts.tsv' WITH (FORMAT csv, DELIMITER E'\t', HEADER true, QUOTE E'\b')

INSERT INTO transcripts (ytid, kind, lang, path, word_count, segment_count)
SELECT ytid, kind, lang, path, word_count, segment_count
FROM transcripts_tmp
WHERE ytid IS NOT NULL AND ytid <> '' AND kind IS NOT NULL AND kind <> ''
ON CONFLICT (ytid, kind, lang) DO UPDATE SET
  path = EXCLUDED.path,
  word_count = COALESCE(EXCLUDED.word_count, transcripts.word_count),
  segment_count = COALESCE(EXCLUDED.segment_count, transcripts.segment_count);

-- Mirror into assets table
INSERT INTO assets (ytid, kind, path, bytes)
SELECT ytid,
       CASE kind WHEN 'vtt' THEN 'vtt' WHEN 'words_ytt' THEN 'words_ytt' WHEN 'words_whisper' THEN 'words_whisper' ELSE kind END,
       path,
       bytes
FROM transcripts_tmp
WHERE path IS NOT NULL AND path <> ''
ON CONFLICT (path) DO UPDATE SET
  ytid = EXCLUDED.ytid,
  kind = EXCLUDED.kind,
  bytes = EXCLUDED.bytes;

COMMIT;
