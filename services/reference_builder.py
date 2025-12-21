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

from dal import FilesystemCache
from .reference_registry import ReferenceRegistry, ReferenceRecord

DEFAULT_CLIPS = 50
MIN_WORDS = 3
MAX_WORDS = 6
MIN_DURATION = 6.0
MAX_DURATION = 12.0
DEFAULT_AUDIO_CHANNELS = 1
DEFAULT_AUDIO_RATE = 16000


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


def _cut_clip(src: Path, start: float, end: float, dest: Path, audio_channels: int = DEFAULT_AUDIO_CHANNELS, audio_rate: int = DEFAULT_AUDIO_RATE) -> None:
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
        str(DEFAULT_AUDIO_CHANNELS),
        "-ar",
        str(DEFAULT_AUDIO_RATE),
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


def _prompt_reference_choice(existing: Sequence[str]) -> Optional[str]:
    print("\nAvailable references:")
    for idx, name in enumerate(existing, 1):
        print(f"  {idx}. {name}")
    print("  0. None (build new)")
    try:
        choice = input("Select a reference to reuse (number), or 0 for none: ").strip()
    except EOFError:
        return None
    if not choice:
        return None
    try:
        num = int(choice)
    except ValueError:
        return None
    if num <= 0:
        return None
    if 1 <= num <= len(existing):
        return existing[num - 1]
    return None


def _prompt_reference_from_registry(records: Sequence[ReferenceRecord]) -> Optional[str]:
    if not records:
        return None
    print("\nExisting references (pick to reuse, or 0 to build new):")
    for idx, rec in enumerate(records, 1):
        meta = []
        if rec.clip_count is not None:
            meta.append(f"clips={rec.clip_count}")
        if rec.model:
            meta.append(f"model={rec.model}")
        if rec.transcript_kind:
            meta.append(f"transcript={rec.transcript_kind}")
        if rec.aggregate_hash:
            meta.append(f"hash={rec.aggregate_hash[:8]}…")
        meta_str = " ".join(meta)
        print(f"  {idx}. {rec.name} {meta_str}")
    print("  0. None (build new)")
    try:
        choice = input("Select reference to reuse: ").strip()
    except EOFError:
        return None
    if not choice:
        return None
    try:
        num = int(choice)
    except ValueError:
        return None
    if num <= 0:
        return None
    if 1 <= num <= len(records):
        return records[num - 1].name
    return None


def _curses_reference_menu(records: Sequence[ReferenceRecord]) -> tuple[Optional[str], bool]:
    """Curses menu to choose an existing reference or build new. Returns (choice, displayed_flag)."""
    try:
        import curses
    except Exception:
        return None, False
    if not sys.stdin.isatty() or not records:
        return None, False

    def _menu(stdscr):
        curses.curs_set(0)
        stdscr.nodelay(False)
        stdscr.keypad(True)
        idx = 0
        options = list(records) + [None]  # last entry = build new
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            header = "↑/↓: move  Enter: select  q: cancel (build new)"
            stdscr.addnstr(0, 0, header, w - 1)
            for i, rec in enumerate(options):
                prefix = ">" if i == idx else " "
                if rec is None:
                    line = f"{prefix} [build new]"
                else:
                    meta = []
                    if rec.clip_count is not None:
                        meta.append(f"clips={rec.clip_count}")
                    if rec.model:
                        meta.append(f"model={rec.model}")
                    if rec.transcript_kind:
                        meta.append(f"tx={rec.transcript_kind}")
                    if rec.aggregate_hash:
                        meta.append(f"hash={rec.aggregate_hash[:8]}…")
                    meta_str = " ".join(meta)
                    line = f"{prefix} {rec.name} {meta_str}"
                stdscr.addnstr(i + 2, 0, line, w - 1)
            ch = stdscr.getch()
            if ch in (curses.KEY_UP, ord("k")):
                idx = (idx - 1) % len(options)
            elif ch in (curses.KEY_DOWN, ord("j")):
                idx = (idx + 1) % len(options)
            elif ch in (curses.KEY_ENTER, 10, 13, ord(" ")):
                choice = options[idx]
                return choice.name if choice else None
            elif ch in (ord("q"), 27):
                return None

    try:
        return curses.wrapper(_menu), True
    except Exception:
        return None, False


