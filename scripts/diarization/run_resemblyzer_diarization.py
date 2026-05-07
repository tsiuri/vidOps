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
import shutil
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

OUT_SUBDIR = "diarization_resemblyzer"
COMPLETE_MARKER = "diarization.completed"
DEFAULT_DB_HOST = "192.168.0.187"
DEFAULT_DB_PORT = 5432
DEFAULT_DB_NAME = "transcripts"
DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Tweaks for better diarization defaults
DEFAULT_CHUNK_SECONDS = 12.0  # was 6.0
DEFAULT_OVERLAP_SECONDS = 2.0  # was 1.0
DEFAULT_REF_CLIPS = 50  # was 10
DEFAULT_REF_CLIP_DURATION = 8.0  # was 6.0


@dataclass
class Word:
    start: float
    end: float
    word: str
    seg: str
    confidence: str
    retried: str


def _require_psycopg2():
    try:
        import psycopg2  # type: ignore
    except ImportError as e:  # pragma: no cover - optional dep
        raise SystemExit(
            "psycopg2 is required for DB diarization (--use-db-words/--write-db). "
            "Install with: pip install psycopg2-binary"
        ) from e
    return psycopg2


def load_db_config() -> Dict[str, object]:
    """Load DB connection settings from `config.yaml` via configuration.load_config().

    Returns a dict using the historical `db_*` key shape so the call site below
    keeps working unchanged. The legacy `db.cfg` JSON file is no longer read;
    `paths.path_prefix` in config.yaml plays the role of the old `db_path_prefix`.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        from configuration import load_config  # type: ignore
    except Exception:
        return {}
    cfg = load_config()
    return {
        "db_host": cfg.database.host,
        "db_port": cfg.database.port,
        "db_name": cfg.database.name,
        "db_user": cfg.database.user,
        "db_password": cfg.database.password,
        "db_path_prefix": cfg.paths.path_prefix or None,
    }


def resolve_db_path(path_str: str, prefix: Optional[str]) -> Path:
    if prefix:
        return Path(prefix) / path_str.lstrip(os.sep)
    return Path(path_str)


def fetch_transcript_paths(ytid: str, db_params: Dict[str, object], mount_prefix: Optional[str] = None) -> List[Path]:
    psycopg2 = _require_psycopg2()
    conn = psycopg2.connect(**db_params)
    out: List[Path] = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT path
                FROM transcripts
                WHERE ytid=%s AND path IS NOT NULL
                  AND lower(kind) IN ('words_whisper','whisper','words_ytt','yt','ytt','words')
                ORDER BY CASE WHEN lower(kind) IN ('words_whisper','whisper') THEN 0
                              WHEN lower(kind) IN ('words_ytt','yt','ytt') THEN 1
                              ELSE 2 END
                """,
                (ytid,),
            )
            for (path,) in cur.fetchall():
                if not path:
                    continue
                path_str = str(path)
                if mount_prefix is not None:
                    out.append(resolve_db_path(path_str, mount_prefix))
                out.append(Path(path_str))
    finally:
        conn.close()
    # De-duplicate while preserving order
    seen: set[Path] = set()
    uniq = []
    for p in out:
        if p in seen:
            continue
        seen.add(p)
        uniq.append(p)
    return uniq


