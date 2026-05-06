# Chunked Diarization Processing

## Overview

To prevent memory exhaustion when processing long audio files (>1 hour), the diarization system now automatically chunks files into ~1 hour segments, processes each separately, and combines the results.

## How It Works

### Automatic Chunking

When a diarization job is submitted, the system:

1. **Checks audio duration** using `ffprobe`
2. **If > 1 hour**: Automatically chunks the file
3. **If ≤ 1 hour**: Processes normally (no chunking overhead)

### Chunking Process

1. **Split**: Audio is divided into chunks with overlap
   - Default: 3600s (1 hour) per chunk
   - Overlap: 30s between chunks (prevents speaker cutoff)

2. **Process**: Each chunk is diarized independently
   - Memory usage per chunk: ~2-4 GB (vs 8-32 GB for full file)
   - Chunks processed sequentially (not parallel)

3. **Combine**: Results are merged with adjusted timestamps
   - Timestamps offset by chunk start time
   - Speaker labels prefixed with chunk index (C0_, C1_, etc.)
   - Single unified output TSV

### Memory Benefits

**Before (4-hour file)**:
```
Models:            800 MB
Audio tensor:     1600 MB (full 4h file)
Intermediates:   3000 MB
CUDA pinned:     3000 MB
-----------------------------------
TOTAL:          8400 MB → Memory limit exceeded!
```

**After (4 x 1-hour chunks)**:
```
Models:            800 MB
Audio tensor:      400 MB (1h chunk)
Intermediates:     750 MB
CUDA pinned:       750 MB
-----------------------------------
TOTAL:          2700 MB per chunk (within 8GB limit)
```

## Configuration

### Environment Variables

```bash
# Chunk threshold (files longer than this use chunking)
export DIAR_CHUNK_THRESHOLD=3600  # Default: 1 hour

# Chunk size
export DIAR_CHUNK_DURATION=3600   # Default: 1 hour

# Overlap between chunks
export DIAR_CHUNK_OVERLAP=30      # Default: 30 seconds
```

### Example: 2-Hour Chunks

```bash
export DIAR_CHUNK_THRESHOLD=7200
export DIAR_CHUNK_DURATION=7200
export DIAR_CHUNK_OVERLAP=60
```

## Output Format

### Standard (No Chunking)

```
results/diarization/video123/
├── diarized_timestamps.tsv
├── diarized_timestamps_matched.tsv  (if reference used)
├── diarized_timestamps_clean.tsv    (post-processed)
├── speaker_words.tsv
└── diarization.json
```

### Chunked Processing

```
results/diarization/video123/
├── diarized_timestamps.tsv          # Combined results
├── speaker_words.tsv                 # (not combined - future work)
└── diarization.json                  # Metadata includes chunking info
```

### Chunked Metadata

```json
{
  "ytid": "video123",
  "source_audio": "/path/to/original.opus",
  "chunked_processing": true,
  "chunks": {
    "num_chunks": 4,
    "chunk_duration": 3600.0,
    "overlap": 30.0,
    "chunk_results": [
      {"chunk_index": 0, "speakers": 2, "segments": 145},
      {"chunk_index": 1, "speakers": 3, "segments": 152},
      {"chunk_index": 2, "speakers": 2, "segments": 138},
      {"chunk_index": 3, "speakers": 2, "segments": 127}
    ]
  },
  "results": {
    "num_speakers": 9,  # C0_SPEAKER_00, C0_SPEAKER_01, C1_SPEAKER_00, etc.
    "speaker_labels": ["C0_SPEAKER_00", "C0_SPEAKER_01", ...],
    "num_segments": 562,
    "total_speech_duration": 12500.5
  },
  "note": "Speaker labels prefixed with chunk index for uniqueness"
}
```

## Speaker Label Mapping

### Current Behavior

Speaker labels are prefixed with chunk index to prevent collisions:

```
Chunk 0: SPEAKER_00 → C0_SPEAKER_00
Chunk 1: SPEAKER_00 → C1_SPEAKER_00  (different speaker)
Chunk 2: SPEAKER_00 → C2_SPEAKER_00  (possibly same as C0_SPEAKER_00)
```

**Why?** The same person may appear in multiple chunks, but PyAnnote assigns labels independently per chunk. Without tracking speaker identity across chunks, we can't merge labels.

### Future: Cross-Chunk Speaker Mapping (TODO)

Use embedding similarity to map speakers across chunks:

```python
# Extract embeddings for C0_SPEAKER_00 and C2_SPEAKER_00
# If similarity > threshold, merge labels
# Result: C0_SPEAKER_00 appears in chunks 0 and 2
```

