#!/usr/bin/env python3
"""
Lightweight legacy-compatible transcript analyzer.

Reads a transcript file (VTT/TSV/plain), emits a JSON summary to --output,
and keeps the interface stable for the workspace.sh analyze bridge.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import List


def load_transcript_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="ignore")
    # Strip WEBVTT headers to reduce noise
    lines = []
    for line in text.splitlines():
        if line.strip().lower().startswith("webvtt"):
            continue
        lines.append(line)
    return "\n".join(lines)


def load_words(path: Path) -> List[str]:
    tokens: List[str] = []
    if not path.exists():
        return tokens
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip() or line.lower().startswith("start"):
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            tokens.append(parts[2].strip())
        else:
            tokens.extend(parts)
    return tokens


def top_terms(words: List[str], limit: int = 10) -> List[str]:
    stop = {"the", "and", "for", "that", "with", "this", "have", "from", "your", "just", "you", "but", "WEBVTT"}
    counter = Counter()
    for token in words:
        token = token.strip().lower()
        if len(token) < 3 or token in stop:
            continue
        counter[token] += 1
    return [term for term, _ in counter.most_common(limit)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze a transcript and emit summary JSON.")
    parser.add_argument("--transcript", required=True, help="Path to transcript file.")
    parser.add_argument("--output", required=True, help="Path to write JSON summary.")
    parser.add_argument("--model", default="legacy", help="Analysis model label for bookkeeping.")
    parser.add_argument("--ytid", default="", help="Optional YouTube ID for context.")
    parser.add_argument("--words", help="Optional words TSV to improve term frequency stats.")
    args = parser.parse_args()

    transcript_path = Path(args.transcript)
    if not transcript_path.exists():
        print(f"[ERROR] Transcript not found: {transcript_path}", file=sys.stderr)
        return 1

    words_tokens: List[str] = []
    if args.words:
        words_tokens = load_words(Path(args.words))

    transcript_text = load_transcript_text(transcript_path)
    sample_lines = [ln.strip() for ln in transcript_text.splitlines() if ln.strip()]
    summary = " ".join(sample_lines[:5])[:800]

    payload = {
        "ytid": args.ytid,
        "model": args.model,
        "summary": summary,
        "char_count": len(transcript_text),
        "line_count": len(sample_lines),
        "top_terms": top_terms(words_tokens) if words_tokens else top_terms(sample_lines),
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[OK] Wrote analysis JSON to {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
