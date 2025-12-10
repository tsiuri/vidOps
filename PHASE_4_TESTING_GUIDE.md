# Phase 4: Testing & Validation Guide

**Status:** READY FOR EXECUTION
**Available Tests:** 4 comprehensive test suites
**Expected Duration:** 20-30 minutes

## Overview

Phase 4 consists of running comprehensive integration and performance tests to validate that the distributed analysis worker system is production-ready. All tests use real task queues and actual LLM inference.

## Test Suite Summary

### Test 1: Task Repository Core Operations
**File:** `/home/billie/tools/db-and-analysis/test_distributed_analysis.py`
**Duration:** ~5 minutes
**What it tests:**
- Database connection and cursor operations
- Task creation with `create_analysis_job()`
- Task claiming with capability matching
- Job progress tracking
- Job completion detection

**Run command:**
```bash
cd /home/billie/tools/db-and-analysis
python3 test_distributed_analysis.py --ytid test_phase4 --num-chunks 3
```

**Expected output:**
- Initial pending tasks displayed
- Multiple task claims by alternating workers
- Final progress summary (all tasks claimed/completed)
- ✓ PASS message

---

### Test 2: Worker Integration (Full Loop)
**File:** `/home/billie/tools/db-and-analysis/test_phase4_worker.py`
**Duration:** ~10 minutes (includes LLM inference)
**What it tests:**
- Worker initialization with config
- Task claiming and execution
- Pass execution (chunk_analysis with actual LLM)
- Result aggregation into analysis_results table
- Job completion detection

**Run command:**
```bash
cd /home/billie/tools/db-and-analysis
python3 test_phase4_worker.py
```

**Expected output:**
- Job creation confirmed
- Initial task count
- 5 tasks processed by worker
- Progress updated
- Results aggregated to analysis_results
- ✓ Phase 4 worker integration test completed successfully!

---

### Test 3: Analysis System Pipeline
**File:** `/home/billie/tools/db-and-analysis/test_analysis_system.py`
**Duration:** ~15 minutes (includes full pipeline)
**What it tests:**
- Full analysis config loading
- Multiple pass types (chunk_analysis, sentiment, categories, etc.)
- Task creation with different pass combinations
- Worker execution of different pass types
- Result format validation

**Run command:**
```bash
cd /home/billie/tools/db-and-analysis
python3 test_analysis_system.py --ytid test_phase4_pipeline --num-chunks 2
```

**Expected output:**
- Config loaded and validated
- Passes identified and enabled
- Multiple task types created
- Each pass type executed and validated
- ✓ SUCCESS message

---

### Test 4: Pipeline Behavior Validation
**File:** `/home/billie/tools/db-and-analysis/test_pipeline_behavior.py`
**Duration:** ~10 minutes
**What it tests:**
- Chunk analysis behavior
- Sentiment detection
- Category classification
- Subchunk processing
- Result structure validation

**Run command:**
```bash
cd /home/billie/tools/db-and-analysis
python3 test_pipeline_behavior.py
```

**Expected output:**
- Pipeline initialized
- Multiple chunks analyzed
- Result structure validated
- Category extraction tested
- Sentiment analysis tested
- ✓ All pipeline behaviors validated

---

## Quick Run: All Tests

Run all tests in sequence:

```bash
cd /home/billie/tools/db-and-analysis

echo "========================================"
echo "Test 1: Repository Operations"
echo "========================================"
python3 test_distributed_analysis.py --ytid test_phase4_all --num-chunks 3

echo ""
echo "========================================"
echo "Test 2: Worker Integration"
echo "========================================"
python3 test_phase4_worker.py

echo ""
echo "========================================"
echo "Test 3: Analysis System"
echo "========================================"
python3 test_analysis_system.py --ytid test_phase4_system --num-chunks 2

echo ""
echo "========================================"
echo "Test 4: Pipeline Behavior"
echo "========================================"
python3 test_pipeline_behavior.py

echo ""
echo "========================================"
echo "ALL TESTS COMPLETE"
echo "========================================"
```

## Individual Test Details

### Test 1: test_distributed_analysis.py

**Purpose:** Validate core repository operations without full worker

**What it does:**
1. Creates synthetic chunks and analysis job
2. Simulates multiple workers claiming tasks
3. Marks tasks as completed with test results
4. Verifies job progress and completion

**Key validations:**
- ✓ Task creation: All tasks created correctly
- ✓ Task claiming: Workers claim appropriate tasks based on capabilities
- ✓ Progress tracking: Counts stay accurate
- ✓ Job completion: is_job_complete() returns correct value

**Expected result:**
```
Total tasks claimed/completed in test: XX
Final progress: {'total': XX, 'completed': XX, 'failed': 0, 'pending': 0}
is_job_complete(...) = True

TEST RESULT: PASS – all tasks completed and job is marked complete.
```

---

### Test 2: test_phase4_worker.py

**Purpose:** Full worker execution with real LLM analysis

**What it does:**
1. Creates analysis job with 2 chunks
2. Initializes AnalysisWorker instance
3. Claims and processes up to 5 tasks
4. Executes chunk_analysis pass (calls OllamaAnalyzer)
5. Aggregates results into analysis_results
6. Verifies results persisted to database

**Key validations:**
- ✓ Worker initialization: All params configured correctly
- ✓ Task claiming: Worker claims pending tasks
- ✓ Pass execution: chunk_analysis runs without errors
- ✓ Aggregation: Results stored in analysis_results table
- ✓ Database persistence: Results readable from DB

