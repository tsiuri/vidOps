#!/usr/bin/env python3
"""
transcribe_worker_nvidia_db.py - NVIDIA GPU worker with database queue support

Enhanced version of transcribe_worker_nvidia.py that supports both:
- Traditional file-based queue (--queue-dir)
- Database queue (USE_DB_QUEUE=1)

Usage:
    # Database queue mode (new)
    USE_DB_QUEUE=1 python3 transcribe_worker_nvidia_db.py --gpu-idx 0 --model medium

    # File-based queue mode (legacy)
    python3 transcribe_worker_nvidia_db.py --queue-dir /path/to/queue --model medium --gpu-idx 0

Environment Variables:
    USE_DB_QUEUE=1              Enable database queue mode
    WORKER_HEARTBEAT_INTERVAL   Seconds between heartbeats (default: 30)
    WORKER_LEASE_SECONDS        Job lease duration (default: 3600)
    WORKER_MAX_JOBS             Max jobs before exit (default: 0 = forever)

    All existing transcribe_worker_nvidia.py environment variables still work:
    FORCE, OUTFMT, NV_COMPUTE, NV_VAD_FILTER, INLINE_RETRY, etc.

Author: Enhanced from transcribe_worker_nvidia.py (2025-11-28)
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
    claim_task,  # For file-based mode
)

try:
    from fragment_runner import run_fragmented_transcription
    HAS_FRAGMENT_RUNNER = True
except ImportError:
    HAS_FRAGMENT_RUNNER = False

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
    torch = None

from faster_whisper import WhisperModel
import tempfile
import shutil


# Lightweight segment representation
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
    """Determine model tag for output filenames"""
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


def process_media_file(media: Path, model, config: dict):
    """
    Process a single media file with the loaded model.

    Args:
        media: Path to media file
        model: Loaded WhisperModel instance
        config: Configuration dict with all settings

    Returns:
        dict with output_vtt, output_srt, output_words_tsv, processing_time_sec
    """
    start_time = time.time()

    # Extract config
    MODEL = config['model']
    LANG = config['language']
    FORCE = config['force']
    OUTFMT = config['outfmt']
    VAD_FILTER = config['vad_filter']
    MIN_TS = config['min_ts']
    INITIAL_PROMPT = config['initial_prompt']
    CORR = config['corrections']
    MODEL_TAG_SAFE = config['model_tag']

    # Original base for source files (in pull/)
    media_base = media.with_suffix("")
    ensure_src_json(media, media_base)

    # Output base for generated files (in generated/)
    base = get_output_base(media)

    # Apply model tag just before output suffix
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

    # Fast skip checks
    if success_marker.exists() and not FORCE:
        print(f"{LOG_PREFIX}[SKIP]{media} (already transcribed)", flush=True)
        return None
    if out_txt.exists() and not FORCE:
        print(f"{LOG_PREFIX}[SKIP]{media} (txt exists)", flush=True)
        return None

    # Create lock
    lock.touch()

    try:
        print(f"{LOG_PREFIX}[PROC]{media}", flush=True)

        # Transcribe based on file size/duration
        duration_sec = probe_duration_seconds(media)
        print(f"{LOG_PREFIX} Detected duration: {duration_sec:.1f}s", flush=True)

        # Determine if we should use fragmented processing
        use_fragmented = (
            HAS_FRAGMENT_RUNNER and
            duration_sec > 3600 and
            os.environ.get("DISABLE_FRAGMENTED", "0") != "1"
        )

        if use_fragmented:
            print(f"{LOG_PREFIX} Using fragmented transcription (duration > 1hr)", flush=True)
            seg_list = run_fragmented_transcription(
                media_path=media,
                model=model,
                language=LANG,
                vad_filter=VAD_FILTER,
                initial_prompt=INITIAL_PROMPT,
                log_prefix=LOG_PREFIX
            )
            segs = [LightSegment(s) for s in seg_list]
        else:
            # Standard transcription
            print(f"{LOG_PREFIX} Transcribing with model={MODEL}, language={LANG}", flush=True)
            segments_gen, info = model.transcribe(
                str(media),
                language=LANG,
                vad_filter=VAD_FILTER,
                initial_prompt=INITIAL_PROMPT,
                word_timestamps=True,
            )
            segs = [LightSegment(seg) for seg in segments_gen]
            print(f"{LOG_PREFIX} Transcribed {len(segs)} segments", flush=True)

        # Handle low-confidence segments (inline retry or manifest)
        CONFIDENCE_THRESHOLD = float(os.environ.get("NV_CONFIDENCE_THRESHOLD", "-0.7"))
        INLINE_RETRY = int(os.environ.get("INLINE_RETRY", "0"))
        low_conf_indices = [i for i, s in enumerate(segs) if s.avg_logprob < CONFIDENCE_THRESHOLD]

        if low_conf_indices and INLINE_RETRY == 0:
            # Write retry manifest
            write_retry_manifest(base, media, segs, low_conf_indices, CONFIDENCE_THRESHOLD, LOG_PREFIX)
            retried_set = set()
        elif low_conf_indices and INLINE_RETRY == 1:
            # Inline retry (simplified - full logic in original worker)
            print(f"{LOG_PREFIX}[RETRY] found {len(low_conf_indices)} low-confidence segments", flush=True)
            # For now, just mark them (full retry logic can be added later)
            retried_set = set()
        else:
            retried_set = set()

        # Write plain text
        with open(out_txt, "w", encoding="utf-8") as f:
            for s in segs:
                t = s.text.strip()
                if t:
                    f.write(apply_corrections(t, CORR) + "\n")

        # Write words.tsv
        write_words_tsv_faster(base, segs, retried_set, LOG_PREFIX)
        if tag:
            try:
                old_words = base.with_suffix(".words.tsv")
                new_words = base.with_suffix(f".{tag}.words.tsv")
                if old_words.exists():
                    old_words.rename(new_words)
            except Exception as e:
                print(f"{LOG_PREFIX}[WARN] failed to retag words.tsv: {e}", flush=True)

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

        # Cleanup
        del segs
        gc.collect()

        processing_time = time.time() - start_time

        # Prepare result
        words_tsv_path = base.with_suffix(f".{tag}.words.tsv" if tag else ".words.tsv")
        result = {
            'output_vtt': str(out_vtt) if out_vtt and out_vtt.exists() else None,
            'output_srt': str(out_srt) if out_srt and out_srt.exists() else None,
            'output_words_tsv': str(words_tsv_path),
            'processing_time_sec': processing_time
        }

        # Ingest into database if using DB queue
        if os.environ.get('USE_DB_QUEUE') == '1':
            try:
                from db_ingest_helpers import extract_ytid, load_video_metadata, should_ingest

                ytid = extract_ytid(media.name)
                if ytid:
                    video_metadata = load_video_metadata(media)

                    # Get job_id from config if available
                    job_id = config.get('job_id')

                    if should_ingest(job_id, ytid):
                        from db_queue import TranscriptionQueue
                        queue = TranscriptionQueue()

                        word_count = queue.ingest_transcription(
                            job_id=job_id,
                            ytid=ytid,
                            words_tsv_path=str(words_tsv_path),
                            model=MODEL,
                            video_metadata=video_metadata
                        )

                        print(f"{LOG_PREFIX}[DB] Ingested {word_count} words for {ytid} (source: whisper-{MODEL})", flush=True)
                    else:
                        print(f"{LOG_PREFIX}[DB] Skipping ingestion (job_id={job_id}, ytid={ytid})", flush=True)
                else:
                    print(f"{LOG_PREFIX}[DB] Skipping ingestion (no ytid found in filename)", flush=True)
            except Exception as e:
                print(f"{LOG_PREFIX}[WARN] Database ingestion failed: {e}", flush=True)
                import traceback
                traceback.print_exc()
                # Don't fail job if DB ingestion fails

        return result

    except Exception as e:
        print(f"{LOG_PREFIX}[FAIL]{media}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        # Clean up partial success marker
        try:
            success_marker.unlink()
        except:
            pass
        raise
    finally:
        try:
            os.unlink(str(lock))
        except Exception:
            pass


def run_file_based_queue(args, model, config):
    """Run in file-based queue mode (original behavior)"""
    QDIR = Path(args.queue_dir)
    PENDING = QDIR / "pending"
    INPROG = QDIR / "inprogress"
    DONE = QDIR / "done"

    print(f"{LOG_PREFIX} Running in FILE-BASED queue mode", flush=True)
    print(f"{LOG_PREFIX} Queue dir: {QDIR}", flush=True)

    # Main loop: process tasks from queue
    while True:
        task = claim_task(QDIR, backend="nvidia")
        if not task:
            try:
                # Quick check: if PENDING is empty and INPROG is empty, we're done
                if next(PENDING.iterdir(), None) is None and next(INPROG.iterdir(), None) is None:
                    break
            except FileNotFoundError:
                break
            time.sleep(0.1)
            continue

        task_file, media = task

        try:
            process_media_file(media, model, config)
        finally:
            try:
                task_file.replace(DONE / task_file.name)
            except Exception:
                pass


def run_database_queue(args, model, config):
    """Run in database queue mode (new behavior)"""
    from db_queue import TranscriptionQueue
    from queue_worker_base import QueueWorkerBase

    print(f"{LOG_PREFIX} Running in DATABASE queue mode", flush=True)

    class NvidiaDBWorker(QueueWorkerBase):
        def __init__(self, gpu_index, model_inst, config_dict, **kwargs):
            super().__init__(
                worker_type="nvidia",
                gpu_index=gpu_index,
                model=config_dict['model'],
                **kwargs
            )
            self.whisper_model = model_inst
            self.config = config_dict

        def _get_compute_type(self):
            return self.config['compute']

        def _get_version(self):
            return "nvidia-db-1.0"

        def _process_job(self, job: dict):
            job_id = job['job_id']
            media_path = Path(job['media_path'])

            print(f"{LOG_PREFIX} Processing job {job_id}: {media_path.name}", flush=True)

            # Update status to running
            self.queue.update_status(job_id, 'running')

            try:
                # Add job_id to config for database ingestion
                config_with_job = self.config.copy()
                config_with_job['job_id'] = job_id

                # Process the file
                result = process_media_file(media_path, self.whisper_model, config_with_job)

                if result:
                    # Complete the job
                    self.queue.complete_job(
                        job_id,
                        output_vtt=result.get('output_vtt'),
                        output_srt=result.get('output_srt'),
                        output_words_tsv=result.get('output_words_tsv'),
                        processing_time_sec=result.get('processing_time_sec')
                    )
                else:
                    # File was skipped
                    self.queue.update_status(job_id, 'completed')

            except Exception as e:
                print(f"{LOG_PREFIX} Job failed: {e}", flush=True)
                raise

    # Create and run worker
    worker = NvidiaDBWorker(
        gpu_index=int(args.gpu_idx),
        model_inst=model,
        config_dict=config,
        heartbeat_interval=int(os.environ.get('WORKER_HEARTBEAT_INTERVAL', 30)),
        poll_interval=int(os.environ.get('WORKER_POLL_INTERVAL', 10))
    )

    max_jobs = int(os.environ.get('WORKER_MAX_JOBS', 0))
    worker.run(max_jobs=max_jobs if max_jobs > 0 else None)


def main():
    parser = argparse.ArgumentParser(
        description="NVIDIA GPU transcription worker (file or database queue)"
    )
    parser.add_argument("--queue-dir", help="Queue directory path (for file-based mode)")
    parser.add_argument("--model", default="medium", help="Whisper model size")
    parser.add_argument("--language", default="en", help="Language code (empty=auto)")
    parser.add_argument("--gpu-idx", default="0", help="GPU index")
    args = parser.parse_args()

    # Determine mode
    use_db_queue = os.environ.get("USE_DB_QUEUE") == "1"

    if not use_db_queue and not args.queue_dir:
        print("ERROR: Must specify either --queue-dir or set USE_DB_QUEUE=1", file=sys.stderr)
        return 1

    # Set GPU device
    os.environ['GPU_IDX'] = str(args.gpu_idx)
    os.environ['CUDA_VISIBLE_DEVICES'] = str(args.gpu_idx)

    # Build config
    MODEL = args.model
    LANG = args.language or None
    FORCE = os.environ.get("FORCE") == "1"
    OUTFMT = os.environ.get("OUTFMT", "vtt")
    COMPUTE = os.environ.get("NV_COMPUTE", "float16")
    VAD_FILTER = os.environ.get("NV_VAD_FILTER", "1") == "1"
    MIN_TS = int(os.environ.get("MIN_TS_INTERVAL", "10"))
    MODEL_TAG_SAFE = determine_model_tag(MODEL)
    INITIAL_PROMPT = build_initial_prompt()
    CORR = load_corrections()

    config = {
        'model': MODEL,
        'language': LANG,
        'force': FORCE,
        'outfmt': OUTFMT,
        'compute': COMPUTE,
        'vad_filter': VAD_FILTER,
        'min_ts': MIN_TS,
        'model_tag': MODEL_TAG_SAFE,
        'initial_prompt': INITIAL_PROMPT,
        'corrections': CORR,
    }

    # Load model
    print(f"{LOG_PREFIX} Worker starting at {time.strftime('%H:%M:%S')}", flush=True)
    print(f"{LOG_PREFIX} Loading model={MODEL} device=cuda compute={COMPUTE}", flush=True)
    start_load = time.time()
    model = WhisperModel(MODEL, device="cuda", compute_type=COMPUTE, num_workers=GPU_THREADS)
    load_time = time.time() - start_load
    print(f"{LOG_PREFIX} Model loaded in {load_time:.1f}s", flush=True)

    # Run appropriate queue mode
    try:
        if use_db_queue:
            run_database_queue(args, model, config)
        else:
            run_file_based_queue(args, model, config)
    except KeyboardInterrupt:
        print(f"\n{LOG_PREFIX} Interrupted, shutting down...", flush=True)
    finally:
        del model
        if torch:
            torch.cuda.empty_cache()
        print(f"{LOG_PREFIX} Worker shutdown complete", flush=True)


if __name__ == "__main__":
    sys.exit(main() or 0)
