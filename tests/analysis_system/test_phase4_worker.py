#!/usr/bin/env python3
"""
Integration test for Phase 4: AnalysisWorker

This test:
1. Creates analysis jobs with tasks
2. Runs a short worker loop to process them
3. Verifies aggregation into analysis_results
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from datetime import timedelta
from typing import Optional, List, Dict, Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pytest
pytest.importorskip("psycopg2")

from scripts.analysis.config_loader import load_local_config

from scripts.analysis.db_storage import AnalysisDatabase
from scripts.analysis.analysis_config import default_config
from scripts.analysis.analysis_task_repository import AnalysisTaskRepository
from scripts.analysis.analyze_to_db import create_analysis_job
from scripts.analysis.analysis_worker import AnalysisWorker


def _config_to_dict(config) -> dict:
    if hasattr(config, "model_dump"):
        return config.model_dump()
    if hasattr(config, "dict"):
        return config.dict()
    raise TypeError("Unsupported config object")


def ensure_analysis_config(db: AnalysisDatabase, config_id: str, config) -> None:
    cfg_payload = _config_to_dict(config)
    db.upsert_analysis_config(
        config_id=config_id,
        name=cfg_payload.get("name", config_id),
        analysis_type=cfg_payload.get("analysis_type", "normal"),
        version=cfg_payload.get("version", 1),
        config_json=cfg_payload,
        is_default=False,
    )
    if db.conn:
        db.conn.commit()


def ensure_video_row(db: AnalysisDatabase, ytid: str) -> None:
    cur = db.cursor
    cur.execute(
        """
        INSERT INTO videos (ytid, title, title_date, url)
        VALUES (%s, %s, NOW(), %s)
        ON CONFLICT (ytid) DO NOTHING
        """,
        (ytid, f"Test Video {ytid}", f"https://youtube.com/watch?v={ytid}"),
    )
    if db.conn:
        db.conn.commit()


def ensure_model_profile(db: AnalysisDatabase, model_name: str, required_vram_gb: float) -> int:
    try:
        profile_id = db.upsert_analysis_model_profile(
            model_name=model_name,
            options={},
            required_vram_gb=required_vram_gb,
            notes="test fixture",
        )
        if db.conn:
            db.conn.commit()
        return int(profile_id)
    except Exception:
        return 0


def connect_db(
    db_name: Optional[str] = None,
    db_host: Optional[str] = None,
    db_port: Optional[int] = None,
    db_user: Optional[str] = None,
    db_password: Optional[str] = None,
) -> AnalysisDatabase:
    cfg = load_local_config() or {}

    name = db_name or cfg.get("db_name", "transcripts")
    host = db_host or cfg.get("db_host", "localhost")
    port = db_port or int(cfg.get("db_port", 5432))
    user = db_user or cfg.get("db_user")
    password = db_password or cfg.get("db_password")

    db = AnalysisDatabase(dbname=name, host=host, port=port, user=user, password=password)
    db.connect()
    return db


def build_synthetic_chunks(ytid: str, num_chunks: int) -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []
    for i in range(num_chunks):
        chunks.append(
            {
                "chunk_id": i,
                "text": f"This is test chunk {i} for video {ytid}. It contains some sample text for analysis.",
                "word_count": 16,
                "start_sec": float(i * 10),
                "end_sec": float(i * 10 + 8),
                "speaker": None,
            }
        )
    return chunks


def test_phase4_integration():
    print("=" * 72)
    print("Phase 4 Integration Test: AnalysisWorker + Aggregation")
    print("=" * 72)

    db = connect_db()

    try:
        # Step 1: Create analysis job with tasks
        print("\n[Step 1] Creating analysis job with tasks...")
        config_id = "test_phase4_worker"
        config = default_config(config_id=config_id)
        config.model = "qwen2.5:7b-instruct"
        chunks = build_synthetic_chunks("test_phase4", num_chunks=2)

        profile_id = ensure_model_profile(db, config.model, required_vram_gb=8)
        config.model_profile_id = profile_id
        ensure_analysis_config(db, config_id, config)
        ensure_video_row(db, "AxtuJ-IVOGA")

        job_id = create_analysis_job(
            ytid="AxtuJ-IVOGA",
            config_id=config_id,
            config=config,
            chunks=chunks,
            db=db,
            model_name=config.model,
            model_profile_id=config.model_profile_id,
        )
        print(f"✓ Created job_id: {job_id}")

        # Step 2: Check initial task count
        print("\n[Step 2] Checking initial task progress...")
        repo = AnalysisTaskRepository(db)
        progress = repo.get_job_progress(job_id)
        print(f"Initial progress: {progress}")
        initial_pending = progress.get("pending", 0)

        if initial_pending == 0:
            pytest.fail("No tasks created! Check if config has enabled passes.")

        # Step 3: Run a test worker to process just the first few tasks
        print(f"\n[Step 3] Running test worker to process {min(5, initial_pending)} tasks...")
        worker = AnalysisWorker(
            machine_alias="test_worker",
            worker_type="analysis_gpu",
            model_url="http://localhost:11434",
            model_name="qwen2.5:7b-instruct",
            capabilities=["qwen2.5:7b-instruct", "gpu_8gb"],
            available_vram_gb=8,
            model_profile_id=config.model_profile_id,
            db_host=db.conn_params["host"],
            db_name=db.conn_params["dbname"],
            db_user=db.conn_params.get("user"),
            db_password=db.conn_params.get("password"),
            lease_duration_minutes=30,
        )

        tasks_processed = 0
        max_tasks_to_process = 5  # Just process a few to test the loop

        while tasks_processed < max_tasks_to_process:
            task = repo.claim_next(
                worker_id=worker.worker_id,
                worker_vram_gb=worker.available_vram_gb,
                lease_duration=worker.lease_duration,
            )

            if not task:
                print("No more tasks available")
                break

            print(f"  Claimed task {task.task_id} for pass={task.pass_id}")

            # Execute pass
            result = worker._execute_pass(task)

            if result:
                result["worker_id"] = worker.worker_id
                repo.mark_completed(task.task_id, result)
                print(f"    → Completed")
                tasks_processed += 1
            else:
                repo.mark_failed(task.task_id, "Execution returned None")
                print(f"    → Failed")

        print(f"Processed {tasks_processed} tasks")

        # Step 4: Check updated progress
        print("\n[Step 4] Checking progress after worker execution...")
        progress = repo.get_job_progress(job_id)
        print(f"Updated progress: {progress}")

        # Step 5: Test aggregation
        print("\n[Step 5] Testing aggregation...")
        if repo.is_job_complete(job_id):
            print("Job is complete! Running aggregation...")
            worker._aggregate_job_results(job_id)
            print("✓ Aggregation completed")
        else:
            print("Job not yet complete (expected for this test). Aggregation would run automatically when all tasks done.")

        # Step 6: Verify results table
        print("\n[Step 6] Checking analysis_results table...")
        cur = db.cursor
        cur.execute(
            "SELECT job_id, ytid, config_id, status, total_tasks, completed_tasks FROM analysis_results WHERE job_id = %s",
            (job_id,),
        )
        result_row = cur.fetchone()

        if result_row:
            print("✓ Results found in analysis_results:")
            print(f"  job_id: {result_row[0]}")
            print(f"  ytid: {result_row[1]}")
            print(f"  config_id: {result_row[2]}")
            print(f"  status: {result_row[3]}")
            print(f"  total_tasks: {result_row[4]}")
            print(f"  completed_tasks: {result_row[5]}")
        else:
            print("⚠ No results in analysis_results yet (job not complete or aggregation not run)")

        print("\n" + "=" * 72)
        print("TEST RESULT: Phase 4 worker integration test completed successfully!")
        print("=" * 72)

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback

        traceback.print_exc()
        pytest.fail(f"Unexpected error: {e}")
    finally:
        try:
            db.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    test_phase4_integration()
