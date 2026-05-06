#!/usr/bin/env bash
# Find all path references in scripts

OUTPUT="path_references_report.txt"
echo "# Path References Report - $(date)" > "$OUTPUT"
echo "" >> "$OUTPUT"

find_in_file() {
    local file="$1"
    local pattern="$2"
    local description="$3"

    if grep -n "$pattern" "$file" 2>/dev/null | head -20; then
        echo "==> $file: $description" >> "$OUTPUT"
        grep -n "$pattern" "$file" 2>/dev/null | head -20 >> "$OUTPUT"
        echo "" >> "$OUTPUT"
    fi
}

echo "==> Scanning Python scripts..."
for script in scripts/*/*.py; do
    [[ -f "$script" ]] || continue
    echo "Scanning: $script"

    # Python imports of local modules
    find_in_file "$script" "^import [a-z_]" "Local imports"
    find_in_file "$script" "^from [a-z_]" "Local from imports"

    # File path references
    find_in_file "$script" "open(" "File open operations"
    find_in_file "$script" "Path(" "Path objects"
    find_in_file "$script" "\\.txt" "Text file references"
    find_in_file "$script" "\\.json" "JSON file references"
    find_in_file "$script" "\\.tsv" "TSV file references"
    find_in_file "$script" "cuts_exact" "Hardcoded cuts_exact paths"
    find_in_file "$script" "voice_filtered" "Hardcoded voice_filtered paths"
done

echo "==> Scanning Shell scripts..."
for script in scripts/*/*.sh scripts/utilities/*.sh; do
    [[ -f "$script" ]] || continue
    echo "Scanning: $script"

    # Source commands
    find_in_file "$script" "^source " "Source commands"
    find_in_file "$script" "^\\. " "Dot source commands"

    # Python script calls
    find_in_file "$script" "python3.*\\.py" "Python script calls"

    # Shell script calls
    find_in_file "$script" "\\.sh" "Shell script calls"

    # File references
    find_in_file "$script" "\\.env" "Environment file references"
    find_in_file "$script" "\\.txt" "Text file references"
    find_in_file "$script" "\\.json" "JSON file references"
    find_in_file "$script" "cuts_exact" "Hardcoded cuts_exact paths"
    find_in_file "$script" "voice_filtered" "Hardcoded voice_filtered paths"
done

echo ""
echo "==> Report saved to: $OUTPUT"
echo "==> Summary of potential issues:"
grep -c "Local imports\|Local from imports\|Source commands\|Python script calls" "$OUTPUT" || echo "0"
