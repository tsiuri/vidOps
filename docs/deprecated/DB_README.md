• Here’s a clean, repeatable import pipeline with inputs and outputs for each step.

  0) Schema

  - Purpose: create tables, indexes, extensions, views.
  - Reads: none
  - Writes: DB tables videos, assets, transcripts, words, hits; indexes; views
  - Run:
      - psql -d transcripts -f db/schema.sql
      - psql -d transcripts -f db/views.sql (after data)

  1) Videos (metadata)

  - Purpose: load per‑video facts from YouTube .info.json.
  - Reads (FS): pull/*__*.info.json
  - Writes (FS interim): logs/db/videos_from_info.tsv
  - Writes (DB): videos
  - Run:
      - python3 scripts/db/export_videos_from_info.py
      - psql -d transcripts -f scripts/db/load_videos_from_info.sql

  2) Title Dates (derived from titles)

  - Purpose: fill videos.title_date by parsing the spelled date in videos.title.
  - Reads (DB): videos.title
  - Writes (DB): videos.title_date
  - Run:
      - psql -d transcripts -f scripts/db/derive_title_dates.sql

  3) Transcripts Manifest (scan generated/)

  - Purpose: discover transcript artifacts (prefer Whisper words; fallback to
    YouTube captions).
  - Reads (FS): generated/**/words.tsv, generated/**/*.words.tsv, generated/**/
    *.words.yt.tsv
  - Prefers: words_whisper over words_ytt (one row per ytid; largest file if
    multiple)
  - Writes (FS): logs/db/transcripts.tsv
  - Writes (DB): transcripts, assets
  - Run:
      - python3 scripts/db/export_transcripts_from_lists_fast.py
      - psql -d transcripts -f scripts/db/load_transcripts.sql

  4) Hits (computed windows)

  - Purpose: load label/time windows you computed.
  - Reads (FS): your hits TSV (e.g., logs/
    israel_hits_including_ytts_debug_convertedytV4.tsv)
  - Writes (DB): videos (ensures URL/ytid), hits
  - Run:
      - bash scripts/db/load_hits.sh logs/
        israel_hits_including_ytts_debug_convertedytV4.tsv transcripts

  5) Words (per‑token timestamps) — optional/large

  - Purpose: store per‑word rows for advanced queries (big table).
  - Reads (FS): generated/**/words.tsv and/or *.words.yt.tsv (from earlier step’s
    list exporter)
  - Writes (FS): logs/db/words_export.tsv
  - Writes (DB): words
  - Run:
      - python3 scripts/db/export_transcripts_and_words_from_lists.py
      - psql -d transcripts -f scripts/db/load_words.sql

  6) Views and Stats

  - Purpose: rollups and planner stats.
  - Reads (DB): hits, videos
  - Writes (DB): daily_title_date, monthly_title_date, missing_title_date
  - Run:
      - psql -d transcripts -f db/views.sql
      - psql -d transcripts -c "ANALYZE;"

  Quick verify commands

  - psql -d transcripts -c "SELECT COUNT(*) FROM videos;"
  - psql -d transcripts -c "SELECT COUNT(*) FROM transcripts; SELECT COUNT(*) FROM
    assets;"
  - psql -d transcripts -c "SELECT COUNT(*) FROM hits; SELECT
    ROUND(SUM(duration_sec),3) FROM hits;"
  - psql -d transcripts -c "SELECT COUNT(*) FROM words;" (if loaded)
  - psql -d transcripts -c "SELECT * FROM daily_title_date ORDER BY total_seconds
    DESC LIMIT 10;"

