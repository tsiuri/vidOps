# VRAM-Based Analysis Scheduling

## Overview

The VRAM-based scheduling system replaces the legacy capabilities-based task assignment with a resource-aware scheduler that matches analysis tasks to workers based on GPU memory requirements.

**Key Components:**
- **Model Profiles** (`analysis_model_profiles`): Registry of models with VRAM requirements and memory-affecting parameters
- **Task VRAM Requirements** (`analysis_tasks.required_vram_gb`): Each task stores its memory footprint
- **Worker VRAM Capacity** (`workers.vram_gb`): Workers advertise available GPU memory
- **Atomic Claiming**: Database-level task matching based on VRAM compatibility

## Model Profiles Table

### Schema

```sql
CREATE TABLE analysis_model_profiles (
    id BIGSERIAL PRIMARY KEY,
    model_name TEXT NOT NULL,
    options JSONB NOT NULL DEFAULT '{}',
    required_vram_gb NUMERIC(5,2) NOT NULL,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (model_name, options)
);
```

### Fields

- **id**: Numeric primary key referenced by tasks and worker configs
- **model_name**: Ollama model identifier (e.g., `granite3.3:8b`, `llama3:8b`)
- **options**: JSONB containing **only VRAM-affecting parameters**:
  - `num_ctx`: Context window size (affects KV cache size)
  - `num_batch`: Batch size for processing
  - `num_keep`: Tokens to keep from initial prompt
- **required_vram_gb**: Measured GPU memory requirement (from `ollama ps`)
- **notes**: Optional description or metadata

**Important:** Inference parameters (temperature, top_p, top_k, stop tokens, repeat_penalty, etc.) do NOT belong in the profile options. They belong in the analysis configuration.

### Seeding Profiles

#### Automated Seeding Script

```bash
# Preview what would be seeded (dry run)
python scripts/db/seed_model_profiles.py --dry-run

# Seed all available Ollama models
python scripts/db/seed_model_profiles.py

# Seed specific models only
python scripts/db/seed_model_profiles.py --models "granite3.3:8b,llama3:8b"

# Use different database/Ollama endpoint
python scripts/db/seed_model_profiles.py \
  --db-host 192.168.0.187 \
  --db-name transcripts \
  --ollama-url http://localhost:11434
```

**How it works:**
1. Lists all available Ollama models
2. For each model:
   - Parses Modelfile to extract VRAM-affecting parameters
   - Loads model via `/api/generate` with a test prompt
   - Reads VRAM usage from `ollama ps` output
   - Inserts/updates profile with measured VRAM + options
   - Unloads model (sends `keep_alive: 0`) to free VRAM
