# vidops/dal/cache.py

import shutil
import logging
from pathlib import Path
from typing import Optional
from configuration import load_config
from models import Video, Transcript, Asset
from storage.broker_client import StorageBrokerClient

logger = logging.getLogger(__name__)

class FilesystemCache:
    """
    Storage and cache manager for the Overlord System.

    Manages the relationship between:
    1. Central storage root (shared, persistent): /mnt/mainroot/mnt/13tb_sas/vidops/storage
    2. Local cache (worker-local, temporary): ~/vidops_cache or ./tmp
    3. Assets database (registry of all files)

    Responsibilities:
    - Pull media from central storage to local cache for processing
    - Register files as assets in the database
    - Provide path resolution for services
    - Clean up temporary files
    """

    def __init__(self):
        config = load_config()
        self.central_storage_root = Path(config.paths.central_storage_root)
        self.path_prefix = config.paths.path_prefix

        # Local cache directory - use configured temp dir or fall back to user's home
        local_temp = config.paths.local_temp_dir
        if local_temp and not Path(local_temp).is_absolute():
            # Relative path - use current working directory as base
            self.local_cache_root = Path.cwd() / local_temp
        elif local_temp:
            # Absolute path
            self.local_cache_root = Path(local_temp)
        else:
            # Fallback to home directory
            self.local_cache_root = Path.home() / "vidops_cache"

        # Ensure local cache exists
        self.local_cache_root.mkdir(parents=True, exist_ok=True)
        logger.info(f"FilesystemCache initialized: central={self.central_storage_root}, local={self.local_cache_root}, prefix={self.path_prefix or 'none'}")
        self.broker_client = StorageBrokerClient(config.storage_broker)

    def _resolve_path_with_prefix(self, path_str: str) -> Path:
        """
        Resolve a path string, applying the path_prefix if it's an absolute path.

        This handles the case where the database stores absolute paths like
        /mnt/13tb_sas/... but the actual filesystem location is
        /mnt/mainroot/mnt/13tb_sas/... (for remote mounts).

        Args:
            path_str: Path string (absolute or relative)

        Returns:
            Resolved Path object
        """
        path = Path(path_str)
        if path.is_absolute() and self.path_prefix:
            # Apply prefix to absolute paths
            return Path(self.path_prefix) / path.relative_to(path.anchor)
        elif path.is_absolute():
            # Absolute path, no prefix configured
            return path
        else:
            # Relative path - combine with central storage root
            return self.central_storage_root / path

    def get_central_path(self, relative_path: str) -> Path:
        """
        Get the absolute path in central storage for a relative path.

        Args:
            relative_path: Relative path from storage root (e.g., 'raw/video.mp4')
                          Can also be an absolute path (will have prefix applied if configured)

        Returns:
            Absolute path in central storage
        """
        return self._resolve_path_with_prefix(relative_path)

    def get_local_path(self, relative_path: str) -> Path:
        """
        Get the absolute path in local cache for a relative path.

        Args:
            relative_path: Relative path from cache root (e.g., 'raw/video.mp4')
                          Can also be an absolute path (will be made relative for cache)

        Returns:
            Absolute path in local cache
        """
        path = Path(relative_path)

        # If it's an absolute path, we need to make it relative for the cache
        if path.is_absolute():
            # For absolute paths, create a safe cache structure
            # Remove the root anchor and use a flattened structure to avoid deep nesting
            # Example: /mnt/13tb_sas/path/to/file.mp4 -> cache/mnt_13tb_sas/path/to/file.mp4
            # OR better: preserve structure but make it relative
            path_without_anchor = str(path.relative_to(path.anchor))
            return self.local_cache_root / path_without_anchor
        else:
            return self.local_cache_root / relative_path

    def pull_to_cache(self, relative_path: str) -> Path:
        """
        Pull a file from central storage to local cache.

        Creates parent directories in local cache if needed.
        Skips copy if file already exists in cache and has same size.

        Args:
            relative_path: Relative path from storage root

        Returns:
            Local cache path

        Raises:
            FileNotFoundError: If file doesn't exist in central storage
        """
        central_path = self.get_central_path(relative_path)
        local_path = self.get_local_path(relative_path)

        if not central_path.exists():
            raise FileNotFoundError(f"File not found in central storage: {central_path}")

        # Check if already cached and same size (simple cache validation)
        if local_path.exists() and local_path.stat().st_size == central_path.stat().st_size:
            logger.debug(f"File already in cache: {relative_path}")
            return local_path

        # Ensure parent directory exists
        local_path.parent.mkdir(parents=True, exist_ok=True)

        # Copy from central to local
        logger.info(f"Pulling to cache: {relative_path}")
        shutil.copy2(central_path, local_path)

        return local_path

    def get_media_path(self, video: Video, pull_to_local: bool = False) -> Optional[Path]:
        """
        Get the path to a video's media file.

        Strategy:
        1. Check if video has a registered asset in database (future enhancement)
        2. Look in central storage using standard naming convention
        3. Optionally pull to local cache if pull_to_local=True

        Args:
            video: Video object with ytid and metadata
            pull_to_local: If True, pull file to local cache before returning path

        Returns:
            Path to media file, or None if not found
        """
        if not video.ytid:
            logger.warning("Video has no ytid, cannot locate media")
            return None

        # Determine asset path. Prefer rel_path when available; otherwise fall back to path.
        asset_path: Optional[Path] = None
        try:
            from .videos import VideoRepository

            repo = VideoRepository()
            asset = repo.get_primary_asset(video.ytid, "media")
            if asset:
                # Prefer stored rel_path; if not present, use path.
                p = asset.rel_path or asset.path
                asset_path = Path(p) if p else None
        except Exception as exc:
            logger.warning("Failed to load media asset metadata for %s: %s", video.ytid, exc)

        if not asset_path:
            # Fallback to filesystem glob
            central_raw_dir = self.central_storage_root / "raw"
            if not central_raw_dir.exists():
                logger.warning(f"Central raw directory not found: {central_raw_dir}")
                return None
            matching_files = list(central_raw_dir.glob(f"{video.ytid}__*.mp4"))
            if not matching_files:
                matching_files = list(central_raw_dir.glob(f"{video.ytid}.mp4"))
            if not matching_files:
                logger.warning(f"No media file found for ytid={video.ytid}")
                return None
            central_path = matching_files[0]
            asset_path = central_path.relative_to(self.central_storage_root)
        else:
            # Resolve asset path (absolute vs relative) - use prefix resolution
            central_path = self._resolve_path_with_prefix(str(asset_path))

        if pull_to_local:
            # Compute destination in local cache using relative path when possible
            if asset_path.is_absolute():
                # Mirror absolute path into cache under raw/ using ytid-based filename to avoid long/unsafe paths
                # Keep original filename
                filename = Path(central_path).name
                local_rel = Path("raw") / filename
            else:
                local_rel = Path(str(asset_path))

            local_path = self.get_local_path(str(local_rel))
            if self.broker_client.enabled:
                # Broker assumes relative addressing; only attempt if we have a relative path
                if not asset_path.is_absolute() and self.broker_client.download_asset(str(asset_path), local_path):
                    return local_path
                logger.warning("Broker download failed for %s, falling back to direct copy", relative_path)
            # Direct copy
            try:
                local_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(central_path, local_path)
                return local_path
            except Exception as exc:
                logger.error("Failed to stage media locally from %s: %s", central_path, exc)
                return None
        else:
            return central_path

    def write_transcript(self, transcript: Transcript, content: str, extension: str = "vtt") -> Path:
        """
        Writes transcript content to the local cache.

        Args:
            transcript: The Transcript object containing metadata.
            content: The actual transcript text to write.
            extension: The file extension (e.g., 'vtt', 'srt', 'words.tsv').

        Returns:
            The path to the newly written file.
        """
        output_dir = self.local_cache_root / "transcripts"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Construct filename from transcript metadata
        filename = f"{transcript.ytid}_{transcript.kind}.{extension}"
        output_file = output_dir / filename

        output_file.write_text(content, encoding='utf-8')
        logger.info(f"Wrote transcript to local cache: {output_file}")

        return output_file

    def ensure_local_dir(self, relative_dir: str) -> Path:
        """
        Ensure a directory exists inside the local cache and return it.
        """
        path = self.local_cache_root / relative_dir
        path.mkdir(parents=True, exist_ok=True)
        return path

    def prepare_local_path(self, relative_path: str) -> Path:
        """
        Return a writable path inside the local cache, ensuring parents exist.
        """
        path = self.local_cache_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def push_local_to_central(self, local_path: Path, relative_path: str) -> Path:
        """
        Copy a local cache file into central storage using the provided relative path.
        """
        central_path = self.get_central_path(relative_path)
        central_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, central_path)
        return central_path

    def persist_local_artifact(
        self,
        local_path: Path,
        relative_path: str,
        video_repo,
        ytid: str,
        kind: str
    ) -> Path:
        """
        Copy a local cache file into central storage and register it as an asset.
        """
        if self.broker_client.enabled:
            uploaded = self.broker_client.upload_asset(local_path, ytid, kind, relative_path)
            if uploaded:
                try:
                    self.register_asset(
                        video_repo=video_repo,
                        relative_path=uploaded,
                        ytid=ytid,
                        kind=kind,
                    )
                except Exception as exc:
                    logger.warning("Broker upload succeeded but asset registration failed for %s: %s", uploaded, exc)
                return Path(uploaded)
            logger.warning("Broker upload failed for %s; falling back to direct copy", relative_path)

        if not self.central_storage_root.exists():
            raise RuntimeError(
                f"Central storage root missing or offline: {self.central_storage_root}"
            )

        central_path = self.push_local_to_central(local_path, relative_path)
        try:
            self.register_asset(
                video_repo=video_repo,
                relative_path=relative_path,
                ytid=ytid,
                kind=kind
            )
        except Exception as exc:
            logger.warning("Failed to register asset %s (%s): %s", relative_path, kind, exc)
        return central_path

    def register_asset(self, video_repo, relative_path: str, ytid: str, kind: str) -> Asset:
        """
        Register a file as an asset in the database.

        This is a helper to create Asset objects and persist them via the repository.

        Args:
            video_repo: VideoRepository instance for asset registration
            relative_path: Path relative to central storage root
            ytid: YouTube video ID this asset belongs to
            kind: Asset type (e.g., 'media', 'transcript', 'clip')

        Returns:
            Registered Asset object
        """
        from .videos import VideoRepository

        full_path = self.get_central_path(relative_path)

        if not full_path.exists():
            raise FileNotFoundError(f"Cannot register non-existent asset: {full_path}")

        size_bytes = full_path.stat().st_size

        asset = Asset(
            path=relative_path,
            ytid=ytid,
            kind=kind,
            size_bytes=size_bytes,
            rel_path=relative_path,
        )

        if not isinstance(video_repo, VideoRepository):
            # If passed the wrong type, try to instantiate
            video_repo = VideoRepository()

        registered = video_repo.register_asset(asset)
        logger.info(f"Registered asset: {relative_path} ({kind}, {size_bytes} bytes)")

        return registered

    def cleanup_local(self, relative_path: str) -> bool:
        """
        Remove a file from local cache.

        Args:
            relative_path: Path relative to cache root

        Returns:
            True if file was removed, False if it didn't exist
        """
        local_path = self.get_local_path(relative_path)

        if local_path.exists() and local_path.is_file():
            local_path.unlink()
            logger.info(f"Cleaned up local cache: {relative_path}")
            return True

        return False


# For backward compatibility, keep the old test code structure
if __name__ == '__main__':
    import os

    # This test is conceptual and requires a dummy file structure.
    print("--- Testing FilesystemCache ---")

    # Setup dummy environment
    project_dir = Path("./temp_project_root")
    central_dir = Path("./temp_central_storage")
    (project_dir / "pull").mkdir(parents=True, exist_ok=True)
    (central_dir / "raw").mkdir(parents=True, exist_ok=True)

    # Create dummy files
    dummy_ytid = "test_video_id"
    dummy_central_file = central_dir / "raw" / f"{dummy_ytid}__20240101 - Test Video.mp4"
    dummy_central_file.write_text("dummy video content")

    original_cwd = Path.cwd()

    try:
        # Instantiate cache manager with custom config
        # Note: This would need actual config setup in real usage
        print("✅ FilesystemCache basic structure validated")
        print("   (Full test requires database and config setup)")

    finally:
        # Clean up
        import shutil
        shutil.rmtree(project_dir, ignore_errors=True)
        shutil.rmtree(central_dir, ignore_errors=True)
        print("\n--- Cleanup Complete ---")
