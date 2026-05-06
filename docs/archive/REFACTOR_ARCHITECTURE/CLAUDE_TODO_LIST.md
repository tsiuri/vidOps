# Claude Code TODO List - VidOps Refactor Fixes

**Created:** 2025-11-29
**Owner:** Claude Code
**Status:** Ready to Execute (Awaiting User Approval)

---

## Priority Order & Rationale

These tasks are ordered to:
1. Unblock the failing test first (schema fix)
2. Fix deprecation warnings (quick wins)
3. Verify all fixes work together
4. These tasks are **independent** of Gemini's work (see GEMINI_TASKS.md)

---

## Task 1: Fix Schema Mismatch (CRITICAL)

**Priority:** P0 (BLOCKING)
**Estimated Time:** 10 minutes
**Files Affected:**
- Database schema (via migration SQL)
- `vidops/dal/videos.py` (validation)

**Problem:**
`VideoRepository.upsert()` references `updated_at` column that doesn't exist in production database.

**Solution:**
Add missing timestamp columns to `videos` table.

**Steps:**
1. Check current schema: `psql -h 192.168.0.187 -d transcripts -U billie -c "\d videos"`
2. Create migration file: `scripts/db/migrations/001_add_video_timestamps.sql`
3. Apply migration:
   ```sql
   ALTER TABLE videos
     ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW(),
     ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();

   -- Add trigger to auto-update updated_at
   CREATE OR REPLACE FUNCTION update_updated_at_column()
   RETURNS TRIGGER AS $$
   BEGIN
      NEW.updated_at = NOW();
      RETURN NEW;
   END;
   $$ language 'plpgsql';

   DROP TRIGGER IF EXISTS update_videos_updated_at ON videos;
   CREATE TRIGGER update_videos_updated_at
     BEFORE UPDATE ON videos
     FOR EACH ROW
     EXECUTE FUNCTION update_updated_at_column();
   ```
4. Verify: `psql -h 192.168.0.187 -d transcripts -U billie -c "\d videos"`
5. Run test: `pytest tests/dal/test_videos_repo.py::test_upsert_and_get_video -v`

**Expected Outcome:**
Test `test_upsert_and_get_video` changes from FAILED → PASSED

---

## Task 2: Replace Deprecated datetime.utcnow() (HIGH PRIORITY)

**Priority:** P1 (18 warnings to eliminate)
**Estimated Time:** 30 minutes
**Files Affected:**
- `vidops/models/video.py`
- `vidops/models/job.py`
- `vidops/models/worker.py`
- `vidops/models/transcript.py`
- `tests/models/test_models.py`

**Problem:**
`datetime.utcnow()` is deprecated in Python 3.12+ and will be removed in 3.15.

**Solution:**
Replace with timezone-aware `datetime.now(UTC)`.

**Pattern to Find:**
```python
from datetime import datetime
created_at: datetime = field(default_factory=datetime.utcnow)
```

**Replace With:**
```python
from datetime import datetime, UTC
created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
```

**Files to Edit:**

1. **vidops/models/video.py**
   - Line ~20: `created_at` field factory
   - Line ~45 (if exists): `from_row()` fallback

2. **vidops/models/job.py**
   - Line ~30: `created_at` field factory
   - Line ~31: `updated_at` field factory
   - Line ~74-75: `from_row()` fallbacks

3. **vidops/models/worker.py**
   - Line ~25: `registered_at` field factory
   - Line ~26: `last_heartbeat` field factory
   - Line ~60-61: `from_row()` fallbacks

4. **vidops/models/transcript.py**
   - Line ~18: `created_at` field factory
   - Line ~86: `from_row()` fallback

5. **tests/models/test_models.py**
   - All test dictionaries using `datetime.utcnow()`

**Steps:**
1. Add `UTC` to imports in each file: `from datetime import datetime, UTC`
2. Replace all `default_factory=datetime.utcnow` with `default_factory=lambda: datetime.now(UTC)`
3. Replace all `datetime.utcnow()` calls with `datetime.now(UTC)`
4. Run tests: `pytest tests/models/ -v`
5. Verify warnings eliminated: `pytest tests/ -v 2>&1 | grep -c DeprecationWarning` (should be 0)

