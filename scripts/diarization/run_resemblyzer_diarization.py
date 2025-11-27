#!/usr/bin/env python3
"""
Resemblyzer-based diarization (reference-guided) using chunked audio, similar to
filter_voice_parallel_chunked.py flow.

Inputs:
  - Audio in pull/ (or --audio)
  - Words TSV in generated/ (or --words)
  - Reference clips + reference.json in generated/diary_reference/<ytid>/

Outputs (by default):
  generated/diarization_resemblyzer/<ytid>/diarized_timestamps.tsv
  generated/diarization_resemblyzer/<ytid>/speaker_words.tsv
  generated/diarization_resemblyzer/<ytid>/diarization.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
import concurrent.futures
from multiprocessing import get_context
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
import librosa
from resemblyzer import VoiceEncoder, preprocess_wav

# Local build_reference copy (kept separate per request)
try:
    from build_reference import build_reference as build_reference_interactive  # type: ignore
except Exception:
    build_reference_interactive = None

MEDIA_EXTS = [
    ".opus",
    ".m4a",
    ".mp3",
    ".wav",
    ".flac",
    ".aac",
    ".ogg",
    ".webm",
    ".mp4",
    ".mkv",
]


@dataclass
class Word:
    start: float
    end: float
    word: str
    seg: str
    confidence: str
    retried: str


def read_words(path: Path) -> List[Word]:
    words: List[Word] = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            try:
                words.append(
                    Word(
                        start=float(row["start"]),
                        end=float(row["end"]),
                        word=row.get("word", ""),
                        seg=row.get("seg", ""),
                        confidence=row.get("confidence", ""),
                        retried=row.get("retried", ""),
                    )
                )
            except Exception:
                continue
    return words


def ffprobe_duration(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    out = subprocess.check_output(cmd, text=True).strip()
    return float(out)


def chunk_audio(path: Path, chunk_sec: float, overlap_sec: float):
    """Yield (start, end, tmp_path) for each chunk created via ffmpeg."""
    total = ffprobe_duration(path)
    t = 0.0
    while t < total:
        start = t
        end = min(total, t + chunk_sec)
        dur = end - start
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-ss",
            f"{start}",
            "-t",
            f"{dur}",
            "-i",
            str(path),
            "-ar",
            "16000",
            "-ac",
            "1",
            str(tmp_path),
        ]
        subprocess.check_call(cmd)
        yield start, end, tmp_path
        t += chunk_sec - overlap_sec


def load_reference_embeddings(ref_dir: Path, encoder: VoiceEncoder, device: str, verbose: bool = False):
    ref_json = ref_dir / "reference.json"
    if not ref_json.exists():
        raise FileNotFoundError(f"reference.json not found in {ref_dir}")
    data = json.loads(ref_json.read_text())
    speakers: Dict[str, np.ndarray] = {}

    def _add_speaker(name: str, clip_list):
        embeds = []
        for clip_rel in clip_list:
            clip_path = Path(clip_rel)
            if not clip_path.is_absolute():
                clip_path = (ref_dir / clip_path).resolve()
            if not clip_path.exists():
                if verbose:
                    print(f"[warn] missing clip {clip_path}")
                continue
            wav = preprocess_wav(str(clip_path))
            # embed_utterance runs on the encoder's device; no per-call device arg
            emb = encoder.embed_utterance(wav)
            embeds.append(emb)
        if embeds:
            speakers[name] = np.mean(embeds, axis=0)

    # New format: speakers: [{name, clips:[...]}]
    if data.get("speakers"):
        for sp in data["speakers"]:
            _add_speaker(sp.get("name", "unknown"), sp.get("clips", []))
    else:
        # Legacy format: speaker_name + clips_selected/clips_generated
        name = data.get("speaker_name", "speaker")
        clips = data.get("clips_selected") or data.get("clips_generated") or []
        _add_speaker(name, clips)

    if not speakers:
        raise RuntimeError("No reference embeddings could be loaded.")
    return speakers


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def diarize_chunks(audio_path: Path, encoder: VoiceEncoder, speakers: Dict[str, np.ndarray], chunk_sec: float, overlap_sec: float, device: str, threshold: float, verbose: bool = False, ytid: str | None = None):
    segments = []
    for start, end, tmp_path in chunk_audio(audio_path, chunk_sec, overlap_sec):
        try:
            # Manual load to avoid preprocess_wav NaNs on silent audio
            wav, sr = librosa.load(str(tmp_path), sr=16000, mono=True)
            rms = float(np.sqrt(np.mean(np.square(wav)))) if wav.size else 0.0
            if rms <= 1e-6 or not np.isfinite(rms):
                if verbose:
                    tag = f"{ytid} " if ytid else ""
                    print(f"[chunk] {tag}{start:.2f}-{end:.2f}s -> UNKNOWN (silent)")
                segments.append(("UNKNOWN", start, end, -1.0))
                continue
            emb = encoder.embed_utterance(wav)
            best_spk = "UNKNOWN"
            best_sim = -1.0
            for name, ref_emb in speakers.items():
                sim = cosine_sim(emb, ref_emb)
                if sim > best_sim:
                    best_sim = sim
                    best_spk = name
            if best_sim < threshold:
                best_spk = "UNKNOWN"
            segments.append((best_spk, start, end, best_sim))
            if verbose:
                tag = f"{ytid} " if ytid else ""
                print(f"[chunk] {tag}{start:.2f}-{end:.2f}s -> {best_spk} (sim {best_sim:.3f})")
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return merge_segments(segments)


def merge_segments(segments):
    """Merge adjacent segments with same speaker."""
    if not segments:
        return []
    segments = sorted(segments, key=lambda x: x[1])
    merged = [segments[0][:3]]  # (spk, start, end)
    for spk, s, e, _sim in segments[1:]:
        last_spk, last_s, last_e = merged[-1]
        if spk == last_spk and s <= last_e + 0.05:
            merged[-1] = (spk, last_s, max(last_e, e))
        else:
            merged.append((spk, s, e))
    return merged


def assign_speakers(words: List[Word], segments: List[Tuple[str, float, float]], gap_threshold: float):
    out = []
    for w in words:
        best_spk = "UNKNOWN"
        best_overlap = 0.0
        for spk, s, e in segments:
            if e + gap_threshold < w.start:
                continue
            if s - gap_threshold > w.end:
                break
            overlap = min(w.end, e) - max(w.start, s)
            if overlap > best_overlap:
                best_overlap = overlap
                best_spk = spk
        out.append((w, best_spk))
    return out


def write_outputs(root: Path, ytid: str, audio_path: Path, segments, words_with_spk, chunk_seconds: float, out_subdir: str = "diarization_resemblyzer"):
    out_dir = root / "generated" / out_subdir / ytid
    out_dir.mkdir(parents=True, exist_ok=True)

    dt_path = out_dir / "diarized_timestamps.tsv"
    with dt_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["ytid", "speaker_name", "start_sec", "end_sec", "duration_sec", "source_audio"])
        for spk, s, e in segments:
            writer.writerow([ytid, spk, f"{s:.3f}", f"{e:.3f}", f"{(e - s):.3f}", str(audio_path)])

    sw_path = out_dir / "speaker_words.tsv"
    with sw_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["start", "end", "word", "seg", "confidence", "retried", "speaker"])
        for w, spk in words_with_spk:
            writer.writerow([w.start, w.end, w.word, w.seg, w.confidence, w.retried, spk])

    meta = {
        "ytid": ytid,
        "source_audio": str(audio_path),
        "segments": len(segments),
        "words": len(words_with_spk),
        "chunk_seconds": chunk_seconds,
    }
    meta_path = out_dir / "diarization.json"
    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    return {"timestamps": dt_path, "speaker_words": sw_path, "metadata": meta_path}


def run_single(
    ytid: str,
    audio_path: Optional[Path],
    words_override: Optional[Path],
    root: Path,
    speakers: Dict[str, np.ndarray],
    encoder_device: str,
    chunk_seconds: float,
    overlap_seconds: float,
    gap_threshold: float,
    similarity_threshold: float,
    verbose: bool = False,
):
    if not audio_path:
        return (ytid, f"Audio missing for {ytid}")
    words_path = words_override or find_words(root, ytid)
    if not words_path:
        return (ytid, f"Words missing for {ytid}")
    if verbose:
        print(f"[info] processing {ytid} audio={audio_path} words={words_path}")
    # Each worker builds its own encoder to avoid pickling issues
    encoder = VoiceEncoder().to(encoder_device)
    segments = diarize_chunks(
        audio_path,
        encoder,
        speakers,
        chunk_sec=chunk_seconds,
        overlap_sec=overlap_seconds,
        device=encoder_device,
        threshold=similarity_threshold,
        verbose=verbose,
        ytid=ytid,
    )
    words = read_words(words_path)
    words_with_spk = assign_speakers(words, segments, gap_threshold)
    paths = write_outputs(root, ytid, audio_path, segments, words_with_spk, chunk_seconds=chunk_seconds)
    return (ytid, paths)


def process_one(
    ytid: str,
    audio_map: Dict[str, Path],
    words_override: Optional[Path],
    root: Path,
    speakers: Dict[str, np.ndarray],
    device: str,
    chunk_seconds: float,
    overlap_seconds: float,
    gap_threshold: float,
    similarity_threshold: float,
    verbose: bool,
):
    return run_single(
        ytid=ytid,
        audio_path=audio_map.get(ytid),
        words_override=words_override,
        root=root,
        speakers=speakers,
        encoder_device=device,
        chunk_seconds=chunk_seconds,
        overlap_seconds=overlap_seconds,
        gap_threshold=gap_threshold,
        similarity_threshold=similarity_threshold,
        verbose=verbose,
    )


def find_audio(root: Path, ytid: str) -> Optional[Path]:
    audio_dir = root / "pull"
    if not audio_dir.exists():
        return None
    for ext in MEDIA_EXTS:
        for cand in sorted(audio_dir.glob(f"{ytid}__*{ext}")) + sorted(audio_dir.glob(f"{ytid}{ext}")):
            return cand
    return None


def find_words(root: Path, ytid: str) -> Optional[Path]:
    gen_dir = root / "generated"
    if not gen_dir.exists():
        return None
    for pat in [f"{ytid}__*.words.tsv", f"{ytid}*.words.tsv"]:
        candidates = sorted(gen_dir.glob(pat))
        if candidates:
            return candidates[0]
    return None


def ytids_from_dir(path: Path) -> List[str]:
    """Return unique ytids derived from media filenames in a directory."""
    if not path.is_dir():
        return []
    seen = set()
    out: List[str] = []
    for entry in sorted(path.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in MEDIA_EXTS:
            continue
        m = re.match(r"([A-Za-z0-9_-]{11})__", entry.name)
        if not m:
            m = re.match(r"([A-Za-z0-9_-]{11})(?:\.|$)", entry.name)
        if not m:
            continue
        ytid = m.group(1)
        if ytid in seen:
            continue
        seen.add(ytid)
        out.append(ytid)
    return out


def build_reference_batch(project_root: Path, ytids: List[str], audio_paths: List[Path], n_clips: int = 10, clip_duration: float = 6.0):
    """Build reference clips across multiple files: pick random file+segment per clip."""
    ref_dir = project_root / "generated" / "diary_reference" / ytids[0]
    clips_dir = ref_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    durations = [ffprobe_duration(p) for p in audio_paths]
    generated = []
    for idx in range(n_clips):
        sel = random.randrange(len(audio_paths))
        dur = durations[sel]
        start = random.uniform(0, max(0.0, dur - clip_duration))
        end = min(dur, start + clip_duration)
        clip_path = clips_dir / f"{idx + 1}_{ytids[sel]}.wav"
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start}",
            "-to",
            f"{end}",
            "-i",
            str(audio_paths[sel]),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-vn",
            "-y",
            str(clip_path),
        ]
        subprocess.run(cmd, check=True)
        generated.append((clip_path, ytids[sel], start, end))

    print("\nReference clips generated (batch):")
    for p, y, s, e in generated:
        print(f"  [{p.stem}] {p} (ytid={y} {s:.1f}-{e:.1f}s)")
    speaker_name = input("Enter speaker name for this reference: ").strip() or "speaker"
    chosen_str = input(f"Enter clip numbers (comma or space separated) that belong to '{speaker_name}' (blank for none): ").strip()
    numbers = {c.strip() for c in re.split(r"[\\s,]+", chosen_str) if c.strip()} if chosen_str else set()
    chosen = [p for p, _y, _s, _e in generated if p.stem.split("_")[0] in numbers or p.stem in numbers]

    speakers = []
    if chosen:
        speakers.append({"name": speaker_name, "clips": [str(p.relative_to(ref_dir)) for p in chosen if p.exists()]})
    elif generated:
        speakers.append({"name": speaker_name, "clips": [str(p.relative_to(ref_dir)) for p, _y, _s, _e in generated if p.exists()]})

    meta = {
        "ytids": ytids,
        "speaker_name": speaker_name,
        "audio_sources": [str(p) for p in audio_paths],
        "clips_generated": [str(p) for p, _y, _s, _e in generated],
        "clips_selected": [str(p) for p in chosen],
        "speakers": speakers,
    }
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "reference.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"\nReference saved to {ref_dir}")


def main():
    parser = argparse.ArgumentParser(description="Reference-guided diarization using Resemblyzer (chunked).")
    parser.add_argument("--ytid", help="YouTube ID (used to locate audio/words/reference)")
    parser.add_argument("--ytids-file", type=Path, help="File with one ytid per line for batch processing")
    parser.add_argument("--ytids-from-dir", type=Path, help="Directory of media files; derive ytids from filenames")
    parser.add_argument("--audio", type=Path, help="Override audio path")
    parser.add_argument("--words", type=Path, help="Override words TSV path")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Project root (default: cwd)")
    parser.add_argument("--chunk-seconds", type=float, default=6.0, help="Chunk length in seconds")
    parser.add_argument("--overlap-seconds", type=float, default=1.0, help="Chunk overlap in seconds")
    parser.add_argument("--similarity-threshold", type=float, default=0.6, help="Cosine similarity threshold to accept a speaker match")
    parser.add_argument("--gap-threshold", type=float, default=0.15, help="Tolerance when matching words to speaker spans (seconds)")
    parser.add_argument("--device", default="auto", help="Device for resemblyzer encoder: auto|cuda|cpu")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("--build-reference", action="store_true", help="Force building reference clips before diarization")
    parser.add_argument("--ref-clips", type=int, default=10, help="Number of random reference clips to generate")
    parser.add_argument("--ref-clip-duration", type=float, default=6.0, help="Length of each reference clip in seconds")
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers for batch mode")
    args = parser.parse_args()

    root = args.project_root
    ytids: List[str] = []
    if args.ytids_file:
        ytids = [line.strip() for line in args.ytids_file.read_text().splitlines() if line.strip()]
    if args.ytids_from_dir:
        ytids.extend(ytids_from_dir(args.ytids_from_dir))
    if args.ytid:
        ytids.append(args.ytid)
    if ytids:
        seen = set()
        ytids = [y for y in ytids if not (y in seen or seen.add(y))]
    if not ytids:
        raise SystemExit("Provide --ytid, --ytids-file, or --ytids-from-dir.")

    device = args.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"

    # Resolve audio paths (shared reference will be under first ytid)
    audio_paths: List[Path] = []
    if len(ytids) == 1 and args.audio:
        audio_paths.append(args.audio)
    else:
        for y in ytids:
            p = find_audio(root, y)
            if not p:
                raise SystemExit(f"Audio file not found for {y}; use --audio for single runs.")
            audio_paths.append(p)

    first_ytid = ytids[0]
    ref_dir = root / "generated" / "diary_reference" / first_ytid
    ref_json = ref_dir / "reference.json"
    if (not ref_json.exists()) and build_reference_interactive:
        if args.build_reference or input("No reference found. Build reference clips? [y/N]: ").strip().lower() == "y":
            if len(ytids) > 1:
                build_reference_batch(root, ytids, audio_paths, n_clips=args.ref_clips, clip_duration=args.ref_clip_duration)
            else:
                build_reference_interactive(root, first_ytid, audio_paths[0], n_clips=args.ref_clips, clip_duration=args.ref_clip_duration)
    if not ref_json.exists():
        raise SystemExit(f"Reference missing: {ref_json}")

    if args.verbose:
        print(f"[info] reference dir: {ref_dir}")
        print(f"[info] device: {device}")
        print(f"[info] chunk={args.chunk_seconds}s overlap={args.overlap_seconds}s threshold={args.similarity_threshold}")

    encoder = VoiceEncoder().to(device)
    speakers = load_reference_embeddings(ref_dir, encoder, device=device, verbose=args.verbose)
    if args.verbose:
        print(f"[info] loaded {len(speakers)} reference speakers: {list(speakers.keys())}")

    audio_map = {y: p for y, p in zip(ytids, audio_paths)}

    results = []
    if args.workers > 1 and len(ytids) > 1:
        ctx = get_context("spawn")
        with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx) as ex:
            futs = [
                ex.submit(
                    process_one,
                    y,
                    audio_map,
                    args.words,
                    root,
                    speakers,
                    device,
                    args.chunk_seconds,
                    args.overlap_seconds,
                    args.gap_threshold,
                    args.similarity_threshold,
                    args.verbose,
                )
                for y in ytids
            ]
            for fut in concurrent.futures.as_completed(futs):
                results.append(fut.result())
    else:
        for y in ytids:
            results.append(
                process_one(
                    y,
                    audio_map,
                    args.words,
                    root,
                    speakers,
                    device,
                    args.chunk_seconds,
                    args.overlap_seconds,
                    args.gap_threshold,
                    args.similarity_threshold,
                    args.verbose,
                )
            )

    for ytid, res in results:
        if isinstance(res, str):
            print(f"[error] {ytid}: {res}", file=sys.stderr)
        elif args.verbose:
            print(f"[info] {ytid} outputs: {res}")


if __name__ == "__main__":
    main()
