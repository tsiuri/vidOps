#!/usr/bin/env bash
# migrate_to_new_structure.sh - Reorganize files to new pull/ and generated/ structure
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR"

# Color output
c_do(){ printf '\033[1;34m==> %s\033[0m\n' "$*"; }
c_ok(){ printf '\033[1;32m✓   %s\033[0m\n' "$*"; }
c_wr(){ printf '\033[1;33m!!  %s\033[0m\n' "$*" >&2; }
c_er(){ printf '\033[1;31mxx  %s\033[0m\n' "$*" >&2; }

DRY_RUN=0
BACKUP=1

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift;;
    --no-backup) BACKUP=0; shift;;
    -h|--help)
      cat <<'EOF'
migrate_to_new_structure.sh - Reorganize files into pull/ and generated/

USAGE:
  ./migrate_to_new_structure.sh [options]

OPTIONS:
  --dry-run      Show what would be moved without actually moving files
  --no-backup    Skip creating backup (not recommended)
  -h, --help     Show this help

WHAT IT DOES:
  1. Creates pull/ and generated/ directories
  2. Moves media files (*.opus, *.mp4, etc.) and metadata to pull/
  3. Moves transcription outputs (*.txt, *.vtt, etc.) to generated/
  4. Keeps download lists and configs in top level
  5. Creates a backup of current state (unless --no-backup)

FILES MOVED TO pull/:
  - Media: *.opus, *.m4a, *.mp3, *.mp4, *.mkv, *.avi, *.mov, *.wav
  - Metadata: *.info.json, *.src.json

FILES MOVED TO generated/:
  - Transcripts: *.txt (except README/LICENSE/hotwords/corrections)
  - Subtitles: *.vtt, *.srt
  - Logs: *.tslog.txt
  - Word data: *.words.tsv

FILES KEPT IN TOP LEVEL:
  - Scripts: *.sh, *.py
  - Configs: *.txt (hotwords, corrections, etc.)
  - Archives: .clips-download-archive.txt
  - TSV files: wanted_clips.tsv, corrections.tsv
  - Directories: clips/, pull/, generated/, dualq.*

EXAMPLES:
  ./migrate_to_new_structure.sh --dry-run    # Preview changes
  ./migrate_to_new_structure.sh              # Do the migration
EOF
      exit 0
      ;;
    *) c_er "Unknown option: $1"; exit 1;;
  esac
done

if [[ $DRY_RUN -eq 1 ]]; then
  c_wr "DRY RUN MODE - No files will be moved"
fi

# Create backup
if [[ $BACKUP -eq 1 && $DRY_RUN -eq 0 ]]; then
  BACKUP_DIR="backup-$(date +%Y%m%d-%H%M%S)"
  c_do "Creating backup: $BACKUP_DIR"
  mkdir -p "$BACKUP_DIR"

  # Backup files that will be moved (not entire directory to avoid recursion)
  for pattern in "*.opus" "*.m4a" "*.mp3" "*.mp4" "*.mkv" "*.avi" "*.mov" "*.wav" \
                 "*.info.json" "*.src.json" "*.vtt" "*.srt" "*.tslog.txt" "*.words.tsv"; do
    find . -maxdepth 1 -type f -name "$pattern" -exec cp -p {} "$BACKUP_DIR/" \; 2>/dev/null || true
  done

  # Backup .txt files (excluding certain patterns)
  find . -maxdepth 1 -type f -name "*.txt" ! -name "hotwords.txt" ! -name "corrections.txt" \
    ! -name "README.txt" ! -name "LICENSE.txt" ! -name ".clips-download-archive.txt" \
    -exec cp -p {} "$BACKUP_DIR/" \; 2>/dev/null || true

  local backup_count
  backup_count=$(find "$BACKUP_DIR" -type f | wc -l)
  c_ok "Backed up $backup_count files to $BACKUP_DIR/"
fi

# Create directories
c_do "Creating directories..."
if [[ $DRY_RUN -eq 0 ]]; then
  mkdir -p pull generated
  c_ok "Created pull/ and generated/"
else
  c_ok "[DRY RUN] Would create pull/ and generated/"
fi

# Count files
count_media=0
count_meta=0
count_trans=0

