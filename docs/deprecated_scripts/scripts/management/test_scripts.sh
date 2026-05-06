#!/usr/bin/env bash
# Test script compatibility after reorganization

set -uo pipefail

TEST_LOG="script_compatibility_test.txt"
echo "# Script Compatibility Test - $(date)" > "$TEST_LOG"
echo "" >> "$TEST_LOG"

PASSED=0
FAILED=0

test_script() {
    local script="$1"
    local test_cmd="$2"
    local description="$3"

    echo -n "Testing: $description... "

    if eval "$test_cmd" &>/dev/null; then
        echo "✓ PASS"
        echo "[PASS] $description" >> "$TEST_LOG"
        echo "  Script: $script" >> "$TEST_LOG"
        echo "  Command: $test_cmd" >> "$TEST_LOG"
        echo "" >> "$TEST_LOG"
        ((PASSED++))
    else
        echo "✗ FAIL"
        echo "[FAIL] $description" >> "$TEST_LOG"
        echo "  Script: $script" >> "$TEST_LOG"
        echo "  Command: $test_cmd" >> "$TEST_LOG"
        echo "  Error output:" >> "$TEST_LOG"
        eval "$test_cmd" 2>&1 | head -10 >> "$TEST_LOG"
        echo "" >> "$TEST_LOG"
        ((FAILED++))
    fi
}

echo "==> Testing Python scripts..."

test_script \
    "scripts/utilities/sort_clips.py" \
    "python3 scripts/utilities/sort_clips.py --help" \
    "sort_clips.py help"

test_script \
    "scripts/date_management/find_missing_dates.py" \
    "python3 scripts/date_management/find_missing_dates.py --help" \
    "find_missing_dates.py help"

test_script \
    "scripts/date_management/create_download_list.py" \
    "python3 scripts/date_management/create_download_list.py --help" \
    "create_download_list.py help"

test_script \
    "scripts/date_management/move_files_by_date.py" \
    "python3 scripts/date_management/move_files_by_date.py --help" \
    "move_files_by_date.py help"

test_script \
    "scripts/voice_filtering/filter_hasan_voice.py" \
    "python3 scripts/voice_filtering/filter_hasan_voice.py --help" \
    "filter_hasan_voice.py help"

echo ""
echo "==> Testing Shell scripts..."

test_script \
    "scripts/video_processing/stitch_videos.sh" \
    "bash -n scripts/video_processing/stitch_videos.sh" \
    "stitch_videos.sh syntax check"

test_script \
    "scripts/video_processing/stitch_videos_batched.sh" \
    "bash -n scripts/video_processing/stitch_videos_batched.sh" \
    "stitch_videos_batched.sh syntax check"

test_script \
    "scripts/utilities/clips.sh" \
    "bash -n scripts/utilities/clips.sh" \
    "clips.sh syntax check"

echo ""
echo "==> Testing wrapper scripts..."

test_script \
    "clips.sh wrapper" \
    "bash -n clips.sh" \
    "clips.sh wrapper syntax check"

test_script \
    "stitch_videos.sh wrapper" \
    "bash -n stitch_videos.sh" \
    "stitch_videos.sh wrapper syntax check"

echo ""
echo "==> Testing sort_clips.py path in stitch scripts..."

# Check if sort_clips.py path was updated correctly
if grep -q "scripts/utilities/sort_clips.py" scripts/video_processing/stitch_videos.sh; then
    echo "✓ stitch_videos.sh has correct sort_clips.py path"
    echo "[PASS] stitch_videos.sh has correct sort_clips.py path" >> "$TEST_LOG"
    ((PASSED++))
else
    echo "✗ stitch_videos.sh has incorrect sort_clips.py path"
    echo "[FAIL] stitch_videos.sh has incorrect sort_clips.py path" >> "$TEST_LOG"
    ((FAILED++))
fi

echo ""
echo "==> Testing directory structure..."

# Check that key directories exist
for dir in scripts data results media logs config docs; do
    if [[ -d "$dir" ]]; then
        echo "✓ Directory exists: $dir/"
        echo "[PASS] Directory exists: $dir/" >> "$TEST_LOG"
        ((PASSED++))
    else
        echo "✗ Directory missing: $dir/"
        echo "[FAIL] Directory missing: $dir/" >> "$TEST_LOG"
        ((FAILED++))
    fi
done

echo ""
echo "========================================="
echo "Test Results:"
echo "  Passed: $PASSED"
echo "  Failed: $FAILED"
echo "========================================="
echo ""
echo "Detailed log saved to: $TEST_LOG"

if [[ $FAILED -eq 0 ]]; then
    echo ""
    echo "✓ All tests passed! The reorganization appears successful."
    exit 0
else
    echo ""
    echo "✗ Some tests failed. Review $TEST_LOG for details."
    exit 1
fi
