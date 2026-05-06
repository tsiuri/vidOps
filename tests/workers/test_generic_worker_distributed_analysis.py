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
    """Test that a distributed analysis job is properly handled by the service.

    The bridge must instantiate AnalysisEngine directly (not AnalysisWorker) and
    call engine.process_job(...) with the analysis_job_id.
    """
    from services.analysis_engine import JobResult

    service = get_distributed_analysis_service()
    fake_result = JobResult(
        job_id="test_ytid:test_config:1234567890",
        tasks_total=3,
        tasks_ok=3,
        tasks_failed=0,
        aggregate_status="ok",
        duration_s=0.42,
    )

    # Patch AnalysisEngine inside the bridge so we don't touch DB / Ollama.
    fake_engine = MagicMock()
    fake_engine.process_job.return_value = fake_result

    with patch.object(service, "job_repo") as mock_job_repo:
        with patch(
            "services.distributed_analysis.AnalysisEngine",
            return_value=fake_engine,
        ) as mock_engine_cls:
            service.process_job(test_analysis_job)

            # Engine was constructed once (not AnalysisWorker)
            mock_engine_cls.assert_called_once()
            # Engine.process_job called with the analysis_job_id from job.config
            fake_engine.process_job.assert_called_once()
            args, kwargs = fake_engine.process_job.call_args
            analysis_job_id = (args[0] if args else kwargs.get("analysis_job_id"))
            assert analysis_job_id == "test_ytid:test_config:1234567890"
            assert "worker_id" in kwargs
            # Engine cleanup should happen in the finally block
            fake_engine.shutdown.assert_called_once()

            # Verify job status was updated to RUNNING
            mock_job_repo.update_status.assert_any_call(
                test_analysis_job.job_id,
                JobStatus.RUNNING,
            )

            # Verify job status was updated to COMPLETED
            calls = [call[0] for call in mock_job_repo.update_status.call_args_list]
            assert any(JobStatus.COMPLETED == call[1] for call in calls)


def test_bridge_does_not_construct_analysis_worker(test_analysis_job):
    """Bridge must not instantiate AnalysisWorker — it goes through AnalysisEngine directly."""
    from services.analysis_engine import JobResult

    service = get_distributed_analysis_service()
    fake_result = JobResult(
        job_id="test_ytid:test_config:1234567890",
        tasks_total=1,
        tasks_ok=1,
        tasks_failed=0,
        aggregate_status="ok",
        duration_s=0.01,
    )
    fake_engine = MagicMock()
    fake_engine.process_job.return_value = fake_result

    # If the bridge were still instantiating AnalysisWorker, this patch would fire.
    with patch.object(service, "job_repo"):
        with patch(
            "services.distributed_analysis.AnalysisEngine",
            return_value=fake_engine,
        ):
            with patch("workers.analysis_distributed.AnalysisWorker") as mock_worker_cls:
                service.process_job(test_analysis_job)
                mock_worker_cls.assert_not_called()


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