**Expected result:**
```
[Step 1] Creating analysis job with tasks...
✓ Created job_id: ...

[Step 2] Checking initial task progress...
Initial progress: {'total': XX, 'pending': XX, ...}

[Step 3] Running test worker to process 5 tasks...
  Claimed task 123 for pass=chunk_analysis
    → Completed
  [repeats for each task]
Processed 5 tasks

[Step 4] Checking progress after worker execution...
Updated progress: {'total': XX, 'completed': 5, ...}

[Step 5] Testing aggregation...
Job is complete! Running aggregation...
✓ Aggregation completed

[Step 6] Checking analysis_results table...
✓ Results found in analysis_results:
  job_id: ...
  status: completed
  total_tasks: XX
  completed_tasks: 5

TEST RESULT: Phase 4 worker integration test completed successfully!
```

---

### Test 3: test_analysis_system.py

**Purpose:** Comprehensive analysis pipeline validation

**What it does:**
1. Loads complete analysis configuration
2. Creates tasks for all enabled passes
3. Processes each task with appropriate logic
4. Validates result structure and content
5. Checks aggregation across multiple pass types

**Key validations:**
- ✓ Config validation: All passes properly configured
- ✓ Task creation: Correct number per pass type
- ✓ Pass execution: Each pass type produces valid results
- ✓ Result format: JSON structure matches expectations
- ✓ Aggregation: All pass results grouped correctly

**Expected result:**
```
Enabled passes: ['chunk_analysis', 'sentiment_pass', 'categories_pass', ...]
Creating XX tasks for 2 chunks...
✓ Tasks created
Processing tasks by pass type...
  chunk_analysis: X tasks
  sentiment_pass: X tasks
  categories_pass: X tasks
  [etc for each pass]

Validating results...
✓ All results validated

SUCCESS: Analysis system fully validated
```

---

### Test 4: test_pipeline_behavior.py

**Purpose:** Unit-level behavior validation of individual passes

**What it does:**
1. Initializes analysis pipeline directly
2. Tests chunk_analysis behavior
3. Tests sentiment detection
4. Tests category classification
5. Tests subchunk processing
6. Validates output structures

**Key validations:**
- ✓ Chunk analysis produces valid output
- ✓ Sentiment values are reasonable
- ✓ Categories are properly extracted
- ✓ Output JSON is well-formed
- ✓ Metadata preserved through pipeline

**Expected result:**
```
Testing chunk analysis...
✓ Chunk analysis works

Testing sentiment...
✓ Sentiment detection works

Testing categories...
✓ Category extraction works

Testing subchunks...
✓ Subchunk processing works

All pipeline behaviors validated successfully!
```

---

## Performance Expectations

These are baseline performance metrics:

| Test | Tasks | Duration | Throughput |
|------|-------|----------|-----------|
| Test 1 | 30 | 5 min | 6 tasks/min (no inference) |
| Test 2 | 5 | 10 min | 0.5 tasks/min (with inference) |
| Test 3 | 10 | 15 min | 0.67 tasks/min (mixed passes) |
| Test 4 | 5 | 10 min | 0.5 tasks/min (unit tests) |

**Total expected time:** 25-30 minutes for full suite

## Troubleshooting

### Issue: "Database connection refused"
**Solution:** Verify PostgreSQL is running and accessible
```bash
psql -h 192.168.0.187 -U transcripts_user -d transcripts -c "SELECT 1;"
```

### Issue: "Ollama connection refused"
**Solution:** Ensure Ollama is running on the worker machine
```bash
curl http://localhost:11434/api/tags
```

### Issue: "Model not found"
**Solution:** Pull the required model first
```bash
ollama pull qwen2.5:7b-instruct
```

### Issue: "Task creation returns 0 tasks"
**Solution:** Verify analysis config has passes enabled
```bash
psql -U transcripts_user transcripts << SQL
SELECT id, enabled FROM analysis_configs LIMIT 5;
SQL
```

### Issue: "Test hangs during inference"
**Solution:** Inference may take several minutes. Use Ctrl+C to kill and check logs
```bash
journalctl -u ollama -f  # Monitor Ollama on worker machine
```

## Success Criteria

All tests should pass with:
- ✅ No database connection errors
- ✅ No assertion failures
- ✅ Proper task creation and claiming
- ✅ Worker execution completes without crashes
- ✅ Results aggregated to analysis_results table
- ✅ All result JSON structures valid
- ✅ Total execution time < 40 minutes

## Post-Test Cleanup

After running tests, you can clean up test data:

```bash
# List test jobs
psql -U transcripts_user transcripts << SQL
SELECT job_id, ytid, config_id, created_at FROM analysis_results
WHERE config_id LIKE 'test_%'
ORDER BY created_at DESC;
SQL

# Delete test data (optional)
psql -U transcripts_user transcripts << SQL
DELETE FROM analysis_tasks WHERE config_id LIKE 'test_%';
DELETE FROM analysis_results WHERE config_id LIKE 'test_%';
DELETE FROM analysis_configs WHERE id LIKE 'test_%';
SQL
```

## Next Steps After Testing

If all tests pass:
1. Run single long-duration test with real-world job
2. Monitor performance and resource usage
3. Deploy to production via systemd service
4. Monitor worker fleet in production

If tests fail:
1. Check logs for error details
2. Verify configuration and dependencies
3. Fix any issues found
4. Re-run failing tests

## References

- Worker Code: `/home/billie/tools/vidops/vidops/workers/analysis_distributed.py`
- Test Files: `/home/billie/tools/db-and-analysis/test_*.py`
- Schema: `/home/billie/tools/db-and-analysis/schema.sql`
- Config: `/home/billie/tools/vidops/vidops/config.py`

---

**Phase 4 Ready:** All tests are prepared and documented. Execute the test suite to validate the complete system.