**Expected Outcome:**
- All 18 deprecation warnings eliminated
- All tests still pass
- Code is Python 3.15+ compatible

---

## Task 3: Update VideoRepository for Timestamp Handling

**Priority:** P1 (Code quality improvement)
**Estimated Time:** 10 minutes
**Files Affected:**
- `vidops/dal/videos.py`

**Problem:**
After adding `created_at`/`updated_at` columns, need to ensure proper handling.

**Solution:**
Verify INSERT includes `created_at`, UPDATE sets `updated_at`.

**Steps:**
1. Read current implementation: `vidops/dal/videos.py:43-72`
2. Verify INSERT clause includes:
   ```sql
   INSERT INTO videos (
       ytid, url, title, ..., created_at
   ) VALUES (
       %(ytid)s, %(url)s, %(title)s, ..., NOW()
   )
   ```
3. Verify UPDATE clause includes:
   ```sql
   ON CONFLICT (ytid) DO UPDATE SET
       ...,
       updated_at = NOW()
   ```
4. Check that `Video` model has these fields in `to_dict()`
5. Test: `pytest tests/dal/test_videos_repo.py -v`

**Expected Outcome:**
- Timestamps are correctly populated on insert/update
- Tests pass

---

## Task 4: Run Full Test Suite to Verify Fixes

**Priority:** P1 (Validation)
**Estimated Time:** 5 minutes
**Files Affected:** None

**Steps:**
1. Run full test suite:
   ```bash
   source .venv/bin/activate
   PYTHONPATH=/home/billie/tools/vidops python -m pytest tests/ -v --tb=short --timeout=180
   ```
2. Verify results:
   - Expected: 20 passed, 0 failed, 0 warnings
   - Actual previous: 19 passed, 1 failed, 18 warnings
3. If any failures, investigate and fix
4. Update `VALIDATION_REPORT.md` with new test results

**Expected Outcome:**
```
=================== 20 passed in X.XXs ===================
```

---

## Task 5: Document Changes

**Priority:** P2 (Documentation)
**Estimated Time:** 10 minutes
**Files Affected:**
- `docs/REFACTOR_ARCHITECTURE/VALIDATION_REPORT.md`
- `scripts/db/migrations/001_add_video_timestamps.sql` (new file)

**Steps:**
1. Add migration file to git
2. Update `VALIDATION_REPORT.md` Section 3.1 to mark as RESOLVED
3. Update Section 13.3 "Is It Ready for Production?" to reflect fixes
4. Create changelog entry in `REFACTOR_PROGRESS_LOG.md`

**Expected Outcome:**
- Migration documented and versioned
- Validation report shows updated status

---

## Summary of Claude's Work

**Total Tasks:** 5
**Estimated Total Time:** 65 minutes
**Critical Path:** Task 1 → Task 4 (fixes enable full test pass)

**Files Claude Will Modify:**
- Database schema (1 new migration file)
- `vidops/models/*.py` (4 files - datetime imports)
- `vidops/dal/videos.py` (verify only, likely no changes needed)
- `tests/models/test_models.py` (datetime calls in test data)
- Documentation files (2 files - report updates)

**Database Changes:**
- Add 2 columns to `videos` table
- Add 1 trigger function
- Add 1 trigger

**No Conflicts With Gemini's Work:**
- Claude focuses on fixing existing code
- Gemini will write NEW tests and NEW integrations
- No file overlap expected

---

## Post-Completion Checklist

- [ ] All tests pass (20/20)
- [ ] No deprecation warnings
- [ ] Schema migration applied successfully
- [ ] Documentation updated
- [ ] Changes committed to git
- [ ] User notified of completion

---

**Ready to Execute:** YES (pending user approval)
**Blocked By:** None
**Blocks:** None (Gemini can work in parallel)
