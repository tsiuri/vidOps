#!/usr/bin/env python3
"""
Mock worker for testing database queue without transcription dependencies.

This demonstrates the queue system is working end-to-end without requiring
Whisper/faster-whisper to be installed.

Usage:
    python3 test_queue_mock_worker.py --gpu-idx 0 --max-jobs 1
"""

import os
import sys
import time
from pathlib import Path

# Add script directory to path
sys.path.insert(0, str(Path(__file__).parent))

from queue_worker_base import QueueWorkerBase


class MockQueueWorker(QueueWorkerBase):
    """Mock worker that creates fake transcription outputs"""

    def __init__(self, gpu_index: int = 0, **kwargs):
        super().__init__(
            worker_type="nvidia",
            gpu_index=gpu_index,
            model="mock",
            **kwargs
        )

    def _get_compute_type(self) -> str:
        return "mock"

    def _get_version(self) -> str:
        return "mock-1.0"

    def _process_job(self, job: dict):
        """Process a job by creating mock output files"""
        job_id = job['job_id']
        media_path = Path(job['media_path'])
        output_format = job['output_format']

        print(f"[{self.worker_id}] MOCK Processing job {job_id}")
        print(f"[{self.worker_id}]   Media: {media_path.name}")

        # Check file exists
        if not media_path.exists():
            raise FileNotFoundError(f"Media file not found: {media_path}")

        # Update status to running
        self.queue.update_status(job_id, 'running')

        start_time = time.time()

        # Simulate processing time
        print(f"[{self.worker_id}] Simulating transcription (3 seconds)...")
        time.sleep(3)

        # Determine output base path
        output_base = media_path.parent / media_path.stem

        # Create mock outputs
        output_vtt = None
        output_srt = None
        output_words_tsv = None

        if output_format in ('vtt', 'both'):
            output_vtt = str(output_base) + ".mock.vtt"
            with open(output_vtt, 'w', encoding='utf-8') as f:
                f.write("WEBVTT\n\n")
                f.write("1\n")
                f.write("00:00:00.000 --> 00:00:05.000\n")
                f.write("This is a mock transcription from the queue system.\n\n")
                f.write("2\n")
                f.write("00:00:05.000 --> 00:00:10.000\n")
                f.write("The database queue is working correctly!\n\n")
            print(f"[{self.worker_id}] Created mock VTT: {output_vtt}")

        if output_format in ('srt', 'both'):
            output_srt = str(output_base) + ".mock.srt"
            with open(output_srt, 'w', encoding='utf-8') as f:
                f.write("1\n")
                f.write("00:00:00,000 --> 00:00:05,000\n")
                f.write("This is a mock transcription from the queue system.\n\n")
                f.write("2\n")
                f.write("00:00:05,000 --> 00:00:10,000\n")
                f.write("The database queue is working correctly!\n\n")
            print(f"[{self.worker_id}] Created mock SRT: {output_srt}")

        # Create mock words TSV
        output_words_tsv = str(output_base) + ".mock.words.tsv"
        with open(output_words_tsv, 'w', encoding='utf-8') as f:
            f.write("start\tend\tword\tconfidence\tseg\tretried\n")
            f.write("0.000\t0.500\tThis\t-0.123\t1\t0\n")
            f.write("0.500\t0.800\tis\t-0.098\t1\t0\n")
            f.write("0.800\t1.000\ta\t-0.087\t1\t0\n")
            f.write("1.000\t1.500\tmock\t-0.145\t1\t0\n")
            f.write("1.500\t2.500\ttranscription\t-0.234\t1\t0\n")
        print(f"[{self.worker_id}] Created mock words: {output_words_tsv}")

        # Calculate processing time
        processing_time = time.time() - start_time

        # Complete the job
        self.queue.complete_job(
            job_id,
            output_vtt=output_vtt,
            output_srt=output_srt,
            output_words_tsv=output_words_tsv,
            processing_time_sec=processing_time
        )

        print(f"[{self.worker_id}] MOCK Job completed in {processing_time:.1f}s")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Mock queue worker (no transcription)")
    parser.add_argument("--gpu-idx", type=int, default=0, help="GPU device index")
    parser.add_argument("--max-jobs", type=int, default=0, help="Max jobs before exit (0=forever)")
    args = parser.parse_args()

    worker = MockQueueWorker(
        gpu_index=args.gpu_idx,
        heartbeat_interval=int(os.environ.get('WORKER_HEARTBEAT_INTERVAL', 5)),
        poll_interval=int(os.environ.get('WORKER_POLL_INTERVAL', 2))
    )

    try:
        worker.run(max_jobs=args.max_jobs if args.max_jobs > 0 else None)
    except KeyboardInterrupt:
        print(f"\n[{worker.worker_id}] Interrupted by user")
    finally:
        print(f"[{worker.worker_id}] Worker shutdown complete")


if __name__ == "__main__":
    sys.exit(main() or 0)
