\set ON_ERROR_STOP on
BEGIN;

DROP TABLE IF EXISTS title_date_tmp;
CREATE TEMP TABLE title_date_tmp (
  ytid TEXT,
  title_date DATE
);

\copy title_date_tmp FROM 'logs/db/title_date_map.tsv' WITH (FORMAT csv, DELIMITER E'\t', HEADER true)

UPDATE videos v
SET title_date = t.title_date
FROM title_date_tmp t
WHERE v.ytid = t.ytid
  AND (v.title_date IS DISTINCT FROM t.title_date);

COMMIT;