def interactive_clip_selector(clips: List[Tuple[Path, str, float, float]], preselected: Optional[List[Path]] = None):
    """
    Minimal TUI to audition and select clips.
    Controls:
      Up/Down or j/k: navigate
      Space/Enter: play current clip (from start)
      a: toggle selection
      s: stop playback
      q: finish
    Playback stops automatically when you leave a clip.
    Returns a set of selected Paths or None on failure.
    """
    try:
        import curses
    except Exception:
        return None

    selected = set(preselected or [])
    idx = 0
    proc: Optional[subprocess.Popen] = None

    def stop_playback():
        nonlocal proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=0.5)
            except Exception:
                proc.kill()
        proc = None

    def play_clip(p: Path):
        nonlocal proc
        stop_playback()
        cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", str(p)]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            proc = None

    def draw(stdscr):
        nonlocal idx
        curses.curs_set(0)
        stdscr.nodelay(False)
        stdscr.keypad(True)
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            header = "Arrows/jk: move | Space/Enter: play | a: toggle | s: stop | q: done"
            stdscr.addnstr(0, 0, header, w - 1)
            for i, (clip_path, ytid, s, e) in enumerate(clips):
                prefix = ">" if i == idx else " "
                mark = "[x]" if clip_path in selected else "[ ]"
                line = f"{prefix} {mark} {i+1}. {clip_path.name} ({s:.1f}-{e:.1f}s) {clip_path}"
                if i + 1 < h:
                    stdscr.addnstr(i + 1, 0, line, w - 1)
            stdscr.refresh()
            ch = stdscr.getch()
            if ch in (curses.KEY_UP, ord("k")):
                stop_playback()
                idx = (idx - 1) % len(clips)
            elif ch in (curses.KEY_DOWN, ord("j")):
                stop_playback()
                idx = (idx + 1) % len(clips)
            elif ch in (curses.KEY_ENTER, 10, 13, ord(" ")):
                play_clip(clips[idx][0])
            elif ch == ord("s"):
                stop_playback()
            elif ch == ord("a"):
                clip_path = clips[idx][0]
                if clip_path in selected:
                    selected.remove(clip_path)
                else:
                    selected.add(clip_path)
            elif ch == ord("q"):
                stop_playback()
                break
        return selected

    try:
        curses.wrapper(draw)
    except KeyboardInterrupt:
        stop_playback()
        return set(selected)
    except Exception:
        try:
            stop_playback()
        except Exception:
            pass
        return None
    stop_playback()
    return selected


def choose_clips_and_speaker(generated: List[Tuple[Path, str, float, float]]):
    """Unified flow: pick clips (interactive or manual), then prompt for speaker name."""
    chosen: List[Path] = []
    speaker_name = "speaker"
    if sys.stdin.isatty():
        try:
            use_ui = input("Use interactive clip selector? [Y/n]: ").strip().lower()
        except EOFError:
            use_ui = "y"
        if use_ui in ("", "y", "yes"):
            ui_selected = interactive_clip_selector(generated)
            if ui_selected:
                chosen = [p for p, _y, _s, _e in generated if p in ui_selected]
    if not chosen:
        try:
            chosen_str = input("Enter clip numbers (comma or space separated) to keep (blank for none): ").strip()
        except EOFError:
            chosen_str = ""
        numbers = {c.strip() for c in re.split(r"[\\s,]+", chosen_str) if c.strip()} if chosen_str else set()
        chosen = [p for p, _y, _s, _e in generated if p.stem.split("_")[0] in numbers or p.stem in numbers]
    try:
        speaker_name = input("Enter speaker name for this reference: ").strip() or "speaker"
    except EOFError:
        speaker_name = "speaker"
    return speaker_name, chosen


def project_root_from_ref_dir(ref_dir: Path, fallback: Path) -> Path:
    # Expected structure: <project>/generated/diary_reference/<ytid>
    try:
        return ref_dir.parents[2]
    except Exception:
        return fallback


def build_db_params(dbname: str, host: str, port: int, user: Optional[str], password: Optional[str]):
    params = {"dbname": dbname, "host": host, "port": port}
    if user:
        params["user"] = user
    if password:
        params["password"] = password
    return params


def _source_priority(src: str) -> int:
    s = (src or "").lower()
    if s in ("whisper", "words_whisper"):
        return 0
    if s in ("yt", "ytt", "words_ytt"):
        return 1
    return 2


