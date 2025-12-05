# Transcript Quality Hierarchy

## Overview

The VidOps system supports automatic selection of the best available transcript for operations like diarization, analysis, and search. Instead of manually specifying a transcript kind (e.g., `words_whisper_medium`), you can use `--transcript-kind best` to automatically select the highest quality transcript available for each video.

## Quality Hierarchy

Transcripts are ranked from highest to lowest quality:

### Whisper Words Format (Highest Quality)
1. `words_whisper_large-v3` - Latest and most accurate model
2. `words_whisper_large-v2` - Previous generation large model
3. `words_whisper_large` - Original large model
4. `words_whisper_turbo` - Fast, high-quality model
5. `words_whisper_medium` - Balanced accuracy and speed
6. `words_whisper_small` - Faster, lower accuracy
7. `words_whisper_base` - Basic model
8. `words_whisper_tiny` - Fastest, lowest accuracy

### Whisper VTT Format (Fallback)
9. `vtt_whisper_large-v3`
10. `vtt_whisper_large-v2`
11. `vtt_whisper_large`
12. `vtt_whisper_turbo`
13. `vtt_whisper_medium`
14. `vtt_whisper_small`
15. `vtt_whisper_base`
16. `vtt_whisper_tiny`

### YouTube Auto-Captions (Lowest Quality)
17. `words_ytt` - YouTube auto-captions in words format
18. `vtt` - Raw YouTube VTT captions

## Usage

### Basic Example

```bash
# Instead of specifying the transcript kind:
python vo_cli.py diarize enqueue 7TdvWiRdhqk --transcript-kind words_whisper_medium

# Use "best" to auto-select:
python vo_cli.py diarize enqueue 7TdvWiRdhqk --transcript-kind best
```

The system will automatically:
1. Query the database for all available transcripts for the ytid
2. Select the highest quality transcript according to the hierarchy
3. Use that transcript for the operation
4. Log which transcript was selected

### Batch Processing

For processing multiple videos, using `best` ensures each video uses its highest quality transcript, even if they have different transcripts available:

```bash
# Process multiple ytids with best available transcript for each
YTIDS=("ytid1" "ytid2" "ytid3")

for ytid in "${YTIDS[@]}"; do
    python vo_cli.py diarize enqueue "$ytid" \
        --transcript-kind best \
        --model resemblyzer \
        --priority 10
done
```

### Example Script

See `examples/diarize_batch_best.sh` for a complete example showing:
- Hard-coded ytid lists
- Reading ytids from a file
- Querying the database for ytids needing diarization

## How It Works

### Selection Algorithm

When you specify `--transcript-kind best`:

1. **Query Database**: Fetch all transcripts for the ytid
2. **Map by Kind**: Create a lookup map of available transcript kinds
3. **Iterate Hierarchy**: Walk through the quality hierarchy in order
4. **First Match**: Return the first transcript kind found in the hierarchy
5. **Fallback**: If no match in hierarchy (unusual), return first available

### Code Location

**Configuration**: `vidops/config.py`
```python
TRANSCRIPT_QUALITY_HIERARCHY = [
    "words_whisper_large-v3",
    "words_whisper_large-v2",
    # ... full hierarchy
]
```

**Repository Method**: `vidops/dal/transcripts.py`
```python
def get_best_available(self, ytid: str) -> Optional[Transcript]:
    """
    Retrieves the highest quality transcript available for a given ytid,
    based on TRANSCRIPT_QUALITY_HIERARCHY.
    """
```

**Service Integration**: `vidops/services/diarization.py`
```python
# Handle "best" transcript selection
if transcript_kind.lower() == "best":
    transcript = self.transcript_repo.get_best_available(ytid)
    if not transcript:
        raise ValueError(f"No transcripts available for video '{ytid}'.")
    logger.info(f"Selected best available transcript for {ytid}: {transcript.kind}")
    transcript_kind = transcript.kind
```

## Benefits

### Consistency
- All videos use their highest quality transcript
- No manual tracking of which model was used for each video
- Reproducible results across different video collections

### Flexibility
- Handles videos with different transcript availability
- Gracefully falls back to lower quality if best not available
- Works with partial transcript sets

### Maintainability
- Single source of truth for quality ranking
- Easy to update hierarchy as new models are released
- Centralized configuration

## Examples by Scenario

### Scenario 1: Mixed Quality Archive

You have 1000 videos transcribed with different models over time:
- 200 videos: `words_whisper_large-v3`
- 500 videos: `words_whisper_medium`
- 300 videos: `words_whisper_small`

Using `--transcript-kind best`:
- 200 videos will use `large-v3` (best available)
- 500 videos will use `medium` (best available for them)
- 300 videos will use `small` (best available for them)

### Scenario 2: Progressive Improvement

You're re-transcribing your archive with better models:

**Initial state**:
- All 1000 videos: `words_whisper_small`

**After re-transcribing 100 videos**:
- 100 videos: `words_whisper_large-v3` + `words_whisper_small` (both exist)
- 900 videos: `words_whisper_small`

Using `--transcript-kind best`:
- 100 videos automatically use `large-v3`
- 900 videos use `small`
- No need to track which videos were re-transcribed
- Same command works for all videos

### Scenario 3: Fallback to Auto-Captions

Some videos don't have Whisper transcripts yet:

- Video A: `words_whisper_medium`, `vtt`
- Video B: `vtt` only (YouTube auto-captions)

Using `--transcript-kind best`:
- Video A uses `words_whisper_medium` (best available)
- Video B uses `vtt` (only option, still works)

## Customization

To modify the quality hierarchy for your use case, edit `vidops/config.py`:

```python
TRANSCRIPT_QUALITY_HIERARCHY = [
    # Add custom transcript kinds here
    "words_whisper_custom_model",

    # Standard hierarchy
    "words_whisper_large-v3",
    # ...
]
```

The system will automatically use the updated hierarchy for all `best` selections.

## Error Handling

### No Transcripts Available

```bash
$ python vo_cli.py diarize enqueue NO_TRANSCRIPT_VIDEO --transcript-kind best
✗ Failed to enqueue diarization job: No transcripts available for video 'NO_TRANSCRIPT_VIDEO'.
```

### Video Not Found

```bash
$ python vo_cli.py diarize enqueue INVALID_YTID --transcript-kind best
✗ Failed to enqueue diarization job: Video with ytid 'INVALID_YTID' not found.
```

## Future Extensions

Potential future enhancements:

- **Quality Hints**: `--transcript-kind best:words` (only words format) or `--transcript-kind best:large` (only large models)
- **Min Quality**: `--min-quality medium` (fail if only small/tiny available)
- **Preference File**: Project-specific quality preferences
- **Auto-Upgrade**: Detect when better transcripts become available

## Testing

To verify the hierarchy works correctly:

```python
from vidops.dal import TranscriptRepository

repo = TranscriptRepository()

# List all available transcripts for a ytid
transcripts = repo.list_for_ytid("7TdvWiRdhqk")
for t in transcripts:
    print(f"- {t.kind}")

# Get best available
best = repo.get_best_available("7TdvWiRdhqk")
print(f"Best: {best.kind}")
```

## See Also

- [Diarization Documentation](REFACTOR_ARCHITECTURE/SOURCE_OF_TRUTH.md) - Full diarization workflow
- [Transcription Documentation](README.md) - Transcript generation
- [Database Schema](DB_README.md) - Transcripts table structure
