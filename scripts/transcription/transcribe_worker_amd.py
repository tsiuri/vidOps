#!/usr/bin/env python3
"""
transcribe_worker_amd.py - AMD/ROCm transcription worker [DEPRECATED]

╔═══════════════════════════════════════════════════════════════════════════╗
║                              ⚠️  DEPRECATED ⚠️                              ║
║                                                                           ║
║  This AMD/ROCm worker uses OpenAI Whisper and is NO LONGER MAINTAINED.   ║
║                                                                           ║
║  Status: ENABLE_AMD=0 by default (disabled in main script)               ║
║  Last tested: 2024 (pre-refactor)                                        ║
║                                                                           ║
║  Requirements (NOT automatically maintained):                            ║
║    - python-pytorch-opt-rocm (ROCm 5.x+)                                 ║
║    - openai-whisper (git+https://github.com/openai/whisper.git)          ║
║    - ROCm drivers and libraries                                          ║
║    - Manual venv setup with system-site-packages                         ║
║                                                                           ║
║  Recommendations:                                                         ║
║    1. Use NVIDIA worker (faster-whisper) if possible - MUCH faster       ║
║    2. Use CPU worker (faster-whisper on CPU) as fallback                 ║
║    3. This worker needs updates for current ROCm/PyTorch versions        ║
║                                                                           ║
║  If you MUST use this worker:                                            ║
║    - Update PyTorch for your ROCm version                                ║
║    - Test thoroughly before production use                               ║
║    - Set ENABLE_AMD=1 in environment                                     ║
║    - Ensure AMD_VENV points to valid venv with ROCm PyTorch              ║
╚═══════════════════════════════════════════════════════════════════════════╝

Author: Extracted from dual_gpu_transcribe.sh (2025-11-26)
"""

import os
import sys
import time
import argparse
from pathlib import Path

# Runtime deprecation check
if os.environ.get("ENABLE_AMD", "0") == "0":
    print("=" * 80)
    print("ERROR: AMD worker is DEPRECATED and disabled by default")
    print("=" * 80)
    print("")
    print("This worker uses OpenAI Whisper with ROCm and requires manual updates.")
    print("")
    print("To use this worker:")
    print("  1. Update PyTorch for current ROCm version (5.x+)")
    print("  2. Install openai-whisper in AMD_VENV")
    print("  3. Test thoroughly on your hardware")
    print("  4. Set ENABLE_AMD=1 in environment")
    print("")
    print("Recommendations:")
    print("  - Use NVIDIA worker (faster, more reliable, actively maintained)")
    print("  - Or use CPU worker (faster-whisper on CPU)")
    print("")
    sys.exit(1)

# Add script directory to path for local imports
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

# Import shared utilities
from transcribe_common import (
    get_output_base,
    probe_duration_seconds,
    ensure_src_json,
    build_initial_prompt,
    load_corrections,
    apply_corrections,
    write_retry_manifest_amd,
    hms,
    claim_task,
)

LOG_PREFIX = "[AMD]"

# Limit CPU thread usage for GPU worker
GPU_THREADS = int(os.environ.get("OMP_NUM_THREADS", "16"))
try:
    import torch
    torch.set_num_threads(GPU_THREADS)
    torch.set_num_interop_threads(GPU_THREADS)
except ImportError:
    pass

try:
    import whisper
except ImportError:
    print(f"{LOG_PREFIX} ERROR: openai-whisper not installed")
    print(f"{LOG_PREFIX} Install with: pip install git+https://github.com/openai/whisper.git")
    sys.exit(1)


def write_vtt_amd(segs, path, retried_indices=None):
    """Write VTT file (handles dict segments from OpenAI Whisper)"""
    retried_indices = retried_indices or set()
    with open(path, "w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n")
        for i, s in enumerate(segs):
            if isinstance(s, dict):
                txt = (s.get("text", "") or "").strip()
                if not txt:
                    continue
                conf = float(s.get("avg_logprob", 0.0))
                start = s.get("start", 0.0)
                end = s.get("end", 0.0)
            else:
                txt = s.text.strip()
                if not txt:
                    continue
                conf = float(getattr(s, "avg_logprob", 0.0))
                start = s.start
                end = s.end

            if i in retried_indices:
                f.write(f"NOTE Confidence: {conf:.3f} [RETRIED]\n\n")
            else:
                f.write(f"NOTE Confidence: {conf:.3f}\n\n")
            f.write(f"{hms(start)} --> {hms(end)}\n{txt}\n\n")


