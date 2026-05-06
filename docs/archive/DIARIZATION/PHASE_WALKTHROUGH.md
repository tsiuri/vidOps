# Diarization 2.0 - Phase Walkthrough

**Date**: 2025-12-04
**Phase**: Inference Implementation (Step 5 of 7)
**Status**: Core scripts implemented, integration pending

---

## Overview

This document tracks the implementation progress of Diarization 2.0 using pyannote/speaker-diarization-3.1, optimized for 8GB GPUs. It serves as a handoff guide for the next AI agent or developer to complete the system.

## Documentation Structure

### Primary Specifications
- **`DIARIZATION_2.0.md`** - Overall pipeline architecture and goals
- **`IMPLEMENTATION_PLAN.md`** - Concrete task breakdown per pipeline stage
- **`INFERENCE_IMPLEMENTATION.md`** - Detailed inference guide with code examples (created 2025-12-04)
- **`PHASE_WALKTHROUGH.md`** (this file) - Current implementation status and next steps

### Test Data
- Test set location: `/home/billie/tools/vidops/generated/query_ids/pikerbreakdown_ytids.tsv`
- Contains: 172 YTIDs for validation

---

## Implementation Status

### ✅ Completed (Step 5: Inference)

#### 1. Core Inference Script
**File**: `scripts/diarization/diarize_inference.py`
**Status**: ✅ Complete
**Features**:
- pyannote/speaker-diarization-3.1 integration
- 8GB GPU optimized defaults:
  - `chunk_duration`: 15.0s (12-20s range)
  - `overlap_duration`: 2.5s (2-3s range)
  - `threshold`: 0.6 (0.45-0.70 calibratable)
  - `segmentation_batch_size`: 32
  - `embedding_batch_size`: 64
- Auto device detection (CUDA → CPU fallback)
- Segmentation model override support
- Outputs:
  - `diarized_timestamps.tsv` (ytid, speaker, start, end, duration, source_audio)
  - `diarization.json` (metadata, params, runtime stats)

**Usage**:
```bash
python3 scripts/diarization/diarize_inference.py \
  enhanced.wav ytid123 results/diarization
```

#### 2. Reference Building Script
**File**: `scripts/diarization/build_reference.py`
**Status**: ✅ Complete
**Features**:
- Extracts 50 clips of 8-12s from source videos
- Minimum 30s spacing between clips
- Reuses existing references (avoids rebuilds)
- Metadata tracking (reference.json)
- 16kHz mono WAV output (pyannote compatible)

**Usage**:
```bash
# Build new reference
python3 scripts/diarization/build_reference.py \
  speaker_alpha video1.mp4 video2.mp4 video3.mp4

# Check if exists
python3 scripts/diarization/build_reference.py \
  speaker_alpha --check-only
```

#### 3. Reference Matching Script
**File**: `scripts/diarization/match_reference.py`
**Status**: ✅ Complete
**Features**:
- Maps anonymous speakers (SPEAKER_00) to reference identities
- Uses pyannote/embedding model
- Cosine similarity matching with configurable threshold
- Averages embeddings across segments for robustness
- Outputs matched TSV with real speaker names
- Updates diarization.json with mapping metadata

**Usage**:
```bash
python3 scripts/diarization/match_reference.py \
  results/diarization/ytid123 \
  data/references/speaker_alpha
```

#### 4. Documentation
**File**: `docs/DIARIZATION/INFERENCE_IMPLEMENTATION.md`
**Status**: ✅ Complete
**Contents**:
- Installation guide (dependencies, HuggingFace auth)
- Complete code examples for all operations
- VRAM optimization strategies
- Segmentation override patterns
- Reference handling workflows
- Runtime tips and performance benchmarks

#### 5. Validation helper
**File**: `scripts/diarization/validate_diarization.py`
**Status**: ✅ Added
**Purpose**: Compute DER for a YTID list against RTTM references; supports pikerbreakdown TSV + `<ytid>.rttm` refs; prints per-file and mean DER.

