#!/usr/bin/env python3
"""
transcribe_worker_nvidia.py - NVIDIA GPU transcription worker

Standalone transcription worker for NVIDIA GPUs using faster-whisper.
Supports multi-GPU parallel transcription via queue-based task claiming.

Usage:
    transcribe_worker_nvidia.py --queue-dir <path> --model <size> \\
        --language <code> --gpu-idx <N>

Environment Variables:
    See dual_gpu_transcribe.sh for complete list of variables.
    Key vars: FORCE, OUTFMT, NV_COMPUTE, NV_VAD_FILTER, INLINE_RETRY

Author: Extracted from dual_gpu_transcribe.sh (2025-11-26)
"""

import os
import sys
import time
import json
import argparse
import subprocess
from pathlib import Path
import gc

# Add script directory to path for local imports
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

# Import shared utilities
from transcribe_common import (
    get_output_base,
    probe_duration_seconds,
    split_audio_into_chunks,
    emit_progress,
    ensure_src_json,
    build_initial_prompt,
    load_corrections,
    apply_corrections,
    write_vtt,
    write_srt,
    write_words_tsv_faster,
    write_retry_manifest,
    make_tslog,
    claim_task,
)
from fragment_runner import run_fragmented_transcription

# GPU index for logging (set by launcher)
def _build_log_prefix() -> str:
    env_label = os.environ.get("WORKER_LABEL")
    if env_label:
        lab = env_label.strip("[]")
        return f"[{lab}]"
    num_env = os.environ.get("WORKER_NUM", "")
    if num_env.isdigit():
        return f"[NV{int(num_env):02d}]"
    # Fallback: derive from GPU_IDX (1-based, zero-padded)
    gpu_idx_raw = os.environ.get("GPU_IDX", "")
    try:
        gpu_num = int(gpu_idx_raw)
        lab = f"NV{gpu_num + 1:02d}"
    except ValueError:
        lab = f"NV{gpu_idx_raw}"
    return f"[{lab}]"


LOG_PREFIX = _build_log_prefix()

# Limit CPU thread usage for GPU worker (GPU does the heavy lifting)
GPU_THREADS = int(os.environ.get("OMP_NUM_THREADS", "16"))
try:
    import torch
    torch.set_num_threads(GPU_THREADS)
    torch.set_num_interop_threads(GPU_THREADS)
except ImportError:
    pass

from faster_whisper import WhisperModel
import gc
import tempfile
import shutil
try:
    import torch
except ImportError:
    torch = None


# Lightweight segment representation to avoid keeping full faster-whisper objects
class LightSegment:
    """Minimal segment data to reduce memory footprint"""
    __slots__ = ['text', 'start', 'end', 'avg_logprob', 'no_speech_prob', 'compression_ratio', 'words']

    def __init__(self, seg):
        self.text = seg.text
        self.start = seg.start
        self.end = seg.end
        self.avg_logprob = float(getattr(seg, "avg_logprob", 0.0))
        self.no_speech_prob = float(getattr(seg, "no_speech_prob", 0.0))
        self.compression_ratio = float(getattr(seg, "compression_ratio", 0.0))

        # Extract word data if available
        seg_words = getattr(seg, "words", None)
        if seg_words:
            # Create lightweight word objects
            self.words = [
                type('Word', (), {
                    'word': getattr(w, "word", ""),
                    'start': float(getattr(w, "start", 0.0)),
                    'end': float(getattr(w, "end", 0.0))
                })()
                for w in seg_words
            ]
        else:
            self.words = None


def determine_model_tag(raw_model_name: str) -> str:
    """
    Determine model tag for output filenames.

    Args:
        raw_model_name: Raw model name from environment (e.g., "medium", "large-v3")

    Returns:
        Safe model tag for filenames (e.g., "medium", "large")
    """
    model_tag_override = os.environ.get("MODEL_TAG")
    if model_tag_override:
        model_tag = model_tag_override
    else:
        n = raw_model_name.lower()
        if "turbo" in n:
            model_tag = "turbo"
        elif "medium" in n:
            model_tag = "medium"
        elif "small" in n:
            model_tag = "small"
        elif "base" in n:
            model_tag = "base"
        elif "tiny" in n:
            model_tag = "tiny"
        elif "large" in n:
            model_tag = "large"
        else:
            model_tag = raw_model_name

    # Sanitize for filenames
    model_tag_safe = "".join(
        (c if (c.isalnum() or c in ("-", "_", ".")) else "_") for c in model_tag
    ).strip(".")

    return model_tag_safe