3. Skips embedding models (they don't support `/api/generate`)

#### Manual Profile Creation

```sql
INSERT INTO analysis_model_profiles (model_name, options, required_vram_gb, notes)
VALUES (
    'custom-model:latest',
    '{"num_ctx": 8192}',
    10.5,
    'Custom fine-tuned model'
);
```

## Task Creation Flow

### Overview

When creating analysis jobs, the system:
1. Resolves the model profile (by ID or by name+options lookup)
2. Determines VRAM requirements per pass type
3. Creates tasks with `required_vram_gb` and `model_profile_id`

### Code Path

**Entry Point:** `scripts/analysis/analyze_to_db.py::create_analysis_job()`

```python
def create_analysis_job(
    ytid: str,
    config_id: str,
    config: AnalysisConfig,
    chunks: List[Dict[str, Any]],
    db: AnalysisDatabase,
    model_name: Optional[str] = None,
    model_profile_id: Optional[int] = None,
) -> str:
```

### Profile Resolution

**Function:** `_resolve_model_profile()`

Priority order:
1. Explicit `model_profile_id` parameter
2. `config.model_profile_id` or `config.backend_params.model_profile_id`
3. Lookup by `(model_name, options)` pair

```python
# Lookup by name + options
profile = db.get_analysis_model_profile_by_name_options(
    model_name="granite3.3:8b",
    options={}  # Empty = using Ollama defaults
)
```

### VRAM Calculation Per Pass

**Function:** `_get_pass_required_vram_gb()`

**LLM passes** (require VRAM):
- `chunk_analysis`
- `sentiment_pass`
- `aggregate_results`
- `hot_targets` (may use different models per target)
- `drills` (may use different models per drill)

**Non-LLM passes** (0 VRAM):
- `db_store`
- `local_json`
- `markdown_report`

**Special handling:**
- `hot_targets`: Checks each target's `model_profile_id` and uses the maximum VRAM
- `drills`: Checks each drill's `model_profile_id` and uses the maximum VRAM

### Task Table Schema

```sql
ALTER TABLE analysis_tasks
    ADD COLUMN required_vram_gb NUMERIC(5,2) DEFAULT 0,
    ADD COLUMN model_profile_id BIGINT REFERENCES analysis_model_profiles(id);
```

## Worker Configuration

### Worker Registration

Workers store VRAM capacity in the `workers` table:

```sql
ALTER TABLE workers
    ADD COLUMN vram_gb NUMERIC(5,2) DEFAULT 0;
```

### Configuration Methods

#### 1. Config File (config.yaml)

```yaml
analysis:
  default_vram_gb: 8
  default_model_profile_id: 3  # granite3.3:8b
```

#### 2. Command-Line Flags

```bash
# Analysis worker
vo worker start analysis --vram-gb 12 --model-profile-id 3

# Generic worker (handles all job types)
vo worker start general --vram-gb 12 --model-profile-id 3
```

#### 3. Per-GPU Configuration

```yaml
gpus:
  0:
    name: "RTX 3060 12GB"
    vram_gb: 12
    model_profile_id: 3
    # ollama_url: http://localhost:11434
  1:
    name: "RTX 3090 24GB"
    vram_gb: 24
    model_profile_id: 22  # gpt-oss:latest
    # ollama_url: http://localhost:11435
```

Then start workers with:
```bash
vo worker start general --gpu 0  # Uses GPU 0 config
vo worker start general --gpu 1  # Uses GPU 1 config
```

## Task Claiming

### Claim Query

**Location:** `scripts/analysis/analysis_task_repository.py::claim_next()`

```sql
SELECT *
FROM analysis_tasks
WHERE status IN ('pending', 'failed')
  AND (claimed_by IS NULL OR lease_expires_at < NOW())
  AND (required_vram_gb IS NULL OR required_vram_gb <= :worker_vram_gb)
  AND (model_profile_id IS NULL OR model_profile_id = :worker_profile_id)
ORDER BY created_at ASC
FOR UPDATE SKIP LOCKED
LIMIT 1
```

**Conditions:**
1. Task is available (pending/failed and not leased)
2. Worker has sufficient VRAM (`required_vram_gb <= worker_vram_gb`)
3. Model profile matches (NULL = any worker, specific ID = exact match)

**Atomicity:** `FOR UPDATE SKIP LOCKED` ensures only one worker claims each task.

### Worker Flow

1. Worker calls `AnalysisTaskRepository.claim_next()` with:
   - `worker_id`: Unique worker identifier
   - `worker_vram_gb`: Available GPU memory
   - `worker_model_profile_id`: Profile ID (optional)
   - `lease_duration`: How long to hold the claim

2. If a compatible task is found:
   - Task status → `claimed`
   - `claimed_by` → worker_id
   - `lease_expires_at` → now + lease_duration
   - Task returned to worker

3. Worker processes task:
   - Loads profile options from `analysis_model_profiles`
   - Passes VRAM-affecting options to Ollama
   - Inference parameters come from analysis config

4. Worker updates task status:
   - Success: status → `completed`, result stored
   - Failure: status → `failed`, error message stored

## Runtime Behavior

### Profile Options Usage

**Worker Initialization** (`workers/analysis_distributed.py`):

```python
# Load profile options at worker startup
if self.model_profile_id:
    profile = self.db.get_analysis_model_profile(self.model_profile_id)
    if profile:
        profile_options = profile.get("options") or {}

# Pass to Ollama analyzer
self.analyzer = OllamaAnalyzer(
    model=model_name,
    base_url=model_url,
    options=profile_options,  # Only num_ctx, num_batch, num_keep
    ...
)
```

**Inference Call:**
- Worker passes profile options (VRAM-affecting) + config options (inference) to Ollama
- Ollama allocates VRAM based on model size + context window
- Inference parameters control generation behavior

### Model Unloading

Workers send `keep_alive: 0` to Ollama after completing tasks to free VRAM:

```python
requests.post(
    f"{ollama_url}/api/generate",
    json={"model": model_name, "prompt": "", "keep_alive": 0}
)
```

## Monitoring

### Check Seeded Profiles

```bash
psql -h 192.168.0.187 -U billie -d transcripts -c \
  "SELECT id, model_name, required_vram_gb, options
   FROM analysis_model_profiles
   ORDER BY required_vram_gb;"
```

### Check Task VRAM Requirements

```bash
psql -h 192.168.0.187 -U billie -d transcripts -c \
  "SELECT task_id, pass_id, required_vram_gb, model_profile_id, status
   FROM analysis_tasks
   WHERE job_id = 'YOUR_JOB_ID';"
```

### Check Worker VRAM Capacity

```bash
psql -h 192.168.0.187 -U billie -d transcripts -c \
  "SELECT worker_id, vram_gb, model_profile_id, status, last_heartbeat
   FROM workers
   WHERE worker_type = 'analysis-distributed';"
```

### Check Active Models in Ollama

```bash
ollama ps
```

## Troubleshooting

### Tasks Not Being Claimed

**Check 1:** Worker has sufficient VRAM
```sql
SELECT required_vram_gb FROM analysis_tasks WHERE task_id = X;
SELECT vram_gb FROM workers WHERE worker_id = 'WORKER';
```

**Check 2:** Profile ID matches
```sql
SELECT model_profile_id FROM analysis_tasks WHERE task_id = X;
SELECT model_profile_id FROM workers WHERE worker_id = 'WORKER';
```

**Check 3:** Task not already claimed
```sql
SELECT status, claimed_by, lease_expires_at
FROM analysis_tasks
WHERE task_id = X;
```

### Model Not Found in Registry

```bash
# List available models
python scripts/db/seed_model_profiles.py --dry-run

# Seed missing model
python scripts/db/seed_model_profiles.py --models "your-model:tag"
```

### VRAM Measurement Inaccurate

Re-seed with clean VRAM:
```bash
# Unload all models
for model in $(ollama list | tail -n +2 | awk '{print $1}'); do
    curl -X POST http://localhost:11434/api/generate \
         -d "{\"model\": \"$model\", \"prompt\": \"\", \"keep_alive\": 0}"
done

# Re-seed
python scripts/db/seed_model_profiles.py
```

## Migration from Legacy Capabilities

### Database Changes

The legacy capabilities system was removed in Migration 007 (2025-12-31).

**Migration 006** added VRAM columns:
```sql
ALTER TABLE analysis_tasks
    ADD COLUMN required_vram_gb NUMERIC(5,2) DEFAULT 0,
    ADD COLUMN model_profile_id BIGINT REFERENCES analysis_model_profiles(id);

ALTER TABLE workers
    ADD COLUMN vram_gb NUMERIC(5,2) DEFAULT 0;
```

**Migration 007** removed the legacy capabilities system:
```sql
-- Drop capabilities columns and indexes
DROP INDEX IF EXISTS tasks_available_for_claiming;
ALTER TABLE analysis_tasks DROP COLUMN IF EXISTS required_capabilities;
ALTER TABLE workers DROP COLUMN IF EXISTS capabilities;
```

### Complete Migration Steps

1. Apply migrations 006 and 007
2. Seed model profiles using `scripts/db/seed_model_profiles.py`
3. Update `config.yaml` with `default_vram_gb` and `default_model_profile_id`
4. Restart workers with `--vram-gb` flags
5. All analysis jobs now use VRAM-based scheduling exclusively

## Best Practices

1. **Seed profiles before creating jobs** - Jobs fail if profile lookup fails
2. **Set realistic VRAM values** - Leave headroom for KV cache growth
3. **Use profile IDs in configs** - More efficient than name+options lookup
4. **Monitor Ollama memory** - Use `ollama ps` and `nvidia-smi` to verify
5. **Update profiles after model changes** - Re-seed if you change Modelfiles
6. **Don't mix inference params in profiles** - Keep temperature, etc. in analysis configs
7. **Test with small models first** - Validate the system with low VRAM requirements

## Future Enhancements

Potential improvements to the system:

- **Multi-GPU support**: Split tasks across multiple GPUs per worker
- **Dynamic VRAM adjustment**: Account for actual runtime usage vs. estimates
- **Profile versioning**: Track changes to model configurations over time
- **Automatic profile refresh**: Re-measure VRAM periodically
- **VRAM pressure metrics**: Monitor system-wide GPU utilization
- **Smart model caching**: Keep frequently-used models loaded
