#!/usr/bin/env python3
"""
transcribe_common.py - Shared utilities for transcription workers

This module contains all common functionality used by NVIDIA, CPU, and AMD
transcription workers. Extracted from dual_gpu_transcribe.sh to eliminate
code duplication and improve maintainability.

Usage:
    from transcribe_common import (
        ensure_src_json,
        build_initial_prompt,
        load_corrections,
        apply_corrections,
        write_vtt,
        write_srt,
        write_words_tsv_faster,
        write_retry_manifest,
        write_retry_manifest_amd,
        make_tslog,
        hms,
        probe_duration_seconds,
        emit_progress,
        get_output_base,
        claim_task,
    )
"""

import os
import re
import json
import subprocess
from pathlib import Path
from typing import Optional, List, Tuple, Set, Any

# ============================================================================
# Path Helpers
# ============================================================================

def get_output_base(media_path: Path) -> Path:
    """
    Convert media path to output base in generated/ directory.

    Args:
        media_path: Path to original media file (e.g., pull/video.mp4)

    Returns:
        Path to output base (e.g., generated/video) without extension

    Note:
        Reads PROJECT_ROOT from environment, defaults to current directory
    """
    project_root = Path(os.environ.get("PROJECT_ROOT", "."))
    output_dir = project_root / "generated"
    output_dir.mkdir(exist_ok=True)
    # Keep just the filename stem, drop pull/ prefix
    return output_dir / media_path.stem


# ============================================================================
# Provenance Tracking
# ============================================================================

