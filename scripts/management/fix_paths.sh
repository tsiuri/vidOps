#!/usr/bin/env bash
# Fix all path references to work with new structure

set -euo pipefail

FIXES="path_fixes_log.txt"
echo "# Path Fixes Log - $(date)" > "$FIXES"

echo "==> Fixing stitch_videos scripts to reference sort_clips.py correctly..."

# Get the absolute path to the workspace root
WORKSPACE_ROOT="$(pwd)"

# Fix all stitch_videos scripts
for script in scripts/video_processing/stitch_videos*.sh; do
    [[ -f "$script" ]] || continue
    echo "Fixing: $script"

    # Replace "python3 sort_clips.py" with absolute path
    sed -i 's|python3 sort_clips\.py|python3 '"$WORKSPACE_ROOT"'/scripts/utilities/sort_clips.py|g' "$script"

    echo "  $script: Updated sort_clips.py path" >> "$FIXES"
done

echo "==> Fixing clips.sh COMP_DIR reference..."

# Update COMP_DIR in clips.sh to point to clips_templates
sed -i 's|COMP_DIR="$SCRIPT_DIR/clips"|COMP_DIR="$SCRIPT_DIR/clips_templates"|g' scripts/utilities/clips.sh

echo "  scripts/utilities/clips.sh: Updated COMP_DIR to clips_templates" >> "$FIXES"

echo "==> Creating wrapper scripts in root for backward compatibility..."

# Create wrapper for clips.sh
cat > clips.sh << 'EOF'
#!/usr/bin/env bash
# Wrapper script for backward compatibility
exec "$(dirname "$0")/scripts/utilities/clips.sh" "$@"
EOF
chmod +x clips.sh
echo "  Created clips.sh wrapper" >> "$FIXES"

# Create wrapper for common scripts
for script_type in voice_filtering video_processing date_management; do
    dir="scripts/$script_type"
    find "$dir" -type f \( -name "*.py" -o -name "*.sh" \) | while read -r script; do
        script_name=$(basename "$script")
        wrapper_name="${script_name}"

        # Only create wrapper if it doesn't conflict
        if [[ ! -e "$wrapper_name" ]]; then
            if [[ "$script" == *.py ]]; then
                cat > "$wrapper_name" << 'EOFWRAPPER'
#!/usr/bin/env bash
# Wrapper script for backward compatibility
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/SCRIPTPATH" "$@"
EOFWRAPPER
                sed -i "s|SCRIPTPATH|$script|g" "$wrapper_name"
            else
                cat > "$wrapper_name" << 'EOFWRAPPER'
#!/usr/bin/env bash
# Wrapper script for backward compatibility
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/SCRIPTPATH" "$@"
EOFWRAPPER
                sed -i "s|SCRIPTPATH|$script|g" "$wrapper_name"
            fi
            chmod +x "$wrapper_name"
            echo "  Created $wrapper_name wrapper" >> "$FIXES"
        fi
    done
done

echo "==> Updating default output paths in scripts..."

# Update extract_and_compare_dates.py to output to results/
sed -i 's|"date_comparison\.txt"|"results/date_comparison.txt"|g' scripts/date_management/extract_and_compare_dates.py

# Update find_missing_dates.py default output
sed -i "s|default='missing_dates\.txt'|default='data/missing_dates.txt'|g" scripts/date_management/find_missing_dates.py

# Update create_download_list.py default output
sed -i "s|default='download_list\.txt'|default='data/download_list.txt'|g" scripts/date_management/create_download_list.py

echo "  Updated default output paths" >> "$FIXES"

echo "==> Creating environment helper script..."

cat > set_paths.sh << EOF
#!/usr/bin/env bash
# Helper script to set environment variables for new structure

export WORKSPACE_ROOT="$WORKSPACE_ROOT"
export DATA_DIR="\$WORKSPACE_ROOT/data"
export RESULTS_DIR="\$WORKSPACE_ROOT/results"
export MEDIA_DIR="\$WORKSPACE_ROOT/media"
export SCRIPTS_DIR="\$WORKSPACE_ROOT/scripts"
export CONFIG_DIR="\$WORKSPACE_ROOT/config"

# Source this file before running scripts:
# source set_paths.sh
EOF
chmod +x set_paths.sh
echo "  Created set_paths.sh environment helper" >> "$FIXES"

echo ""
echo "==> Path fixes complete!"
echo "==> Log saved to: $FIXES"
echo ""
echo "==> Summary of changes:"
cat "$FIXES"
