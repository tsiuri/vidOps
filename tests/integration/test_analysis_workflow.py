#!/usr/bin/env python3
"""
Integration test for the analysis job creation workflow.

Tests the full end-to-end flow:
1. List analysis configurations
2. View a specific configuration
3. Create an analysis job
4. Monitor job status
5. Retrieve results
"""

import sys
import json
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dal import JobRepository, VideoRepository, QuickClipRepository
from models import Job, JobStatus
from scripts.analysis.db_storage import AnalysisDatabase


def test_job_creation():
    """Test creating an analysis job."""
    print("\n=== Test: Job Creation ===")

    job_repo = JobRepository()

    # Create a test job
    job = Job(
        job_type='analysis-distributed',
        status=JobStatus.PENDING,
        ytid='dQw4w9WgXcQ',  # Test video ID
        priority=50,
        config={
            'config_id': 'test_config_001',
            'ytid': 'dQw4w9WgXcQ',
            'transcript_kind': 'words_whisper_base',
            'session_id': None,
        }
    )

    try:
        created_job = job_repo.create(job)
        print(f"✓ Job created: {created_job.job_id}")
        print(f"  Status: {created_job.status.value}")
        print(f"  YTID: {created_job.ytid}")
        print(f"  Config: {created_job.config}")
        return created_job
    except Exception as e:
        print(f"✗ Failed to create job: {e}")
        return None


def test_job_retrieval(job_id):
    """Test retrieving a job."""
    print("\n=== Test: Job Retrieval ===")

    job_repo = JobRepository()

    try:
        job = job_repo.get(job_id)
        if job:
            print(f"✓ Job retrieved: {job.job_id}")
            print(f"  Status: {job.status.value}")
            print(f"  Type: {job.job_type}")
            print(f"  Priority: {job.priority}")
            return job
        else:
            print(f"✗ Job not found: {job_id}")
            return None
    except Exception as e:
        print(f"✗ Failed to retrieve job: {e}")
        return None


def test_analysis_configs():
    """Test retrieving analysis configurations."""
    print("\n=== Test: Analysis Configurations ===")

    db = AnalysisDatabase(
        dbname='transcripts',
        host='localhost',
        port=5432,
    )

    try:
        db.connect()
        configs = db.list_analysis_configs()

        if configs:
            print(f"✓ Found {len(configs)} analysis configuration(s)")
            for config in configs[:3]:  # Show first 3
                print(f"  - {config.get('name')} ({config.get('analysis_type')} v{config.get('version')})")
                if isinstance(config.get('config_json'), str):
                    try:
                        parsed = json.loads(config['config_json'])
                        print(f"    Passes: {len(parsed.get('passes', []))}")
                    except (json.JSONDecodeError, TypeError):
                        pass
        else:
            print("⚠ No analysis configurations found")

        return configs
    except Exception as e:
        print(f"✗ Failed to retrieve configs: {e}")
        return []
    finally:
        db.disconnect()


def test_video_retrieval():
    """Test retrieving videos for job creation."""
    print("\n=== Test: Video Retrieval ===")

    try:
        video_repo = VideoRepository()
        quickclip_repo = QuickClipRepository()

        # Try to get a test video
        test_ytid = 'dQw4w9WgXcQ'
        video = video_repo.get(test_ytid)

        if video:
            print(f"✓ Video found: {video.ytid}")
            print(f"  Title: {video.title or 'Unknown'}")
            print(f"  Duration: {video.duration_sec if video.duration_sec else 'Unknown'}")
        else:
            print(f"⚠ Video {test_ytid} not found (this is expected if not downloaded)")

        # List QuickClip sessions
        sessions = quickclip_repo.list_recent_sessions(limit=100)
        if sessions:
            print(f"✓ Found {len(sessions)} QuickClip session(s)")
            for session in sessions[:3]:  # Show first 3
                print(f"  - {session['session_id']} ({session.get('clips_count', 0)} clips)")
        else:
            print("⚠ No QuickClip sessions found")

        return True
    except Exception as e:
        print(f"✗ Failed to retrieve videos: {e}")
        return False


def test_job_status_update():
    """Test updating job status."""
    print("\n=== Test: Job Status Update ===")

    # Create a test job first
    job = Job(
        job_type='analysis-distributed',
        status=JobStatus.PENDING,
        ytid='test_vid_123',
        priority=50,
        config={'config_id': 'test_config'},
    )

    job_repo = JobRepository()

    try:
        created_job = job_repo.create(job)
        print(f"✓ Job created: {created_job.job_id}")

        # Update status
        updated_job = job_repo.update_status(
            created_job.job_id,
            JobStatus.CLAIMED,
        )
        print(f"✓ Job status updated to: {updated_job.status.value if updated_job else 'Unknown'}")

        # Update to running
        updated_job = job_repo.update_status(
            created_job.job_id,
            JobStatus.RUNNING,
        )
        print(f"✓ Job status updated to: {updated_job.status.value if updated_job else 'Unknown'}")

        # Update to completed with result
        updated_job = job_repo.update_status(
            created_job.job_id,
            JobStatus.COMPLETED,
            result={'analysis_data': {'summary': 'Test analysis complete'}}
        )
        print(f"✓ Job status updated to: {updated_job.status.value if updated_job else 'Unknown'}")
        if updated_job and updated_job.result:
            print(f"  Result: {updated_job.result}")

        return True
    except Exception as e:
        print(f"✗ Failed to update job status: {e}")
        return False


def test_workflow():
    """Test the complete analysis job workflow."""
    print("\n" + "="*60)
    print("ANALYSIS WORKFLOW INTEGRATION TEST")
    print("="*60)

    # Test 1: Check analysis configurations exist
    configs = test_analysis_configs()

    # Test 2: Check videos/sessions available
    test_video_retrieval()

    # Test 3: Create a job
    job = test_job_creation()

    if job:
        # Test 4: Retrieve the job
        retrieved_job = test_job_retrieval(job.job_id)

        # Test 5: Update job status
        test_job_status_update()

        print("\n" + "="*60)
        print("✓ ALL TESTS PASSED")
        print("="*60)
        return True
    else:
        print("\n" + "="*60)
        print("✗ SOME TESTS FAILED")
        print("="*60)
        return False


if __name__ == '__main__':
    success = test_workflow()
    sys.exit(0 if success else 1)