def _curses_param_form(fields: list[dict]) -> list[dict]:
    """Generic curses form for numeric fields with simple +/- adjustment and edit."""
    try:
        import curses
    except Exception:
        return fields
    if not sys.stdin.isatty():
        return fields

    def _edit_value(stdscr, row, prompt, current, is_float):
        curses.echo()
        stdscr.addstr(row, 0, prompt)
        stdscr.clrtoeol()
        val = stdscr.getstr(row, len(prompt)).decode("utf-8").strip()
        curses.noecho()
        if not val:
            return current
        try:
            return float(val) if is_float else int(val)
        except ValueError:
            return current

    def _form(stdscr):
        curses.curs_set(0)
        stdscr.nodelay(False)
        stdscr.keypad(True)
        idx = 0
        options = fields + [{"label": "[accept]", "value": None, "step": 0, "min": 0, "is_float": False}]
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            stdscr.addnstr(0, 0, "↑/↓: move  +/-: adjust  e/Enter: edit/select  q: accept", w - 1)
            for i, f in enumerate(options):
                prefix = ">" if i == idx else " "
                val = "" if f["value"] is None else f": {f['value']}"
                stdscr.addnstr(i + 2, 0, f"{prefix} {f['label']}{val}", w - 1)
            ch = stdscr.getch()
            if ch in (curses.KEY_UP, ord("k")):
                idx = (idx - 1) % len(options)
            elif ch in (curses.KEY_DOWN, ord("j")):
                idx = (idx + 1) % len(options)
            elif ch in (ord("+"), ord("=")):
                if idx < len(fields):
                    step = fields[idx]["step"]
                    fields[idx]["value"] = fields[idx]["value"] + step
            elif ch == ord("-"):
                if idx < len(fields):
                    step = fields[idx]["step"]
                    fields[idx]["value"] = max(fields[idx]["min"], fields[idx]["value"] - step)
            elif ch in (curses.KEY_ENTER, 10, 13, ord("e")):
                if idx >= len(fields):
                    return fields
                f = fields[idx]
                prompt = f"Set {f['label']} (current {f['value']}): "
                fields[idx]["value"] = _edit_value(stdscr, len(options) + 3, prompt, f["value"], f["is_float"])
            elif ch in (ord("q"), 27):
                return fields

    try:
        return curses.wrapper(_form)
    except Exception:
        # Curses failed; fall back to text prompts
        print("\nAdjust reference building parameters:")
        for field in fields:
            try:
                prompt = f"  {field['label']} (current {field['value']}): "
                val = input(prompt).strip()
                if val:
                    field["value"] = float(val) if field["is_float"] else int(val)
            except (ValueError, EOFError):
                pass
        return fields


def _prompt_reference_name(default_name: str) -> str:
    """Prompt for reference name (curses if available, fallback to input)."""
    try:
        import curses
    except Exception:
        try:
            val = input(f"Reference name [{default_name}]: ").strip()
            return val or default_name
        except EOFError:
            return default_name

    if not sys.stdin.isatty():
        try:
            val = input(f"Reference name [{default_name}]: ").strip()
            return val or default_name
        except EOFError:
            return default_name

    def _edit(stdscr):
        curses.curs_set(1)
        stdscr.nodelay(False)
        stdscr.keypad(True)
        prompt = f"Reference name [{default_name}]: "
        stdscr.erase()
        stdscr.addstr(0, 0, prompt)
        curses.echo()
        val = stdscr.getstr(0, len(prompt)).decode("utf-8").strip()
        curses.noecho()
        return val or default_name

    try:
        return curses.wrapper(_edit)
    except Exception:
        # Curses failed; fall back to text input
        try:
            val = input(f"Reference name [{default_name}]: ").strip()
            return val or default_name
        except EOFError:
            return default_name


def _curses_input(prompt: str, default: str = "") -> str:
    try:
        import curses
    except Exception:
        try:
            val = input(f"{prompt} [{default}]: ").strip()
            return val or default
        except EOFError:
            return default
    if not sys.stdin.isatty():
        try:
            val = input(f"{prompt} [{default}]: ").strip()
            return val or default
        except EOFError:
            return default

    def _edit(stdscr):
        curses.curs_set(1)
        stdscr.nodelay(False)
        stdscr.keypad(True)
        stdscr.erase()
        full = f"{prompt} [{default}]: "
        stdscr.addstr(0, 0, full)
        curses.echo()
        val = stdscr.getstr(0, len(full)).decode("utf-8").strip()
        curses.noecho()
        return val or default

    try:
        return curses.wrapper(_edit)
    except Exception:
        # Curses failed; fall back to text input
        try:
            val = input(f"{prompt} [{default}]: ").strip()
            return val or default
        except EOFError:
            return default