def fetch_words_from_db(ytid: str, db_params: Dict[str, object], preferred_source: Optional[str] = None, verbose: bool = False, mount_prefix: Optional[str] = None):
    psycopg2 = _require_psycopg2()
    conn = psycopg2.connect(**db_params)
    chosen_source: Optional[str] = None
    try:
        with conn.cursor() as cur:
            # Try to load from transcripts.path using mount prefix first (faster than pulling all words rows)
            cur.execute(
                """
                SELECT kind, path
                FROM transcripts
                WHERE ytid=%s AND path IS NOT NULL
                  AND lower(kind) IN ('words_whisper','whisper','words_ytt','yt','ytt','words')
                ORDER BY CASE WHEN lower(kind) IN ('words_whisper','whisper') THEN 0
                              WHEN lower(kind) IN ('words_ytt','yt','ytt') THEN 1
                              ELSE 2 END
                """,
                (ytid,),
            )
            for kind, path in cur.fetchall():
                if not path:
                    continue
                path_str = str(path)
                candidates = []
                if mount_prefix is not None:
                    cand_prefixed = resolve_db_path(path_str, mount_prefix)
                    candidates.append(cand_prefixed)
                candidates.append(Path(path_str))
                seen_paths = set()
                for candidate in candidates:
                    if candidate in seen_paths:
                        continue
                    seen_paths.add(candidate)
                    if not candidate.exists():
                        if verbose:
                            print(f"[debug] USING DB WORDS; words path missing: {candidate}")
                        continue
                    try:
                        words = read_words(candidate)
                    except Exception:
                        if verbose:
                            print(f"[debug] USING DB WORDS; failed to read words at {candidate}")
                        continue
                    if words:
                        chosen_source = f"path:{candidate}"
                        if verbose:
                            print(f"[db] fetched {len(words)} words for {ytid} from {candidate}")
                        return words, chosen_source

            cur.execute("SELECT DISTINCT source FROM words WHERE ytid=%s", (ytid,))
            available_sources = [row[0] for row in cur.fetchall() if row and row[0]]

            candidates: List[Optional[str]] = []
            if preferred_source:
                candidates.append(preferred_source.lower())
            for src in sorted(available_sources, key=_source_priority):
                low = src.lower()
                if low not in candidates:
                    candidates.append(low)
            if not candidates:
                candidates.append(None)

            rows = []
            for src in candidates:
                chosen_source = src
                if src:
                    cur.execute(
                        """
                        SELECT start_sec, end_sec, word, COALESCE(segment_id, idx) AS seg, confidence
                        FROM words
                        WHERE ytid=%s AND lower(source)=%s
                        ORDER BY start_sec, idx
                        """,
                        (ytid, src),
                    )
                else:
                    cur.execute(
                        """
                        SELECT start_sec, end_sec, word, COALESCE(segment_id, idx) AS seg, confidence
                        FROM words
                        WHERE ytid=%s
                        ORDER BY start_sec, idx
                        """,
                        (ytid,),
                    )
                rows = cur.fetchall()
                if rows:
                    break

            words: List[Word] = []
            for start, end, word, seg, conf in rows:
                try:
                    words.append(
                        Word(
                            start=float(start) if start is not None else 0.0,
                            end=float(end) if end is not None else 0.0,
                            word=word or "",
                            seg=str(seg) if seg is not None else "",
                            confidence=str(conf) if conf is not None else "",
                            retried="0",
                        )
                    )
                except Exception:
                    continue
            if verbose:
                src_label = chosen_source or "any"
                print(f"[db] fetched {len(words)} words for {ytid} (source {src_label})")
            return words, chosen_source
    finally:
        conn.close()


