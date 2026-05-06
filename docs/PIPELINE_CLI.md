# Pipeline CLI - Complete Video Processing Pipelines

## Overview

The Pipeline CLI (`vo pipeline`) enables users to enqueue complete video processing pipelines with a single command. Instead of manually managing multiple stages (download → transcription → diarization → analysis), users can start an entire pipeline with automatic dependency management.

**Key Feature**: All jobs are created immediately with dependencies encoded in the database (download → transcription → diarization → analysis-distributed by default). GenericWorker automatically respects dependencies without any special application logic.

### Interactive preflight (no DB writes until configured)
- When diarization is included and no `--reference-name` is provided, a curses/text menu prompts you to pick or build a reference (or skip diarization). Cancelling the prompt aborts enqueue entirely.
- When analysis is included and no `--analysis-config-id` is provided, a curses/text menu lets you pick a config or explicitly skip analysis. Cancelling aborts before any jobs are written.
- Configuration prompts run before any DB changes so a failed or cancelled selection leaves the database untouched.

## Usage

### Basic Command Structure

```bash
vo pipeline enqueue <YTID_OR_URL> [OPTIONS]
```

### Examples

#### Full Pipeline from YouTube URL
```bash
vo pipeline enqueue https://youtube.com/watch?v=dQw4w9WgXcQ
```

Output:
```
Enqueuing pipeline pipe_a1b2c3d4 for dQw4w9WgXcQ
  ✓ Download job created: job_111
  ✓ Transcription job created: job_222 (depends on job_111)
  ✓ Diarization job created: job_333 (depends on job_222)
  ✓ Analysis job created: job_444 (depends on job_333)

✓ Pipeline pipe_a1b2c3d4 enqueued with 4 stages

Pipeline flow:
  ├─ download: job_111
  ├─ transcription: job_222
  ├─ diarization: job_333
  └─ analysis-distributed: job_444

Monitor progress:
  vo status jobs
  vo pipeline status pipe_a1b2c3d4
```

#### Skip Stages
```bash
# Skip diarization - useful for quick turnaround
vo pipeline enqueue XYZ --skip-diarization

# Already have video, skip download
vo pipeline enqueue XYZ --skip-download

# Download and transcribe only (skip diarization and analysis)
vo pipeline enqueue XYZ --skip-diarization --skip-analysis

# Start from a later stage (force skipping earlier ones)
vo pipeline enqueue XYZ --force-from diarization
```

#### Custom Configuration
```bash
# Use larger Whisper model
vo pipeline enqueue XYZ --transcription-model large-v3

# Specify diarization device
vo pipeline enqueue XYZ --diarization-device cuda:0

# Custom analysis config
vo pipeline enqueue XYZ --analysis-config-id 5

# Adjust priority
vo pipeline enqueue XYZ --priority 100
```

#### Combined Options
```bash
# Full control: large model, CPU diarization, skip analysis
vo pipeline enqueue XYZ \
  --transcription-model large-v3 \
  --diarization-device cpu \
  --skip-analysis

# Build/use a diarization reference before enqueueing (aborts enqueue on failure/cancel; defaults to prompting for one)
vo pipeline enqueue XYZ --reference-name myref
# Skip analysis via TUI (press “s” when selecting analysis config) or CLI flag
vo pipeline enqueue XYZ --skip-analysis
```

### Monitoring Pipeline Progress

```bash
# Check all jobs
vo status jobs

# Check specific pipeline
vo pipeline status pipe_a1b2c3d4

# Output:
# Pipeline: pipe_a1b2c3d4
# Job ID      Stage                   Status      Depends On  Created    Completed
# job_111     download                completed   -           23:04:12   23:05:45
# job_222     transcription           running     job_111     23:05:46   -
# job_333     diarization             pending     job_222     23:05:47   -
# job_444     analysis-distributed    pending     job_333     23:05:48   -
```

- `vo pipeline status <pipeline_id>` now returns non-zero and prints error snippets when any job in the pipeline has failed, so your shell scripts/terminals surface failures immediately.

## Configuration

### Default Configuration

Pipeline stages read from `config.yaml`:

**Download stage** (`download.*` section):
```yaml
download:
  format: "bestaudio/best"
  audio_only: true
  audio_format: "opus"
```

**Transcription stage** (`transcription.*` section):
```yaml
transcription:
  model: "small"
  language: "en"
```

**Diarization stage** (`diarization.*` section):
```yaml
diarization:
  model: "pyannote"
  device: "auto"
  chunk_seconds: 15.0
  overlap_seconds: 2.5
  similarity_threshold: 0.6
  gap_threshold: 0.15
  match_threshold: 0.75
  match_margin: 0.01
  match_force_best: true
```

