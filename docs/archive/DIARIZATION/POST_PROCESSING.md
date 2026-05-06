# Post-Processing & Word Mapping (Phase 4)

Inputs: diarization turns `(speaker, start, end)`, canonical audio path, words TSV (start/end). Config knobs: `micro_gap=0.3`, `min_turn=0.45`, `overlap_mode=keep|unknown`, `gap_tolerance=0.1–0.2`.

## Steps
1) **Merge micro-gaps**
   - Sort turns by start; merge adjacent same-speaker turns if gap < `micro_gap`.
   - Pseudocode:
     ```python
     merged=[]
     for t in sorted(turns, key=lambda x: x.start):
         if merged and t.spk==merged[-1].spk and (t.start - merged[-1].end) < micro_gap:
             merged[-1].end = max(merged[-1].end, t.end)
         else:
             merged.append(t)
     turns = merged
     ```

2) **Drop/Merge ultra-short**
   - For turns with duration < `min_turn`: merge into neighbor with max temporal overlap or nearest boundary; prefer same-speaker adjacency; else drop if isolated.
   - Pseudocode:
     ```python
     cleaned=[]
     for i,t in enumerate(turns):
         if t.end - t.start >= min_turn:
             cleaned.append(t); continue
         prev = cleaned[-1] if cleaned else None
         nxt = turns[i+1] if i+1 < len(turns) else None
         candidate = prev or nxt
         if candidate:
             candidate.end = max(candidate.end, t.end)
             candidate.start = min(candidate.start, t.start)
         # else drop
     turns = cleaned
     ```

3) **Optional recluster (drift smoothing)**
   - Extract embeddings per turn; run AHC/k-means; reassign speaker labels; preserve time bounds.
   - Optional PLDA/score normalization before clustering.

4) **Overlap handling**
   - Mode `keep`: leave overlaps as-is.
   - Mode `unknown`: for overlapping intervals, keep dominant (longer/higher energy) and relabel the other as `UNKNOWN` or split boundaries to non-overlap spans.

5) **Word mapping (gap tolerance)**
   - For each word `[w.start, w.end]`, find overlapping turn(s); skip turns where `turn.end + tol < w.start`; break when `turn.start - tol > w.end`.
   - Choose speaker with max overlap; if none, `UNKNOWN`.
   - Pseudocode:
     ```python
     def assign(words, turns, tol=0.15):
         out=[]
         for w in words:
             best=None; best_ov=0.0
             for spk,s,e in turns:
                 if e + tol < w.start: continue
                 if s - tol > w.end: break
                 ov = min(w.end, e) - max(w.start, s)
                 if ov > best_ov:
                     best_ov = ov; best = spk
             out.append((w, best or "UNKNOWN"))
         return out
     ```

## TSV Schemas
- `diarized_timestamps.tsv`: `ytid  speaker_name  start_sec  end_sec  duration_sec  source_audio`
- `speaker_words.tsv`: `start  end  word  seg  confidence  retried  speaker`

## Warnings / Logging
- Log merge/drop counts; warn on overlap demotions.
- Warn if no turns remain after cleanup.
- Warn if large fraction of words map to `UNKNOWN`.
- Persist warnings and params (`micro_gap`, `min_turn`, `gap_tolerance`, `overlap_mode`, recluster on/off) in `diarization.json`.