def write_diarization_to_db(
    ytid: str,
    segments: List[Tuple[str, float, float]],
    source_path: Path,
    db_params: Dict[str, object],
    replace_existing: bool = True,
    verbose: bool = False,
):
    if not segments:
        return 0
    psycopg2 = _require_psycopg2()
    from psycopg2.extras import execute_values  # type: ignore

    conn = psycopg2.connect(**db_params)
    inserted = 0
    try:
        with conn:
            with conn.cursor() as cur:
                if replace_existing:
                    cur.execute("DELETE FROM diarized_timestamps WHERE ytid=%s", (ytid,))
                rows = [(ytid, spk, round(s, 3), round(e, 3), str(source_path)) for spk, s, e in segments]
                execute_values(
                    cur,
                    """
                    INSERT INTO diarized_timestamps
                      (ytid, speaker_name, start_sec, end_sec, source_path)
                    VALUES %s
                    ON CONFLICT (ytid, speaker_name, start_sec, end_sec) DO NOTHING
                    """,
                    rows,
                )
                inserted = len(rows)
        if verbose:
            action = "replaced" if replace_existing else "appended"
            print(f"[db] {ytid}: {action} {inserted} diarized spans in database")
    finally:
        conn.close()
    return inserted


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


def diarize_chunks(audio_path: Path, encoder: VoiceEncoder, speakers: Dict[str, np.ndarray], chunk_sec: float, overlap_sec: float, device: str, threshold: float, verbose: bool = False, ytid: str | None = None, worker_id: Optional[int] = None):
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
                wid = f"[w{worker_id}] " if worker_id is not None else ""
                print(f"[chunk] {wid}{tag}{start:.2f}-{end:.2f}s -> {best_spk} (sim {best_sim:.3f})")
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


def get_out_dir(root: Path, out_subdir: str, ytid: str, from_db: bool = False) -> Path:
    base = root / "generated"
    if from_db:
        base = base / "from-db"
    return base / out_subdir / ytid


def write_outputs(
    out_dir: Path,
    ytid: str,
    audio_path: Path,
    segments,
    words_with_spk,
    chunk_seconds: float,
    words_source: Optional[str] = None,
    words_from_db: bool = False,
):
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
    if words_source is not None:
        meta["words_source"] = words_source
    if words_from_db:
        meta["words_from_db"] = True
    meta_path = out_dir / "diarization.json"
    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    return {"timestamps": dt_path, "speaker_words": sw_path, "metadata": meta_path}