**Implementation:**
1. Extract speaker embeddings from each chunk
2. Compute similarity matrix across chunks
3. Cluster similar speakers globally
4. Re-label with consistent IDs

## Usage Examples

### CLI Worker

```bash
# Worker automatically uses chunking for long files
python vo_cli.py worker start
```

### Manual Testing

```bash
# Test chunking script directly
python scripts/diarization/chunk_audio.py \
  path/to/long_audio.opus \
  tmp/chunks/ \
  --chunk-duration 3600 \
  --overlap 30

# Test combination (after diarizing chunks)
python scripts/diarization/combine_chunks.py \
  tmp/chunks/ \
  results/chunk_results/ \
  results/combined/ \
  video123
```

### Disable Chunking

```bash
# Set threshold very high
export DIAR_CHUNK_THRESHOLD=999999

# Or process shorter files
```

## Performance

### Processing Time

**4-hour file example:**

- **Without chunking**: ~15 minutes (but OOMs)
- **With chunking (4 chunks)**: ~20 minutes (4 × 5 min)
  - Overhead: ~5 minutes (chunking + combining)

### Disk Usage

**Temporary storage:**

- Chunks: ~110 MB per 1-hour chunk at 16kHz mono
- 4-hour file: ~440 MB chunks + 183 MB original = 623 MB total
- Cleaned up after job completes

## Troubleshooting

### Chunk Processing Fails

If a chunk fails diarization:

```
ERROR: Chunk 2 diarization failed with exit 1
```

**Solutions:**

1. Check chunk-specific logs in `logs/diarization/`
2. Process chunk manually:
   ```bash
   python scripts/diarization/batch_diarize.py \
     tmp/chunk_002_ytids.txt \
     results/chunks/ \
     --config config/diarization.yaml
   ```
3. Reduce chunk size if still OOMing:
   ```bash
   export DIAR_CHUNK_DURATION=1800  # 30 minutes
   ```

### Speaker Count Too High

With chunking, you'll see more speakers (due to chunk prefixing):

**Expected:**
- 4-hour stream with 2 speakers
- Without chunking: 2 speakers (SPEAKER_00, SPEAKER_01)
- With chunking: 8 speakers (C0_SPEAKER_00, C0_SPEAKER_01, C1_SPEAKER_00, ...)

**Workaround:** Implement cross-chunk speaker mapping (future work)

### Combining Fails

If combination fails:

```
ERROR: Chunk combination failed with exit 1
```

**Debug:**

1. Check chunks were diarized:
   ```bash
   ls -la tmp/chunk_results/video123_chunk_*/
   ```

2. Verify metadata:
   ```bash
   cat tmp/chunks/chunks_metadata.json
   ```

3. Run combine manually:
   ```bash
   python scripts/diarization/combine_chunks.py \
     tmp/chunks/ \
     tmp/chunk_results/ \
     results/diarization/video123/ \
     video123 \
     --verbose
   ```

## Implementation Details

### Scripts

1. **chunk_audio.py**: Splits audio into chunks
   - Uses `ffmpeg` for extraction
   - Saves metadata for reconstruction

2. **combine_chunks.py**: Merges diarization results
   - Adjusts timestamps
   - Prefixes speaker labels
   - Combines metadata

3. **diarization.py**: Service orchestration
   - `_get_audio_duration()`: Check duration
   - `_run_chunked_diarization()`: Chunk → Process → Combine
   - `_run_single_diarization()`: Standard processing

### Flow Diagram

```
Job Submission
     ↓
Check Duration
     ↓
  > 1 hour?
     ↓ Yes
[Chunked Processing]
  1. chunk_audio.py → chunks/
  2. For each chunk:
     - batch_diarize.py → chunk_results/
  3. combine_chunks.py → final/
     ↓
   Output
```

## Future Improvements

1. **Cross-chunk speaker mapping**
   - Use embedding similarity
   - Global speaker clustering

2. **Parallel chunk processing**
   - Multi-GPU support
   - Process chunks concurrently

3. **Smart overlap adjustment**
   - Detect speaker boundaries
   - Minimize mid-speech cuts

4. **Word-level combination**
   - Currently only combines timestamps
   - Should also merge `speaker_words.tsv`

5. **Adaptive chunk sizing**
   - Smaller chunks for low-memory systems
   - Larger chunks for high-memory systems

---

**Created**: 2025-12-06
**Author**: Claude Code
**Related**: INFERENCE_IMPLEMENTATION.md, vad_memory.md
