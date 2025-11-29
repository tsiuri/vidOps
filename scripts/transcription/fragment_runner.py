#!/usr/bin/env python3
"""
Reusable fragment transcription runner.

This module exposes a `run_fragmented_transcription` function that performs
single-process, chunked transcription using an already-loaded Whisper model.
It mirrors the previous fragmented_transcribe.py behavior but avoids the extra
model load by reusing the caller's model instance.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import sys
import shutil
from pathlib import Path
from typing import List, Optional, Set, Tuple

from transcribe_common import (
    ensure_src_json,
    build_initial_prompt,
    load_corrections,
    apply_corrections,
    write_vtt,
    write_srt,
    write_words_tsv_faster,
    write_retry_manifest,
    probe_duration_seconds,
    emit_progress,
)


MEDIA_EXTS = ["mp4", "mkv", "mov", "avi", "mp3", "wav", "m4a", "opus", "webm"]


@dataclass
class LightSegment:
    text: str
    start: float
    end: float
    avg_logprob: float
    no_speech_prob: float
    compression_ratio: float
    words: Optional[list]


def convert_segment(seg, clip_start: float, keep_start: float, keep_end: float) -> Optional[LightSegment]:
    g_start = clip_start + float(getattr(seg, "start", 0.0))
    g_end = clip_start + float(getattr(seg, "end", 0.0))
    if g_end <= keep_start or g_start >= keep_end:
        return None

    start = max(g_start, keep_start)
    end = min(g_end, keep_end)

    seg_words = getattr(seg, "words", None)
    words = []
    if seg_words:
        for w in seg_words:
            w_start = clip_start + float(getattr(w, "start", 0.0))
            w_end = clip_start + float(getattr(w, "end", 0.0))
            if w_end <= keep_start or w_start >= keep_end:
                continue
            words.append(
                type(
                    "Word",
                    (),
                    {
                        "word": getattr(w, "word", ""),
                        "start": max(w_start, keep_start),
                        "end": min(w_end, keep_end),
                    },
                )()
            )

    ls = LightSegment(
        text=getattr(seg, "text", "").strip(),
        start=start,
        end=end,
        avg_logprob=float(getattr(seg, "avg_logprob", 0.0)),
        no_speech_prob=float(getattr(seg, "no_speech_prob", 0.0)),
        compression_ratio=float(getattr(seg, "compression_ratio", 0.0)),
        words=words or None,
    )
    return ls


def transcribe_chunk(model, chunk_path: Path, clip_start: float, keep_start: float, keep_end: float, initial_prompt: Optional[str], vad_filter: bool, lang: Optional[str], log_prefix: str) -> Tuple[List[LightSegment], float]:
    next_mark = [0.10]
    last_end = 0.0

    kwargs_fast = dict(
        language=lang,
        vad_filter=vad_filter,
        initial_prompt=initial_prompt,
        condition_on_previous_text=False,
        temperature=0.0,
        beam_size=1,
        word_timestamps=True,
    )

    segments, _info = model.transcribe(str(chunk_path), **kwargs_fast)
    out: List[LightSegment] = []

    chunk_total = keep_end - keep_start
    for s in segments:
        ls = convert_segment(s, clip_start, keep_start, keep_end)
        if ls is None:
            continue
        out.append(ls)

        text = ls.text.strip()
        if text:
            print(f"{log_prefix}[TEXT] {text}", flush=True)

        last_end = max(last_end, ls.end - keep_start)
        emit_progress(f"{log_prefix}[PROG]", last_end, chunk_total, next_mark)

    return out, chunk_total


def run_fragmented_transcription(
    model,
    media: Path,
    model_tag: str,
    lang: Optional[str],
    force: bool,
    outfmt: str,
    chunk_len: float,
    overlap: float,
    tmp_root: Path,
    initial_prompt: Optional[str],
    corrections,
    log_prefix: str,
    confidence_threshold: float = 0.2,
    inline_retry: bool = False,
    min_ts_interval: int = 10,
    tag: Optional[str] = None,
):
    """Transcribe a single media file with chunking using an already loaded model."""
    # Prepare paths
    base = Path(os.environ.get("PROJECT_ROOT", ".")) / "generated" / media.stem
    txt_suffix = f".{tag}.txt" if tag else ".txt"
    vtt_suffix = f".{tag}.vtt" if tag else ".vtt"
    srt_suffix = f".{tag}.srt" if tag else ".srt"
    tslog_suffix = f".{tag}.tslog.txt" if tag else ".tslog.txt"

    out_txt = base.with_suffix(txt_suffix)
    do_vtt = outfmt in ("vtt", "both")
    do_srt = outfmt in ("srt", "both")
    out_vtt = base.with_suffix(vtt_suffix) if do_vtt else None
    out_srt = base.with_suffix(srt_suffix) if do_srt else None
    lock = base.with_suffix(".transcribing.lock")
    success_marker = base.with_suffix(".transcribed")

    if success_marker.exists() and not force:
        print(f"{log_prefix}[SKIP]{media} (already transcribed)", flush=True)
        return
    if out_txt.exists() and not force:
        print(f"{log_prefix}[SKIP]{media} (txt exists)", flush=True)
        return

    # Lock handling
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.close(fd)
    except FileExistsError:
        print(f"{log_prefix}[LOCK]{media} (held elsewhere) — skipping", flush=True)
        return

    media_base = media.with_suffix("")
    ensure_src_json(media, media_base)

    total = probe_duration_seconds(media)
    if not total or total <= 0:
        print(f"{log_prefix}[FAIL]{media} duration not found", flush=True)
        try:
            os.unlink(lock)
        except Exception:
            pass
        return

    print(f"{log_prefix}[RUN ]{media} duration={total:.1f}s", flush=True)
    plan = _chunk_plan(total, chunk_len, overlap)
    if not plan:
        print(f"{log_prefix}[WARN]{media} produced no chunk plan", flush=True)
        try:
            os.unlink(lock)
        except Exception:
            pass
        return

    all_segments: List[LightSegment] = []
    low_conf_indices: List[int] = []
    total_chunks = len(plan)

    tmp_root.mkdir(parents=True, exist_ok=True)
    file_tmp = tmp_root / media.stem
    file_tmp.mkdir(parents=True, exist_ok=True)

    for chunk_num, (idx, keep_start, keep_end, clip_start, clip_end) in enumerate(plan, 1):
        chunk_path = file_tmp / f"{media.stem}.chunk{idx:04d}.wav"
        if not _extract_chunk(media, chunk_path, clip_start, clip_end, log_prefix):
            continue

        print(f"{log_prefix}[{chunk_num} of {total_chunks}][CHUNK]{idx} {keep_start:.1f}-{keep_end:.1f}s (clip {clip_start:.1f}-{clip_end:.1f}s)", flush=True)
        segs, _chunk_total = transcribe_chunk(
            model, chunk_path, clip_start, keep_start, keep_end, initial_prompt, bool(os.environ.get("NV_VAD_FILTER", "1") == "1"), lang, log_prefix
        )

        for seg in segs:
            global_idx = len(all_segments)
            all_segments.append(seg)
            if seg.avg_logprob < confidence_threshold:
                low_conf_indices.append(global_idx)

        if not int(os.environ.get("KEEP_FRAGMENTS", "0")):
            try:
                chunk_path.unlink()
            except Exception:
                pass

    if not all_segments:
        print(f"{log_prefix}[FAIL]{media} produced no segments", flush=True)
        try:
            os.unlink(lock)
        except Exception:
            pass
        return

    retried_set: Set[int] = set()
    if low_conf_indices and inline_retry:
        retried_set = _inline_retry_segments(model, media, all_segments, low_conf_indices, lang, bool(os.environ.get("NV_VAD_FILTER", "1") == "1"), initial_prompt, log_prefix)
        low_conf_indices = [i for i, s in enumerate(all_segments) if s.avg_logprob < confidence_threshold]

    with open(out_txt, "w", encoding="utf-8") as f:
        for s in all_segments:
            t = s.text.strip()
            if t:
                f.write(apply_corrections(t, corrections) + "\n")

    write_words_tsv_faster(base, all_segments, retried_set, log_prefix)
    if tag:
        try:
            old_words = base.with_suffix(".words.tsv")
            new_words = base.with_suffix(f".{tag}.words.tsv")
            if old_words.exists():
                old_words.rename(new_words)
        except Exception as e:
            print(f"{log_prefix}[WARN] failed to retag words.tsv with model: {e}", flush=True)

    cap = None
    if do_vtt:
        write_vtt(all_segments, out_vtt, retried_set)
        cap = out_vtt
    if do_srt:
        write_srt(all_segments, out_srt)
        cap = cap or out_srt
    if cap:
        from transcribe_common import make_tslog  # reuse existing helper

        make_tslog(cap, base.with_suffix(tslog_suffix), min_ts_interval)

    if low_conf_indices and not inline_retry:
        write_retry_manifest(base, media, all_segments, low_conf_indices, confidence_threshold, log_prefix)

    success_marker.touch()
    print(f"{log_prefix}[DONE]{media}", flush=True)

    if not int(os.environ.get("KEEP_FRAGMENTS", "0")):
        try:
            shutil.rmtree(file_tmp, ignore_errors=True)
        except Exception:
            pass

    try:
        os.unlink(lock)
    except Exception:
        pass


def _extract_chunk(media: Path, chunk_path: Path, clip_start: float, clip_end: float, log_prefix: str) -> bool:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{clip_start}",
        "-to",
        f"{clip_end}",
        "-i",
        str(media),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(chunk_path),
    ]
    try:
        subprocess_run = __import__("subprocess").run
        subprocess_run(cmd, check=True)
        return True
    except __import__("subprocess").CalledProcessError as e:
        print(f"{log_prefix}[FAIL] ffmpeg chunk extraction failed for {media} ({clip_start:.1f}-{clip_end:.1f}s): {e}", flush=True)
        return False


def _chunk_plan(total_sec: float, chunk_len: float, overlap: float):
    plan = []
    t = 0.0
    idx = 0
    while t < total_sec:
        clip_start = t
        clip_end = min(total_sec, t + chunk_len)
        keep_start = t
        keep_end = min(total_sec, clip_end)
        plan.append((idx, keep_start, keep_end, clip_start, clip_end))
        t += chunk_len - overlap
        idx += 1
    return plan


def _inline_retry_segments(model, media: Path, segments: List[LightSegment], low_conf_indices: List[int], lang: Optional[str], vad_filter: bool, initial_prompt: Optional[str], log_prefix: str) -> Set[int]:
    retried: Set[int] = set()
    for idx in low_conf_indices:
        seg = segments[idx]
        clip_start = max(0.0, seg.start - 5)
        clip_end = seg.end + 5

        with __import__("tempfile").NamedTemporaryFile(delete=False, suffix=".wav") as tmp_clip:
            clip_path = Path(tmp_clip.name)
        try:
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{clip_start}",
                "-to",
                f"{clip_end}",
                "-i",
                str(media),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-y",
                str(clip_path),
            ]
            __import__("subprocess").run(cmd, check=True)

            kwargs_retry = dict(
                language=lang,
                vad_filter=vad_filter,
                initial_prompt=initial_prompt,
                condition_on_previous_text=True,
                temperature=0.2,
                beam_size=5,
                word_timestamps=True,
            )
            retry_segments, _retry_info = model.transcribe(str(clip_path), **kwargs_retry)
            if retry_segments:
                rs = retry_segments[0]
                start_time = clip_start
                new_words = None
                seg_words = getattr(rs, "words", None)
                if seg_words:
                    new_words = [
                        type(
                            "Word",
                            (),
                            {
                                "word": getattr(w, "word", ""),
                                "start": start_time + float(getattr(w, "start", 0.0)),
                                "end": start_time + float(getattr(w, "end", 0.0)),
                            },
                        )()
                        for w in seg_words
                    ]
                segments[idx] = LightSegment(
                    text=getattr(rs, "text", "").strip(),
                    start=start_time + float(getattr(rs, "start", 0.0)),
                    end=start_time + float(getattr(rs, "end", 0.0)),
                    avg_logprob=float(getattr(rs, "avg_logprob", 0.0)),
                    no_speech_prob=float(getattr(rs, "no_speech_prob", 0.0)),
                    compression_ratio=float(getattr(rs, "compression_ratio", 0.0)),
                    words=new_words or segments[idx].words,
                )
                retried.add(idx)
        except Exception as e:
            print(f"{log_prefix}[RETRY] segment {idx} failed: {e}", flush=True)
        finally:
            try:
                clip_path.unlink()
            except Exception:
                pass
    return retried
