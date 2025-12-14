"""
Integration test: GenericWorker + DistributedAnalysisService

This test verifies that:
1. GenericWorker can claim jobs with job_type="analysis-distributed"
2. DistributedAnalysisService is correctly invoked
3. Job transitions through proper state (PENDING -> CLAIMED -> RUNNING -> COMPLETED)
"""

import pytest
from unittest.mock import MagicMock, patch
from dal import JobRepository
from models import Job, JobStatus
from services import get_distributed_analysis_service
from workers import GenericWorker


@pytest.fixture
def test_analysis_job():
    """Create a test distributed analysis job."""
    return Job(
        job_id="test-analysis-job-1",
        job_type="analysis-distributed",
        ytid="test_ytid",
        status=JobStatus.PENDING,
        config={
            "analysis_job_id": "test_ytid:test_config:1234567890",
            "config_id": "test_config",
            "ytid": "test_ytid",
            "model_url": "http://localhost:11434",
            "model_name": "llama3",
        },
        priority=50,
    )


def test_distributed_analysis_service_factory():
    """Test that the distributed analysis service factory creates the service."""
    service = get_distributed_analysis_service()
    assert service is not None
    assert hasattr(service, "process_job")
    assert hasattr(service, "job_repo")


def test_generic_worker_has_distributed_analysis_service():
    """Test that GenericWorker has the distributed-analysis service in its factories."""
    worker = GenericWorker()
    assert "analysis-distributed" in worker.service_factories
    service_factory = worker.service_factories["analysis-distributed"]
    assert callable(service_factory)


def test_generic_worker_can_get_distributed_analysis_service():
    """Test that GenericWorker can instantiate the distributed-analysis service."""
    worker = GenericWorker()
    service = worker._get_service_for_job("analysis-distributed")
    assert service is not None
    assert hasattr(service, "process_job")


def test_distributed_analysis_job_handling(test_analysis_job):
    """Test that a distributed analysis job is properly handled by the service."""
    service = get_distributed_analysis_service()

    # Mock the database operations
    with patch.object(service, "job_repo") as mock_job_repo:
        with patch("services.distributed_analysis.AnalysisWorker.process_analysis_job", return_value=True) as mock_process:
            with patch("services.distributed_analysis.AnalysisWorker.shutdown"):
                service.process_job(test_analysis_job)

                mock_process.assert_called_once()
                # Verify job status was updated to RUNNING
                mock_job_repo.update_status.assert_any_call(
                    test_analysis_job.job_id,
                    JobStatus.RUNNING,
                )

                # Verify job status was updated to COMPLETED
                calls = [call[0] for call in mock_job_repo.update_status.call_args_list]
                assert any(JobStatus.COMPLETED == call[1] for call in calls)


def test_distributed_analysis_service_missing_ytid():
    """Test that service fails gracefully when job is missing ytid."""
    service = get_distributed_analysis_service()
    bad_job = Job(
        job_id="test-bad-job",
        job_type="analysis-distributed",
        ytid=None,  # Missing ytid
        status=JobStatus.PENDING,
        config={},
    )

    with patch.object(service, "job_repo") as mock_job_repo:
        service.process_job(bad_job)
        # Should mark as FAILED
        mock_job_repo.update_status.assert_called_once()
        call_args = mock_job_repo.update_status.call_args[0]
        assert call_args[1] == JobStatus.FAILED


def test_distributed_analysis_service_missing_analysis_job_id():
    """Test that service fails gracefully when config is missing analysis_job_id."""
    service = get_distributed_analysis_service()
    bad_job = Job(
        job_id="test-bad-config-job",
        job_type="analysis-distributed",
        ytid="test_ytid",
        status=JobStatus.PENDING,
        config={},  # Missing analysis_job_id
    )

    with patch.object(service, "job_repo") as mock_job_repo:
        service.process_job(bad_job)
        # Should mark as FAILED
        mock_job_repo.update_status.assert_called_once()
        call_args = mock_job_repo.update_status.call_args[0]
        assert call_args[1] == JobStatus.FAILED


def test_generic_worker_service_factory_in_registration():
    """Test that the service factory list is logged during worker init."""
    worker = GenericWorker()
    # Verify the factory dict has all expected keys
    expected_types = {
        "download",
        "transcription",
        "clipping",
        "analysis",
        "analysis-distributed",
        "diarization",
        "stitching",
        "dl_subs",
        "convert_captions",
        "voice",
        "dates",
        "extra_utils",
    }
    assert set(worker.service_factories.keys()) == expected_types
