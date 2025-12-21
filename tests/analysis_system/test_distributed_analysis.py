#!/usr/bin/env python3
"""
In-process test harness for the distributed analysis system.

This script exercises the core workflow WITHOUT requiring the full
vidops integration or any actual LLM calls:

    1. Build a synthetic AnalysisConfig (using default_config()).
    2. Generate a few fake chunks for a given ytid.
    3. Call create_analysis_job(...) to insert tasks into analysis_tasks.
    4. Use AnalysisTaskRepository to:
         - claim_next(...)
         - mark_completed(...)
         - check get_job_progress(...)
         - check is_job_complete(...)

NOTE: This script WILL write to your analysis_tasks table. Run against
a test database if you don't want to pollute production.
"""

from __future__ import annotations

import argparse
from datetime import timedelta
from typing import Any, Dict, List, Optional

from scripts.analysis.config_loader import load_local_config

import pytest
pytest.importorskip("psycopg2")

from scripts.analysis.db_storage import AnalysisDatabase
from scripts.analysis.analysis_config import AnalysisConfig, default_config
from scripts.analysis.analysis_task_repository import AnalysisTaskRepository
from scripts.analysis.analyze_to_db import create_analysis_job


def _config_to_dict(config: AnalysisConfig) -> Dict[str, Any]:
    if hasattr(config, "model_dump"):
        return config.model_dump()
    if hasattr(config, "dict"):
        return config.dict()
    raise TypeError("Unsupported AnalysisConfig payload")


def ensure_analysis_config(db: AnalysisDatabase, config: AnalysisConfig) -> None:
    payload = _config_to_dict(config)
    base_name = payload.get("name") or "Test Analysis Config"
    payload["name"] = f"{base_name} ({payload.get('id')})"
    db.upsert_analysis_config(
        config_id=payload.get("id", "test_config"),
        name=payload.get("name", "test"),
        analysis_type=payload.get("analysis_type", "normal"),
        version=payload.get("version", 1),
        config_json=payload,
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


def build_test_config(config_id: str) -> AnalysisConfig:
    return default_config(config_id=config_id)


def build_synthetic_chunks(ytid: str, num_chunks: int) -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []
    for i in range(num_chunks):
        chunks.append(
            {
                "chunk_id": i,
                "text": f"Test chunk {i} for video {ytid}",
                "word_count": 7,
                "start_sec": float(i * 10),
                "end_sec": float(i * 10 + 8),
                "speaker": None,
            }
        )
    return chunks


def run_distributed_test(
    ytid: str,
    num_chunks: int,
    db_name: Optional[str],
    db_host: Optional[str],
    db_port: Optional[int],
    db_user: Optional[str],
    db_password: Optional[str],
) -> int:
    print("=" * 72)
    print("Distributed Analysis Test Harness")
    print("=" * 72)

    db = connect_db(db_name, db_host, db_port, db_user, db_password)

    try:
        config_id = "test_distributed_analysis"
        config = build_test_config(config_id=config_id)
        chunks = build_synthetic_chunks(ytid, num_chunks=num_chunks)

        enabled_passes = [p.id for p in config.passes if getattr(p, "enabled", True)]

        print(f"- Using config_id: {config.id!r}")
        print(f"- Enabled passes: {enabled_passes}")
        print(f"- Synthetic chunks: {len(chunks)}")

        ensure_video_row(db, ytid)
        ensure_analysis_config(db, config)

        job_id = create_analysis_job(
            ytid=ytid,
            config_id=config.id,
            config=config,
            chunks=chunks,
            db=db,
        )
        print(f"\n✓ Created job_id: {job_id}")

        repo = AnalysisTaskRepository(db)
        progress = repo.get_job_progress(job_id)
        print(f"Initial progress: {progress}")

        worker_a = "test_worker_a"
        worker_b = "test_worker_b"
        caps: List[str] = ["qwen2.5:7b-instruct", "gpu_8gb", "phi:2.2b", "cpu"]
        lease = timedelta(minutes=30)

        current_worker = worker_a
        claim_count = 0

        while True:
            task = repo.claim_next(
                worker_id=current_worker,
                worker_capabilities=caps,
                lease_duration=lease,
            )

            if not task:
                print(f"\nNo more tasks claimable for job {job_id}")
                break

            claim_count += 1
            print(
                f"  [{current_worker}] claimed task_id={task.task_id} "
                f"job={task.job_id} pass={task.pass_id} chunk_id={task.chunk_id}"
            )

            result_payload: Dict[str, Any] = {
                "pass_id": task.pass_id,
                "status": "test_completed",
                "worker_id": current_worker,
                "test_flag": True,
            }
            ok = repo.mark_completed(task.task_id, result_payload)
            print(f"    → mark_completed={ok}")

            current_worker = worker_b if current_worker == worker_a else worker_a

        print(f"\nTotal tasks claimed/completed in test: {claim_count}")

        final_progress = repo.get_job_progress(job_id)
        print(f"Final progress: {final_progress}")
        is_complete = repo.is_job_complete(job_id)
        print(f"is_job_complete({job_id!r}) = {is_complete}")

        print("\nSummary:")
        print(f"  job_id:        {job_id}")
        print(f"  total_tasks:   {final_progress.get('total', 0)}")
        print(f"  completed:     {final_progress.get('completed', 0)}")
        print(f"  failed:        {final_progress.get('failed', 0)}")
        print(f"  pending/other: {final_progress.get('pending', 0)} (plus any claimed)")

        if is_complete and final_progress.get("total", 0) == final_progress.get("completed", 0):
            print("\nTEST RESULT: PASS – all tasks completed and job is marked complete.")
            return 0
        else:
            print(
                "\nTEST RESULT: WARN – job not fully complete, or progress counts "
                "do not match expectations."
            )
            return 1
    finally:
        try:
            db.disconnect()
        except Exception:
            pass


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="In-process test harness for the distributed analysis system."
    )
    parser.add_argument(
        "--ytid",
        required=True,
        help="YouTube video ID (only used for labeling test data).",
    )
    parser.add_argument(
        "--num-chunks",
        type=int,
        default=3,
        help="Number of synthetic chunks to create (default: 3).",
    )
    parser.add_argument("--db-name", help="Database name (optional; overrides local_config).")
    parser.add_argument("--db-host", help="Database host (optional; overrides local_config).")
    parser.add_argument("--db-port", type=int, help="Database port (optional; overrides local_config).")
    parser.add_argument("--db-user", help="Database user (optional; overrides local_config).")
    parser.add_argument("--db-password", help="Database password (optional; overrides local_config).")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    return run_distributed_test(
        ytid=args.ytid,
        num_chunks=args.num_chunks,
        db_name=args.db_name,
        db_host=args.db_host,
        db_port=args.db_port,
        db_user=args.db_user,
        db_password=args.db_password,
    )


if __name__ == "__main__":
    raise SystemExit(main())


def test_distributed_analysis_harness():
    cfg = load_local_config() or {}
    rc = run_distributed_test(
        ytid="AxtuJ-IVOGA",
        num_chunks=2,
        db_name=cfg.get("db_name"),
        db_host=cfg.get("db_host"),
        db_port=int(cfg.get("db_port", 5432)),
        db_user=cfg.get("db_user"),
        db_password=cfg.get("db_password"),
    )
    assert rc == 0