def run_single(
    ytid: str,
    audio_path: Optional[Path],
    words_override: Optional[Path],
    root: Path,
    out_dir: Path,
    speakers: Dict[str, np.ndarray],
    encoder_device: str,
    chunk_seconds: float,
    overlap_seconds: float,
    gap_threshold: float,
    similarity_threshold: float,
    verbose: bool = False,
    use_db_words: bool = False,
    db_params: Optional[Dict[str, object]] = None,
    db_source_preference: Optional[str] = None,
    db_path_prefix: Optional[str] = None,
    write_db: bool = False,
    db_append: bool = False,
    worker_id: Optional[int] = None,
):
    words: List[Word] = []
    words_source: Optional[str] = None
    if use_db_words:
        if not db_params:
            return (ytid, {"error": "db_missing", "audio": str(audio_path)})
        try:
            words, words_source = fetch_words_from_db(
                ytid,
                db_params=db_params,
                preferred_source=db_source_preference,
                verbose=verbose,
                mount_prefix=db_path_prefix,
            )
        except Exception as exc:
            return (ytid, f"DB words fetch failed: {exc}")
        if not words:
            return (ytid, {"error": "words_missing", "audio": str(audio_path)})
        if verbose:
            src = words_source or db_source_preference or "any"
            print(f"[info] processing {ytid} audio={audio_path} words=db:{src}")
        if not audio_path and words_source and words_source.startswith("path:"):
            candidate_path = Path(words_source.split("path:", 1)[1])
            audio_path = find_audio_from_words_path(candidate_path, ytid, verbose=verbose)
            if audio_path and verbose:
                print(f"[info] derived audio from words path: {audio_path}")
    else:
        words_path = words_override or find_words(root, ytid)
        if not words_path:
            return (ytid, {"error": "words_missing", "audio": str(audio_path)})
        if verbose:
            print(f"[info] processing {ytid} audio={audio_path} words={words_path}")
        words = read_words(words_path)
        words_source = str(words_path)
        if not audio_path:
            audio_path = find_audio_from_words_path(words_path, ytid, verbose=verbose)

    if not audio_path:
        if verbose:
            print(f"[error] audio missing for {ytid}; words_source={words_source} project_root={root}")
        return (ytid, f"Audio missing for {ytid}")
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
        worker_id=worker_id,
    )
    words_with_spk = assign_speakers(words, segments, gap_threshold)
    paths = write_outputs(
        out_dir,
        ytid,
        audio_path,
        segments,
        words_with_spk,
        chunk_seconds=chunk_seconds,
        words_source=words_source or db_source_preference,
        words_from_db=use_db_words,
    )
    if write_db and db_params:
        try:
            write_diarization_to_db(
                ytid,
                segments,
                paths["timestamps"],
                db_params,
                replace_existing=not db_append,
                verbose=verbose,
            )
        except Exception as exc:
            print(f"[warn] {ytid}: failed to write diarization to DB ({exc})", file=sys.stderr)
    marker = out_dir / COMPLETE_MARKER
    try:
        marker.write_text("ok\n", encoding="utf-8")
    except OSError:
        pass
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
    use_db_words: bool,
    db_params: Optional[Dict[str, object]],
    db_source_preference: Optional[str],
    db_path_prefix: Optional[str],
    write_db: bool,
    db_append: bool,
    worker_id: Optional[int] = None,
):
    out_dir = get_out_dir(root, OUT_SUBDIR, ytid, from_db=use_db_words)
    marker = out_dir / COMPLETE_MARKER
    if marker.exists():
        if verbose:
            print(f"[info] {ytid} skipped (complete marker found)")
        return (ytid, {"skipped": "complete_marker"})
    return run_single(
        ytid=ytid,
        audio_path=audio_map.get(ytid),
        words_override=words_override,
        root=root,
        out_dir=out_dir,
        speakers=speakers,
        encoder_device=device,
        chunk_seconds=chunk_seconds,
        overlap_seconds=overlap_seconds,
        gap_threshold=gap_threshold,
        similarity_threshold=similarity_threshold,
        verbose=verbose,
        use_db_words=use_db_words,
        db_params=db_params,
        db_source_preference=db_source_preference,
        db_path_prefix=db_path_prefix,
        write_db=write_db,
        db_append=db_append,
        worker_id=worker_id,
    )


def find_audio(root: Path, ytid: str) -> Optional[Path]:
    audio_dir = root / "pull"
    if audio_dir.exists():
        for ext in MEDIA_EXTS:
            for cand in sorted(audio_dir.glob(f"{ytid}__*{ext}")) + sorted(audio_dir.glob(f"{ytid}{ext}")):
                return cand
    return None


def find_audio_from_words_path(words_path: Path, ytid: str, verbose: bool = False) -> Optional[Path]:
    # Walk up until we find the project root that contains generated/
    for parent in words_path.parents:
        if parent.name == "generated":
            project_root = parent.parent
            if project_root.exists():
                audio = find_audio(project_root, ytid)
                if verbose:
                    print(f"[debug] derived project root from words path: {project_root}, audio={audio}")
                if audio:
                    return audio
    if verbose:
        print(f"[debug] could not derive audio from words path: {words_path}")
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
    speaker_name, chosen = choose_clips_and_speaker(generated)

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


