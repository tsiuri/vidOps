# Workspace Management Scripts

Utility scripts for maintaining and testing the workspace structure.

## Scripts

### Reorganization & Path Management

**`reorganize.sh`**
- Reorganizes workspace files into the current directory structure
- Creates a manifest of all moves for tracking
- **Status:** Already executed; kept for reference

**`fix_paths.sh`**
- Fixes all path references after reorganization
- Updates script paths, wrapper paths, and internal references
- Creates wrapper scripts for backward compatibility
- **Status:** Already executed; kept for reference

**`find_path_references.sh`**
- Searches for specific path references in scripts
- Useful for finding dependencies before making changes

**`set_paths.sh`**
- Utility for setting path variables
- Helper for path management tasks

### Testing Scripts

**`test_scripts.sh`**
- Tests all scripts for syntax errors and compatibility
- Verifies:
  - Python script syntax (via py_compile)
  - Shell script syntax (via bash -n)
  - Wrapper script functionality
  - Path references
  - Directory structure
- Run this after making structural changes

**`test_clips_wrapper.sh`**
- Specifically tests the clips.sh wrapper
- Verifies it runs from workspace root correctly
- Ensures pull/ directory is created in the right location

## When to Use

### After Making Changes
```bash
# Test everything still works
./scripts/management/test_scripts.sh
```

### Finding Dependencies
```bash
# Find all references to a specific path
./scripts/management/find_path_references.sh "sort_clips.py"
```

### Reference Only
- `reorganize.sh` and `fix_paths.sh` were one-time operations
- They're kept for documentation of what was done
- Don't run them again unless you're starting over

## Notes

These scripts operate on the workspace structure itself. Most users will never need them - they're for maintenance and development of the workspace infrastructure.

For regular video processing tasks, use `../../workspace.sh` instead.
