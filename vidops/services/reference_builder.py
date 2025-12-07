# vidops/services/reference_builder.py

"""
Reference builder for diarization jobs.

Generates short reference clips from the target audio using word timestamps,
lets the operator choose clips (curses TUI when available, prompt fallback),
and writes reference.json + clips under generated/diary_reference/<ytid>/.
"""

from __future__ import annotations

import csv
import json
import os
import random
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from vidops.dal import FilesystemCache

DEFAULT_CLIPS = 50
MIN_WORDS = 3
MAX_WORDS = 6
MIN_DURATION = 6.0
MAX_DURATION = 12.0


@dataclass
class WordSpan:
    start: float
    end: float
    text: str


def _read_words(words_path: Path) -> List[WordSpan]:
    spans: List[WordSpan] = []
    with words_path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        for row in reader:
            if len(row) < 3:
                continue
            try:
                start = float(row[0])
                end = float(row[1])
            except ValueError:
                continue
            text = row[2]
            spans.append(WordSpan(start=start, end=end, text=text))
    return spans


def _ffprobe_duration(path: Path) -> float:
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


def _cut_clip(src: Path, start: float, end: float, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
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
        str(src),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-vn",
        "-y",
        str(dest),
    ]
    subprocess.run(cmd, check=True)


def _interactive_clip_selector(clips: Sequence[Tuple[Path, float, float]], preselected: Optional[Iterable[Path]] = None):
    try:
        import curses
    except Exception:
        return None

    selected = set(preselected or [])
    idx = 0
    view_offset = 0
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

    def adjust_view(max_rows: int):
        nonlocal view_offset
        if idx < view_offset:
            view_offset = idx
        elif idx >= view_offset + max_rows:
            view_offset = max(0, idx - max_rows + 1)

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
            max_rows = max(1, h - 2)
            adjust_view(max_rows)
            visible = list(enumerate(clips))[view_offset:view_offset + max_rows]
            for line_idx, (i, (clip_path, s, e)) in enumerate(visible, start=1):
                prefix = ">" if i == idx else " "
                mark = "[x]" if clip_path in selected else "[ ]"
                line = f"{prefix} {mark} {i+1}. {clip_path.name} ({s:.1f}-{e:.1f}s)"
                stdscr.addnstr(line_idx, 0, line, w - 1)
            stdscr.refresh()
            ch = stdscr.getch()
            if ch in (curses.KEY_UP, ord("k")):
                stop_playback()
                idx = (idx - 1) % len(clips)
            elif ch in (curses.KEY_DOWN, ord("j")):
                stop_playback()
                idx = (idx + 1) % len(clips)
            elif ch in (curses.KEY_PPAGE,):  # Page up
                stop_playback()
                idx = max(0, idx - max_rows)
            elif ch in (curses.KEY_NPAGE,):  # Page down
                stop_playback()
                idx = min(len(clips) - 1, idx + max_rows)
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


def _prompt_selection(clips: Sequence[Tuple[Path, float, float]], labels: Optional[Sequence[str]] = None):
    print("\nGenerated reference clips:")
    for idx, (p, s, e) in enumerate(clips, 1):
        label = f" [{labels[idx-1]}]" if labels and idx-1 < len(labels) else ""
        print(f"  {idx}: {p}{label} ({s:.1f}-{e:.1f}s)")
    try:
        chosen_str = input("\nEnter clip numbers to keep (comma or space separated, blank for none): ").strip()
    except EOFError:
        chosen_str = ""
    numbers = {c.strip() for c in chosen_str.replace(",", " ").split() if c.strip()}
    chosen = [p for i, (p, _s, _e) in enumerate(clips, 1) if str(i) in numbers or p.stem in numbers]
    return chosen


