# tests/models/test_models.py

import pytest
from datetime import datetime, date, UTC
from vidops.models import Video, Asset, Job, JobStatus, Worker, WorkerStatus, Transcript, Word

@pytest.fixture
def sample_video_data():
    return {
        'ytid': 'test_ytid',
        'url': 'http://example.com/video',
        'title': 'Test Video Title',
        'upload_date': date(2023, 1, 1),
        'duration_sec': 300,
        'channel': 'Test Channel',
        'created_at': datetime.now(UTC),
        'updated_at': datetime.now(UTC)
    }

@pytest.fixture
def sample_asset_data():
    return {
        'path': '/path/to/asset.mp4',
        'ytid': 'test_ytid',
        'kind': 'media',
        'size_bytes': 1024 * 1024,
        'created_at': datetime.now(UTC)
    }

@pytest.fixture
def sample_job_data():
    return {
        'job_id': 'job_123',
        'job_type': 'transcription',
        'status': JobStatus.PENDING.value,
        'priority': 10,
        'ytid': 'test_ytid',
        'media_path': '/path/to/media.opus',
        'config': {'model': 'small'},
        'created_at': datetime.now(UTC),
        'updated_at': datetime.now(UTC)
    }

@pytest.fixture
def sample_worker_data():
    return {
        'worker_id': 'worker_abc',
        'machine_alias': 'test-machine',
        'worker_type': 'transcription',
        'status': WorkerStatus.IDLE.value,
        'capabilities': ['gpu', 'cuda'],
        'pid': 1234,
        'hostname': 'test-host',
        'registered_at': datetime.now(UTC),
        'last_heartbeat': datetime.now(UTC)
    }

@pytest.fixture
def sample_transcript_data():
    return {
        'ytid': 'test_ytid',
        'kind': 'words_whisper_small',
        'lang': 'en',
        'path': '/path/to/transcript.vtt',
        'word_count': 100,
        'segment_count': 10,
        'created_at': datetime.now(UTC)
    }

@pytest.fixture
def sample_word_data():
    return {
        'ytid': 'test_ytid',
        'source': 'whisper-small',
        'word': 'hello',
        'start_sec': 0.0,
        'end_sec': 0.5,
        'confidence': -0.1,
        'idx': 0
    }


@pytest.mark.unit
def test_video_model(sample_video_data):
    """Test Video model instantiation and serialization."""
    video = Video.from_row(sample_video_data)
    assert video.ytid == sample_video_data['ytid']
    assert video.title == sample_video_data['title']
    assert video.upload_date == sample_video_data['upload_date']
    
    # Test to_dict
    assert video.to_dict()['ytid'] == sample_video_data['ytid']

@pytest.mark.unit
def test_asset_model(sample_asset_data):
    """Test Asset model instantiation and serialization."""
    asset = Asset.from_row(sample_asset_data)
    assert asset.path == sample_asset_data['path']
    assert asset.kind == sample_asset_data['kind']
    
    # Test to_dict
    assert asset.to_dict()['path'] == sample_asset_data['path']

@pytest.mark.unit
def test_job_model(sample_job_data):
    """Test Job model instantiation and serialization."""
    job = Job.from_row(sample_job_data)
    assert job.job_id == sample_job_data['job_id']
    assert job.job_type == sample_job_data['job_type']
    assert job.status == JobStatus.PENDING # Check enum conversion
    
    # Test to_dict
    assert job.to_dict()['job_id'] == sample_job_data['job_id']
    assert job.to_dict()['status'] == 'pending' # Check enum conversion to string

@pytest.mark.unit
def test_worker_model(sample_worker_data):
    """Test Worker model instantiation and serialization."""
    worker = Worker.from_row(sample_worker_data)
    assert worker.worker_id == sample_worker_data['worker_id']
    assert worker.worker_type == sample_worker_data['worker_type']
    assert worker.status == WorkerStatus.IDLE # Check enum conversion
    
    # Test to_dict
    assert worker.to_dict()['worker_id'] == sample_worker_data['worker_id']
    assert worker.to_dict()['status'] == 'idle' # Check enum conversion to string

@pytest.mark.unit
def test_transcript_model(sample_transcript_data):
    """Test Transcript model instantiation and serialization."""
    transcript = Transcript.from_row(sample_transcript_data)
    assert transcript.ytid == sample_transcript_data['ytid']
    assert transcript.kind == sample_transcript_data['kind']
    
    # Test to_dict
    assert transcript.to_dict()['ytid'] == sample_transcript_data['ytid']

@pytest.mark.unit
def test_word_model(sample_word_data):
    """Test Word model instantiation and serialization."""
    word = Word.from_row(sample_word_data)
    assert word.ytid == sample_word_data['ytid']
    assert word.word == sample_word_data['word']
    
    # Test to_tuple
    assert word.to_tuple()[3] == sample_word_data['word']