# Function to move file
move_file() {
  local src="$1"
  local dest="$2"

  if [[ ! -f "$src" ]]; then
    return 0
  fi

  # Skip if already in target directory
  local src_dir
  src_dir=$(dirname "$src")
  local dest_dir
  dest_dir=$(dirname "$dest")

  if [[ "$src_dir" == "$dest_dir" ]]; then
    return 0
  fi

  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  [DRY RUN] $src -> $dest"
    return 0
  fi

  # Check if destination exists
  if [[ -f "$dest" ]]; then
    c_wr "Destination exists, skipping: $dest"
    return 0
  fi

  mv -n "$src" "$dest"
  echo "  Moved: $(basename "$src")"
}

# Move media files to pull/
c_do "Moving media files to pull/..."
for pattern in "*.opus" "*.m4a" "*.mp3" "*.mp4" "*.mkv" "*.avi" "*.mov" "*.wav"; do
  while IFS= read -r -d '' file; do
    move_file "$file" "pull/$(basename "$file")"
    ((count_media++))
  done < <(find . -maxdepth 1 -type f -name "$pattern" -print0 2>/dev/null || true)
done
c_ok "Processed $count_media media files"

# Move metadata to pull/
c_do "Moving metadata files to pull/..."
for pattern in "*.info.json" "*.src.json"; do
  while IFS= read -r -d '' file; do
    move_file "$file" "pull/$(basename "$file")"
    ((count_meta++))
  done < <(find . -maxdepth 1 -type f -name "$pattern" -print0 2>/dev/null || true)
done
c_ok "Processed $count_meta metadata files"

# Move transcription outputs to generated/
c_do "Moving transcription outputs to generated/..."

# Move .vtt, .srt files
for pattern in "*.vtt" "*.srt"; do
  while IFS= read -r -d '' file; do
    move_file "$file" "generated/$(basename "$file")"
    ((count_trans++))
  done < <(find . -maxdepth 1 -type f -name "$pattern" -print0 2>/dev/null || true)
done

# Move .tslog.txt and .words.tsv
for pattern in "*.tslog.txt" "*.words.tsv"; do
  while IFS= read -r -d '' file; do
    move_file "$file" "generated/$(basename "$file")"
    ((count_trans++))
  done < <(find . -maxdepth 1 -type f -name "$pattern" -print0 2>/dev/null || true)
done

# Move .txt files (excluding config files)
while IFS= read -r -d '' file; do
  local basename
  basename=$(basename "$file")

  # Skip config and special files
  case "$basename" in
    hotwords.txt|corrections.txt|README.txt|LICENSE.txt|.clips-download-archive.txt|wanted_clips.txt)
      continue
      ;;
  esac

  # Skip .tsv files that look like they're lists/configs
  if [[ "$basename" == *.tsv ]]; then
    continue
  fi

  move_file "$file" "generated/$basename"
  ((count_trans++))
done < <(find . -maxdepth 1 -type f -name "*.txt" -print0 2>/dev/null || true)

c_ok "Processed $count_trans transcription files"

# Summary
echo
c_ok "═══════════════════════════════════════════════════════"
c_ok "Migration Summary:"
c_ok "  Media files moved to pull/:      $count_media"
c_ok "  Metadata files moved to pull/:   $count_meta"
c_ok "  Transcripts moved to generated/: $count_trans"

if [[ $DRY_RUN -eq 1 ]]; then
  echo
  c_wr "This was a DRY RUN - no files were actually moved"
  c_do "Run without --dry-run to perform the migration"
else
  echo
  c_ok "Migration complete!"

  if [[ $BACKUP -eq 1 ]]; then
    c_ok "Backup saved to: $BACKUP_DIR/"
  fi

  echo
  c_do "Next steps:"
  echo "  1. Review the new structure in pull/ and generated/"
  echo "  2. Activate the patched dual_gpu_transcribe.sh:"
  echo "     mv dual_gpu_transcribe.sh dual_gpu_transcribe.sh.original"
  echo "     mv dual_gpu_transcribe.sh.patched dual_gpu_transcribe.sh"
  echo "  3. Test the workflow:"
  echo "     ./clips.sh pull <URL>              # Downloads to pull/"
  echo "     ./dual_gpu_transcribe.sh           # Transcribes to generated/"
  echo "     ./clips.sh hits -q 'search term'   # Searches generated/"
fi