class ReferenceBuilder:
    def __init__(self, fs_cache: FilesystemCache, workspace_root: Path):
        self.fs_cache = fs_cache
        self.workspace_root = workspace_root

    def build(
        self,
        ytid: str,
        media_path: Path,
        words_path: Path,
        reference_rel: str,
        clips_count: int = DEFAULT_CLIPS,
        max_duration: float = MAX_DURATION,
    ) -> Path:
        skip_prompts = os.environ.get("BATCH_DIARIZE_SKIP_PROMPT") or not sys.stdin.isatty()
        words = _read_words(words_path)
        if not words:
            raise ValueError(f"No words found at {words_path}")

        duration = _ffprobe_duration(media_path)
        candidates = self._generate_candidates(words, duration, clips_count, max_duration)
        if not candidates:
            raise ValueError("No reference candidates generated from words.")

        local_clips = self._cut_candidates(media_path, candidates, ytid, name_prefix=ytid)

        selected: list[Path] = []
        if not skip_prompts and sys.stdin.isatty():
            ui_selected = _interactive_clip_selector(local_clips)
            if ui_selected:
                selected = [p for p, _s, _e in local_clips if p in ui_selected]
        if not selected:
            if skip_prompts:
                selected = [p for p, _s, _e in local_clips[:clips_count]]
            else:
                selected = _prompt_selection(local_clips)
        if not selected:
            raise ValueError("No reference clips selected.")

        if skip_prompts:
            speaker_name = Path(reference_rel).name or "speaker"
        else:
            try:
                speaker_name = input("Enter speaker name for this reference: ").strip() or "speaker"
            except EOFError:
                speaker_name = "speaker"

        dest_dir = self.fs_cache.get_central_path(reference_rel)
        dest_dir.mkdir(parents=True, exist_ok=True)
        clips_dir = dest_dir
        clips_dir.mkdir(parents=True, exist_ok=True)

        copied = []
        for clip in selected:
            target = clips_dir / clip.name
            target.write_bytes(clip.read_bytes())
            copied.append(target)

        meta = {
            "ytid": ytid,
            "speaker_name": speaker_name,
            "audio_source": str(media_path),
            "clips_generated": [str(p) for p, _s, _e in local_clips],
            "clips_selected": [str(p) for p in copied],
            "sample_strategy": {"words_per_clip": f"{MIN_WORDS}-{MAX_WORDS}", "max_duration_sec": max_duration},
        }
        (dest_dir / "reference.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return dest_dir

    def build_shared(
        self,
        reference_rel: str,
        sources: Sequence[Tuple[str, Path, Path]],
        clips_per_video: int = 1,
        max_clips: int = DEFAULT_CLIPS,
        max_duration: float = MAX_DURATION,
    ) -> Path:
        """
        Build a shared reference set from multiple videos.

        Each source contributes up to clips_per_video candidates; we keep up to max_clips total.

        Interactive mode:
        - If stdin is a tty AND BATCH_DIARIZE_SKIP_PROMPT is not set, shows interactive clip selector
        - User can select clips via curses UI or manual prompt
        - User will be prompted to enter speaker name

        Non-interactive mode:
        - Automatically selects first max_clips clips
        - Uses reference name as speaker name
        """
        skip_prompts = os.environ.get("BATCH_DIARIZE_SKIP_PROMPT") or not sys.stdin.isatty()
        combined: list[Tuple[Path, float, float, str]] = []
        for ytid, media_path, words_path in sources:
            words = _read_words(words_path)
            if not words:
                continue
            duration = _ffprobe_duration(media_path)
            windows = self._generate_candidates(words, duration, clips_per_video, max_duration)
            if not windows:
                continue
            local_clips = self._cut_candidates(media_path, windows, ytid, name_prefix=ytid)
            combined.extend((p, s, e, ytid) for p, s, e in local_clips[:clips_per_video])
            if len(combined) >= max_clips:
                break

        if not combined:
            raise ValueError("No reference candidates generated from provided sources.")

        combined = combined[:max_clips]
        clips = [(p, s, e) for (p, s, e, _ytid) in combined]
        labels = [ytid for (_p, _s, _e, ytid) in combined]

        selected: list[Path] = []
        if not skip_prompts and sys.stdin.isatty():
            ui_selected = _interactive_clip_selector(clips, preselected=None)
            if ui_selected:
                selected = [p for p, _s, _e in clips if p in ui_selected]
        if not selected:
            if skip_prompts:
                selected = [p for p, _s, _e in clips[:max_clips]]
            else:
                selected = _prompt_selection(clips, labels=labels)
        if not selected:
            raise ValueError("No reference clips selected.")

        if skip_prompts:
            speaker_name = Path(reference_rel).name or "speaker"
        else:
            try:
                speaker_name = input("Enter speaker name for this reference: ").strip() or "speaker"
            except EOFError:
                speaker_name = "speaker"

        dest_dir = self.fs_cache.get_central_path(reference_rel)
        dest_dir.mkdir(parents=True, exist_ok=True)
        clips_dir = dest_dir
        clips_dir.mkdir(parents=True, exist_ok=True)

        copied = []
        for clip in selected:
            target = clips_dir / clip.name
            target.write_bytes(clip.read_bytes())
            copied.append(target)

        meta = {
            "ytids_used": labels,
            "speaker_name": speaker_name,
            "clips_generated": [str(p) for p, _s, _e in clips],
            "clips_selected": [str(p) for p in copied],
            "sample_strategy": {
                "words_per_clip": f"{MIN_WORDS}-{MAX_WORDS}",
                "max_duration_sec": max_duration,
                "clips_per_video": clips_per_video,
                "max_clips": max_clips,
            },
        }
        (dest_dir / "reference.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return dest_dir

    def _generate_candidates(
        self, words: List[WordSpan], duration: float, clips_count: int, max_duration: float
    ) -> List[Tuple[float, float]]:
        windows: List[Tuple[float, float]] = []
        for i in range(len(words)):
            n_words = random.randint(MIN_WORDS, MAX_WORDS)
            if i + n_words > len(words):
                continue
            start = words[i].start
            end = words[i + n_words - 1].end
            end = min(end, start + max_duration, duration)
            # Enforce a minimum duration by extending if possible
            if end - start < MIN_DURATION:
                end = min(start + MIN_DURATION, duration)
            if end - start <= 0:
                continue
            windows.append((start, end))
        random.shuffle(windows)
        return windows[:clips_count]

    def _cut_candidates(
        self, media_path: Path, windows: Sequence[Tuple[float, float]], ytid: str, name_prefix: Optional[str] = None
    ) -> List[Tuple[Path, float, float]]:
        out_dir = self.workspace_root / "tmp" / "reference_builder" / ytid
        out_dir.mkdir(parents=True, exist_ok=True)
        generated: List[Tuple[Path, float, float]] = []
        for idx, (start, end) in enumerate(windows, 1):
            stem = f"{name_prefix}_{idx}" if name_prefix else f"{idx}"
            clip_path = out_dir / f"{stem}.wav"
            _cut_clip(media_path, start, end, clip_path)
            generated.append((clip_path, start, end))
        return generated
