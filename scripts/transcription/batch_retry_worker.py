#!/usr/bin/env python3
"""
batch_retry_worker.py - Process retry manifests and patch VTT/SRT files
"""
import sys
import os
import csv
import re
import shutil
import tempfile
import subprocess
from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Tuple
from concurrent.futures import ProcessPoolExecutor, as_completed

# Get config from environment
MODEL = os.environ.get("MODEL", "small")
LANGUAGE = os.environ.get("LANGUAGE", "en")
if LANGUAGE == "auto" or LANGUAGE == "":
    LANGUAGE = None  # Auto-detect
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "."))
WORKERS = int(os.environ.get("WORKERS", "1"))

def log(msg):
    print(f"[RETRY] {msg}", flush=True)

def warn(msg):
    print(f"[WARN] {msg}", flush=True, file=sys.stderr)

def parse_manifest(manifest_path: Path) -> List[Dict]:
    """Parse retry manifest TSV"""
    segments = []
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter='\t')
            for row in reader:
                segments.append({
                    'media_file': row['media_file'],
                    'segment_idx': int(row['segment_idx']),
                    'start_time': float(row['start_time']),
                    'end_time': float(row['end_time']),
                    'confidence': float(row['confidence']),
                    'text': row['text']
                })
    except Exception as e:
        warn(f"Failed to parse {manifest_path}: {e}")
        return []
    return segments

def group_by_media(segments: List[Dict]) -> Dict[str, List[Dict]]:
    """Group segments by media file"""
    grouped = defaultdict(list)
    for seg in segments:
        grouped[seg['media_file']].append(seg)
    return grouped