def build_reference_single(project_root: Path, ytid: str, audio_path: Path, n_clips: int = 10, clip_duration: float = 6.0):
    if audio_path is None:
        raise SystemExit(f"Audio missing for {ytid}; cannot build reference.")
    ref_dir = project_root / "generated" / "diary_reference" / ytid
    clips_dir = ref_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    dur = ffprobe_duration(audio_path)
    generated = []
    for idx in range(n_clips):
        start = random.uniform(0, max(0.0, dur - clip_duration))
        end = min(dur, start + clip_duration)
        clip_path = clips_dir / f"{idx + 1}_{ytid}.wav"
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
            str(audio_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-vn",
            "-y",
            str(clip_path),
        ]
        subprocess.run(cmd, check=True)
        generated.append((clip_path, ytid, start, end))

    print("\nReference clips generated:")
    for p, y, s, e in generated:
        print(f"  [{p.stem}] {p} (ytid={y} {s:.1f}-{e:.1f}s)")
    speaker_name, chosen = choose_clips_and_speaker(generated)

    speakers = []
    if chosen:
        speakers.append({"name": speaker_name, "clips": [str(p.relative_to(ref_dir)) for p in chosen if p.exists()]})
    elif generated:
        speakers.append({"name": speaker_name, "clips": [str(p.relative_to(ref_dir)) for p, _y, _s, _e in generated if p.exists()]})

    meta = {
        "ytids": [ytid],
        "speaker_name": speaker_name,
        "audio_sources": [str(audio_path)],
        "clips_generated": [str(p) for p, _y, _s, _e in generated],
        "clips_selected": [str(p) for p in chosen],
        "speakers": speakers,
    }
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "reference.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"\nReference saved to {ref_dir}")


