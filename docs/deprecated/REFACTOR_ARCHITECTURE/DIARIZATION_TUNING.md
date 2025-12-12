# Diarization Tuning Notes

Defaults tuned for better accuracy:
- Reference clips: 50 candidates (default) at 8.0s each (was 10 @ 6s). Use `--ref-clips` / `--ref-clip-duration` to override.
- Chunking: 12.0s chunks with 2.0s overlap (was 6s/1s) to reduce boundary artifacts. Override with `--chunk-seconds` / `--overlap-seconds`.
- Shared references: `vo diarize enqueue-file --shared-reference-name <name>` builds one reference (sampling up to 50 clips across the list) and reuses it for all jobs.

Similarity threshold (cosine) calibration:
- Default `--similarity-threshold` is 0.6. If you see missed matches (over-splitting), lower toward 0.45–0.55; if you see false merges, raise toward 0.65–0.7.
- Calibrate on a labeled subset: run a small grid (e.g., 0.45, 0.5, 0.55, 0.6, 0.65, 0.7) and pick the best F1/DER. Keep chunk/overlap fixed while tuning.

Usage examples:
```bash
# Batch with shared reference
python vo_cli.py diarize enqueue-file generated/query_ids/pop_trigger_ytids.tsv \
  --transcript-kind best \
  --shared-reference-name poptrigger_shared \
  --priority 5

# Tweaking thresholds/chunking
python vo_cli.py diarize enqueue-file generated/query_ids/pop_trigger_ytids.tsv \
  --transcript-kind best \
  --shared-reference-name poptrigger_shared \
  --similarity-threshold 0.55 \
  --chunk-seconds 15 --overlap-seconds 3
```

Ground truth / validation helpers:
- Convert Audacity labels (start TAB end TAB speaker) to RTTM via `scripts/diarization/labels_to_rttm.py <labels.txt> <ytid> [--out-dir DIR]`.
- Sample labeled RTTMs live at `/home/billie/projects/vidops/manual_diarized_rttm/` for ytids: 6SJYw-wVXio, ec77Rt_cHE, jLPaPSHIZWk, SclRF-9dCdc.