def ensure_src_json(media_path: Path, base_path: Path) -> None:
    """
    Create .src.json sidecar file with video metadata for provenance tracking.

    Sources metadata in priority order:
    1. {base_path}.info.json (from yt-dlp)
    2. ffprobe purl tag (container metadata)
    3. Filename prefix (11-char YouTube ID followed by __)

    Args:
        media_path: Path to media file (used for fallback probing)
        base_path: Base path for output (e.g., pull/video without extension)

    Output:
        Creates {base_path}.src.json with fields:
            - platform: "YouTube"
            - id: Video ID (if detected)
            - url: Full URL (if detected)
            - title: Video title (from info.json)
            - uploader: Channel name (from info.json)
            - upload_date: YYYYMMDD format (from info.json)
            - duration: Seconds (from info.json)
            - base_offset: 0.0 (for clip offset tracking)

    Note:
        Only writes if .src.json doesn't already exist
    """
    src_path = base_path.with_suffix(".src.json")
    if src_path.exists():
        return

    info = None
    info_path = base_path.with_suffix(".info.json")
    if info_path.exists():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            info = None

    url = None
    vid = None
    title = None
    uploader = None
    upload_date = None
    duration = None
    platform = "YouTube"

    if info:
        url = info.get("webpage_url") or info.get("original_url")
        vid = info.get("id")
        title = info.get("title")
        uploader = info.get("uploader")
        upload_date = info.get("upload_date")
        duration = info.get("duration")
        platform = info.get("extractor_key") or platform
    else:
        # Fallback: purl tag
        try:
            out = subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_entries", "format_tags=purl",
                 "-of", "default=nk=1:nw=1", str(media_path)],
                text=True
            ).strip()
            if out:
                url = out
                m = re.search(r"v=([A-Za-z0-9_-]{11})", url)
                if m:
                    vid = m.group(1)
        except Exception:
            pass
        # Extra fallback: filename prefix "ID__"
        if not vid:
            bn = media_path.name
            m = re.match(r"^([A-Za-z0-9_-]{11})__", bn)
            if m:
                vid = m.group(1)
                url = url or f"https://www.youtube.com/watch?v={vid}"

    src = {
        "platform": platform,
        "id": vid,
        "url": url or (f"https://www.youtube.com/watch?v={vid}" if vid else None),
        "title": title,
        "uploader": uploader,
        "upload_date": upload_date,  # YYYYMMDD if present
        "duration": duration,         # seconds if present
        "base_offset": 0.0
    }
    src_path.write_text(json.dumps(src, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================================
# Hotwords & Corrections
# ============================================================================

def build_initial_prompt() -> Optional[str]:
    """
    Build comma-separated hotwords prompt from HOTWORDS_FILE.

    Reads hotwords file (one term per line), removes duplicates,
    and builds a comma-separated string for Whisper's initial_prompt.

    Environment:
        HOTWORDS_FILE: Path to hotwords file (optional)
        PROMPT_PREFIX: Prefix string to add before hotwords (optional)

    Returns:
        "{PROMPT_PREFIX} term1, term2, term3" or None if no hotwords

    Example hotwords file:
        Hasan Piker
        AOC
        Alexandria Ocasio-Cortez
        Bernie Sanders
    """
    hp = os.environ.get("HOTWORDS_FILE")
    pref = os.environ.get("PROMPT_PREFIX", "")
    if not hp or not os.path.isfile(hp):
        return None
    seen = set()
    terms = []
    with open(hp, encoding="utf-8", errors="ignore") as f:
        for line in f:
            t = line.strip()
            if t and t not in seen:
                seen.add(t)
                terms.append(t)
    if not terms:
        return None
    if pref:
        return f"{pref} " + ", ".join(terms)
    else:
        return ", ".join(terms)


def load_corrections() -> List[Tuple[str, str]]:
    """
    Load corrections from tab-separated TSV file.

    Environment:
        CORRECTIONS_TSV: Path to corrections file (optional)

    Returns:
        List of (miss, fix) tuples

    File format:
        miss<TAB>fix
        Hassan<TAB>Hasan
        Burnie<TAB>Bernie
        # comments ignored
    """
    path = os.environ.get("CORRECTIONS_TSV")
    if not path or not os.path.isfile(path):
        return []
    pairs = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "\t" not in line:
                continue
            miss, fix = line.split("\t", 1)
            miss = miss.strip()
            fix = fix.strip()
            if miss and fix:
                pairs.append((miss, fix))
    return pairs


def apply_corrections(text: str, pairs: List[Tuple[str, str]]) -> str:
    """
    Apply corrections to text (handles exact, lowercase, and title case).

    Args:
        text: Original text to correct
        pairs: List of (miss, fix) tuples from load_corrections()

    Returns:
        Corrected text

    Note:
        Replaces all case variations: exact, lowercase, title case
    """
    for miss, fix in pairs:
        text = text.replace(miss, fix).replace(miss.lower(), fix).replace(miss.title(), fix)
    return text


# ============================================================================
# Output Writers
# ============================================================================

def hms(t: float, sep: str = ".", ms: int = 3) -> str:
    """
    Format seconds as HH:MM:SS.mmm timestamp.

    Args:
        t: Time in seconds (float)
        sep: Separator between seconds and milliseconds ("." for VTT, "," for SRT)
        ms: Millisecond precision (number of digits, default 3)

    Returns:
        Formatted timestamp string (e.g., "00:12:34.567")
    """
    t = max(0, float(t))
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    msv = int(round((t - int(t)) * 10**ms))
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{msv:03d}"


def write_vtt(segs: List[Any], path: Path, retried_indices: Optional[Set[int]] = None) -> None:
    """
    Write WebVTT subtitle file with confidence notes.

    Args:
        segs: List of segment objects from Whisper (must have .text, .start, .end, .avg_logprob)
        path: Output VTT file path
        retried_indices: Set of segment indices that were retried (adds [RETRIED] marker)

    Output format:
        WEBVTT

        NOTE Confidence: -0.234

        00:00:00.000 --> 00:00:03.480
        Hello and welcome.

        NOTE Confidence: -0.156 [RETRIED]

        00:00:03.520 --> 00:00:07.880
        Today we're discussing important topics.
    """
    retried_indices = retried_indices or set()
    with open(path, "w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n")
        for i, s in enumerate(segs):
            txt = s.text.strip()
            if not txt:
                continue
            conf = float(getattr(s, "avg_logprob", 0.0))
            if i in retried_indices:
                f.write(f"NOTE Confidence: {conf:.3f} [RETRIED]\n\n")
            else:
                f.write(f"NOTE Confidence: {conf:.3f}\n\n")
            f.write(f"{hms(s.start)} --> {hms(s.end)}\n{txt}\n\n")


def write_srt(segs: List[Any], path: Path) -> None:
    """
    Write SubRip (SRT) subtitle file.

    Args:
        segs: List of segment objects from Whisper
        path: Output SRT file path

    Output format:
        1
        00:00:00,000 --> 00:00:03,480
        Hello and welcome.

        2
        00:00:03,520 --> 00:00:07,880
        Today we're discussing important topics.
    """
    with open(path, "w", encoding="utf-8") as f:
        for i, s in enumerate(segs, 1):
            txt = s.text.strip()
            if not txt:
                continue
            a = hms(s.start, sep=",")
            b = hms(s.end, sep=",")
            f.write(f"{i}\n{a} --> {b}\n{txt}\n\n")


def write_words_tsv_faster(
    base_path: Path,
    segs: List[Any],
    retried_indices: Optional[Set[int]] = None,
    log_prefix: str = ""
) -> None:
    """
    Write per-word timestamps to TSV file (faster-whisper format).

    Args:
        base_path: Base output path (e.g., generated/video)
        segs: List of segment objects with .words attribute
        retried_indices: Set of retried segment indices
        log_prefix: Prefix for log messages (e.g., "[NV0]")

    Output:
        {base_path}.words.tsv with columns:
            start, end, word, seg, confidence, retried

    Example:
        start	end	word	seg	confidence	retried
        0.000	0.480	hello	0	-0.234	0
        0.520	0.880	world	0	-0.234	0
    """
    retried_indices = retried_indices or set()
    words_tsv = base_path.with_suffix(".words.tsv")
    try:
        with open(words_tsv, "w", encoding="utf-8") as wf:
            wf.write("start\tend\tword\tseg\tconfidence\tretried\n")
            for si, s in enumerate(segs):
                ws = getattr(s, "words", None)
                if not ws:
                    continue
                conf = float(getattr(s, "avg_logprob", 0.0))
                retried = 1 if si in retried_indices else 0
                for w in ws:
                    word = (getattr(w, "word", "") or "").strip()
                    if not word:
                        continue
                    wf.write(
                        f"{float(getattr(w, 'start', 0.0)):.3f}\t"
                        f"{float(getattr(w, 'end', 0.0)):.3f}\t"
                        f"{word}\t{si}\t{conf:.3f}\t{retried}\n"
                    )
    except Exception as e:
        if log_prefix:
            print(f"{log_prefix}[WARN] failed to write words.tsv: {e}", flush=True)


def make_tslog(caption: Path, tslog: Path, interval: int) -> None:
    """
    Create sparse timestamped log from VTT file for quick scanning.

    Args:
        caption: Input VTT file path
        tslog: Output tslog file path
        interval: Minimum seconds between timestamped entries

    Output format:
        [00:00:00] First line of text at time 0
        [00:00:10] Line at 10+ seconds
        [00:00:20] Line at 20+ seconds

    Purpose:
        Allows quick scanning of long transcripts by showing text at
        regular time intervals (e.g., every 10 seconds)
    """
    rx = re.compile(r'^(\d\d):(\d\d):(\d\d)')
    last = -1e9
    start = None
    with open(caption, encoding="utf-8", errors="ignore") as i, \
         open(tslog, "w", encoding="utf-8") as o:
        for line in i:
            if "-->" in line and rx.match(line):
                h, m, s = map(int, line[:8].split(":"))
                start = h * 3600 + m * 60 + s
                continue
            if not line.strip():
                continue
            if start is None:
                continue
            if start - last >= interval:
                o.write(f"[{start//3600:02d}:{(start%3600)//60:02d}:{start%60:02d}] {line}")
                last = start
            else:
                o.write(line)


# ============================================================================
# Retry Manifests
# ============================================================================

def write_retry_manifest(
    base_path: Path,
    media_path: Path,
    segs: List[Any],
    low_conf_indices: List[int],
    threshold: float,
    log_prefix: str = ""
) -> None:
    """
    Write retry manifest for low-confidence segments (faster-whisper format).

    Args:
        base_path: Base output path (e.g., generated/video)
        media_path: Original media file path
        segs: All segments
        low_conf_indices: Indices of low-confidence segments
        threshold: Confidence threshold used (for documentation)
        log_prefix: Prefix for log messages (e.g., "[NV0]")

    Output:
        {base_path}.retry_manifest.tsv with columns:
            media_file, segment_idx, start_time, end_time, confidence, zero_length, text

    Note:
        Zero-length segments (end <= start) are expanded to 1-second windows
        Used by batch_retry.sh for post-processing
    """
    manifest_path = base_path.with_suffix(".retry_manifest.tsv")
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("media_file\tsegment_idx\tstart_time\tend_time\tconfidence\tzero_length\ttext\n")
        for idx in low_conf_indices:
            seg = segs[idx]
            start = float(getattr(seg, "start", 0.0))
            end = float(getattr(seg, "end", 0.0))
            conf = float(getattr(seg, "avg_logprob", 0.0))
            text = (getattr(seg, "text", "") or "").strip()

            # Mark and expand zero-length segments
            zero_length = 1 if end <= start else 0
            if zero_length:
                end = start + 1.0  # give a 1-second span

            text = text.replace("\t", " ").replace("\n", " ")
            f.write(
                f"{media_path}\t{idx}\t{start:.3f}\t{end:.3f}\t{conf:.3f}\t{zero_length}\t{text}\n"
            )
    if log_prefix:
        print(
            f"{log_prefix}[MANIFEST] wrote retry manifest for {len(low_conf_indices)} "
            f"segments to {manifest_path.name}",
            flush=True
        )


def write_retry_manifest_amd(
    base_path: Path,
    media_path: Path,
    segs: List[Any],
    low_conf_indices: List[int],
    threshold: float
) -> None:
    """
    Write retry manifest for AMD worker (handles both dict and object segments).

    This is a variant of write_retry_manifest that handles OpenAI Whisper's
    segment format which can be either dict or object.

    Args:
        base_path: Base output path
        media_path: Original media file path
        segs: All segments (dict or object format)
        low_conf_indices: Indices of low-confidence segments
        threshold: Confidence threshold (for documentation)

    Output:
        Same format as write_retry_manifest()
    """
    manifest_path = base_path.with_suffix(".retry_manifest.tsv")
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("media_file\tsegment_idx\tstart_time\tend_time\tconfidence\tzero_length\ttext\n")
        for idx in low_conf_indices:
            seg = segs[idx]
            # Handle both dict and object formats
            if isinstance(seg, dict):
                start = float(seg.get("start", 0.0))
                end = float(seg.get("end", 0.0))
                conf = float(seg.get("avg_logprob", 0.0))
                text = (seg.get("text", "") or "").strip()
            else:
                start = float(getattr(seg, "start", 0.0))
                end = float(getattr(seg, "end", 0.0))
                conf = float(getattr(seg, "avg_logprob", 0.0))
                text = (getattr(seg, "text", "") or "").strip()

            # Mark and expand zero-length spans
            zero_length = 1 if end <= start else 0
            if zero_length:
                end = start + 1.0  # 1-second window for retry

            text = text.replace("\t", " ").replace("\n", " ")
            f.write(
                f"{media_path}\t{idx}\t{start:.3f}\t{end:.3f}\t{conf:.3f}\t{zero_length}\t{text}\n"
            )
    print(
        f"[AMD][MANIFEST] wrote retry manifest for {len(low_conf_indices)} "
        f"segments to {manifest_path.name}",
        flush=True
    )


# ============================================================================
# Progress & Probing
# ============================================================================

def probe_duration_seconds(path: Path) -> Optional[float]:
    """
    Get media duration in seconds using ffprobe.

    Args:
        path: Path to media file

    Returns:
        Duration in seconds (float) or None on error
    """
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nk=1:nw=1", str(path)],
            text=True
        ).strip()
        return float(out) if out else None
    except Exception:
        return None


def emit_progress(prefix: str, done_sec: float, total_sec: float, next_mark: List[float]) -> None:
    """
    Print percent progress at 10% increments.

    Args:
        prefix: Log prefix (e.g., "[NV0][PROG]")
        done_sec: Seconds processed so far
        total_sec: Total duration in seconds
        next_mark: List with single float (next threshold to print, e.g., [0.10])
                   Modified in place as thresholds are crossed

    Output:
        Prints lines like:
        [NV0][PROG][ 10%] 123.4s / 1234.5s
        [NV0][PROG][ 20%] 246.8s / 1234.5s
        ...

    Note:
        next_mark is a list (mutable) so it can be updated across calls
    """
    if not total_sec or total_sec <= 0:
        return
    frac = max(0.0, min(1.0, done_sec / total_sec))
    while frac + 1e-9 >= next_mark[0] and next_mark[0] < 1.0:
        pct = int(next_mark[0] * 100)
        print(f"{prefix}[{pct:3d}%] {done_sec:,.1f}s / {total_sec:,.1f}s", flush=True)
        next_mark[0] += 0.10


# ============================================================================
# Queue Operations
# ============================================================================

def claim_task(
    queue_dir: Path,
    pending_dir: str = "pending",
    inprogress_dir: str = "inprogress",
    backend: str = "nvidia"
) -> Optional[Tuple[Path, Path]]:
    """
    Claim next task from queue (atomic operation).

    Args:
        queue_dir: Queue base directory (e.g., ./dualq.XXXXXX)
        pending_dir: Name of pending subdirectory (default: "pending")
        inprogress_dir: Name of inprogress subdirectory (default: "inprogress")
        backend: "nvidia" | "cpu" | "amd" (affects file size preference)

    Returns:
        (task_file_path, media_file_path) tuple or None if queue empty

    Behavior:
        - Lists all task files in pending/
        - Sorts by backend preference:
            nvidia: Large files first (better GPU utilization)
            cpu: Small files first (faster turnaround)
            amd: FIFO (no size preference)
        - Atomically moves pending/task.N → inprogress/task.N
        - Returns first successful claim
        - Returns None if queue empty

    Thread Safety:
        Uses atomic file rename (POSIX guarantees)
        Safe for concurrent workers claiming tasks
    """
    import random

    PENDING = queue_dir / pending_dir
    INPROG = queue_dir / inprogress_dir

    try:
        tasks = []
        for entry in PENDING.iterdir():
            if not entry.is_file():
                continue
            try:
                media_path = Path(entry.read_text(encoding="utf-8", errors="ignore"))
                if media_path.exists():
                    size = media_path.stat().st_size
                else:
                    size = 0  # missing file = deprioritize

                # Add small random offset to break ties
                sort_key = size + random.randint(0, max(1, size // 1000))
                tasks.append((sort_key, entry, media_path))
            except Exception:
                continue

        if not tasks:
            return None

        # Sort by backend preference
        if backend == "nvidia":
            # NVIDIA: prefer LARGE files (better GPU utilization once started)
            tasks.sort(key=lambda x: x[0], reverse=True)
        elif backend == "cpu":
            # CPU: prefer SMALL files (faster turnaround)
            tasks.sort(key=lambda x: x[0], reverse=False)
        else:
            # AMD/other: FIFO (no preference, keep discovery order)
            pass

        # Try to claim first available task (atomic rename)
        for _, entry, media_path in tasks:
            dest = INPROG / entry.name
            try:
                entry.replace(dest)  # Atomic on POSIX
                return dest, media_path
            except (FileNotFoundError, PermissionError, OSError):
                # Already claimed by another worker, try next
                continue

    except FileNotFoundError:
        # Queue directory was cleaned up
        return None

    return None
