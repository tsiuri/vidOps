import pytest
from datetime import datetime, timedelta, timezone

from dal import WorkerRepository
from models import Worker, WorkerStatus
from db import check_connection, get_connection

# Skip entire module if DB unavailable
try:
    db_available = check_connection(max_retries=1, delay_sec=1)
except Exception:
    db_available = False

pytestmark = pytest.mark.skipif(not db_available, reason="database not available for integration tests")


@pytest.fixture
def worker_repo() -> WorkerRepository:
    """Provides a WorkerRepository instance pointed at the test table."""
    return WorkerRepository("workers_test")


@pytest.fixture
def sample_worker() -> Worker:
    """Returns a baseline Worker object."""
    return Worker(
        worker_id="worker-atlas-1",
        machine_alias="atlas-test-machine",
        worker_type="transcription",
        hostname="atlas-host",
        capabilities=["cpu"],
        vram_gb=0,
    )


class TestWorkerRepository:
    def test_register_and_get_worker(self, worker_repo: WorkerRepository, sample_worker: Worker):
        registered = worker_repo.register(sample_worker)
        assert registered.worker_id == sample_worker.worker_id
        assert registered.status == WorkerStatus.REGISTERING

        fetched = worker_repo.get(sample_worker.worker_id)
        assert fetched is not None
        assert fetched.worker_id == sample_worker.worker_id
        assert fetched.machine_alias == sample_worker.machine_alias

    def test_heartbeat_updates_timestamp(self, worker_repo: WorkerRepository, sample_worker: Worker):
        worker_repo.register(sample_worker)
        before = worker_repo.get(sample_worker.worker_id).last_heartbeat
        worker_repo.heartbeat(sample_worker.worker_id)
        after = worker_repo.get(sample_worker.worker_id).last_heartbeat
        assert after >= before

    def test_update_status_sets_current_job(self, worker_repo: WorkerRepository, sample_worker: Worker):
        worker_repo.register(sample_worker)
        job_id = "job-heartbeat-test"
        updated = worker_repo.update_status(sample_worker.worker_id, WorkerStatus.BUSY, current_job_id=job_id)
        assert updated.status == WorkerStatus.BUSY
        assert updated.current_job_id == job_id

    def test_list_active_and_purge_stale(self, worker_repo: WorkerRepository):
        fresh_worker = Worker(
            worker_id="worker-fresh",
            machine_alias="atlas-fresh",
            worker_type="download",
            hostname="atlas-host",
            capabilities=[],
            vram_gb=0,
        )
        stale_worker = Worker(
            worker_id="worker-stale",
            machine_alias="atlas-stale",
            worker_type="download",
            hostname="atlas-host",
            capabilities=[],
            vram_gb=0,
        )

        worker_repo.register(fresh_worker)
        worker_repo.register(stale_worker)

        # Force stale heartbeat
        stale_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE workers_test SET last_heartbeat = %s WHERE worker_id = %s",
                    (stale_time, stale_worker.worker_id)
                )
            conn.commit()

        active = worker_repo.list_active(threshold=timedelta(minutes=5))
        ids = [w.worker_id for w in active]
        assert fresh_worker.worker_id in ids
        assert stale_worker.worker_id not in ids

        purged = worker_repo.purge_stale(stale_threshold=timedelta(minutes=5))
        assert purged == 1
        updated_stale = worker_repo.get(stale_worker.worker_id)
        assert updated_stale.status == WorkerStatus.STALE
