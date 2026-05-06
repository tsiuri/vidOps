#!/usr/bin/env python3
"""
Helper functions for database ingestion during transcription.
"""

import re
import json
from pathlib import Path
from typing import Optional, Dict, Any


def extract_ytid(filename: str) -> Optional[str]:
    """
    Extract YouTube video ID from filename.

    Pattern: {YTID}__*
    Example: "dQw4w9WgXcQ__2024-01-15 - Video Title.mp4"

    Args:
        filename: Filename or path to extract ytid from

    Returns:
        ytid (11-character string) or None if not found
    """
    match = re.search(r'([A-Za-z0-9_-]{11})__', filename)
    return match.group(1) if match else None


def load_video_metadata(media_path: Path) -> Optional[Dict[str, Any]]:
    """
    Load video metadata from .info.json file.

    Args:
        media_path: Path to media file

    Returns:
        Dictionary with video metadata or None if not found
    """
    # Try both possible locations for .info.json
    candidates = [
        media_path.with_suffix('.info.json'),  # Same as media file
        media_path.parent / (media_path.stem + '.info.json')  # In same directory
    ]

    for info_path in candidates:
        if info_path.exists():
            try:
                with open(info_path, 'r', encoding='utf-8') as f:
                    info = json.load(f)
                    return {
                        'url': info.get('webpage_url'),
                        'title': info.get('title'),
                        'upload_date': info.get('upload_date'),
                        'duration': info.get('duration'),
                        'uploader': info.get('uploader'),
                        'channel_id': info.get('channel_id')
                    }
            except (json.JSONDecodeError, IOError) as e:
                print(f"[WARN] Failed to read {info_path}: {e}")
                continue

    return None


def should_ingest(job_id: Optional[str], ytid: Optional[str]) -> bool:
    """
    Determine if transcription should be ingested into database.

    Args:
        job_id: Queue job ID (None if not using queue)
        ytid: Extracted YouTube video ID (None if not a YT video)

    Returns:
        True if should ingest, False otherwise
    """
    import os

    # Only ingest if using database queue
    if os.environ.get('USE_DB_QUEUE') != '1':
        return False

    # Only ingest if we have a valid ytid
    if not ytid:
        return False

    # Only ingest if we have a job_id (running in queue mode)
    if not job_id:
        return False

    return True
