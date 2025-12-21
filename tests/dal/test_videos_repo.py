# tests/dal/test_videos_repo.py

import pytest
from datetime import date
from models import Video
from dal import VideoRepository
from db import check_connection

# Mark all tests in this file as 'integration' and skip if DB is not available
try:
    db_available = check_connection(max_retries=1, delay_sec=1)
except Exception:
    db_available = False

pytestmark = pytest.mark.skipif(not db_available, reason="database not available for integration tests")


@pytest.fixture
def video_repo() -> VideoRepository:
    """Provides a VideoRepository instance for testing."""
    return VideoRepository()

@pytest.fixture
def sample_video() -> Video:
    """Provides a sample Video object."""
    return Video(
        ytid="test001",
        url="http://example.com/test001",
        title="Test Video 1",
        upload_date=date(2023, 1, 1),
        duration_sec=120
    )

def test_upsert_and_get_video(video_repo: VideoRepository, sample_video: Video):
    """
    Tests that a video can be inserted, updated, and retrieved.
    """
    # 1. Insert the video
    inserted_video = video_repo.upsert(sample_video)
    
    assert inserted_video is not None
    assert inserted_video.ytid == sample_video.ytid
    assert inserted_video.title == "Test Video 1"
    
    # 2. Retrieve the video
    retrieved_video = video_repo.get(sample_video.ytid)
    
    assert retrieved_video is not None
    assert retrieved_video.ytid == sample_video.ytid
    assert retrieved_video.title == "Test Video 1"
    
    # 3. Update the video
    retrieved_video.title = "Test Video 1 (Updated)"
    updated_video = video_repo.upsert(retrieved_video)
    
    assert updated_video is not None
    assert updated_video.title == "Test Video 1 (Updated)"
    
    # 4. Retrieve again to confirm update
    retrieved_again = video_repo.get(sample_video.ytid)
    assert retrieved_again is not None
    assert retrieved_again.title == "Test Video 1 (Updated)"

    # Clean up (optional, if not using a transaction-based test fixture)
    # For now, we assume the test DB can be dirtied. A real test suite
    # would use transactions and rollbacks.
    # A real cleanup would go here, e.g., video_repo.delete(sample_video.ytid)

def test_get_nonexistent_video(video_repo: VideoRepository):
    """
    Tests that getting a non-existent video returns None.
    """
    retrieved_video = video_repo.get("nonexistent_id_12345")
    assert retrieved_video is None