**Analysis stage** (`analysis.*` section):
```yaml
analysis:
  default_capabilities:
    - granite3.3:8b
    - qwen2.5:7b-instruct
```
Note: `analysis.default_capabilities` are legacy tags only. VRAM-based scheduling uses
`analysis.default_vram_gb` and `analysis.default_model_profile_id` instead.

### CLI Overrides

CLI options override config defaults:

```bash
# Override transcription model from config.yaml
vo pipeline enqueue XYZ --transcription-model large-v3

# Override diarization device
vo pipeline enqueue XYZ --diarization-device cuda:1
```

**Precedence** (highest to lowest):
1. CLI options (`--transcription-model`, `--diarization-device`, etc.)
2. config.yaml defaults (`transcription.model`, `diarization.device`, etc.)
3. Hardcoded fallbacks in service code

## How It Works

### Architecture

```
User runs: vo pipeline enqueue XYZ

            ↓

Pipeline CLI creates ALL jobs immediately:
  - job_111: download       [PENDING, depends_on: NULL]
  - job_222: transcription  [PENDING, depends_on: job_111]
  - job_333: diarization    [PENDING, depends_on: job_222]
  - job_444: analysis       [PENDING, depends_on: job_333]

            ↓

GenericWorker polls claim_next() every cycle

            ↓

Database filters claimable jobs:
  - Checks: WHERE depends_on IS NULL OR dep.status = 'COMPLETED'
  - Returns: Only job_111 (no dependency)

            ↓

Worker claims job_111, processes, marks COMPLETED

            ↓

Next poll: job_222 becomes claimable (depends_on satisfied)

            ↓

Repeat until all jobs complete
```

### Dependency Tracking

Each job stores dependency information in `job.config`:

```json
{
  "pipeline_id": "pipe_a1b2c3d4",
  "pipeline_stage": "transcription",
  "depends_on": "job_111",
  "model": "medium",
  "language": "en"
}
```

Fields:
- `pipeline_id`: Unique identifier grouping all stages (allows querying all jobs in pipeline)
- `pipeline_stage`: Current stage name (download, transcription, diarization, analysis-distributed)
- `depends_on`: Job ID of prerequisite (if any)
- Stage-specific config: model, language, device, etc.

### Database-Level Enforcement

Dependency checking happens in `JobRepository.claim_next()` using SQL:

```sql
WHERE (j.config->>'depends_on' IS NULL
       OR dep.status = 'COMPLETED')
FOR UPDATE OF j SKIP LOCKED
```

**Why this works**:
1. `LEFT JOIN jobs dep ON j.config->>'depends_on' = dep.job_id` - Join to dependency (if exists)
2. `j.config->>'depends_on' IS NULL` - No dependency (always claimable)
3. `dep.status = 'COMPLETED'` - Has dependency AND it's done (now claimable)
4. `FOR UPDATE OF j SKIP LOCKED` - Lock main table only (PostgreSQL requirement), skip already-locked rows

**Result**: Workers only ever receive jobs whose dependencies are satisfied. No application-level logic needed.

## Skip Flags

Skip flags allow partial pipelines without creating unnecessary jobs:

| Flag | Effect | When to Use |
|------|--------|------------|
| `--skip-download` | Start from transcription | Video already present in storage |
| `--skip-transcription` | Skip to diarization | Transcripts already created |
| `--skip-diarization` | Skip to analysis | Only need analysis output |
| `--skip-analysis` | Stop after diarization | Only need speaker info |

**Example**: Test transcription quickly
```bash
# Create fake audio, skip analysis
vo pipeline enqueue XYZ --skip-download --skip-analysis
```

## Job Status Flow

```
PENDING → CLAIMED → RUNNING → COMPLETED
  ↑
  └─ Only PENDING jobs with satisfied dependencies are claimable
```

### Manual Retry

If a job fails, you can manually retry:

```bash
# Get failed job ID from status
vo status jobs

# Release it back to pending
vo release-job job_222

# It will be claimed again on next worker cycle
```

## Troubleshooting

### Jobs Stuck in PENDING

**Symptom**: Job stays PENDING but dependency shows COMPLETED

**Check**:
```bash
# Verify dependency is truly completed
vo status jobs | grep job_111

# Check pipeline config
psql -U billie -d transcripts -c \
  "SELECT job_id, status, config->>'depends_on' as dep FROM jobs WHERE job_id IN ('job_111', 'job_222')"
```

**Solutions**:
1. Worker may not be running - start one: `vo worker start general`
2. Dependency check issue - check worker logs for SQL errors
3. Job may be stale - Overlord releases stale jobs after ~12 hours