def main():
    parser = argparse.ArgumentParser(
        description="NVIDIA GPU transcription worker using faster-whisper"
    )
    parser.add_argument("--queue-dir", required=True, help="Queue directory path")
    parser.add_argument("--model", default="medium", help="Whisper model size")
    parser.add_argument("--language", default="en", help="Language code (empty=auto)")
    parser.add_argument("--gpu-idx", default="0", help="GPU index (for logging)")
    args = parser.parse_args()

    # Environment variables
    MODEL = args.model
    LANG = args.language or None  # Empty string → None for auto-detect
    FORCE = os.environ.get("FORCE") == "1"
    OUTFMT = os.environ.get("OUTFMT", "vtt")
    COMPUTE = os.environ.get("NV_COMPUTE", "float16")
    VAD_FILTER = os.environ.get("NV_VAD_FILTER", "1") == "1"
    MIN_TS = int(os.environ.get("MIN_TS_INTERVAL", "10"))
    QDIR = Path(args.queue_dir)
    PENDING = QDIR / "pending"
    INPROG = QDIR / "inprogress"
    DONE = QDIR / "done"

    # Model tag for output filenames
    MODEL_TAG_SAFE = determine_model_tag(MODEL)

    # Hotwords and corrections
    INITIAL_PROMPT = build_initial_prompt()
    CORR = load_corrections()

    # Load faster-whisper model
    print(f"{LOG_PREFIX} Worker starting at {time.strftime('%H:%M:%S')}", flush=True)
    print(f"{LOG_PREFIX} Worker starting with {GPU_THREADS} CPU threads (GPU worker)", flush=True)
    print(
        f"{LOG_PREFIX} loading faster-whisper model={MODEL} device=cuda "
        f"compute={COMPUTE} num_workers={GPU_THREADS}",
        flush=True
    )
    start_load = time.time()
    model = WhisperModel(MODEL, device="cuda", compute_type=COMPUTE, num_workers=GPU_THREADS)
    load_time = time.time() - start_load
    print(
        f"{LOG_PREFIX} Model loaded successfully in {load_time:.1f}s at "
        f"{time.strftime('%H:%M:%S')}, entering main loop",
        flush=True
    )

    # Main loop: process tasks from queue
    while True:
        task = claim_task(QDIR, backend="nvidia")
        if not task:
            try:
                # Quick check: if PENDING is empty and INPROG is empty, we're done
                if next(PENDING.iterdir(), None) is None and next(INPROG.iterdir(), None) is None:
                    break
            except FileNotFoundError:
                # Queue directory was cleaned up, exit gracefully
                break
            time.sleep(0.1)
            continue

        task_file, media = task
        # Original base for source files (in pull/)
        media_base = media.with_suffix("")
        ensure_src_json(media, media_base)

        # Output base for generated files (in generated/)
        base = get_output_base(media)

        # Apply model tag just before output suffix (e.g., turbo.vtt, medium.words.tsv)
        tag = MODEL_TAG_SAFE
        txt_suffix = f".{tag}.txt" if tag else ".txt"
        vtt_suffix = f".{tag}.vtt" if tag else ".vtt"
        srt_suffix = f".{tag}.srt" if tag else ".srt"
        tslog_suffix = f".{tag}.tslog.txt" if tag else ".tslog.txt"

        out_txt = base.with_suffix(txt_suffix)
        do_vtt = OUTFMT in ("vtt", "both")
        do_srt = OUTFMT in ("srt", "both")
        out_vtt = base.with_suffix(vtt_suffix) if do_vtt else None
        out_srt = base.with_suffix(srt_suffix) if do_srt else None
        lock = base.with_suffix(".transcribing.lock")
        success_marker = base.with_suffix(".transcribed")

        try:
            # Fast skip checks before any heavy work or delegation
            if success_marker.exists() and not FORCE:
                print(f"{LOG_PREFIX}[SKIP]{media} (already transcribed)", flush=True)
                continue
            if out_txt.exists() and not FORCE:
                print(f"{LOG_PREFIX}[SKIP]{media} (txt exists)", flush=True)
                continue

            # Acquire lock early to avoid doing work on locked jobs
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                os.close(fd)
                got_lock = True
            except FileExistsError:
                got_lock = False

            if not got_lock:
                print(f"{LOG_PREFIX}[LOCK]{media} (held elsewhere) — skipping", flush=True)
                continue

            # Transcribe
            print(f"{LOG_PREFIX}[RUN ]{media}", flush=True)

            total = probe_duration_seconds(media) or 0.0
            print(f"{LOG_PREFIX}[INFO]{media} duration={total:.1f}s", flush=True)

            # Delegation: use fragmented_transcribe.py for very long files
            FRAGMENTED_THRESHOLD = float(os.environ.get("FRAGMENTED_THRESHOLD", "3600"))  # 1 hour default
            force_fragment = os.environ.get("FRAGMENT", "0") == "1" or os.environ.get("FRAGMENT_FORCE", "0") == "1"
            if force_fragment or total > FRAGMENTED_THRESHOLD:
                print(
                    f"{LOG_PREFIX}[DELEGATE] File duration {total/3600:.2f}h exceeds threshold "
                    f"{FRAGMENTED_THRESHOLD/3600:.2f}h, running in-process fragmented mode",
                    flush=True
                )
                # In-process fragmented transcription using existing model
                try:
                    tmp_root = Path(os.environ.get("FRAG_TMPDIR", "")) or Path(os.environ.get("PROJECT_ROOT", ".")) / "tmp" / "transcribe_chunks"
                    inline_retry_env = os.environ.get("INLINE_RETRY", "0")
                    inline_retry_val = int(inline_retry_env) if inline_retry_env.isdigit() else 0
                    run_fragmented_transcription(
                        model=model,
                        media=media,
                        model_tag=MODEL_TAG_SAFE,
                        lang=LANG,
                        force=FORCE,
                        outfmt=OUTFMT,
                        chunk_len=float(os.environ.get("CHUNK_LEN", "3600")),
                        overlap=float(os.environ.get("CHUNK_OVERLAP", "5")),
                        tmp_root=tmp_root,
                        initial_prompt=INITIAL_PROMPT,
                        corrections=CORR,
                        log_prefix=f"{LOG_PREFIX}[FRAG]",
                        confidence_threshold=float(os.environ.get("NV_CONFIDENCE_THRESHOLD", "0.2")),
                        inline_retry=inline_retry_val,
                        min_ts_interval=int(os.environ.get("MIN_TS_INTERVAL", "10")),
                        tag=None,
                    )
                except Exception as e:
                    print(f"{LOG_PREFIX}[FAIL] Fragmented transcription failed: {e}", flush=True)
                continue

            # Standard processing for files under threshold
            # Split long files into chunks to reduce memory usage
            # Files > 1.5 hours are chunked into 1-hour segments
            chunks = split_audio_into_chunks(media, chunk_duration=3600, log_prefix=LOG_PREFIX)
            temp_dir_to_cleanup = chunks[0][2] if len(chunks) > 0 else None

            try:
                next_mark = [0.10]  # 10%, 20%, … 90%
                last_end = 0.0
                all_segs = []  # Accumulate segments from all chunks

                # Fast first pass: beam_size=1 (greedy decoding)
                vad_status = "enabled" if VAD_FILTER else "DISABLED"
                print(
                    f"{LOG_PREFIX}[PASS1] fast transcribe ({COMPUTE}, greedy, VAD {vad_status}) "
                    f"starting at {time.strftime('%H:%M:%S')}",
                    flush=True
                )
                pass1_start = time.time()

                # Process each chunk
                for chunk_idx, (chunk_path, chunk_offset, _) in enumerate(chunks):
                    if len(chunks) > 1:
                        print(
                            f"{LOG_PREFIX}[CHUNK {chunk_idx+1}/{len(chunks)}] "
                            f"Processing chunk starting at {chunk_offset/3600:.2f}h",
                            flush=True
                        )

                    kwargs_fast = dict(
                        language=LANG,
                        vad_filter=VAD_FILTER,
                        initial_prompt=INITIAL_PROMPT,
                        condition_on_previous_text=False,
                        temperature=0.0,  # Greedy only
                        beam_size=1,      # Greedy search
                        word_timestamps=True,
                    )
                    segments, info = model.transcribe(str(chunk_path), **kwargs_fast)

                    # Use lightweight segment representation to reduce memory footprint
                    chunk_segs = []
                    for s in segments:  # streaming
                        # Extract only essential data, discard full faster-whisper object
                        light_seg = LightSegment(s)

                        # Adjust timestamps by chunk offset
                        light_seg.start += chunk_offset
                        light_seg.end += chunk_offset
                        if light_seg.words:
                            for w in light_seg.words:
                                w.start += chunk_offset
                                w.end += chunk_offset

                        chunk_segs.append(light_seg)

                        # Print segment text in real-time
                        text = light_seg.text.strip()
                        if text:
                            print(f"{LOG_PREFIX}[TEXT] {text}", flush=True)
                        last_end = max(last_end, light_seg.end)
                        emit_progress(f"{LOG_PREFIX}[PROG]", last_end, total, next_mark)

                    # Explicitly delete iterator and force garbage collection
                    # This releases ~6GB of internal buffers for long files
                    del segments
                    gc.collect()

                    # Add chunk segments to accumulated list
                    all_segs.extend(chunk_segs)

                    if len(chunks) > 1:
                        print(
                            f"{LOG_PREFIX}[CHUNK {chunk_idx+1}/{len(chunks)}] "
                            f"Completed, {len(chunk_segs)} segments",
                            flush=True
                        )

                # Use accumulated segments for rest of processing
                segs = all_segs

                if total > 0:
                    print(f"{LOG_PREFIX}[100%] {total:.1f}s / {total:.1f}s", flush=True)

                pass1_elapsed = time.time() - pass1_start
                print(
                    f"{LOG_PREFIX}[PASS1] completed in {pass1_elapsed:.1f}s at "
                    f"{time.strftime('%H:%M:%S')}",
                    flush=True
                )

            finally:
                # Clean up temporary chunk directory if it was created
                if temp_dir_to_cleanup:
                    try:
                        shutil.rmtree(temp_dir_to_cleanup, ignore_errors=True)
                        print(f"{LOG_PREFIX}[CLEANUP] Removed temporary chunks from {temp_dir_to_cleanup}", flush=True)
                    except Exception as e:
                        print(f"{LOG_PREFIX}[WARN] Failed to clean up temp dir: {e}", flush=True)

            # Check confidence scores and identify low-confidence segments
            CONFIDENCE_THRESHOLD = float(os.environ.get("NV_CONFIDENCE_THRESHOLD", "-0.7"))
            INLINE_RETRY = int(os.environ.get("INLINE_RETRY", "0"))
            low_conf_indices = []
            for i, s in enumerate(segs):
                avg_logprob = float(getattr(s, "avg_logprob", 0.0))
                if avg_logprob < CONFIDENCE_THRESHOLD:
                    low_conf_indices.append(i)

            # Handle low-confidence segments based on INLINE_RETRY setting
            if low_conf_indices and INLINE_RETRY == 0:
                # Deferred retry: write manifest for batch post-processing
                print(
                    f"{LOG_PREFIX}[DEFER] found {len(low_conf_indices)}/{len(segs)} "
                    f"low-confidence segments (avg_logprob < {CONFIDENCE_THRESHOLD})",
                    flush=True
                )
                write_retry_manifest(base, media, segs, low_conf_indices, CONFIDENCE_THRESHOLD, LOG_PREFIX)
                retried_set = set()  # No inline retries performed

            elif low_conf_indices and INLINE_RETRY == 1:
                # Inline retry (old behavior) for low-confidence segments
                retry_start_time = time.time()
                print(
                    f"{LOG_PREFIX}[RETRY] found {len(low_conf_indices)}/{len(segs)} "
                    f"low-confidence segments (avg_logprob < {CONFIDENCE_THRESHOLD})",
                    flush=True
                )
                print(
                    f"{LOG_PREFIX}[RETRY] re-transcribing with quality settings (beam_size=5) at "
                    f"{time.strftime('%H:%M:%S')}",
                    flush=True
                )

                # Extract and retry each low-confidence segment
                for idx in low_conf_indices:
                    seg = segs[idx]
                    start_time = float(getattr(seg, "start", 0.0))
                    end_time = float(getattr(seg, "end", 0.0))

                    # Extract audio clip for this segment using ffmpeg
                    clip_path = base.with_suffix(f".clip_{idx}.wav")
                    try:
                        subprocess.run(
                            ["ffmpeg", "-y", "-v", "error",
                             "-i", str(media),
                             "-ss", str(start_time),
                             "-to", str(end_time),
                             "-ac", "1", "-ar", "16000",
                             str(clip_path)],
                            check=True
                        )

                        # Re-transcribe with quality settings
                        kwargs_quality = dict(
                            language=LANG,
                            vad_filter=VAD_FILTER,
                            initial_prompt=INITIAL_PROMPT,
                            condition_on_previous_text=False,
                            temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
                            beam_size=5,
                            word_timestamps=True,
                            compression_ratio_threshold=2.4,
                            log_prob_threshold=-1.0,
                        )
                        retry_segs, _ = model.transcribe(str(clip_path), **kwargs_quality)
                        retry_list = list(retry_segs)

                        if retry_list:
                            # Use first segment from retry (adjust timestamps)
                            retry_seg = retry_list[0]

                            # Reconstruct segment with corrected timestamps
                            class RetriedSegment:
                                def __init__(self, orig_seg, retry_seg, base_time):
                                    self.text = retry_seg.text
                                    self.start = base_time + float(getattr(retry_seg, "start", 0.0))
                                    self.end = base_time + float(getattr(retry_seg, "end", 0.0))
                                    self.avg_logprob = float(getattr(retry_seg, "avg_logprob", 0.0))
                                    self.no_speech_prob = float(getattr(retry_seg, "no_speech_prob", 0.0))
                                    self.compression_ratio = float(getattr(retry_seg, "compression_ratio", 0.0))
                                    # Copy words if available, adjusting timestamps
                                    retry_words = getattr(retry_seg, "words", None)
                                    if retry_words:
                                        class Word:
                                            def __init__(self, word, start, end):
                                                self.word = word
                                                self.start = start
                                                self.end = end
                                        self.words = [
                                            Word(
                                                getattr(w, "word", ""),
                                                base_time + float(getattr(w, "start", 0.0)),
                                                base_time + float(getattr(w, "end", 0.0))
                                            )
                                            for w in retry_words
                                        ]
                                    else:
                                        self.words = getattr(orig_seg, "words", None)

                            segs[idx] = RetriedSegment(seg, retry_seg, start_time)

                        # Clean up clip
                        os.unlink(clip_path)
                    except Exception as e:
                        print(f"{LOG_PREFIX}[RETRY] segment {idx} failed: {e}", flush=True)

                retry_elapsed = time.time() - retry_start_time
                print(
                    f"{LOG_PREFIX}[RETRY] completed retry for {len(low_conf_indices)} segments in "
                    f"{retry_elapsed:.1f}s at {time.strftime('%H:%M:%S')}",
                    flush=True
                )
                retried_set = set(low_conf_indices)
            else:
                # No low-confidence segments found
                retried_set = set()

            # Write plain text
            with open(out_txt, "w", encoding="utf-8") as f:
                for s in segs:
                    t = s.text.strip()
                    if t:
                        f.write(apply_corrections(t, CORR) + "\n")

            # Write words.tsv with confidence scores, then retag filename with model
            write_words_tsv_faster(base, segs, retried_set, LOG_PREFIX)
            if tag:
                try:
                    old_words = base.with_suffix(".words.tsv")
                    new_words = base.with_suffix(f".{tag}.words.tsv")
                    if old_words.exists():
                        old_words.rename(new_words)
                except Exception as e:
                    print(f"{LOG_PREFIX}[WARN] failed to retag words.tsv with model: {e}", flush=True)

            # Captions + tslog
            cap = None
            if do_vtt:
                write_vtt(segs, out_vtt, retried_set)
                cap = out_vtt
            if do_srt:
                write_srt(segs, out_srt)
                cap = cap or out_srt
            if cap:
                make_tslog(cap, base.with_suffix(tslog_suffix), MIN_TS)

            # Mark successful completion
            success_marker.touch()
            print(f"{LOG_PREFIX}[DONE]{media}", flush=True)

            # Explicit memory cleanup after each file
            del segs
            gc.collect()

        except Exception as e:
            print(f"{LOG_PREFIX}[FAIL]{media}: {e}", flush=True)
            # Clean up partial success marker if exists
            try:
                success_marker.unlink()
            except:
                pass
        finally:
            try:
                os.unlink(str(lock))
            except Exception:
                pass
            try:
                task_file.replace(DONE / task_file.name)
            except Exception:
                pass


if __name__ == "__main__":
    main()