def extract_audio_segment(media_path: Path, start: float, end: float, output_path: Path) -> bool:
    """Extract audio segment using ffmpeg"""
    try:
        duration = end - start if end > start else 0.1  # Minimum 0.1s
        cmd = [
            'ffmpeg', '-y',
            '-ss', str(start),
            '-i', str(media_path),
            '-t', str(duration),
            '-vn',  # No video
            '-acodec', 'libopus',
            '-b:a', '128k',
            str(output_path)
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        warn(f"Failed to extract audio {start:.1f}s-{end:.1f}s: {e}")
        return False

def transcribe_segment(audio_path: Path) -> Tuple[str, float]:
    """Transcribe audio segment using faster-whisper. Returns (text, avg_confidence)"""
    try:
        from faster_whisper import WhisperModel

        model = WhisperModel(MODEL, device="cuda", compute_type="float16")

        segments, info = model.transcribe(
            str(audio_path),
            language=LANGUAGE,
            beam_size=5,
            vad_filter=False  # Skip VAD for short segments
        )

        # Combine all segments and calculate average confidence
        texts = []
        confidences = []
        for seg in segments:
            texts.append(seg.text.strip())
            # avg_logprob is the confidence metric
            confidences.append(float(getattr(seg, 'avg_logprob', 0.0)))

        text = " ".join(texts).strip()
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

        # Log detected language if available
        detected_lang = getattr(info, 'language', None)
        if detected_lang and detected_lang != LANGUAGE:
            warn(f"    Language mismatch: requested={LANGUAGE}, detected={detected_lang}")

        return text, avg_conf
    except Exception as e:
        warn(f"Transcription failed for {audio_path}: {e}")
        return "", 0.0

def process_single_segment(args):
    """Worker function for parallel segment processing. Returns processed segment dict."""
    media_path, seg, temp_dir = args

    start = seg['start_time']
    end = seg['end_time']
    idx = seg['segment_idx']

    # Skip zero-duration segments
    if end <= start:
        warn(f"Skipping zero-duration segment {idx} at {start:.1f}s")
        return None

    log(f"  Segment {idx}: {start:.1f}s-{end:.1f}s (conf={seg['confidence']:.3f})")

    # Extract audio
    audio_file = temp_dir / f"seg_{idx}.opus"
    if not extract_audio_segment(media_path, start, end, audio_file):
        return None

    # Re-transcribe
    if not DRY_RUN:
        new_text, new_conf = transcribe_segment(audio_file)
        seg['new_text'] = new_text
        seg['new_confidence'] = new_conf
        log(f"    Old: {seg['text'][:60]} (conf={seg['confidence']:.3f})")
        log(f"    New: {new_text[:60]} (conf={new_conf:.3f})")
    else:
        seg['new_text'] = f"[DRY RUN - would re-transcribe]"
        seg['new_confidence'] = 0.0

    # Clean up audio file
    try:
        audio_file.unlink()
    except:
        pass

    return seg

def parse_vtt(vtt_path: Path) -> List[Dict]:
    """Parse VTT file into list of segments with confidence notes"""
    segments = []
    try:
        with open(vtt_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # Look for confidence NOTE before timestamp
            confidence = None
            if line.startswith('NOTE Confidence:'):
                try:
                    confidence = float(line.split(':')[1].strip())
                except:
                    pass
                i += 1
                line = lines[i].strip() if i < len(lines) else ""

            # Look for timestamp line (HH:MM:SS.mmm --> HH:MM:SS.mmm)
            if '-->' in line:
                times = line.split('-->')
                start_str = times[0].strip()
                end_str = times[1].strip()

                # Get text (next non-empty lines until blank line)
                text_lines = []
                i += 1
                while i < len(lines) and lines[i].strip():
                    text_lines.append(lines[i].rstrip())
                    i += 1

                segments.append({
                    'start_str': start_str,
                    'end_str': end_str,
                    'text': '\n'.join(text_lines),
                    'confidence': confidence
                })
            i += 1
    except Exception as e:
        warn(f"Failed to parse VTT {vtt_path}: {e}")
    return segments

def time_str_to_seconds(time_str: str) -> float:
    """Convert HH:MM:SS.mmm to seconds"""
    time_str = time_str.replace(',', '.')  # Handle SRT format
    parts = time_str.split(':')
    h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
    return h * 3600 + m * 60 + s

def write_vtt(segments: List[Dict], output_path: Path):
    """Write VTT file from segments with confidence notes"""
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("WEBVTT\n\n")
        for seg in segments:
            # Write confidence NOTE if present
            if seg.get('confidence') is not None:
                f.write(f"NOTE Confidence: {seg['confidence']:.3f}\n\n")
            f.write(f"{seg['start_str']} --> {seg['end_str']}\n")
            f.write(f"{seg['text']}\n\n")

def patch_vtt_file(vtt_path: Path, retry_segments: List[Dict]):
    """Patch VTT file with re-transcribed segments"""
    if not vtt_path.exists():
        warn(f"VTT file not found: {vtt_path}")
        return False

    # Parse existing VTT
    vtt_segments = parse_vtt(vtt_path)
    if not vtt_segments:
        warn(f"No segments found in {vtt_path}")
        return False

    log(f"Patching {len(retry_segments)} segments in {vtt_path.name}")

    # Build mapping of segment index to new text and confidence
    retries_by_idx = {seg['segment_idx']: seg for seg in retry_segments}

    # Patch segments
    patched_count = 0
    for idx, vtt_seg in enumerate(vtt_segments):
        if idx in retries_by_idx:
            retry_seg = retries_by_idx[idx]
            if 'new_text' in retry_seg and retry_seg['new_text']:
                vtt_seg['text'] = retry_seg['new_text']
                # Update confidence if available
                if 'new_confidence' in retry_seg:
                    vtt_seg['confidence'] = retry_seg['new_confidence']
                patched_count += 1

    log(f"Successfully patched {patched_count}/{len(retry_segments)} segments")

    if DRY_RUN:
        log("DRY RUN - not writing changes")
        return True

    # Backup original
    backup_path = vtt_path.with_suffix('.vtt.bak')
    if not backup_path.exists():
        shutil.copy2(vtt_path, backup_path)
        log(f"Created backup: {backup_path.name}")

    # Write patched VTT
    write_vtt(vtt_segments, vtt_path)
    return True

def process_media_file(media_path: Path, segments: List[Dict]) -> bool:
    """Process all retry segments for a media file. Returns True if successful."""
    log(f"Processing {len(segments)} segments for {media_path.name}")

    # Determine VTT path (in generated/)
    generated_dir = PROJECT_ROOT / "generated"
    base_name = media_path.stem
    vtt_path = generated_dir / f"{base_name}.vtt"

    if not vtt_path.exists():
        warn(f"VTT file not found: {vtt_path}")
        return False

    # Create temp directory
    temp_dir = Path(tempfile.mkdtemp(prefix="retry_"))

    try:
        # Re-transcribe segments (with optional parallelism)
        retried_segments = []

        if WORKERS > 1:
            # Parallel processing
            log(f"Using {WORKERS} parallel workers")
            with ProcessPoolExecutor(max_workers=WORKERS) as executor:
                # Prepare args for each segment
                segment_args = [(media_path, seg, temp_dir) for seg in segments]

                # Submit all tasks
                futures = {executor.submit(process_single_segment, args): args for args in segment_args}

                # Collect results as they complete
                for future in as_completed(futures):
                    try:
                        result = future.result()
                        if result:
                            retried_segments.append(result)
                    except Exception as e:
                        warn(f"Segment processing failed: {e}")
        else:
            # Sequential processing (original behavior)
            for seg in segments:
                result = process_single_segment((media_path, seg, temp_dir))
                if result:
                    retried_segments.append(result)

    finally:
        # Cleanup temp files
        shutil.rmtree(temp_dir, ignore_errors=True)

    # Patch VTT file
    if retried_segments:
        success = patch_vtt_file(vtt_path, retried_segments)
        if success:
            # Also update words.tsv
            words_tsv = generated_dir / f"{base_name}.words.tsv"
            if words_tsv.exists():
                update_words_tsv(words_tsv, retried_segments)
        return success
    return True

def update_words_tsv(words_tsv_path: Path, retry_segments: List[Dict]):
    """Update confidence scores and retried flag in words.tsv for retried segments"""
    try:
        # Read existing words.tsv
        with open(words_tsv_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        if not lines:
            return

        # Parse header
        header = lines[0].strip().split('\t')
        if 'seg' not in header or 'confidence' not in header or 'retried' not in header:
            warn(f"words.tsv missing required columns: {words_tsv_path}")
            return

        seg_idx = header.index('seg')
        conf_idx = header.index('confidence')
        retried_idx = header.index('retried')

        # Build map of segment_idx -> new_confidence
        retries_by_seg = {}
        for retry_seg in retry_segments:
            if 'new_confidence' in retry_seg:
                retries_by_seg[retry_seg['segment_idx']] = retry_seg['new_confidence']

        if DRY_RUN:
            log(f"DRY RUN - would update {len(retries_by_seg)} segments in {words_tsv_path.name}")
            return

        # Backup words.tsv (only if backup doesn't exist)
        backup_path = words_tsv_path.with_suffix('.tsv.bak')
        if not backup_path.exists():
            shutil.copy2(words_tsv_path, backup_path)
            log(f"Created backup: {backup_path.name}")

        # Update confidence and retried flag for matching segments
        updated_count = 0
        for i in range(1, len(lines)):  # Skip header
            parts = lines[i].strip().split('\t')
            if len(parts) > max(seg_idx, conf_idx, retried_idx):
                seg_num = int(parts[seg_idx])
                if seg_num in retries_by_seg:
                    # Update confidence
                    parts[conf_idx] = f"{retries_by_seg[seg_num]:.3f}"
                    # Mark as retried
                    parts[retried_idx] = '1'
                    lines[i] = '\t'.join(parts) + '\n'
                    updated_count += 1

        # Write updated words.tsv
        with open(words_tsv_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)

        log(f"Updated {updated_count} words in {words_tsv_path.name}")

    except Exception as e:
        warn(f"Failed to update words.tsv {words_tsv_path}: {e}")

def main():
    if len(sys.argv) < 2:
        print("Usage: batch_retry_worker.py <manifest1> [manifest2...]")
        sys.exit(1)

    manifest_paths = [Path(p) for p in sys.argv[1:]]

    log(f"Processing {len(manifest_paths)} manifest files")
    log(f"Model: {MODEL}, Language: {LANGUAGE or 'auto'}, Workers: {WORKERS}")

    # Parse all manifests and track which ones we process
    manifest_segments = {}  # manifest_path -> segments
    for manifest_path in manifest_paths:
        segments = parse_manifest(manifest_path)
        if segments:
            log(f"Loaded {len(segments)} segments from {manifest_path.name}")
            manifest_segments[manifest_path] = segments

    if not manifest_segments:
        warn("No segments found in manifests")
        return

    all_segments = []
    for segments in manifest_segments.values():
        all_segments.extend(segments)

    log(f"Total segments to retry: {len(all_segments)}")

    # Group by media file
    grouped = group_by_media(all_segments)
    log(f"Segments grouped into {len(grouped)} media files")

    # Process each media file and track success
    processed_manifests = set()
    for media_file, segments in grouped.items():
        media_path = PROJECT_ROOT / media_file
        if not media_path.exists():
            warn(f"Media file not found: {media_file}")
            continue

        success = process_media_file(media_path, segments)

        # If successful, mark all manifests that contributed segments for this media
        if success:
            for manifest_path, manifest_segs in manifest_segments.items():
                # Check if any segments from this manifest were for this media file
                if any(seg['media_file'] == media_file for seg in manifest_segs):
                    processed_manifests.add(manifest_path)

    # Rename processed manifests
    if not DRY_RUN:
        for manifest_path in processed_manifests:
            processed_path = Path(str(manifest_path) + ".processed")
            try:
                manifest_path.rename(processed_path)
                log(f"Marked as processed: {manifest_path.name}")
            except Exception as e:
                warn(f"Failed to rename manifest {manifest_path}: {e}")
    else:
        log(f"DRY RUN - would mark {len(processed_manifests)} manifests as processed")

    log("Batch retry complete!")

if __name__ == "__main__":
    main()
