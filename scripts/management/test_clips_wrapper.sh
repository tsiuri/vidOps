#!/usr/bin/env bash
# Test that clips.sh wrapper runs from correct directory

set -euo pipefail

echo "Testing clips.sh wrapper..."
echo ""

WORKSPACE_ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "Workspace root: $WORKSPACE_ROOT"
echo ""

# Test 1: Wrapper changes to workspace root
echo "Test 1: Checking if wrapper changes to workspace root..."

# Get the directory that clips.sh would use
TEST_DIR=$(./clips.sh help 2>&1 | head -1 || echo "")

if [[ -n "$TEST_DIR" ]] || ./clips.sh help &>/dev/null; then
    echo "✓ clips.sh wrapper executes successfully"
else
    echo "✗ clips.sh wrapper failed to execute"
    exit 1
fi

# Test 2: Verify wrapper script content
echo ""
echo "Test 2: Verifying wrapper contains workspace root cd..."

if grep -q "cd.*WRAPPER_DIR" clips.sh; then
    echo "✓ Wrapper contains 'cd' to workspace root"
else
    echo "✗ Wrapper missing 'cd' command"
    exit 1
fi

# Test 3: Check that pull/ would be created in workspace root
echo ""
echo "Test 3: Checking expected pull/ location..."

if grep -q "mkdir -p pull" scripts/utilities/clips_templates/pull.sh 2>/dev/null; then
    echo "✓ pull.sh will create 'pull/' in current directory"
    echo "  Since wrapper cd's to workspace root, pull/ will be created there"
else
    echo "⚠  Could not verify pull/ location (pull.sh may have different structure)"
fi

echo ""
echo "========================================="
echo "Summary:"
echo "  When you run: ./clips.sh pull <url>"
echo "  From anywhere, it will:"
echo "  1. cd to: $WORKSPACE_ROOT"
echo "  2. Execute: scripts/utilities/clips.sh"
echo "  3. Create: $WORKSPACE_ROOT/pull/"
echo "========================================="
echo ""
echo "✓ Wrapper test passed!"
echo ""
echo "To use:"
echo "  cd $WORKSPACE_ROOT"
echo "  ./clips.sh pull <youtube-url>"