class ReferenceBuilder:
    def __init__(self, fs_cache: FilesystemCache, workspace_root: Path):
        self.fs_cache = fs_cache
        self.workspace_root = workspace_root
        self.registry = ReferenceRegistry()
        # Pull defaults from config when available
        try:
            from configuration import load_config
            cfg = load_config()
            global DEFAULT_CLIPS, MIN_WORDS, MAX_WORDS, MIN_DURATION, MAX_DURATION
            global DEFAULT_AUDIO_CHANNELS, DEFAULT_AUDIO_RATE
            DEFAULT_CLIPS = cfg.diarization.refs_clips_count
            MIN_WORDS = cfg.diarization.refs_min_words
            MAX_WORDS = cfg.diarization.refs_max_words
            MIN_DURATION = cfg.diarization.refs_min_clip_seconds
            MAX_DURATION = cfg.diarization.refs_max_clip_seconds
            DEFAULT_AUDIO_CHANNELS = cfg.diarization.refs_audio_channels
            DEFAULT_AUDIO_RATE = cfg.diarization.refs_audio_rate
        except Exception:
            pass

    def build(
        self,
        ytid: str,
        media_path: Path,
        words_path: Path,
        reference_rel: str,
        clips_count: int = DEFAULT_CLIPS,
        max_duration: float = MAX_DURATION,
        min_duration: float = MIN_DURATION,
        min_words: int = MIN_WORDS,
        max_words: int = MAX_WORDS,
        audio_channels: int = DEFAULT_AUDIO_CHANNELS,
        audio_rate: int = DEFAULT_AUDIO_RATE,
    ) -> Path:
        skip_prompts = os.environ.get("BATCH_DIARIZE_SKIP_PROMPT") or not sys.stdin.isatty()

        # Offer reuse of an existing reference (interactive only) before any cutting
        if not skip_prompts:
            try:
                records = self.registry.list_records(limit=50)
            except Exception:
                records = []
            reuse, displayed = _curses_reference_menu(records)
            if reuse:
                reused_path = self.fs_cache.get_central_path(f"data/references/{reuse}")
                if reused_path.exists():
                    return reused_path
            elif not displayed:
                # Only fall back to text prompts if curses UI did not show
                reuse = _prompt_reference_from_registry(records) or _prompt_reference_choice([r.name for r in records])
                if reuse:
                    reused_path = self.fs_cache.get_central_path(f"data/references/{reuse}")
                    if reused_path.exists():
                        return reused_path
        if not skip_prompts:
            ref_name = _prompt_reference_name(Path(reference_rel).name)
            reference_rel = f"data/references/{ref_name}"

        words = _read_words(words_path)
        if not words:
            raise ValueError(f"No words found at {words_path}")

        if not skip_prompts:
            fields = [
                {"label": "clips_count", "value": clips_count, "step": 1, "min": 1, "is_float": False},
                {"label": "max_clip_seconds", "value": max_duration, "step": 0.5, "min": 1.0, "is_float": True},
                {"label": "min_clip_seconds", "value": min_duration, "step": 0.5, "min": 0.1, "is_float": True},
                {"label": "min_words", "value": min_words, "step": 1, "min": 1, "is_float": False},
                {"label": "max_words", "value": max_words, "step": 1, "min": 1, "is_float": False},
                {"label": "audio_channels", "value": audio_channels, "step": 1, "min": 1, "is_float": False},
                {"label": "audio_rate", "value": audio_rate, "step": 1000, "min": 8000, "is_float": False},
            ]
            fields = _curses_param_form(fields)
            clips_count = int(fields[0]["value"])
            max_duration = float(fields[1]["value"])
            min_duration = float(fields[2]["value"])
            min_words = int(fields[3]["value"])
            max_words = int(fields[4]["value"])
            audio_channels = int(fields[5]["value"])
            audio_rate = int(fields[6]["value"])

        duration = _ffprobe_duration(media_path)
        candidates = self._generate_candidates(
            words,
            duration,
            clips_count,
            max_duration,
            min_duration,
            min_words,
            max_words,
        )
        if not candidates:
            raise ValueError("No reference candidates generated from words.")

        local_clips = self._cut_candidates(
            media_path,
            candidates,
            ytid,
            name_prefix=ytid,
            audio_channels=audio_channels,
            audio_rate=audio_rate,
        )

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
            speaker_name = _curses_input("Enter speaker name", Path(reference_rel).name or "speaker")

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
        min_duration: float = MIN_DURATION,
        min_words: int = MIN_WORDS,
        max_words: int = MAX_WORDS,
        audio_channels: int = DEFAULT_AUDIO_CHANNELS,
        audio_rate: int = DEFAULT_AUDIO_RATE,
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
        if not skip_prompts:
            try:
                records = self.registry.list_records(limit=50)
            except Exception:
                records = []
            reuse, displayed = _curses_reference_menu(records)
            if reuse:
                reused_path = self.fs_cache.get_central_path(f"data/references/{reuse}")
                if reused_path.exists():
                    return reused_path
            elif not displayed:
                reuse = _prompt_reference_from_registry(records) or _prompt_reference_choice([r.name for r in records])
                if reuse:
                    reused_path = self.fs_cache.get_central_path(f"data/references/{reuse}")
                    if reused_path.exists():
                        return reused_path

        if not skip_prompts:
            ref_name = _prompt_reference_name(Path(reference_rel).name)
            reference_rel = f"data/references/{ref_name}"

        if not skip_prompts:
            fields = [
                {"label": "clips_per_video", "value": clips_per_video, "step": 1, "min": 1, "is_float": False},
                {"label": "max_clips", "value": max_clips, "step": 1, "min": 1, "is_float": False},
                {"label": "max_clip_seconds", "value": max_duration, "step": 0.5, "min": 1.0, "is_float": True},
                {"label": "min_clip_seconds", "value": min_duration, "step": 0.5, "min": 0.1, "is_float": True},
                {"label": "min_words", "value": min_words, "step": 1, "min": 1, "is_float": False},
                {"label": "max_words", "value": max_words, "step": 1, "min": 1, "is_float": False},
                {"label": "audio_channels", "value": audio_channels, "step": 1, "min": 1, "is_float": False},
                {"label": "audio_rate", "value": audio_rate, "step": 1000, "min": 8000, "is_float": False},
            ]
            fields = _curses_param_form(fields)
            clips_per_video = int(fields[0]["value"])
            max_clips = int(fields[1]["value"])
            max_duration = float(fields[2]["value"])
            min_duration = float(fields[3]["value"])
            min_words = int(fields[4]["value"])
            max_words = int(fields[5]["value"])
            audio_channels = int(fields[6]["value"])
            audio_rate = int(fields[7]["value"])

        combined: list[Tuple[Path, float, float, str]] = []
        for ytid, media_path, words_path in sources:
            words = _read_words(words_path)
            if not words:
                continue
            duration = _ffprobe_duration(media_path)
            windows = self._generate_candidates(
                words,
                duration,
                clips_per_video,
                max_duration,
                min_duration,
                min_words,
                max_words,
            )
            if not windows:
                continue
            local_clips = self._cut_candidates(
                media_path,
                windows,
                ytid,
                name_prefix=ytid,
                audio_channels=audio_channels,
                audio_rate=audio_rate,
            )
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
            speaker_name = _curses_input("Enter speaker name", Path(reference_rel).name or "speaker")

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
        self,
        words: List[WordSpan],
        duration: float,
        clips_count: int,
        max_duration: float,
        min_duration: float,
        min_words: int,
        max_words: int,
    ) -> List[Tuple[float, float]]:
        windows: List[Tuple[float, float]] = []
        for i in range(len(words)):
            n_words = random.randint(min_words, max_words)
            if i + n_words > len(words):
                continue
            start = words[i].start
            end = words[i + n_words - 1].end
            end = min(end, start + max_duration, duration)
            # Enforce a minimum duration by extending if possible
            if end - start < min_duration:
                end = min(start + min_duration, duration)
            if end - start <= 0:
                continue
            windows.append((start, end))
        random.shuffle(windows)
        return windows[:clips_count]

    def _cut_candidates(
        self,
        media_path: Path,
        windows: Sequence[Tuple[float, float]],
        ytid: str,
        name_prefix: Optional[str] = None,
        audio_channels: int = DEFAULT_AUDIO_CHANNELS,
        audio_rate: int = DEFAULT_AUDIO_RATE,
    ) -> List[Tuple[Path, float, float]]:
        out_dir = self.workspace_root / "tmp" / "reference_builder" / ytid
        out_dir.mkdir(parents=True, exist_ok=True)
        generated: List[Tuple[Path, float, float]] = []
        for idx, (start, end) in enumerate(windows, 1):
            stem = f"{name_prefix}_{idx}" if name_prefix else f"{idx}"
            clip_path = out_dir / f"{stem}.wav"
            _cut_clip(media_path, start, end, clip_path, audio_channels=audio_channels, audio_rate=audio_rate)
            generated.append((clip_path, start, end))
        return generated
