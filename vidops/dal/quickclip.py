# vidops/dal/quickclip.py

from typing import List, Optional, Dict, Any
from datetime import datetime
import psycopg2.extras

from vidops.config import load_config

class QuickClipRepository:
    """Repository for QuickClip session and clip data."""

    def __init__(self, conn=None):
        if conn:
            self.conn = conn
            self.should_close = False
        else:
            config = load_config()
            self.conn = psycopg2.connect(
                host=config.database.host,
                port=config.database.port,
                dbname=config.database.name,
                user=config.database.user,
                password=config.database.password,
            )
            self.should_close = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.should_close:
            self.conn.close()

    def create_session(
        self,
        session_id: str,
        ytid: str,
        url: str,
        description: Optional[str] = None,
        tags: Optional[str] = None,
        quality_profile: str = "best",
        session_dir: Optional[str] = None,
    ) -> str:
        """Create a new QuickClip session."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO quickclip_sessions
                (session_id, ytid, url, description, tags, quality_profile, session_dir)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING session_id
                """,
                (session_id, ytid, url, description, tags, quality_profile, session_dir),
            )
            self.conn.commit()
            return cur.fetchone()[0]

    def update_session_stats(
        self,
        session_id: str,
        clips_count: int,
        total_duration_sec: float,
        full_video_saved: bool = False,
    ) -> None:
        """Update session statistics after clips are created."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE quickclip_sessions
                SET clips_count = %s, total_duration_sec = %s, full_video_saved = %s
                WHERE session_id = %s
                """,
                (clips_count, total_duration_sec, full_video_saved, session_id),
            )
            self.conn.commit()

    def create_clip(
        self,
        clip_id: str,
        session_id: str,
        ytid: str,
        start_sec: float,
        end_sec: float,
        label: Optional[str] = None,
        clip_index: int = 1,
        asset_path: Optional[str] = None,
    ) -> str:
        """Create a new clip record."""
        duration_sec = end_sec - start_sec
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO quickclip_clips
                (clip_id, session_id, ytid, start_sec, end_sec, duration_sec, label, clip_index, asset_path)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING clip_id
                """,
                (clip_id, session_id, ytid, start_sec, end_sec, duration_sec, label, clip_index, asset_path),
            )
            self.conn.commit()
            return cur.fetchone()[0]

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session details by ID."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM quickclip_sessions WHERE session_id = %s
                """,
                (session_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_session_clips(self, session_id: str) -> List[Dict[str, Any]]:
        """Get all clips for a session."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM quickclip_clips
                WHERE session_id = %s
                ORDER BY clip_index
                """,
                (session_id,),
            )
            return [dict(row) for row in cur.fetchall()]

    def list_recent_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """List recent QuickClip sessions."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT s.*, v.title
                FROM quickclip_sessions s
                LEFT JOIN videos v ON s.ytid = v.ytid
                ORDER BY s.created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]

    def search_sessions(self, query: str) -> List[Dict[str, Any]]:
        """Search sessions by description, tags, or video title."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT s.*, v.title
                FROM quickclip_sessions s
                LEFT JOIN videos v ON s.ytid = v.ytid
                WHERE s.description ILIKE %s
                   OR s.tags ILIKE %s
                   OR v.title ILIKE %s
                ORDER BY s.created_at DESC
                LIMIT 50
                """,
                (f"%{query}%", f"%{query}%", f"%{query}%"),
            )
            return [dict(row) for row in cur.fetchall()]