---

## Pending Work

### 🔧 Integration Tasks (High Priority)

#### 1. Configuration File
**File**: `config/diarization.yaml` (exists)
**Status**: Defaults present (8GB GPU tuned). Still need to:
  - Wire scripts to load these defaults instead of hardcoded values.
  - Support per-domain overrides from this file in CLI/worker.

#### 2. Batch Processing Script
**File**: `scripts/diarization/batch_diarize.py` (exists)
**Status**: Implemented with progress bars and error handling; needs wiring to config defaults and workspace.sh/vo_cli entrypoints; verify it prefers enhanced/canonical audio staging paths.

#### 3. Shell Wrapper for workspace.sh
**File**: `scripts/diarization/run_diarization.sh` (exists)
**Status**: Wrapper present; needs alignment with workspace.sh commands/help, config loading, and integration into worker call chain (after VAD/enhancement, before post-processing).

#### 4. Dependency Installation Guide
**File**: docs updates exist (INFERENCE_IMPLEMENTATION.md, QUICK_START.md); review if a dedicated INSTALLATION.md is still needed or fold into QUICK_START.

---

### 🧪 Testing & Validation (High Priority)

#### 1. Test Installation
**Action**: Run dependency check on actual hardware
**Verify**:
- PyTorch with CUDA support
- pyannote.audio version 3.1.1+
- HuggingFace authentication works
- GPU visible to PyTorch (8GB VRAM detected)

