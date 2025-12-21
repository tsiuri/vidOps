\set ON_ERROR_STOP on
-- Derive videos.title_date from videos.title by parsing the spelled-out date in the title
-- Handles full and common abbreviated month names, optional comma: "July 7, 2021" or "Jul 7 2021" or "Sept 7 2021"

WITH m AS (
  SELECT
    ytid,
    regexp_match(title,
      '(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s*' ||
      '([0-9]{1,2})\s*,?\s*([0-9]{4})',
      'i'
    ) AS mm
  FROM videos
)
UPDATE videos v
SET title_date = make_date(
    (m.mm)[3]::int,
    CASE lower((m.mm)[1])
      WHEN 'january' THEN 1 WHEN 'jan' THEN 1
      WHEN 'february' THEN 2 WHEN 'feb' THEN 2
      WHEN 'march' THEN 3 WHEN 'mar' THEN 3
      WHEN 'april' THEN 4 WHEN 'apr' THEN 4
      WHEN 'may' THEN 5
      WHEN 'june' THEN 6 WHEN 'jun' THEN 6
      WHEN 'july' THEN 7 WHEN 'jul' THEN 7
      WHEN 'august' THEN 8 WHEN 'aug' THEN 8
      WHEN 'september' THEN 9 WHEN 'sep' THEN 9 WHEN 'sept' THEN 9
      WHEN 'october' THEN 10 WHEN 'oct' THEN 10
      WHEN 'november' THEN 11 WHEN 'nov' THEN 11
      WHEN 'december' THEN 12 WHEN 'dec' THEN 12
    END,
    (m.mm)[2]::int
)
FROM m
WHERE v.ytid = m.ytid
  AND m.mm IS NOT NULL
  AND (v.title_date IS DISTINCT FROM make_date(
        (m.mm)[3]::int,
        CASE lower((m.mm)[1])
          WHEN 'january' THEN 1 WHEN 'jan' THEN 1
          WHEN 'february' THEN 2 WHEN 'feb' THEN 2
          WHEN 'march' THEN 3 WHEN 'mar' THEN 3
          WHEN 'april' THEN 4 WHEN 'apr' THEN 4
          WHEN 'may' THEN 5
          WHEN 'june' THEN 6 WHEN 'jun' THEN 6
          WHEN 'july' THEN 7 WHEN 'jul' THEN 7
          WHEN 'august' THEN 8 WHEN 'aug' THEN 8
          WHEN 'september' THEN 9 WHEN 'sep' THEN 9 WHEN 'sept' THEN 9
          WHEN 'october' THEN 10 WHEN 'oct' THEN 10
          WHEN 'november' THEN 11 WHEN 'nov' THEN 11
          WHEN 'december' THEN 12 WHEN 'dec' THEN 12
        END,
        (m.mm)[2]::int
      ));

-- Report counts
\echo 'Derived title_date rows:'
SELECT COUNT(*) AS updated_from_title
FROM videos v
WHERE v.title ~* '(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s*[0-9]{1,2}\s*,?\s*[0-9]{4}'
  AND v.title_date IS NOT NULL;

\echo 'Missing title_date after derivation:'
SELECT COUNT(*) AS missing_title_date
FROM videos v
WHERE v.title_date IS NULL;

