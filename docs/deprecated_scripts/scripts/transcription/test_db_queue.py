#!/usr/bin/env python3
"""Test script for TranscriptionQueue"""

import sys
from pathlib import Path

# Add script dir to path
sys.path.insert(0, str(Path(__file__).parent))

from db_queue import TranscriptionQueue

def test_queue():
    print("Testing TranscriptionQueue...")
    print()

    # Initialize
    try:
        queue = TranscriptionQueue()
        print("✓ Queue initialized")
    except Exception as e:
        print(f"✗ Failed to initialize queue: {e}")
        return False

    # Enqueue a job
    try:
        job_id = queue.enqueue(
            media_path="/tmp/test_video.mp4",
            model="medium",
            language="en",
            priority=10,
            options={"test": True}
        )
        print(f"✓ Job enqueued: {job_id}")
    except Exception as e:
        print(f"✗ Failed to enqueue job: {e}")
        return False

    # Register a worker
    try:
        queue.register_worker(
            worker_id="test-py-worker",
            worker_type="nvidia",
            hostname="localhost",
            gpu_index=0,
            metadata={"version": "1.0", "model": "medium"}
        )
        print("✓ Worker registered")
    except Exception as e:
        print(f"✗ Failed to register worker: {e}")
        return False

    # Send heartbeat
    try:
        queue.heartbeat("test-py-worker", status="idle")
        print("✓ Heartbeat sent")
    except Exception as e:
        print(f"✗ Failed to send heartbeat: {e}")
        return False

    # Claim the job
    try:
        job = queue.claim("test-py-worker", "nvidia")
        if job:
            print(f"✓ Job claimed: {job['job_id']}")
            assert job['job_id'] == job_id, "Job ID mismatch"
            assert job['media_path'] == "/tmp/test_video.mp4", "Media path mismatch"
            assert job['model'] == "medium", "Model mismatch"
        else:
            print("✗ Failed to claim job (no job returned)")
            return False
    except Exception as e:
        print(f"✗ Failed to claim job: {e}")
        return False

    # Update status to running
    try:
        queue.update_status(job['job_id'], 'running')
        print("✓ Status updated to running")
    except Exception as e:
        print(f"✗ Failed to update status: {e}")
        return False

    # Get job details
    try:
        job_details = queue.get_job(job_id)
        if job_details and job_details['status'] == 'running':
            print("✓ Job status verified: running")
        else:
            print(f"✗ Job status mismatch: {job_details['status'] if job_details else 'None'}")
            return False
    except Exception as e:
        print(f"✗ Failed to get job details: {e}")
        return False

    # Complete the job
    try:
        queue.complete_job(
            job['job_id'],
            output_vtt="/tmp/test.vtt",
            output_words_tsv="/tmp/test.words.tsv",
            processing_time_sec=45.2
        )
        print("✓ Job completed")
    except Exception as e:
        print(f"✗ Failed to complete job: {e}")
        return False

    # Get stats
    try:
        stats = queue.get_queue_stats()
        print(f"✓ Queue stats: {stats}")
        if 'completed' not in stats or stats['completed'] < 1:
            print("⚠ Warning: Expected at least 1 completed job in stats")
    except Exception as e:
        print(f"✗ Failed to get stats: {e}")
        return False

    # Test pending count
    try:
        pending_count = queue.get_pending_count()
        print(f"✓ Pending jobs: {pending_count}")
    except Exception as e:
        print(f"✗ Failed to get pending count: {e}")
        return False

    # Cleanup
    try:
        with queue._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM transcribe_job_log WHERE job_id = %s", (job_id,))
                cur.execute("DELETE FROM transcribe_jobs WHERE job_id = %s", (job_id,))
                cur.execute("DELETE FROM transcribe_workers WHERE worker_id = %s", ("test-py-worker",))
        print("✓ Test data cleaned up")
    except Exception as e:
        print(f"⚠ Warning: Failed to clean up test data: {e}")

    print()
    print("✓ All tests passed!")
    return True

if __name__ == "__main__":
    success = test_queue()
    sys.exit(0 if success else 1)
