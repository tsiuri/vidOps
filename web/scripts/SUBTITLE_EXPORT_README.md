Subtitle Export (SRT/WebVTT)

Overview
- Export reconstructed subtitles from the `words` table, grouping words into lines when the time gap between consecutive words reaches a threshold. Optional filters allow limiting to diarized windows (by speaker or any speaker) and by absolute time window.
- Script: `scripts/export_subtitles.py` (outputs `.srt` or `.vtt`).

Prerequisites
- Database populated with `words` (and optionally `diarized_timestamps`).
- Views created by `schema.sql` (notably: `speaker_words`).
- Python dependencies (see `requirements.txt`, includes `psycopg2-binary`).

Usage Examples
- Whole video to SRT, 1.0s grouping:
  - `python3 scripts/export_subtitles.py --ytid dQw4w9WgXcQ --format srt --gap-sec 1.0 --output dQw4w9WgXcQ.srt`

- Only a diarized speaker (e.g., Hasan), 0.8s grouping, within [100s, 20000s], to VTT:
  - `python3 scripts/export_subtitles.py --ytid dQw4w9WgXcQ --speaker-name "Hasan" --start-sec 100 --end-sec 20000 --gap-sec 0.8 --format vtt --output hasan.vtt`

- Any diarized windows (ignore speaker identity), 1-hour window:
  - `python3 scripts/export_subtitles.py --ytid dQw4w9WgXcQ --restrict-to-diarized --start-sec 0 --end-sec 3600 --format srt`

Key Flags
- `--speaker-name <name>`: Restrict output to words within diarized spans for that speaker (uses `speaker_words`).
- `--restrict-to-diarized`: Include only words that fall within any diarized span (ignores speaker identity). Default off.
- `--start-sec <float>` / `--end-sec <float>`: Time window for selection (applies to `words.start_sec`).
- `--gap-sec <float>`: Start a new subtitle line when the gap between the previous word’s end and the next word’s start ≥ this value. Default 1.0.
- `--format srt|vtt`: Output format. Default `srt`.
- `--output <path>`: Output file path. Defaults to `<ytid>.<ext>`.

SQL-Only (DBeaver) Grouping Example
If you prefer to export grouped rows via DBeaver (e.g., CSV), you can use this SQL to reconstruct lines by gap. Replace the `ytid`, window, and `1.0` gap as needed:

```
WITH w AS (
  SELECT ytid, idx, word, start_sec, end_sec,
         LAG(end_sec) OVER (PARTITION BY ytid ORDER BY start_sec, idx) AS prev_end
  FROM words
  WHERE ytid = 'dQw4w9WgXcQ' AND start_sec BETWEEN 100 AND 20000
),
g AS (
  SELECT *, CASE WHEN prev_end IS NULL OR start_sec - prev_end >= 1.0 THEN 1 ELSE 0 END AS new_group
  FROM w
),
gg AS (
  SELECT *, SUM(new_group) OVER (PARTITION BY ytid ORDER BY start_sec, idx) AS grp
  FROM g
)
SELECT grp AS cue_no,
       MIN(start_sec) AS start_sec,
       MAX(end_sec) AS end_sec,
       STRING_AGG(word, ' ' ORDER BY start_sec, idx) AS text
FROM gg
GROUP BY grp
ORDER BY start_sec;
```

Note: DBeaver can export this result to CSV. Producing actual `.srt`/`.vtt` from SQL alone is cumbersome — the Python script handles proper timestamp formatting and output structure directly.

Notes
- Diarization filters require `diarized_timestamps` and the `speaker_words` view (created in `schema.sql`).
- Timestamps in the DB may be stored as decimals; the exporter handles coercion to float.
- For very long videos, consider larger `--gap-sec` or applying a time window.