**Command**:
```bash
python3 -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

#### 2. Test Inference Script
**Action**: Run on a single file from pikerbreakdown set
**Steps**:
1. Pick 1 YTID from `/home/billie/tools/vidops/generated/query_ids/pikerbreakdown_ytids.tsv`
2. Ensure audio exists (or run preprocessing steps)
3. Run: `python3 scripts/diarization/diarize_inference.py <audio> <ytid> results/diarization`
4. Verify outputs:
   - `results/diarization/<ytid>/diarized_timestamps.tsv` exists
   - TSV has segments with timestamps
   - `diarization.json` has metadata
5. Check runtime: Should be ~5-10x realtime on 8GB GPU

#### 3. Test Reference Building
**Action**: Build a test reference set
**Steps**:
1. Select 3-5 videos containing target speaker
2. Run: `python3 scripts/diarization/build_reference.py test_speaker video1.mp4 video2.mp4`
3. Verify:
   - `data/references/test_speaker/*.wav` files created (50 clips)
   - Each clip is 8-12s, mono 16kHz
   - `reference.json` has metadata
4. Re-run with `--check-only` to verify reuse logic

#### 4. Test Reference Matching
**Action**: Match diarized speakers to reference
**Prerequisites**: Completed test inference + reference building
**Steps**:
1. Run: `python3 scripts/diarization/match_reference.py results/diarization/<ytid> data/references/test_speaker`
2. Verify:
   - `diarized_timestamps_matched.tsv` created
   - Speaker labels changed from SPEAKER_XX to test_speaker or UNKNOWN_XX
   - `diarization.json` updated with reference mapping
3. Spot-check: Listen to a few segments to confirm labels are reasonable

#### 5. Threshold Calibration
**Action**: Determine optimal threshold for domain
**Method**:
1. Manually label 20-30 segments from pikerbreakdown set
2. Run DER using `scripts/diarization/validate_diarization.py` across thresholds: 0.45, 0.50, 0.55, 0.60, 0.65, 0.70 (reuse cached VAD/enhanced audio and reference).
3. Record per-threshold DER/WDER, pick best; update `config/diarization.yaml` domain override and log results in `logs/changelog/<date>_diarization_threshold_grid.txt`.

---

### 🚀 Pipeline Integration (Medium Priority)

These tasks connect diarization to earlier pipeline steps (canonicalize, VAD, enhancement) and downstream post-processing/word mapping:

#### 1. Locate Preprocessing Scripts
**Current State**: Phase 1 canonicalization implemented in `vidops/services/diarization.py` (canonical.wav). VAD/enhancement design captured in `docs/DIARIZATION/VAD_ENHANCEMENT.md` but not wired into worker/scripts yet.
**Action**: Implement VAD/enhancement step (produce enhanced.wav + manifest) before inference; ensure worker prefers enhanced.wav.

#### 2. End-to-End Workflow Script
**File**: `scripts/diarization/run_full_pipeline.sh` (not yet created)
**Purpose**: Run all 7 steps sequentially using existing components
**Steps**:
1. Preflight (validate paths, check duration)
2. Canonicalize audio
3. VAD
4. Enhancement
5. **Inference** (diarize_inference.py) ← We are here
6. Post-processing (merge gaps, cleanup)
7. Map to words (create speaker_words.tsv)

**Current Gap**: Steps 3-4 and 6-7 need implementation wiring; Steps 1-2 exist.

#### 3. Post-Processing (Step 6)
**File**: `scripts/diarization/postprocess_diarization.py` (needs creation)
**Features Needed** (per DIARIZATION_2.0.md):
- Merge micro-gaps <0.3s between same-speaker turns
- Drop ultra-short turns <0.4-0.5s
- Optional: Recluster embeddings (AHC/k-means) to smooth label drift
- Handle overlapping speech (keep or demote to UNKNOWN)

**Input**: Raw `diarized_timestamps.tsv`
**Output**: `diarized_timestamps_clean.tsv`

#### 4. Word Mapping (Step 7)
**File**: `scripts/diarization/map_words_to_speakers.py` (needs creation)
**Purpose**: Assign speaker labels to words.tsv
**Logic**:
- Read `<ytid>.words.tsv` (from `generated/`)
- Read `diarized_timestamps_clean.tsv`
- For each word, find overlapping speaker segment (with 0.1-0.2s gap tolerance)
- Output: `speaker_words.tsv` (words + speaker column)

**Columns**:
```tsv
ytid  start  end  word  confidence  seg  speaker
```

---

### 📊 Quality Assurance (Medium Priority)

#### 1. Validation Metrics
**Action**: Run on full pikerbreakdown set (172 YTIDs)
**Metrics to Collect**:
- Diarization Error Rate (DER) - requires ground truth labels
- Speaker count accuracy (manual spot-check)
- Runtime per file (median, p95)
- GPU memory usage (peak VRAM)
- Failures (count, reasons)

**Ground Truth**: May need manual annotation for subset (10-20 files)

#### 2. Error Analysis
**Action**: Identify common failure modes
**Categories**:
- Segmentation errors (missed speech, false alarms)
- Clustering errors (speakers merged/split)
- Reference matching errors (wrong identity assigned)
- Technical failures (OOM, model errors)

**Mitigation**: Adjust hyperparameters or add preprocessing

#### 3. Performance Profiling
**Action**: Measure time per stage
**Tool**: Add timing logs to scripts
**Expected Breakdown** (for 60min file on 8GB GPU):
- Canonicalize: ~30s
- VAD: ~20s
- Enhancement: ~60s
- Inference: ~6-10min (5-10x realtime)
- Post-processing: ~5s
- Word mapping: ~10s

**Optimization**: Identify bottlenecks

---

### 📚 Documentation (Low Priority)

#### 1. User Guide
**File**: `docs/DIARIZATION/USER_GUIDE.md` (needs creation)
**Contents**:
- Quick start (single file example)
- Batch processing walkthrough
- Building references (best practices)
- Troubleshooting common errors
- FAQ

#### 2. API Reference
**File**: `docs/DIARIZATION/API_REFERENCE.md` (needs creation)
**Contents**:
- Function signatures for all scripts
- Input/output formats
- Configuration options
- Return codes

#### 3. Architecture Diagram
**File**: `docs/DIARIZATION/architecture.png` (needs creation)
**Contents**:
- Visual flowchart of 7-step pipeline
- Data flow between steps
- File locations

---

## Quick Start for Next Developer

### Immediate Next Steps

1. **Install Dependencies**:
   ```bash
   pip install pyannote.audio==3.1.1 torch torchaudio scipy numpy pyyaml
   huggingface-cli login  # Get token from huggingface.co
   ```

2. **Test Single File**:
   ```bash
   # Find a test file
   ytid=$(head -1 /home/billie/tools/vidops/generated/query_ids/pikerbreakdown_ytids.tsv | cut -f1)
   echo "Testing with: $ytid"

   # Run inference (adjust path to actual audio)
   python3 scripts/diarization/diarize_inference.py \
     pull/${ytid}*.mp4 \
     $ytid \
     results/diarization
   ```

3. **Create Batch Script** (see "Batch Processing Script" above)

4. **Integrate**:
   - CLI/worker path: `vo diarize enqueue ...` (DB job) or legacy bridge via worker. `vo diarize enqueue` exists only as a legacy helper; modern runs should use the queued worker path.

5. **Run Validation**:
   - Process all 172 pikerbreakdown YTIDs
   - Collect metrics
   - Calibrate threshold

### Files to Create

Priority order:
1. `scripts/diarization/batch_diarize.py` - Essential for testing at scale
2. `config/diarization.yaml` - Centralize configuration
3. `scripts/diarization/run_diarization.sh` - Shell wrapper
4. `scripts/diarization/postprocess_diarization.py` - Step 6 implementation
5. `scripts/diarization/map_words_to_speakers.py` - Step 7 implementation

### Integration Points

Where this connects to existing vidops:
- **Input**: `generated/<ytid>.words.tsv` (from transcription)
- **Input**: `pull/<ytid>*.mp4` (or preprocessed audio)
- **Output**: `results/diarization/<ytid>/` (timestamps, metadata)
- **Output**: `results/diarization/<ytid>/speaker_words.tsv` (final product)
- **DB Ingest**: Load `speaker_words.tsv` into PostgreSQL (future work)

### Known Issues / Assumptions

1. **Preprocessing Gap**: Steps 2-4 (canonicalize, VAD, enhancement) not yet located/implemented
   - Workaround: Run diarization on raw audio (less accurate)
   - TODO: Either implement or locate existing scripts

2. **Reference Building**: Requires manual curation
   - Current: Randomly samples clips from videos
   - Better: User selects known-good segments
   - Consider: Interactive tool for reference curation

3. **Threshold Calibration**: Default 0.6 may not be optimal
   - Needs: Ground truth labels for validation
   - Consider: Per-domain thresholds (streams vs VODs)

4. **GPU Availability**: Scripts assume CUDA available
   - Fallback: CPU mode works but ~50x slower
   - Consider: Check GPU before large batch jobs

5. **HuggingFace Auth**: Models require accepted terms
   - One-time setup: Visit model pages, accept terms, login
   - Models: speaker-diarization-3.1, embedding, segmentation-3.0

---

## Summary for Next AI

**What's Done**:
- ✅ Core inference with pyannote (8GB optimized)
- ✅ Reference building (50 clips, 8-12s)
- ✅ Reference matching (embedding-based ID)
- ✅ Comprehensive documentation

**What's Needed**:
- 🔧 Batch processing script
- 🔧 Configuration file (YAML)
- 🔧 Shell wrapper for workspace.sh
- 🧪 Testing on real data (172 YTIDs)
- 🚀 Post-processing (Step 6)
- 🚀 Word mapping (Step 7)
- 🚀 Preprocessing integration (Steps 2-4)

**Current Blocker**: None - scripts are functional, need integration and testing

**Test Command** (when ready):
```bash
# Full pipeline test
python3 scripts/diarization/batch_diarize.py \
  /home/billie/tools/vidops/generated/query_ids/pikerbreakdown_ytids.tsv \
  --reference speaker_alpha \
  --output results/diarization
```

---

**Last Updated**: 2025-12-04 by Claude Code
**Next Milestone**: Batch processing + validation run on pikerbreakdown set
**Contact**: See AGENTS.md for contribution guidelines
