import os
from pathlib import Path

import pytest

from dal.cache import FilesystemCache
from models import Video, Transcript
import configuration as config_module


@pytest.fixture
def cache_env(monkeypatch, tmp_path):
    """
    Sets up temporary central/local storage paths for FilesystemCache tests.
    """
    central_root = tmp_path / "central"
    central_root.mkdir()
    (central_root / "raw").mkdir()

    # Ensure load_config reads these values
    monkeypatch.setenv("VIDOPS_CENTRAL_STORAGE", str(central_root))
    monkeypatch.chdir(tmp_path)

    # Reset cached config so env vars take effect
    config_module._config_instance = None

    yield central_root

    config_module._config_instance = None


def _create_video(ytid: str) -> Video:
    return Video(
        ytid=ytid,
        url=f"https://youtube.com/watch?v={ytid}",
        title="Test Video"
    )


def test_pull_to_cache_and_get_media_path(cache_env):
    central_root = cache_env
    media_file = central_root / "raw" / "ABC123XYZ__20240101 - Test.mp4"
    media_file.write_text("dummy content")

    cache = FilesystemCache()
    video = _create_video("ABC123XYZ")

    # Central path lookup
    central_path = cache.get_media_path(video, pull_to_local=False)
    assert central_path == media_file

    # Local cache pull
    local_path = cache.get_media_path(video, pull_to_local=True)
    assert local_path.exists()
    assert local_path.read_text() == "dummy content"
    assert local_path.is_file()
    assert Path(local_path).relative_to(cache.local_cache_root).parts[0] == "raw"


def test_write_transcript_and_cleanup(cache_env):
    cache = FilesystemCache()
    transcript = Transcript(
        ytid="DEF456ABC",
        kind="words_whisper_medium",
        lang="en"
    )

    path = cache.write_transcript(transcript, "WEBVTT\n\n00:00 --> 00:01\nHello")
    assert path.exists()
    assert "DEF456ABC_words_whisper_medium.vtt" in path.name
    assert path.read_text() == "WEBVTT\n\n00:00 --> 00:01\nHello"

    rel_path = path.relative_to(cache.local_cache_root)
    removed = cache.cleanup_local(str(rel_path))
    assert removed
    assert not path.exists()
