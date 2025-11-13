#!/usr/bin/env python3
"""
Transcript Analysis Tool
Analyzes video transcripts using AI to extract topics, people, sentiment, and key points.
"""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import List, Dict, Any, Tuple
from datetime import timedelta

try:
    import requests
except ImportError:
    print("Error: 'requests' library not found. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)


class VTTParser:
    """Parse WebVTT transcript files"""

    @staticmethod
    def parse(vtt_path: Path) -> str:
        """Parse VTT file and return clean text"""
        with open(vtt_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Remove WEBVTT header
        content = re.sub(r'^WEBVTT.*?\n\n', '', content, flags=re.MULTILINE)

        # Extract text (skip timestamps and cue identifiers)
        lines = []
        for line in content.split('\n'):
            # Skip timestamp lines (contain -->)
            if '-->' in line:
                continue
            # Skip NOTE blocks or confidence annotations often present in auto captions
            if line.strip().startswith('NOTE') or 'NOTE Confidence' in line:
                continue
            # Skip cue identifiers (just numbers)
            if line.strip().isdigit():
                continue
            # Skip empty lines
            if not line.strip():
                continue
            # Clean HTML tags if present
            line = re.sub(r'<[^>]+>', '', line)
            lines.append(line.strip())

        return ' '.join(lines)


class TranscriptChunker:
    """Chunk transcripts intelligently with overlap"""

    def __init__(self, chunk_size: int = 1000, overlap: int = 150):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(self, text: str) -> List[Dict[str, Any]]:
        """Split text into overlapping chunks at sentence boundaries"""
        # Split into sentences (simple approach)
        sentences = re.split(r'([.!?]+\s+)', text)

        chunks = []
        current_chunk = []
        current_word_count = 0
        chunk_id = 0

        for i in range(0, len(sentences), 2):
            if i + 1 < len(sentences):
                sentence = sentences[i] + sentences[i+1]
            else:
                sentence = sentences[i]

            word_count = len(sentence.split())

            # If adding this sentence exceeds chunk size, start new chunk
            if current_word_count + word_count > self.chunk_size and current_chunk:
                chunk_text = ''.join(current_chunk).strip()
                chunks.append({
                    'chunk_id': chunk_id,
                    'text': chunk_text,
                    'word_count': current_word_count
                })
                chunk_id += 1

                # Keep last sentences for overlap
                overlap_text = chunk_text
                overlap_words = overlap_text.split()
                if len(overlap_words) > self.overlap:
                    overlap_text = ' '.join(overlap_words[-self.overlap:])

                current_chunk = [overlap_text + ' ']
                current_word_count = len(overlap_text.split())

            current_chunk.append(sentence)
            current_word_count += word_count

        # Add final chunk
        if current_chunk:
            chunks.append({
                'chunk_id': chunk_id,
                'text': ''.join(current_chunk).strip(),
                'word_count': current_word_count
            })

        return chunks


class OllamaAnalyzer:
    """Analyze chunks using Ollama local LLM"""

    def __init__(self, model: str = "llama3.2", base_url: str = "http://localhost:11434", options: Dict[str, Any] | None = None):
        self.model = model
        self.base_url = base_url
        self.api_url = f"{base_url}/api/generate"
        self.options = options or {}

    def analyze_chunk(self, chunk_text: str, chunk_id: int) -> Dict[str, Any]:
        """Analyze a single chunk"""
        prompt = f"""Analyze this transcript segment and extract structured information.

Return ONLY valid JSON with this exact structure (no markdown, no explanations):
{{
  "people": ["name1", "name2"],
  "topics": [],
  "sentiment": "one_of: analytical|excited|frustrated|joking|serious|concerned|optimistic|pessimistic|angry|sad|joyful|neutral",
  "key_points": [],
  "notable_quotes": [],
  "categories": [],
  "summary": ""
}}

Rules:
- Return a single best-fit sentiment label (not a list, no pipes).
- Categories must be a list of separate lowercase tags relevant to THIS segment. Do not combine with '|'. If none, return [].
- Avoid placeholder values like "none found".

Transcript segment:
{chunk_text[:2000]}

Return only the JSON object."""

        try:
            # Simple retry for transient failures
            last_exc = None
            for attempt in range(3):
                try:
                    response = requests.post(
                        self.api_url,
                        json={
                            "model": self.model,
                            "prompt": prompt,
                            "stream": False,
                            "format": "json",
                            "options": self.options
                        },
                        timeout=180
                    )
                    break
                except requests.exceptions.RequestException as e:
                    last_exc = e
                    if attempt < 2:
                        continue
                    else:
                        raise

            if response.status_code != 200:
                print(f"Warning: Ollama API error for chunk {chunk_id}: {response.status_code}", file=sys.stderr)
                return self._empty_analysis()

            result = response.json()
            response_text = result.get('response', '{}')

            # Parse JSON response
            try:
                analysis = json.loads(response_text)
                return self._normalize_analysis(analysis)
            except json.JSONDecodeError:
                print(f"Warning: Invalid JSON from Ollama for chunk {chunk_id}", file=sys.stderr)
                return self._empty_analysis()

        except requests.exceptions.RequestException as e:
            print(f"Warning: Ollama request failed for chunk {chunk_id}: {e}", file=sys.stderr)
            return self._empty_analysis()

    def summarize(self, final_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Generate multiple summary variants from aggregated analysis"""
        meta = final_analysis.get('metadata', {})
        data = final_analysis.get('analysis', {})

        # Build a compact digest for context
        people = ", ".join([p.get('name','') for p in data.get('people_mentioned', [])][:10])
        topics = ", ".join([t.get('topic','') for t in data.get('topics', [])][:10])
        points = "\n- ".join(data.get('key_points', [])[:10])
        quotes = "\n> ".join(data.get('notable_quotes', [])[:5])

        prompt = f"""
Using the provided structured digest, write multiple summaries in strict JSON.

Return ONLY valid JSON with this exact structure:
{{
  "tldr_one_sentence": "",
  "summary_paragraph": "",
  "bullet_points": ["", ""],
  "social_caption": "",
  "hashtags": ["#tag1", "#tag2"]
}}

Rules:
- tldr_one_sentence: <= 30 words, single sentence.
- summary_paragraph: 3-6 sentences, concise and neutral.
- bullet_points: 6-10 bullets, each <= 18 words, concrete and specific.
- social_caption: <= 240 characters, engaging but accurate, no hashtags.
- hashtags: 5-10 relevant tags (strings starting with #), lowercase where appropriate, no spaces, no duplicates.

Context Digest:
Title: {meta.get('title','')}
Dominant sentiment: {data.get('dominant_sentiment','unknown')}
Top people: {people}
Top topics: {topics}
Key points:\n- {points}
Notable quotes:\n> {quotes}

Return only the JSON object.
"""

        try:
            response = requests.post(
                self.api_url,
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": self.options
                },
                timeout=120
            )
            if response.status_code != 200:
                return self._empty_summaries()
            result = response.json()
            response_text = result.get('response', '{}')
            try:
                raw = json.loads(response_text)
            except json.JSONDecodeError:
                return self._empty_summaries()
            return self._normalize_summaries(raw)
        except requests.exceptions.RequestException:
            return self._empty_summaries()

    def _empty_summaries(self) -> Dict[str, Any]:
        return {
            "tldr_one_sentence": "",
            "summary_paragraph": "",
            "bullet_points": [],
            "social_caption": "",
            "hashtags": []
        }

    def _normalize_summaries(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        tldr = str(raw.get("tldr_one_sentence", "")).strip()
        para = str(raw.get("summary_paragraph", "")).strip()
        bullets_raw = raw.get("bullet_points", [])
        if isinstance(bullets_raw, str):
            bullets = [b.strip("- • ").strip() for b in bullets_raw.split("\n") if b.strip()]
        elif isinstance(bullets_raw, list):
            bullets = [str(b).strip() for b in bullets_raw if str(b).strip()]
        else:
            bullets = []
        caption = str(raw.get("social_caption", "")).strip()
        hashtags_raw = raw.get("hashtags", [])
        if isinstance(hashtags_raw, str):
            tags = [t.strip() for t in re.split(r"[\s,]+", hashtags_raw) if t.strip()]
        else:
            tags = [str(t).strip() for t in hashtags_raw if str(t).strip()]
        # normalize hashtags
        norm_tags = []
        seen = set()
        for t in tags:
            t = t.lower()
            if not t.startswith('#'):
                t = '#' + t.replace(' ', '')
            t = re.sub(r"[^#a-z0-9_]", "", t)
            if len(t) > 1 and t not in seen:
                norm_tags.append(t)
                seen.add(t)
        # trim lengths
        if len(caption) > 240:
            caption = caption[:240].rstrip() + '…'
        return {
            "tldr_one_sentence": tldr,
            "summary_paragraph": para,
            "bullet_points": bullets[:10],
            "social_caption": caption,
            "hashtags": norm_tags[:10]
        }

    def _empty_analysis(self) -> Dict[str, Any]:
        """Return empty analysis structure"""
        return {
            "people": [],
            "topics": [],
            "sentiment": "unknown",
            "key_points": [],
            "notable_quotes": [],
            "categories": [],
            "summary": ""
        }

    def _normalize_analysis(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize/clean model output types and values"""
        def to_list(val) -> List[str]:
            if isinstance(val, list):
                arr = val
            elif isinstance(val, str):
                arr = re.split(r"[|,;/]+", val)
            else:
                arr = []
            return [s.strip() for s in arr if isinstance(s, str) and s.strip()]

        # Normalize people
        people = [re.sub(r"[^\w'\-\s]", "", p).strip() for p in to_list(analysis.get("people"))]
        people = [p for p in people if len(p) >= 2 and not re.search(r"\d", p)]

        # Normalize topics
        topics = [t.strip() for t in to_list(analysis.get("topics"))]
        # Filter placeholder-y tokens like topic1/topic 2 etc.
        invalid_topics = {"topic1", "topic 1", "topic2", "topic 2", "subject1", "subject 1", "subject2", "subject 2"}
        topics = [t for t in topics if len(t) >= 2 and t.lower() not in invalid_topics]

        # Normalize key points
        def _clean_point(s: str) -> str:
            t = s.strip().strip('"\'')
            # remove placeholder tokens like point1/point 1/point2/point 2
            t = re.sub(r'\bpoint\s*1\b', '', t, flags=re.IGNORECASE)
            t = re.sub(r'\bpoint\s*2\b', '', t, flags=re.IGNORECASE)
            # collapse extra spaces
            t = re.sub(r'\s{2,}', ' ', t).strip()
            # strip trailing punctuation-only
            t = t.rstrip(' .;:,-"\'')
            return t

        key_points_raw = [_clean_point(kp) for kp in to_list(analysis.get("key_points"))]
        invalid_points = {"none", "none found", "n/a", "na", "point", "bullet", "item"}
        key_points = [kp for kp in key_points_raw if kp and kp.lower() not in invalid_points and len(kp) >= 5]

        # Normalize quotes
        quotes = [q.strip().strip('"\'').strip() for q in to_list(analysis.get("notable_quotes"))]
        invalid_quote_markers = {"none", "none found", "short direct quote", "direct quote", "quote", "sample quote", "placeholder", "exact quote from the text"}
        quotes = [q for q in quotes if len(q) >= 5 and q.lower() not in invalid_quote_markers and "short direct quote" not in q.lower() and 'note confidence' not in q.lower()]

        # Normalize categories
        categories = [c.lower() for c in to_list(analysis.get("categories"))]

        # Normalize sentiment: pick a single allowed label
        allowed = {"analytical","excited","frustrated","joking","serious","concerned","optimistic","pessimistic","angry","sad","joyful","neutral"}
        sent_val = analysis.get("sentiment")
        chosen = "unknown"
        if isinstance(sent_val, str):
            for token in re.split(r"[|,;/\s]+", sent_val):
                tok = token.strip().lower()
                if tok in allowed:
                    chosen = tok
                    break
        elif isinstance(sent_val, list):
            for token in sent_val:
                tok = str(token).strip().lower()
                if tok in allowed:
                    chosen = tok
                    break

        # De-duplicate while preserving order
        def dedupe(seq: List[str]) -> List[str]:
            seen = set()
            out = []
            for s in seq:
                if s not in seen:
                    out.append(s)
                    seen.add(s)
            return out

        # Normalize summary
        summary = analysis.get("summary")
        if isinstance(summary, list):
            summary = " ".join([str(s).strip() for s in summary if str(s).strip()])
        elif not isinstance(summary, str):
            summary = ""
        summary = summary.strip()

        return {
            "people": dedupe(people),
            "topics": dedupe(topics),
            "sentiment": chosen,
            "key_points": dedupe(key_points)[:10],
            "notable_quotes": dedupe(quotes)[:5],
            "categories": dedupe(categories),
            "summary": summary
        }


class AnalysisAggregator:
    """Aggregate analysis results from multiple chunks"""

    @staticmethod
    def aggregate(chunk_analyses: List[Dict[str, Any]], metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Combine chunk analyses into final report"""
        # Count occurrences
        people_count = {}
        topics_count = {}
        sentiments = []
        all_points = []
        all_quotes = []
        categories_count = {}
        per_chunk_summaries: List[Dict[str, Any]] = []

        for analysis in chunk_analyses:
            # People
            for person in analysis.get('people', []):
                people_count[person] = people_count.get(person, 0) + 1

            # Topics
            for topic in analysis.get('topics', []):
                topics_count[topic] = topics_count.get(topic, 0) + 1

            # Sentiment (support single label or pipe-delimited)
            sentiment = analysis.get('sentiment', 'unknown')
            if isinstance(sentiment, str):
                tokens = [t.strip() for t in re.split(r"[|,;/]+", sentiment) if t.strip()]
                sentiments.extend(tokens or ([] if sentiment == 'unknown' else [sentiment]))
            elif isinstance(sentiment, list):
                sentiments.extend([str(s).strip() for s in sentiment if str(s).strip()])

            # Key points
            all_points.extend(analysis.get('key_points', []))

            # Quotes
            all_quotes.extend(analysis.get('notable_quotes', []))

            # Categories (split any combined tags)
            cats = analysis.get('categories', []) or []
            norm_cats: List[str] = []
            for c in cats:
                if isinstance(c, str):
                    norm_cats.extend([t.strip().lower() for t in re.split(r"[|,;/]+", c) if t.strip()])
            for cat in norm_cats:
                categories_count[cat] = categories_count.get(cat, 0) + 1

            # Collect per-chunk summary
            summary_text = analysis.get('summary', '')
            if isinstance(summary_text, list):
                summary_text = " ".join([str(s).strip() for s in summary_text if str(s).strip()])
            if isinstance(summary_text, str) and summary_text.strip():
                per_chunk_summaries.append({
                    'chunk_id': analysis.get('chunk_id'),
                    'word_count': analysis.get('word_count'),
                    'summary': summary_text.strip(),
                    'sentiment': (analysis.get('sentiment') or 'unknown')
                })

        # Remove placeholder topics before sorting
        for bad in ["topic1", "topic 1", "topic2", "topic 2", "subject1", "subject 1", "subject2", "subject 2"]:
            topics_count.pop(bad, None)

        # Sort by frequency
        top_people = sorted(people_count.items(), key=lambda x: x[1], reverse=True)
        top_topics = sorted(topics_count.items(), key=lambda x: x[1], reverse=True)
        top_categories = sorted(categories_count.items(), key=lambda x: x[1], reverse=True)

        # Most common sentiment (single label)
        if sentiments:
            sentiment_counts = {}
            for s in sentiments:
                sentiment_counts[s] = sentiment_counts.get(s, 0) + 1
            dominant_sentiment = max(sentiment_counts.items(), key=lambda x: x[1])[0]
        else:
            dominant_sentiment = "unknown"

        return {
            "metadata": metadata,
            "analysis": {
                "people_mentioned": [
                    {"name": name, "mentions": count}
                    for name, count in top_people
                ],
                "topics": [
                    {"topic": topic, "occurrences": count}
                    for topic, count in top_topics[:15]
                ],
                "dominant_sentiment": dominant_sentiment,
                "sentiment_distribution": dict(sorted(
                    {s: sentiments.count(s) for s in set(sentiments)}.items(),
                    key=lambda x: x[1],
                    reverse=True
                )),
                "key_points": all_points[:10],
                "notable_quotes": all_quotes[:5],
                "categories": [cat for cat, _ in top_categories]
            },
            "stats": {
                "total_chunks": len(chunk_analyses),
                "unique_people": len(people_count),
                "unique_topics": len(topics_count)
            },
            "chunk_summaries": per_chunk_summaries
        }


def generate_markdown_report(analysis: Dict[str, Any], output_path: Path):
    """Generate human-readable markdown report"""
    md = []
    meta = analysis['metadata']
    data = analysis['analysis']
    stats = analysis['stats']

    md.append(f"# Transcript Analysis: {meta['title']}")
    md.append(f"\n**Video ID**: {meta['video_id']}")
    md.append(f"**Date**: {meta['date']}")
    md.append(f"\n## Overview")
    md.append(f"- **Dominant Sentiment**: {data['dominant_sentiment']}")
    md.append(f"- **Chunks Analyzed**: {stats['total_chunks']}")
    md.append(f"- **Unique People Mentioned**: {stats['unique_people']}")
    md.append(f"- **Unique Topics**: {stats['unique_topics']}")

    if data['categories']:
        md.append(f"\n## Categories")
        md.append(", ".join(data['categories']))

    if data['people_mentioned']:
        md.append(f"\n## People Mentioned")
        for person in data['people_mentioned']:
            md.append(f"- **{person['name']}** ({person['mentions']} mentions)")

    if data['topics']:
        md.append(f"\n## Main Topics")
        for topic in data['topics'][:10]:
            md.append(f"- {topic['topic']} ({topic['occurrences']} occurrences)")

    if data['key_points']:
        md.append(f"\n## Key Points")
        for i, point in enumerate(data['key_points'], 1):
            md.append(f"{i}. {point}")

    if data['notable_quotes']:
        md.append(f"\n## Notable Quotes")
        for quote in data['notable_quotes']:
            md.append(f"> {quote}")

    if data['sentiment_distribution']:
        md.append(f"\n## Sentiment Distribution")
        for sentiment, count in data['sentiment_distribution'].items():
            md.append(f"- {sentiment}: {count} chunks")

    # Per-chunk summaries
    chunk_summaries = analysis.get('chunk_summaries') or []
    if chunk_summaries:
        md.append(f"\n## Chunk Summaries")
        for cs in chunk_summaries:
            cid = cs.get('chunk_id')
            wc = cs.get('word_count')
            sent = cs.get('sentiment', 'unknown')
            sm = cs.get('summary', '')
            md.append(f"- Chunk {cid} ({wc} words, {sent}): {sm}")

    # Optional summaries
    summaries = analysis.get('summaries')
    if summaries and any(summaries.values()):
        md.append(f"\n## Summaries")
        if summaries.get('tldr_one_sentence'):
            md.append(f"\n### TL;DR")
            md.append(summaries['tldr_one_sentence'])
        if summaries.get('summary_paragraph'):
            md.append(f"\n### Summary")
            md.append(summaries['summary_paragraph'])
        if summaries.get('bullet_points'):
            md.append(f"\n### Bullet Points")
            for b in summaries['bullet_points']:
                md.append(f"- {b}")
        if summaries.get('social_caption') or summaries.get('hashtags'):
            md.append(f"\n### Social Caption")
            if summaries.get('social_caption'):
                md.append(summaries['social_caption'])
            if summaries.get('hashtags'):
                md.append(" ".join(summaries['hashtags']))

    md.append(f"\n---\n*Generated by VidOps Transcript Analyzer*")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(md))

def generate_chunk_markdown(chunk_analysis: Dict[str, Any], meta: Dict[str, Any], output_path: Path):
    """Generate markdown report for a single chunk"""
    md: List[str] = []
    cid = chunk_analysis.get('chunk_id')
    words = chunk_analysis.get('word_count')
    sent = chunk_analysis.get('sentiment', 'unknown')

    md.append(f"# Chunk Analysis: {meta['title']}")
    md.append(f"\n**Video ID**: {meta['video_id']}")
    md.append(f"**Date**: {meta['date']}")
    md.append(f"**Chunk**: {cid}")
    md.append(f"**Words**: {words}")
    md.append(f"**Sentiment**: {sent}")

    people = chunk_analysis.get('people') or []
    if people:
        md.append("\n## People")
        for p in people:
            md.append(f"- {p}")

    topics = chunk_analysis.get('topics') or []
    if topics:
        md.append("\n## Topics")
        for t in topics:
            md.append(f"- {t}")

    kps = chunk_analysis.get('key_points') or []
    if kps:
        md.append("\n## Key Points")
        for i, kp in enumerate(kps, 1):
            md.append(f"{i}. {kp}")

    quotes = chunk_analysis.get('notable_quotes') or []
    if quotes:
        md.append("\n## Notable Quotes")
        for q in quotes:
            md.append(f"> {q}")

    cats = chunk_analysis.get('categories') or []
    if cats:
        md.append("\n## Categories")
        md.append(", ".join(cats))

    summary = (chunk_analysis.get('summary') or '').strip()
    if summary:
        md.append("\n## Summary")
        md.append(summary)

    md.append("\n---\n*Generated by VidOps Transcript Analyzer*")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(md))


def main():
    parser = argparse.ArgumentParser(description='Analyze video transcripts with AI')
    parser.add_argument('inputs', nargs='+', type=Path, help='One or more transcript files or directories')
    parser.add_argument('--output', type=Path, help='Output directory (default: analyzed/)')
    parser.add_argument('--model', default='llama3.2', help='Ollama model to use')
    parser.add_argument('--chunk-size', type=int, default=1000, help='Words per chunk')
    parser.add_argument('--overlap', type=int, default=150, help='Overlap between chunks')
    parser.add_argument('--ollama-url', default='http://localhost:11434', help='Ollama API URL')
    parser.add_argument('--no-summaries', action='store_true', help='Skip generating summary variants')
    # Quality and generation controls
    parser.add_argument('--quality', choices=['fast','balanced','thorough'], default='balanced', help='Preset quality profile')
    parser.add_argument('--temperature', type=float, help='Sampling temperature (e.g., 0.2)')
    parser.add_argument('--top-p', type=float, dest='top_p', help='Top-p nucleus sampling')
    parser.add_argument('--top-k', type=int, dest='top_k', help='Top-k sampling')
    parser.add_argument('--num-predict', type=int, dest='num_predict', help='Max tokens to generate')
    parser.add_argument('--num-ctx', type=int, dest='num_ctx', help='Context window size (tokens)')
    parser.add_argument('--repeat-penalty', type=float, dest='repeat_penalty', help='Repetition penalty')

    args = parser.parse_args()

    # Resolve inputs (files, globs already expanded by shell, or directories)
    def resolve_paths(paths: List[Path]) -> List[Path]:
        resolved: List[Path] = []
        for p in paths:
            if p.is_dir():
                # Prefer *.transcript.en.vtt, otherwise any .vtt
                vtts = sorted(list(p.glob('*.transcript.en.vtt')))
                if not vtts:
                    vtts = sorted(list(p.glob('*.vtt')))
                resolved.extend(vtts)
            else:
                # If path includes wildcards and wasn't expanded (rare), expand here
                if any(ch in str(p) for ch in ['*', '?', '[']):
                    for match in sorted(p.parent.glob(p.name)):
                        if match.is_file():
                            resolved.append(match)
                else:
                    if p.exists() and p.is_file():
                        resolved.append(p)
        # De-duplicate while keeping order
        seen = set()
        unique: List[Path] = []
        for x in resolved:
            if x not in seen:
                unique.append(x)
                seen.add(x)
        return unique

    files = resolve_paths(args.inputs)
    if not files:
        print("Error: No transcript files found to analyze.", file=sys.stderr)
        sys.exit(1)

    # Set output directory
    output_dir = args.output or Path('analyzed')
    output_dir.mkdir(parents=True, exist_ok=True)

    # Shared helpers
    def analyze_file(input_path: Path) -> None:
        # Extract metadata from filename
        filename = input_path.stem
        parts = filename.split('__')
        video_id = parts[0] if parts else 'unknown'
        title = parts[1] if len(parts) > 1 else 'Unknown Title'
        # Strip trailing transcript language markers from title, if present
        title = re.sub(r"\.(transcript\.[a-z]{2}|[a-z]{2})$", "", title)

        # Try to load richer metadata from sibling info.json
        date = 'unknown'
        info_path = None
        try:
            cand = next((p for p in input_path.parent.glob(f"{video_id}__*.info.json")), None)
            if cand and cand.exists():
                info_path = cand
                with open(cand, 'r', encoding='utf-8') as jf:
                    info = json.load(jf)
                    title = info.get('title', title)
                    # Try typical date fields
                    date = info.get('upload_date') or info.get('date') or date
                    # Normalize date like YYYYMMDD -> YYYY-MM-DD
                    if isinstance(date, str) and re.match(r'^\d{8}$', date):
                        date = f"{date[0:4]}-{date[4:6]}-{date[6:8]}"
        except Exception:
            pass

        # Extract date from title as fallback
        if date == 'unknown':
            date_match = re.search(r'(\d{4}-\d{2}-\d{2})', title)
            date = date_match.group(1) if date_match else 'unknown'

        metadata = {
            'video_id': video_id,
            'title': title,
            'date': date,
            'source_file': str(input_path)
        }

        print(f"Analyzing: {title}")
        print(f"Video ID: {video_id}")

        # Parse transcript
        print("Parsing VTT transcript...")
        parser_obj = VTTParser()
        text = parser_obj.parse(input_path)
        total_words = len(text.split())
        print(f"Total words: {total_words:,}")

        # Chunk transcript
        print(f"Chunking transcript (size={args.chunk_size}, overlap={args.overlap})...")
        chunker = TranscriptChunker(chunk_size=args.chunk_size, overlap=args.overlap)
        chunks = chunker.chunk(text)
        print(f"Created {len(chunks)} chunks")

        # Analyze chunks
        print(f"Analyzing chunks with {args.model}...")
        # Build Ollama options from quality profile and overrides
        def build_options() -> Dict[str, Any]:
            # Presets
            presets = {
                'fast':      { 'temperature': 0.6, 'top_p': 0.9, 'top_k': 40,  'num_predict': 256,  'repeat_penalty': 1.05 },
                'balanced':  { 'temperature': 0.3, 'top_p': 0.9, 'top_k': 40,  'num_predict': 512,  'repeat_penalty': 1.1  },
                'thorough':  { 'temperature': 0.2, 'top_p': 0.9, 'top_k': 50,  'num_predict': 1024, 'repeat_penalty': 1.15 },
            }
            opts = presets.get(args.quality, {}).copy()
            # Reasonable context default for many Llama variants; user can override
            if 'num_ctx' not in opts:
                opts['num_ctx'] = 4096
            # Apply CLI overrides when provided
            if args.temperature is not None: opts['temperature'] = args.temperature
            if args.top_p is not None:       opts['top_p'] = args.top_p
            if args.top_k is not None:       opts['top_k'] = args.top_k
            if args.num_predict is not None: opts['num_predict'] = args.num_predict
            if args.num_ctx is not None:     opts['num_ctx'] = args.num_ctx
            if args.repeat_penalty is not None: opts['repeat_penalty'] = args.repeat_penalty
            return opts

        analyzer = OllamaAnalyzer(model=args.model, base_url=args.ollama_url, options=build_options())
        chunk_analyses = []
        # Prepare per-chunk output directory
        chunk_dir = (output_dir / f"{video_id}_chunks")
        chunk_dir.mkdir(parents=True, exist_ok=True)

        for i, chunk in enumerate(chunks, 1):
            print(f"  Chunk {i}/{len(chunks)} ({chunk['word_count']} words)...", end=' ')
            sys.stdout.flush()
            analysis = analyzer.analyze_chunk(chunk['text'], chunk['chunk_id'])
            # Post-filter quotes to ensure they come from this chunk text and are not placeholders
            try:
                chunk_lower = chunk['text'].lower()
                filtered_quotes = []
                for q in (analysis.get('notable_quotes', []) or []):
                    ql = str(q).strip()
                    if not ql:
                        continue
                    qll = ql.lower()
                    if qll in {"short direct quote", "none", "none found", "direct quote", "quote", "sample quote", "placeholder", "exact quote from the text"}:
                        continue
                    if 'note confidence' in qll:
                        continue
                    if qll in chunk_lower:
                        filtered_quotes.append(ql)
                analysis['notable_quotes'] = filtered_quotes[:5]
            except Exception:
                pass
            # Light category clean-up: drop generic example tags if not present in text
            try:
                cats_in = [str(c).strip().lower() for c in (analysis.get('categories') or []) if str(c).strip()]
                generic = {"music", "classical", "review"}
                cats_out = []
                for c in cats_in:
                    if c in generic and c not in chunk_lower:
                        continue
                    cats_out.append(c)
                analysis['categories'] = cats_out
            except Exception:
                pass
            # Clean or synthesize per-chunk summary if model returned placeholder or low-fidelity
            try:
                placeholder_phrases = {
                    'one or two sentence summary of this segment',
                    'summary of this segment',
                    'write a summary',
                    'short summary',
                    'tldr'
                }
                raw_sum = (analysis.get('summary') or '').strip()
                clower = chunk['text'].lower()

                def split_sentences(t: str):
                    sents = re.split(r'(?<=[.!?])\s+', t.strip())
                    return [s.strip() for s in sents if s.strip()]

                def synthesize_from_chunk() -> str:
                    sents = split_sentences(chunk['text'])
                    chosen = []
                    for s in sents:
                        wc = len(s.split())
                        if 12 <= wc <= 35:
                            chosen.append(s)
                            if len(chosen) == 2:
                                break
                    if not chosen and sents:
                        chosen.append(sents[0][:400])
                    return ' '.join(chosen).strip()

                def needs_fallback(summary_text: str) -> bool:
                    if not summary_text:
                        return True
                    sl = summary_text.lower().strip()
                    if sl in placeholder_phrases:
                        return True
                    words = [w for w in re.findall(r"[a-zA-Z]{4,}", sl)]
                    if not words:
                        return True
                    unmatched = [w for w in words if w not in clower]
                    return (len(unmatched) / max(1, len(words))) > 0.5

                if needs_fallback(raw_sum):
                    kps = [str(k).strip() for k in (analysis.get('key_points') or []) if str(k).strip()]
                    if kps:
                        fallback = ' '.join(kps[:2])
                    else:
                        fallback = synthesize_from_chunk()
                    analysis['summary'] = fallback
                else:
                    sents = split_sentences(raw_sum)
                    analysis['summary'] = ' '.join(sents[:2])
            except Exception:
                pass
            # Attach metadata for downstream aggregate/reporting
            analysis['chunk_id'] = chunk['chunk_id']
            analysis['word_count'] = chunk['word_count']
            chunk_analyses.append(analysis)
            # Save per-chunk outputs (JSON + Markdown)
            try:
                # Build safe base name from input filename
                safe_base = re.sub(r"[^A-Za-z0-9._\- ]+", "_", filename)
                chunk_json_path = chunk_dir / f"{safe_base}__chunk_{chunk['chunk_id']:03d}.json"
                with open(chunk_json_path, 'w', encoding='utf-8') as cf:
                    json.dump({
                        'metadata': {
                            'video_id': video_id,
                            'title': title,
                            'date': date,
                            'chunk_id': chunk['chunk_id'],
                            'word_count': chunk['word_count'],
                            'source_file': str(input_path)
                        },
                        'analysis': analysis
                    }, cf, indent=2, ensure_ascii=False)

                chunk_md_path = chunk_dir / f"{safe_base}__chunk_{chunk['chunk_id']:03d}.md"
                generate_chunk_markdown(analysis, metadata, chunk_md_path)
            except Exception:
                pass
            print("✓")

        # Aggregate results
        print("Aggregating results...")
        aggregator = AnalysisAggregator()
        final_analysis = aggregator.aggregate(chunk_analyses, metadata)

        # Recount People Mentioned directly from transcript text (approximate last-name matching)
        try:
            people_list = final_analysis.get('analysis', {}).get('people_mentioned', []) or []
            if people_list:
                # ASCII fold and tokenize transcript
                t_ascii = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii').lower()
                words = re.findall(r"[a-z]+", t_ascii)

                def norm_token(s: str) -> str:
                    s = s.lower()
                    s = re.sub(r"[^a-z]", "", s)
                    s = s.replace('ck', 'k')
                    s = re.sub(r"(.)\1+", r"\1", s)  # collapse repeats
                    s = re.sub(r"[aeiou]+", "", s)    # drop vowels
                    return s

                # Build frequency map of normalized words in transcript
                from collections import Counter
                norm_counts = Counter(norm_token(w) for w in words if w)

                def count_mentions(name: str) -> int:
                    # Prefer full name exact matches first
                    full_re = re.compile(r"\\b" + re.escape(name) + r"\\b", re.IGNORECASE)
                    cnt = len(full_re.findall(t_ascii))
                    # Fallback: approximate last token match using normalized form
                    tokens = re.findall(r"[A-Za-z']+", name)
                    if tokens:
                        last = tokens[-1]
                        if len(last) >= 4:
                            cnt = max(cnt, norm_counts.get(norm_token(last), 0))
                    return cnt

                recounted = []
                for entry in people_list:
                    nm = str(entry.get('name', '')).strip()
                    if not nm:
                        continue
                    recounted.append({'name': nm, 'mentions': count_mentions(nm)})

                if any(e['mentions'] > 0 for e in recounted):
                    recounted.sort(key=lambda x: x['mentions'], reverse=True)
                    final_analysis['analysis']['people_mentioned'] = recounted
        except Exception:
            pass

        # Keep original people_mentioned list from aggregation without post recount

        # Optional summaries
        if not args.no_summaries:
            print("Generating summaries...")
            summaries = analyzer.summarize(final_analysis)
            final_analysis['summaries'] = summaries

        # Save outputs
        json_output = output_dir / f"{video_id}_analysis.json"
        md_output = output_dir / f"{video_id}_analysis.md"

        print(f"Saving JSON to: {json_output}")
        with open(json_output, 'w', encoding='utf-8') as f:
            json.dump(final_analysis, f, indent=2, ensure_ascii=False)

        print(f"Saving report to: {md_output}")
        generate_markdown_report(final_analysis, md_output)

        # Also place overall summaries into the chunk folder alongside per-chunk files
        try:
            safe_base = re.sub(r"[^A-Za-z0-9._\- ]+", "_", filename)
            overall_json_in_dir = chunk_dir / f"{safe_base}__analysis.json"
            overall_md_in_dir = chunk_dir / f"{safe_base}__analysis.md"
            with open(overall_json_in_dir, 'w', encoding='utf-8') as jf:
                json.dump(final_analysis, jf, indent=2, ensure_ascii=False)
            generate_markdown_report(final_analysis, overall_md_in_dir)
        except Exception:
            pass

        print("\n✓ Analysis complete!\n")

    # Process each file and continue on errors
    total = len(files)
    for idx, fpath in enumerate(files, 1):
        print(f"=== [{idx}/{total}] {fpath} ===")
        try:
            analyze_file(fpath)
        except Exception as e:
            print(f"Error analyzing {fpath}: {e}", file=sys.stderr)
            continue


if __name__ == '__main__':
    main()
