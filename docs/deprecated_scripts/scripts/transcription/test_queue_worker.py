#!/usr/bin/env python3
"""
Minimal test worker for database queue integration.

This is a simplified worker to test the queue system end-to-end.
For production use, integrate with existing transcribe_worker_nvidia.py.

Usage:
    python3 test_queue_worker.py --gpu-idx 0 --model tiny --max-jobs 1
"""

import os
import sys
import time
import argparse
from pathlib import Path

# Add script directory to path
sys.path.insert(0, str(Path(__file__).parent))

from queue_worker_base import QueueWorkerBase

# Lazy import faster-whisper to allow worker to start even if model not loaded
WhisperModel = None


class TestQueueWorker(QueueWorkerBase):
    """Minimal test worker for queue integration"""

    def __init__(self, gpu_index: int, model: str = "tiny", **kwargs):
        super().__init__(
            worker_type="nvidia",
            gpu_index=gpu_index,
            model=model,
            **kwargs
        )

        # Set GPU device
        os.environ['CUDA_VISIBLE_DEVICES'] = str(gpu_index)

        # Lazy load model (only when first job is processed)
        self.whisper_model = None
        self.current_model = None

    def _get_compute_type(self) -> str:
        return os.environ.get('NV_COMPUTE', 'float16')

    def _get_version(self) -> str:
        return "test-queue-1.0"

    def _ensure_model_loaded(self, model_size: str):
        """Load Whisper model if not already loaded"""
        global WhisperModel
        if WhisperModel is None:
            from faster_whisper import WhisperModel as WM
            WhisperModel = WM

        if self.whisper_model is not None and self.current_model == model_size:
            return  # Already loaded

        # Unload previous model
        if self.whisper_model is not None:
            del self.whisper_model
            import gc
            gc.collect()
            try:
                import torch
                torch.cuda.empty_cache()
            except:
                pass

        # Load new model
        compute_type = self._get_compute_type()
        print(f"[{self.worker_id}] Loading model: {model_size} ({compute_type})")

        self.whisper_model = WhisperModel(
            model_size,
            device="cuda",
            compute_type=compute_type
        )
        self.current_model = model_size
        print(f"[{self.worker_id}] Model loaded: {model_size}")

    def _process_job(self, job: dict):
        """Process a single transcription job from the queue"""
        job_id = job['job_id']
        media_path = Path(job['media_path'])
        model = job['model']
        language = job['language'] or None
        output_format = job['output_format']
        options = job.get('options', {})

        print(f"[{self.worker_id}] Processing job {job_id}")
        print(f"[{self.worker_id}]   Media: {media_path.name}")
        print(f"[{self.worker_id}]   Model: {model}, Language: {language}")

        # Check file exists
        if not media_path.exists():
            raise FileNotFoundError(f"Media file not found: {media_path}")

        # Update status to running
        self.queue.update_status(job_id, 'running')

        start_time = time.time()

        try:
            # Ensure model is loaded
            self._ensure_model_loaded(model)

            # Determine output base path (same directory as media, or use generated/)
            output_base = media_path.parent / media_path.stem

            # Transcribe
            print(f"[{self.worker_id}] Transcribing...")
            segments, info = self.whisper_model.transcribe(
                str(media_path),
                language=language,
                vad_filter=options.get('vad_filter', True),
                word_timestamps=True,
            )

            # Convert to list to materialize generator
            segments = list(segments)
            print(f"[{self.worker_id}] Transcribed {len(segments)} segments")

            # Write outputs
            output_vtt = None
            output_srt = None
            output_words_tsv = None

            if output_format in ('vtt', 'both'):
                output_vtt = str(output_base) + ".test.vtt"
                self._write_vtt(segments, output_vtt, info)
                print(f"[{self.worker_id}] Wrote VTT: {output_vtt}")

            if output_format in ('srt', 'both'):
                output_srt = str(output_base) + ".test.srt"
                self._write_srt(segments, output_srt)
                print(f"[{self.worker_id}] Wrote SRT: {output_srt}")

            # Write words TSV
            output_words_tsv = str(output_base) + ".test.words.tsv"
            self._write_words_tsv(segments, output_words_tsv)
            print(f"[{self.worker_id}] Wrote words: {output_words_tsv}")

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

            print(f"[{self.worker_id}] Job completed in {processing_time:.1f}s")

        except Exception as e:
            print(f"[{self.worker_id}] Job failed: {e}")
            import traceback
            traceback.print_exc()
            raise

    def _write_vtt(self, segments, output_path, info):
        """Write VTT subtitle file"""
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("WEBVTT\n\n")
            for i, seg in enumerate(segments):
                start = self._format_timestamp(seg.start)
                end = self._format_timestamp(seg.end)
                text = seg.text.strip()
                f.write(f"{i+1}\n")
                f.write(f"{start} --> {end}\n")
                f.write(f"{text}\n\n")

    def _write_srt(self, segments, output_path):
        """Write SRT subtitle file"""
        with open(output_path, 'w', encoding='utf-8') as f:
            for i, seg in enumerate(segments):
                start = self._format_timestamp_srt(seg.start)
                end = self._format_timestamp_srt(seg.end)
                text = seg.text.strip()
                f.write(f"{i+1}\n")
                f.write(f"{start} --> {end}\n")
                f.write(f"{text}\n\n")

    def _write_words_tsv(self, segments, output_path):
        """Write per-word TSV file"""
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("start\tend\tword\tconfidence\tseg\tretried\n")
            for seg_idx, seg in enumerate(segments, 1):
                if hasattr(seg, 'words') and seg.words:
                    for word in seg.words:
                        word_text = word.word.strip()
                        start = f"{word.start:.3f}"
                        end = f"{word.end:.3f}"
                        confidence = f"{getattr(seg, 'avg_logprob', 0.0):.3f}"
                        f.write(f"{start}\t{end}\t{word_text}\t{confidence}\t{seg_idx}\t0\n")

    def _format_timestamp(self, seconds):
        """Format timestamp for VTT (HH:MM:SS.mmm)"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"

    def _format_timestamp_srt(self, seconds):
        """Format timestamp for SRT (HH:MM:SS,mmm)"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:06.3f}".replace('.', ',')


def main():
    parser = argparse.ArgumentParser(description="Test queue worker for NVIDIA GPU")
    parser.add_argument("--gpu-idx", type=int, required=True, help="GPU device index")
    parser.add_argument("--model", default="tiny", help="Whisper model size")
    parser.add_argument("--max-jobs", type=int, default=0, help="Max jobs before exit (0=forever)")
    args = parser.parse_args()

    # Create and run worker
    worker = TestQueueWorker(
        gpu_index=args.gpu_idx,
        model=args.model,
        heartbeat_interval=int(os.environ.get('WORKER_HEARTBEAT_INTERVAL', 10)),
        poll_interval=int(os.environ.get('WORKER_POLL_INTERVAL', 5))
    )

    try:
        worker.run(max_jobs=args.max_jobs if args.max_jobs > 0 else None)
    except KeyboardInterrupt:
        print(f"\n[{worker.worker_id}] Interrupted by user, shutting down...")
    finally:
        # Cleanup
        if worker.whisper_model:
            del worker.whisper_model
        print(f"[{worker.worker_id}] Worker shutdown complete")


if __name__ == "__main__":
    sys.exit(main() or 0)
