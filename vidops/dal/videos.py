# vidops/dal/videos.py

from typing import Optional, List
from psycopg2.extras import Json
from vidops.db import get_connection
from vidops.models import Video, Asset

class VideoRepository:
    """
    Data Access Layer for the 'videos' and related tables.
    Handles all database operations for video metadata.
    """

    def get(self, ytid: str) -> Optional[Video]:
        """
        Retrieves a single video by its YouTube ID.

        Args:
            ytid: The YouTube ID of the video.

        Returns:
            A Video object if found, otherwise None.
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM videos WHERE ytid = %s", (ytid,))
                row = cur.fetchone()
                return Video.from_row(row) if row else None

    def upsert(self, video: Video) -> Video:
        """
        Inserts a new video or updates an existing one based on ytid.
        This is an idempotent operation.

        Args:
            video: The Video object to insert or update.

        Returns:
            The inserted or updated Video object from the database.
        """
        video_dict = video.to_dict()
        # Convert lists to JSONB for tags and categories
        if 'tags' in video_dict and video_dict['tags'] is not None:
            video_dict['tags'] = Json(video_dict['tags'])
        if 'categories' in video_dict and video_dict['categories'] is not None:
            video_dict['categories'] = Json(video_dict['categories'])

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO videos (
                        ytid, url, title, upload_date, duration_sec, channel,
                        channel_id, extractor_key, tags, categories, upload_type, title_date
                    )
                    VALUES (
                        %(ytid)s, %(url)s, %(title)s, %(upload_date)s, %(duration_sec)s, %(channel)s,
                        %(channel_id)s, %(extractor_key)s, %(tags)s, %(categories)s, %(upload_type)s,
                        %(title_date)s
                    )
                    ON CONFLICT (ytid) DO UPDATE SET
                        url = EXCLUDED.url,
                        title = EXCLUDED.title,
                        upload_date = EXCLUDED.upload_date,
                        duration_sec = EXCLUDED.duration_sec,
                        channel = EXCLUDED.channel,
                        channel_id = EXCLUDED.channel_id,
                        extractor_key = EXCLUDED.extractor_key,
                        tags = EXCLUDED.tags,
                        categories = EXCLUDED.categories,
                        upload_type = EXCLUDED.upload_type,
                        title_date = EXCLUDED.title_date,
                        updated_at = NOW()
                    RETURNING *;
                    """,
                    video_dict
                )
                updated_row = cur.fetchone()
                return Video.from_row(updated_row)

    def get_without_transcripts(self, model: str, limit: int = 100) -> List[Video]:
        """
        Gets a list of videos that do not have a transcript of a specific kind.

        Args:
            model: The whisper model name (e.g., 'medium', 'large-v2').
            limit: The maximum number of videos to return.

        Returns:
            A list of Video objects.
        """
        transcript_kind = f"words_whisper_{model}"
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT v.*
                    FROM videos v
                    LEFT JOIN transcripts t ON v.ytid = t.ytid AND t.kind = %s
                    WHERE t.ytid IS NULL
                    ORDER BY v.upload_date DESC
                    LIMIT %s;
                    """,
                    (transcript_kind, limit)
                )
                rows = cur.fetchall()
                return [Video.from_row(row) for row in rows]

# --- Asset related methods ---

    def register_asset(self, asset: Asset) -> Asset:
        """
        Registers a file asset in the 'assets' table.
        If an asset with the same path already exists, it's updated.

        Args:
            asset: The Asset object to register.

        Returns:
            The registered Asset object.
        """
        asset_dict = asset.to_dict()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO assets (path, ytid, kind, size_bytes)
                    VALUES (%(path)s, %(ytid)s, %(kind)s, %(size_bytes)s)
                    ON CONFLICT (path) DO UPDATE SET
                        ytid = EXCLUDED.ytid,
                        kind = EXCLUDED.kind,
                        size_bytes = EXCLUDED.size_bytes
                    RETURNING *;
                    """,
                    asset_dict
                )
                row = cur.fetchone()
                return Asset.from_row(row)

    def list_assets(self, ytid: str, kind: Optional[str] = None) -> List[Asset]:
        """
        Returns all assets for a video, optionally filtered by kind.
        """
        sql = "SELECT * FROM assets WHERE ytid = %s"
        params: List = [ytid]
        if kind:
            sql += " AND kind = %s"
            params.append(kind)
        sql += " ORDER BY created_at DESC"

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
                return [Asset.from_row(row) for row in rows]

    def get_primary_asset(self, ytid: str, kind: str) -> Optional[Asset]:
        """
        Convenience helper to fetch the newest asset of a given kind.
        """
        assets = self.list_assets(ytid, kind)
        return assets[0] if assets else None

    def get_asset(self, path: str) -> Optional[Asset]:
        """
        Retrieves an asset by its unique path.

        Args:
            path: The canonical path of the asset.

        Returns:
            An Asset object if found, otherwise None.
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM assets WHERE path = %s", (path,))
                row = cur.fetchone()
                return Asset.from_row(row) if row else None