### No Jobs Being Claimed

**Check**:
1. Is GenericWorker running? `vo status workers`
2. Are there PENDING jobs? `vo status jobs`
3. Check worker logs for errors

## Performance Considerations

### Disk Space
- Download stage uses ~2-3x source video size (temporary cache)
- Configure workspace limits in `config.yaml`:
  ```yaml
  workspace:
    max_workspace_size_gb: 100.0
    monitor_tmp_separately: true
    max_tmp_size_gb: 50.0
  ```

### Processing Time (per stage, ~hour-long video)
- Download: 2-10 minutes (depends on network, video quality)
- Transcription: 5-30 minutes (depends on model and hardware)
- Diarization: 3-15 minutes (depends on audio complexity)
- Analysis: 2-5 minutes (depends on model and config)

### Multiple Workers
Pipelines work seamlessly with multiple workers:
```bash
# Terminal 1
vo worker start general

# Terminal 2
vo worker start general

# Both workers will claim available jobs
# Dependency enforcement ensures no conflicts
```

## Advanced Usage

### Programmatic Pipeline Creation

Instead of CLI, you can enqueue pipelines directly:

```python
from cli.pipeline import enqueue_pipeline
from click.testing import CliRunner

runner = CliRunner()
result = runner.invoke(
    enqueue_pipeline,
    ['XYZ', '--skip-diarization', '--priority', '100']
)
```

### Monitoring via Database

Query pipeline progress directly:

```sql
-- Find all pipelines
SELECT DISTINCT config->>'pipeline_id' as pipeline_id
FROM jobs
WHERE config->>'pipeline_id' IS NOT NULL;

-- Get pipeline details
SELECT job_id, job_type, status,
       config->>'pipeline_stage' as stage,
       config->>'depends_on' as depends_on
FROM jobs
WHERE config->>'pipeline_id' = 'pipe_a1b2c3d4'
ORDER BY created_at ASC;

-- Count jobs by status in pipeline
SELECT config->>'pipeline_stage' as stage, status, COUNT(*)
FROM jobs
WHERE config->>'pipeline_id' = 'pipe_a1b2c3d4'
GROUP BY stage, status;
```

### Bulk Pipeline Enqueue

Create multiple pipelines from a list:

```bash
# Create file: video_list.txt
# Each line: YTID or URL

while IFS= read -r video; do
  vo pipeline enqueue "$video" --priority 50
done < video_list.txt
```

## Related Commands

| Command | Purpose |
|---------|---------|
| `vo pipeline enqueue` | Create new pipeline |
| `vo pipeline status` | Monitor pipeline progress |
| `vo status jobs` | View all jobs across pipelines |
| `vo status workers` | Check worker availability |
| `vo worker start general` | Start GenericWorker to process jobs |
| `vo download enqueue` | Enqueue single download (no dependencies) |
| `vo transcribe enqueue` | Enqueue single transcription |
| `vo diarize enqueue` | Enqueue single diarization |

## Reference

### Job Config Schema

```json
{
  "pipeline_id": "pipe_xxxxxxxx",
  "pipeline_stage": "transcription|diarization|analysis-distributed",
  "depends_on": "job_xyz|null",

  // Download config
  "url": "https://youtube.com/watch?v=...",
  "ytdlp": { /* yt-dlp options */ },

  // Transcription config
  "model": "small|base|medium|large-v3",
  "language": "en",

  // Diarization config
  "diarization_model": "pyannote",
  "device": "auto|cuda|cpu",
  "transcript_kind": "words_whisper_medium",
  "chunk_seconds": 15.0,
  "overlap_seconds": 2.5,
  "similarity_threshold": 0.6,

  // Analysis config
  "config_id": 1,
  "model_url": "http://localhost:11434"
}
```

### CLI Options Reference

```bash
vo pipeline enqueue <YTID_OR_URL> \
  [--skip-download]              # Skip download stage
  [--skip-transcription]         # Skip transcription
  [--skip-diarization]           # Skip diarization
  [--skip-analysis]              # Skip analysis
  [--transcription-model MODEL]  # Whisper model (default from config)
  [--transcription-language LANG] # Language code (default: en)
  [--diarization-device DEVICE]  # cuda/cpu/auto (default from config)
  [--analysis-config-id ID]      # Analysis config (default: 1)
  [--priority NUM]               # Job priority (default: 50)
  [--help]                       # Show this help
```

## See Also

- `docs/ARCHITECTURE.md` - System architecture overview
- `config.yaml` - Default configuration for all stages
- `cli/pipeline.py` - CLI implementation
- `dal/jobs.py` - Dependency enforcement in claim_next()
