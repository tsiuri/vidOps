DB Ingest: Diarized Speaker Timestamps
=====================================

This folder includes a small ingestion script and the database objects needed to align diarized speaker spans with your per‑word captions and then query just the words said by a chosen speaker.

What This Provides
------------------
- Table `diarized_timestamps` for storing speaker spans per video (start/end seconds).
- View `speaker_words` that joins `diarized_timestamps` to the `words` table to return only words spoken inside diarized spans for each speaker.
- CLI script `ingest_diarized_timestamps.py` that prompts for a speaker name and ingests VTT‑like timestamp files.

Prerequisites
-------------
- Your database has the standard `videos` and `words` tables (the latter contains per‑word timings).
- You have applied the repository schema updates (see below) so `diarized_timestamps` and `speaker_words` exist.
- Your diarization output files (VTT‑like with cue timestamps) live under a directory you specify.

Apply the Schema
----------------
- From the repo root (where `schema.sql` lives):

```
psql -d transcripts -v ON_ERROR_STOP=1 -f schema.sql
```

This creates:
- `diarized_timestamps(ytid, speaker_name, normalized_speaker, start_sec, end_sec, duration_sec, source_path)`
- `speaker_words(ytid, speaker_name, normalized_speaker, idx, word, start_sec, end_sec)`

Ingestion Script
----------------

Script: `scripts/ingest_diarized_timestamps.py`

Usage example:

```
scripts/ingest_diarized_timestamps.py \
  --path "/home/billie/scripts/hasan-transcription" \
  --db-name transcripts
```

What it does:
- Prompts you: “Enter speaker name to tag these spans:” — type e.g. `Hasan`.
- Scans the `--path` for `*.vtt` files (recursively if a directory).
- Parses each VTT’s cue lines (HH:MM:SS.mmm --> HH:MM:SS.mmm) and inserts each span.
- Infers the YouTube video id (ytid) from the filename prefix `<VIDEOID>__...` or falls back to the stem.
- Stores `source_path` for provenance.
- Uses a unique constraint so re‑ingest won’t duplicate spans.

Expected VTT format:
- Standard WebVTT cues; the script only uses the time lines, ignoring cue text.
  Example:
  ```
  00:10:01.000 --> 00:10:07.500
  (any text here is ignored)
  ```

Normalization:
- `speaker_name` is stored as entered; `normalized_speaker` is lower‑cased and trimmed.
- Use consistent names (“Hasan”, “Guest X”) so downstream queries match reliably.

Querying Speaker Words
----------------------

Once spans are ingested:

```
SELECT *
FROM speaker_words
WHERE normalized_speaker='hasan'
  AND ytid='VIDEOID'
ORDER BY start_sec
LIMIT 100;
```

This returns the words (`word`) and their timings (`start_sec`, `end_sec`) for that speaker inside the diarized windows.

Overlay/Export Ideas
--------------------
- Build a “speaker transcript” by concatenating `speaker_words.word` ordered by time.
- Compare total speech time per speaker: `SELECT normalized_speaker, SUM(duration_sec) FROM diarized_timestamps GROUP BY 1 ORDER BY 2 DESC;`
- Intersect with your `segment_overview` to study speaker contributions within analyzed segments.

Operational Notes
-----------------
- Re‑ingest is safe: duplicate spans (same ytid/speaker/start/end) are ignored.
- If your diarization output isn’t WebVTT, adapt the parser in `parse_vtt_spans()`.
- If filenames don’t contain `<VIDEOID>__...`, adjust `infer_ytid()` accordingly.

Troubleshooting
---------------
- “No VTT files found”: confirm `--path` and that files match `*.vtt`.
- “relation diarized_timestamps does not exist”: apply `schema.sql` first.
- Words missing: ensure your `words` table is populated for the same `ytid` values.

