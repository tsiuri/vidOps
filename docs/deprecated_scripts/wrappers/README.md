# Backward Compatibility Wrappers

This directory contains wrapper scripts for backward compatibility with the old interface.

## Recommended Usage

Use the unified interface instead:
```bash
# In workspace root
./workspace.sh <command> [options]
```

See `../START_HERE.md` for the complete guide.

## What's Here

These wrappers allow you to call scripts from the root directory using the old names:

### Voice Filtering Wrappers
- `filter_voice.py` → `scripts/voice_filtering/filter_voice.py`
- `filter_voice_parallel.py` → `scripts/voice_filtering/filter_voice_parallel.py`
- `filter_voice_parallel_chunked.py` → `scripts/voice_filtering/filter_voice_parallel_chunked.py`
- `filter_voice_batched.py` → `scripts/voice_filtering/filter_voice_batched.py`

### Video Stitching Wrappers
- `stitch_videos.sh` → `scripts/video_processing/stitch_videos.sh`
- `stitch_videos_batched.sh` → `scripts/video_processing/stitch_videos_batched.sh`
- `stitch_videos_cfr.sh` → `scripts/video_processing/stitch_videos_cfr.sh`
- `stitch_videos_batched_filter.sh` → `scripts/video_processing/stitch_videos_batched_filter.sh`
- `stitch_videos_concat_filter.sh` → `scripts/video_processing/stitch_videos_concat_filter.sh`
- `concat_filter_from_list.sh` → `scripts/video_processing/concat_filter_from_list.sh`

### Date Management Wrappers
- `create_download_list.py` → `scripts/date_management/create_download_list.py`
- `extract_and_compare_dates.py` → `scripts/date_management/extract_and_compare_dates.py`
- `find_missing_dates.py` → `scripts/date_management/find_missing_dates.py`
- `move_files_by_date.py` → `scripts/date_management/move_files_by_date.py`

## Do You Need These?

**No, not if you use `workspace.sh`!**

These exist only for backward compatibility. If you have old scripts or muscle memory using the old names, you can symlink them back to root:

```bash
ln -s wrappers/filter_voice_parallel_chunked.py .
ln -s wrappers/stitch_videos_batched.sh .
# etc.
```

But we recommend updating to use `workspace.sh` instead.