def main():
    parser = argparse.ArgumentParser(description="Reference-guided diarization using Resemblyzer (chunked).")
    parser.add_argument("--ytid", help="YouTube ID (comma-separated supported) used to locate audio/words/reference")
    parser.add_argument("--ytids-file", type=Path, help="File with one ytid per line for batch processing")
    parser.add_argument("--ytids-from-dir", type=Path, help="Directory of media files; derive ytids from filenames")
    parser.add_argument("--audio", type=Path, help="Override audio path")
    parser.add_argument("--words", type=Path, help="Override words TSV path")
    parser.add_argument("--use-db-words", action="store_true", help="Fetch words from Postgres instead of local TSVs")
    parser.add_argument("--db-words-source", help="Preferred words.source (whisper|yt) when pulling from DB")
    parser.add_argument("--db-name", default=None, help="Postgres database name (fallback: config.yaml database.name, DB_NAME, default transcripts)")
    parser.add_argument("--db-host", default=None, help="Postgres host (fallback: config.yaml database.host, DB_HOST, default 192.168.0.187)")
    parser.add_argument("--db-port", type=int, default=None, help="Postgres port (fallback: config.yaml database.port, DB_PORT, default 5432)")
    parser.add_argument("--db-user", default=None, help="Postgres user (fallback: config.yaml database.user or DB_USER)")
    parser.add_argument("--db-password", default=None, help="Postgres password (fallback: config.yaml database.password or DB_PASSWORD)")
    parser.add_argument("--db-path-prefix", default=None, help="Prefix to prepend to transcript paths stored in DB (e.g., /mnt)")
    parser.add_argument("--write-db", action="store_true", help="Insert diarized spans into diarized_timestamps")
    parser.add_argument("--db-append", action="store_true", help="Keep existing diarized spans instead of replacing them")
    parser.add_argument("--project-root", type=Path, default=None, help="Project root for outputs/reference (default: current dir or repo root)")
    parser.add_argument(
        "--chunk-seconds",
        type=float,
        default=DEFAULT_CHUNK_SECONDS,
        help="Chunk length in seconds (longer reduces boundary errors; default 12.0)",
    )
    parser.add_argument(
        "--overlap-seconds",
        type=float,
        default=DEFAULT_OVERLAP_SECONDS,
        help="Chunk overlap in seconds (default 2.0)",
    )
    parser.add_argument("--similarity-threshold", type=float, default=0.6, help="Cosine similarity threshold to accept a speaker match")
    parser.add_argument("--gap-threshold", type=float, default=0.15, help="Tolerance when matching words to speaker spans (seconds)")
    parser.add_argument("--device", default="auto", help="Device for resemblyzer encoder: auto|cuda|cpu")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("--build-reference", action="store_true", help="Force building reference clips before diarization")
    parser.add_argument(
        "--ref-clips",
        type=int,
        default=DEFAULT_REF_CLIPS,
        help="Number of random reference clips to generate (default 50)",
    )
    parser.add_argument(
        "--ref-clip-duration",
        type=float,
        default=DEFAULT_REF_CLIP_DURATION,
        help="Length of each reference clip in seconds (default 8.0)",
    )
    parser.add_argument("--workers", type=int, default=None, help="Parallel workers for batch mode (fallback: config diarization-workers, else 3)")
    args = parser.parse_args()

    env_root = os.environ.get("PROJECT_ROOT") or os.environ.get("VIDOPS_ROOT")
    root = args.project_root or (Path(env_root).expanduser() if env_root else None) or DEFAULT_PROJECT_ROOT
    ytids: List[str] = []
    if args.ytids_file:
        ytids = [line.strip() for line in args.ytids_file.read_text().splitlines() if line.strip()]
    if args.ytids_from_dir:
        ytids.extend(ytids_from_dir(args.ytids_from_dir))
    if args.ytid:
        ytids.extend([t.strip() for t in re.split(r"[,\s]+", args.ytid) if t.strip()])
    if ytids:
        seen = set()
        ytids = [y for y in ytids if not (y in seen or seen.add(y))]
    if not ytids:
        raise SystemExit("Provide --ytid, --ytids-file, or --ytids-from-dir.")
    first_ytid = ytids[0]

    db_path_prefix: Optional[str] = None
    db_params: Optional[Dict[str, object]] = None
    db_config: Dict[str, object] = {}
    if args.use_db_words or args.write_db:
        db_config = load_db_config()
        db_host = args.db_host or db_config.get("db_host") or os.environ.get("DB_HOST") or DEFAULT_DB_HOST
        db_port_val = args.db_port or db_config.get("db_port") or os.environ.get("DB_PORT") or DEFAULT_DB_PORT
        try:
            db_port = int(db_port_val)  # type: ignore[arg-type]
        except Exception:
            db_port = DEFAULT_DB_PORT
        db_name = args.db_name or db_config.get("db_name") or os.environ.get("DB_NAME") or DEFAULT_DB_NAME
        db_user = args.db_user or db_config.get("db_user") or os.environ.get("DB_USER")
        db_password = args.db_password or db_config.get("db_password") or os.environ.get("DB_PASSWORD")
        db_path_prefix = args.db_path_prefix or db_config.get("db_path_prefix") or db_config.get("words_path_prefix") or os.environ.get("DB_PATH_PREFIX")
        db_params = build_db_params(db_name, db_host, db_port, db_user, db_password)
        if args.words and args.use_db_words:
            print("[warn] --words path is ignored when --use-db-words is set", file=sys.stderr)
        if args.write_db and not args.use_db_words:
            print("[warn] --write-db requires --use-db-words; DB insert disabled for this run", file=sys.stderr)
            args.write_db = False
    config_workers = None
    if db_config:
        config_workers = (
            db_config.get("diarization-workers")
            or db_config.get("diarization_workers")
            or os.environ.get("DB_DIARIZATION_WORKERS")
        )
        try:
            config_workers = int(config_workers) if config_workers is not None else None
        except Exception:
            config_workers = None
    workers = args.workers or config_workers or 3

    device = args.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[info] workers={workers} device={device}")

    # Resolve audio paths (shared reference will be under first ytid)
    candidate_roots: List[Path] = [root]
    transcript_paths: List[Path] = []
    if args.use_db_words and db_params:
        transcript_paths = fetch_transcript_paths(first_ytid, db_params, mount_prefix=db_path_prefix)
        for tp in transcript_paths:
            for parent in tp.parents:
                if parent.name == "generated":
                    proj = parent.parent
                    if proj not in candidate_roots:
                        candidate_roots.append(proj)
                    break
    if args.verbose and len(candidate_roots) > 1:
        print(f"[info] candidate project roots for assets: {candidate_roots}")

    def find_audio_multi(yt: str) -> Optional[Path]:
        for r in candidate_roots:
            p = find_audio(r, yt)
            if p:
                if args.verbose:
                    print(f"[info] found audio for {yt} at {p} (root {r})")
                return p
        return None

    audio_paths: List[Optional[Path]] = []
    if len(ytids) == 1 and args.audio:
        audio_paths.append(args.audio)
    else:
        for y in ytids:
            p = find_audio_multi(y)
            if not p and (args.use_db_words or args.write_db):
                # Allow deferred discovery from DB paths when using DB words
                audio_paths.append(None)
                if args.verbose:
                    print(f"[warn] audio not found under candidate roots for {y}; will try deriving from DB words path")
            elif not p:
                raise SystemExit(f"Audio file not found for {y}; use --audio for single runs.")
            else:
                audio_paths.append(p)

    first_ytid = ytids[0]
    ref_dir = root / "generated" / "diary_reference" / first_ytid
    ref_json = ref_dir / "reference.json"

    if (not ref_json.exists()) and sys.stdin.isatty():
        if args.build_reference or input("No reference found. Build reference clips? [y/N]: ").strip().lower() == "y":
            target_root = project_root_from_ref_dir(ref_dir, root)
            if len(ytids) > 1:
                build_reference_batch(target_root, ytids, [p for p in audio_paths if p], n_clips=args.ref_clips, clip_duration=args.ref_clip_duration)
            else:
                build_reference_single(target_root, first_ytid, audio_paths[0], n_clips=args.ref_clips, clip_duration=args.ref_clip_duration)
            ref_json = (target_root / "generated" / "diary_reference" / first_ytid / "reference.json")
            ref_dir = ref_json.parent
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
    missing_words: List[Tuple[str, Optional[str]]] = []
    skipped: List[str] = []
    if workers > 1 and len(ytids) > 1:
        ctx = get_context("spawn")
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
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
                    args.use_db_words,
                    db_params,
                    args.db_words_source,
                    db_path_prefix,
                    args.write_db,
                    args.db_append,
                    worker_id=idx + 1,
                )
                for idx, y in enumerate(ytids)
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
                    args.use_db_words,
                    db_params,
                    args.db_words_source,
                    db_path_prefix,
                    args.write_db,
                    args.db_append,
                    worker_id=1,
                )
            )

    for ytid, res in results:
        if isinstance(res, dict) and res.get("error") == "words_missing":
            missing_words.append((ytid, res.get("audio")))
            print(f"[error] {ytid}: Words missing for {ytid}", file=sys.stderr)
        elif isinstance(res, dict) and res.get("skipped"):
            skipped.append(ytid)
            if args.verbose:
                print(f"[info] {ytid} skipped (complete marker present)")
        elif isinstance(res, dict) and res.get("error"):
            print(f"[error] {ytid}: {res.get('error')}", file=sys.stderr)
        elif isinstance(res, str):
            print(f"[error] {ytid}: {res}", file=sys.stderr)
        elif args.verbose:
            print(f"[info] {ytid} outputs: {res}")

    if missing_words:
        log_dir = root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "diarization_error.log"
        with log_path.open("a", encoding="utf-8") as logf:
            for ytid, audio in missing_words:
                logf.write(f"{ytid}\t{audio or 'audio_not_found'}\n")
        print(f"[warn] Missing words for {len(missing_words)} file(s); see {log_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