def write_srt_amd(segs, path):
    """Write SRT file (handles dict segments)"""
    with open(path, "w", encoding="utf-8") as f:
        for i, s in enumerate(segs, 1):
            if isinstance(s, dict):
                txt = (s.get("text", "") or "").strip()
                if not txt:
                    continue
                start = s.get("start", 0.0)
                end = s.get("end", 0.0)
            else:
                txt = s.text.strip()
                if not txt:
                    continue
                start = s.start
                end = s.end

            a = hms(start, sep=",")
            b = hms(end, sep=",")
            f.write(f"{i}\n{a} --> {b}\n{txt}\n\n")


def write_words_tsv_whisper(base_path: Path, segs, retried_indices=None):
    """Write words.tsv (handles dict/object segments from OpenAI Whisper)"""
    retried_indices = retried_indices or set()
    words_tsv = base_path.with_suffix(".words.tsv")
    try:
        with open(words_tsv, "w", encoding="utf-8") as wf:
            wf.write("start\tend\tword\tseg\tconfidence\tretried\n")
            for si, s in enumerate(segs):
                # Handle dict or object
                if isinstance(s, dict):
                    ws = s.get("words", [])
                    conf = float(s.get("avg_logprob", 0.0))
                else:
                    ws = getattr(s, "words", None)
                    conf = float(getattr(s, "avg_logprob", 0.0))

                if not ws:
                    continue

                retried = 1 if si in retried_indices else 0
                for w in ws:
                    # Words may be dict or object
                    if isinstance(w, dict):
                        start = float(w.get("start", 0.0))
                        end = float(w.get("end", 0.0))
                        word = (w.get("word") or "").strip()
                    else:
                        start = float(getattr(w, "start", 0.0))
                        end = float(getattr(w, "end", 0.0))
                        word = (getattr(w, "word", "") or "").strip()
                    if not word:
                        continue
                    wf.write(f"{start:.3f}\t{end:.3f}\t{word}\t{si}\t{conf:.3f}\t{retried}\n")
    except Exception as e:
        print(f"{LOG_PREFIX}[WARN] failed to write words.tsv: {e}", flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="AMD/ROCm transcription worker using OpenAI Whisper [DEPRECATED]",
        epilog="⚠️  This worker is deprecated. Use NVIDIA or CPU workers instead."
    )
    parser.add_argument("--queue-dir", required=True, help="Queue directory path")
    parser.add_argument("--model", default="medium", help="Whisper model size")
    parser.add_argument("--language", default="en", help="Language code (empty=auto)")
    args = parser.parse_args()

    # Environment variables
    MODEL = args.model
    LANG = args.language or None
    FORCE = os.environ.get("FORCE") == "1"
    OUTFMT = os.environ.get("OUTFMT", "vtt")
    MIN_TS = int(os.environ.get("MIN_TS_INTERVAL", "10"))

    # AMD-specific tuning parameters
    NO_SPEECH_THRESHOLD = float(os.environ.get("AMD_NO_SPEECH_THRESHOLD", "0.4"))
    COMPRESSION_RATIO_THRESHOLD = float(os.environ.get("AMD_COMPRESSION_RATIO_THRESHOLD", "3.0"))
    LOGPROB_THRESHOLD = float(os.environ.get("AMD_LOGPROB_THRESHOLD", "-1.5"))
    FP16 = os.environ.get("AMD_FP16", "1") == "1"

    QDIR = Path(args.queue_dir)
    PENDING = QDIR / "pending"
    INPROG = QDIR / "inprogress"
    DONE = QDIR / "done"

    # Hotwords and corrections
    INITIAL_PROMPT = build_initial_prompt()
    CORR = load_corrections()

    # Load OpenAI Whisper model
    print(f"{LOG_PREFIX} Worker starting with {GPU_THREADS} CPU threads (GPU worker)", flush=True)
    print(
        f"{LOG_PREFIX} Tuning: no_speech_threshold={NO_SPEECH_THRESHOLD}, "
        f"compression_ratio_threshold={COMPRESSION_RATIO_THRESHOLD}, "
        f"logprob_threshold={LOGPROB_THRESHOLD}, fp16={FP16}",
        flush=True
    )
    print(f"{LOG_PREFIX} loading whisper model={MODEL} device=cuda", flush=True)
    print(f"{LOG_PREFIX} ⚠️  WARNING: This is a DEPRECATED worker - consider using NVIDIA or CPU workers", flush=True)

    model = whisper.load_model(MODEL, device="cuda")

    # Main loop
    while True:
        task = claim_task(QDIR, backend="amd")
        if not task:
            try:
                if next(PENDING.iterdir(), None) is None and next(INPROG.iterdir(), None) is None:
                    break
            except FileNotFoundError:
                break
            time.sleep(0.1)
            continue

        task_file, media = task
        media_base = media.with_suffix("")
        ensure_src_json(media, media_base)

        base = get_output_base(media)

        out_txt = base.with_suffix(".txt")
        do_vtt = OUTFMT in ("vtt", "both")
        do_srt = OUTFMT in ("srt", "both")
        out_vtt = base.with_suffix(".vtt") if do_vtt else None
        out_srt = base.with_suffix(".srt") if do_srt else None
        lock = base.with_suffix(".transcribing.lock")
        success_marker = base.with_suffix(".transcribed")

        try:
            if success_marker.exists() and not FORCE:
                print(f"{LOG_PREFIX}[SKIP]{media} (already transcribed)", flush=True)
                continue
            if out_txt.exists() and not FORCE:
                print(f"{LOG_PREFIX}[SKIP]{media} (txt exists)", flush=True)
                continue

            # Acquire lock
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                os.close(fd)
                got_lock = True
            except FileExistsError:
                got_lock = False

            if not got_lock:
                print(f"{LOG_PREFIX}[LOCK]{media} (held elsewhere) — skipping", flush=True)
                continue

            print(f"{LOG_PREFIX}[RUN ]{media}", flush=True)

            total = probe_duration_seconds(media) or 0.0
            print(f"{LOG_PREFIX}[INFO]{media} duration={total:.1f}s", flush=True)

            # Build transcribe kwargs
            wkwargs = dict(
                language=LANG,
                verbose=True,  # Show progress
                initial_prompt=INITIAL_PROMPT,
                condition_on_previous_text=False,
                temperature=0.0,
                beam_size=1,
                word_timestamps=True,
                fp16=FP16,
                no_speech_threshold=NO_SPEECH_THRESHOLD,
                compression_ratio_threshold=COMPRESSION_RATIO_THRESHOLD,
                logprob_threshold=LOGPROB_THRESHOLD,
            )

            # Transcribe (with fallback for older whisper versions)
            try:
                res = model.transcribe(str(media), **wkwargs)
            except TypeError:
                # Retry without thresholds for older versions
                wkwargs.pop('compression_ratio_threshold', None)
                wkwargs.pop('logprob_threshold', None)
                wkwargs.pop('no_speech_threshold', None)
                print(f"{LOG_PREFIX}[WARN] Using older whisper version without threshold support", flush=True)
                res = model.transcribe(str(media), **wkwargs)

            segs = res.get("segments", [])

            # Write plain text
            with open(out_txt, "w", encoding="utf-8") as f:
                for s in segs:
                    if isinstance(s, dict):
                        t = (s.get("text", "") or "").strip()
                    else:
                        t = s.text.strip()
                    if t:
                        f.write(apply_corrections(t, CORR) + "\n")

            # Write words.tsv
            write_words_tsv_whisper(base, segs)

            # Write captions
            if do_vtt:
                write_vtt_amd(segs, out_vtt)
            if do_srt:
                write_srt_amd(segs, out_srt)

            # Mark success
            success_marker.touch()
            print(f"{LOG_PREFIX}[DONE]{media}", flush=True)

        except Exception as e:
            print(f"{LOG_PREFIX}[FAIL]{media}: {e}", flush=True)
            try:
                success_marker.unlink()
            except:
                pass
        finally:
            try:
                os.unlink(str(lock))
            except:
                pass
            try:
                task_file.replace(DONE / task_file.name)
            except:
                pass


if __name__ == "__main__":
    main()
