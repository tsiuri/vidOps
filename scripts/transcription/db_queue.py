#!/usr/bin/env python3
"""
PostgreSQL-backed transcription queue client.

This module provides the TranscriptionQueue class for interacting with the
database queue system for GPU/CPU transcription workers.

Usage:
    from db_queue import TranscriptionQueue

    queue = TranscriptionQueue()
    job_id = queue.enqueue('/path/to/video.mp4', model='medium')
    job = queue.claim('worker-1', 'nvidia')
    queue.complete_job(job['job_id'], output_vtt='/path/to/output.vtt')
"""

import os
import json
import psycopg2
import psycopg2.extras
from typing import Optional, Dict, Any, List
from pathlib import Path


class TranscriptionQueue:
    """PostgreSQL-backed transcription queue client"""

    def __init__(self, dsn: Optional[str] = None):
        """
        Initialize queue connection.

        Args:
            dsn: PostgreSQL connection string, or reads from config
        """
        if dsn is None:
            dsn = self._load_dsn_from_config()
        self.dsn = dsn

    def _load_dsn_from_config(self) -> str:
        """Load database credentials from db.cfg or config.local.json"""
        # Try db.cfg first (vidops style - can be JSON or INI)
        db_cfg = Path(__file__).parent.parent.parent / "db.cfg"
        if db_cfg.exists():
            content = db_cfg.read_text()
            # Try JSON format first
            try:
                cfg = json.loads(content)
                return f"host={cfg['db_host']} port={cfg['db_port']} " \
                       f"dbname={cfg['db_name']} user={cfg['db_user']} " \
                       f"password={cfg['db_password']}"
            except json.JSONDecodeError:
                # Try INI format
                config = {}
                for line in content.splitlines():
                    if '=' in line and not line.strip().startswith('#'):
                        key, val = line.split('=', 1)
                        config[key.strip()] = val.strip()
                return f"host={config['DB_HOST']} port={config['DB_PORT']} " \
                       f"dbname={config['DB_NAME']} user={config['DB_USER']} " \
                       f"password={config['DB_PASSWORD']}"

        # Fall back to config.local.json (db-and-analysis style)
        config_path = Path(__file__).parent.parent.parent / "config.local.json"
        if config_path.exists():
            cfg = json.loads(config_path.read_text())
            return f"host={cfg['db_host']} port={cfg['db_port']} " \
                   f"dbname={cfg['db_name']} user={cfg['db_user']} " \
                   f"password={cfg['db_password']}"

        raise RuntimeError("No database config found (db.cfg or config.local.json)")

    def _conn(self):
        """Create database connection"""
        return psycopg2.connect(self.dsn)

    def enqueue(
        self,
        media_path: str,
        model: str = "medium",
        language: str = "en",
        output_format: str = "vtt",
        priority: int = 0,
        options: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Submit a transcription job to the queue.

        Args:
            media_path: Absolute path to media file
            model: Whisper model size (tiny|base|small|medium|large)
            language: Language code or None for auto-detect
            output_format: vtt|srt|both
            priority: Higher = more urgent (default: 0)
            options: Additional options (VAD, thresholds, etc.)

        Returns:
            job_id: Unique job identifier
        """
        opts = options or {}
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT enqueue_transcription_job(%s, %s, %s, %s, %s)",
                    (media_path, model, language, priority, json.dumps(opts))
                )
                job_id = cur.fetchone()[0]
        return job_id

    def claim(
        self,
        worker_id: str,
        worker_type: str,
        lease_seconds: int = 3600
    ) -> Optional[Dict[str, Any]]:
        """
        Claim the next available job from the queue.

        Args:
            worker_id: Unique worker identifier
            worker_type: nvidia|cpu|amd
            lease_seconds: How long to hold the lease

        Returns:
            Job details dict or None if no jobs available
        """
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM claim_transcription_job(%s, %s, %s)",
                    (worker_id, worker_type, lease_seconds)
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def update_status(
        self,
        job_id: str,
        status: str,
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Update job status"""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT update_job_status(%s, %s, %s, %s)",
                    (job_id, status, error, json.dumps(metadata) if metadata else None)
                )

    def heartbeat(
        self,
        worker_id: str,
        status: str = 'idle',
        stats: Optional[Dict[str, Any]] = None
    ):
        """Send worker heartbeat"""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT worker_heartbeat(%s, %s, %s)",
                    (worker_id, status, json.dumps(stats or {}))
                )

    def register_worker(
        self,
        worker_id: str,
        worker_type: str,
        hostname: str,
        gpu_index: Optional[int] = None,
        gpu_uuid: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Register worker with the queue"""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT register_worker(%s, %s, %s, %s, %s, %s)",
                    (worker_id, worker_type, hostname, gpu_index, gpu_uuid,
                     json.dumps(metadata or {}))
                )

    def complete_job(
        self,
        job_id: str,
        output_vtt: Optional[str] = None,
        output_srt: Optional[str] = None,
        output_words_tsv: Optional[str] = None,
        processing_time_sec: Optional[float] = None
    ):
        """Mark job as completed with output paths"""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE transcribe_jobs
                    SET
                        status = 'completed',
                        completed_at = now(),
                        output_vtt = %s,
                        output_srt = %s,
                        output_words_tsv = %s,
                        processing_time_sec = %s
                    WHERE job_id = %s
                    """,
                    (output_vtt, output_srt, output_words_tsv,
                     processing_time_sec, job_id)
                )
                cur.execute(
                    """
                    INSERT INTO transcribe_job_log (job_id, event_type, message)
                    VALUES (%s, 'completed', 'Job completed successfully')
                    """,
                    (job_id,)
                )

    def fail_job(self, job_id: str, error: str):
        """Mark job as failed"""
        self.update_status(job_id, 'failed', error)

    def ingest_transcription(
        self,
        job_id: str,
        ytid: str,
        words_tsv_path: str,
        model: str,
        video_metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Ingest transcription words into database.

        Args:
            job_id: Queue job ID
            ytid: YouTube video ID (extracted from filename)
            words_tsv_path: Path to words.tsv file
            model: Whisper model used (tiny/base/small/medium/large)
            video_metadata: Optional video metadata from .info.json

        Returns:
            Number of words inserted

        Raises:
            Exception if ingestion fails
        """
        import csv

        source = f'whisper-{model}'

        with self._conn() as conn:
            with conn.cursor() as cur:
                # 1. Ensure video exists (create if needed)
                if video_metadata:
                    cur.execute(
                        """
                        INSERT INTO videos (ytid, url, title, upload_date, duration_sec)
                        VALUES (%(ytid)s, %(url)s, %(title)s, %(upload_date)s, %(duration)s)
                        ON CONFLICT (ytid) DO UPDATE SET
                            title = COALESCE(EXCLUDED.title, videos.title),
                            upload_date = COALESCE(EXCLUDED.upload_date, videos.upload_date),
                            duration_sec = COALESCE(EXCLUDED.duration_sec, videos.duration_sec)
                        """,
                        {
                            'ytid': ytid,
                            'url': video_metadata.get('url'),
                            'title': video_metadata.get('title'),
                            'upload_date': video_metadata.get('upload_date'),
                            'duration': video_metadata.get('duration')
                        }
                    )
                else:
                    # Ensure ytid exists even without metadata
                    cur.execute(
                        "INSERT INTO videos (ytid) VALUES (%s) ON CONFLICT (ytid) DO NOTHING",
                        (ytid,)
                    )

                # 2. Read and parse words.tsv
                words_data = []
                idx = 0
                with open(words_tsv_path, 'r', encoding='utf-8', errors='ignore') as f:
                    reader = csv.DictReader(f, delimiter='\t')
                    for row in reader:
                        try:
                            start_sec = float(row['start'])
                            end_sec = float(row['end'])
                            word = row['word'].strip()
                            confidence = float(row.get('confidence', 0.0))
                            seg = int(row.get('seg', 0)) if row.get('seg') else None

                            if word and end_sec >= start_sec:
                                words_data.append((
                                    ytid,
                                    source,
                                    idx,
                                    word.lower(),
                                    start_sec,
                                    end_sec,
                                    confidence,
                                    seg if seg else None
                                ))
                                idx += 1
                        except (ValueError, KeyError) as e:
                            # Skip malformed rows
                            continue

                # 3. Bulk insert words
                if words_data:
                    from psycopg2.extras import execute_values
                    execute_values(
                        cur,
                        """
                        INSERT INTO words (ytid, source, idx, word, start_sec, end_sec, confidence, segment_id)
                        VALUES %s
                        ON CONFLICT (ytid, source, idx) DO UPDATE SET
                            word = EXCLUDED.word,
                            start_sec = EXCLUDED.start_sec,
                            end_sec = EXCLUDED.end_sec,
                            confidence = EXCLUDED.confidence,
                            segment_id = EXCLUDED.segment_id
                        """,
                        words_data
                    )
                    word_count = len(words_data)
                else:
                    word_count = 0

                # 4. Register transcript
                cur.execute(
                    """
                    INSERT INTO transcripts (ytid, kind, lang, path, word_count)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (ytid, kind, lang) DO UPDATE SET
                        path = EXCLUDED.path,
                        word_count = EXCLUDED.word_count
                    """,
                    (ytid, f'words_whisper_{model}', 'en', words_tsv_path, word_count)
                )

                # 5. Log ingestion
                cur.execute(
                    """
                    INSERT INTO transcribe_job_log (job_id, event_type, message, metadata)
                    VALUES (%s, 'progress', %s, %s)
                    """,
                    (
                        job_id,
                        f'Ingested {word_count} words into database',
                        json.dumps({'words': word_count, 'model': model, 'source': source})
                    )
                )

                return word_count

    def get_queue_stats(self) -> Dict[str, int]:
        """Get queue statistics"""
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM transcribe_queue_stats")
                return {row['status']: row['job_count'] for row in cur.fetchall()}

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get job details by job_id"""
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM transcribe_jobs WHERE job_id = %s", (job_id,))
                row = cur.fetchone()
                return dict(row) if row else None

    def get_pending_count(self) -> int:
        """Get count of pending jobs"""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM transcribe_jobs WHERE status = 'pending'")
                return cur.fetchone()[0]

    def recover_stale_jobs(self, lease_buffer_seconds: int = 300) -> List[Dict[str, Any]]:
        """Recover jobs with expired leases"""
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM recover_stale_jobs(%s)", (lease_buffer_seconds,))
                return [dict(row) for row in cur.fetchall()]


if __name__ == "__main__":
    # Quick test
    print("Testing TranscriptionQueue...")
    queue = TranscriptionQueue()
    print("✓ Queue initialized successfully")
    stats = queue.get_queue_stats()
    print(f"✓ Queue stats: {stats}")
