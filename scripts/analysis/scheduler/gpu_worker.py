#!/usr/bin/env python3
"""
Simple GPU worker that pulls tasks from the shared queue (Postgres or in-memory)
and executes them using the same executor logic as the main pipeline.

Usage:
  python -m scripts.analysis.scheduler.gpu_worker --gpu-scheduler-config config/analysis_gpu_scheduling.json

Notes:
- This worker only handles hot_target/drill/chunk/summary tasks as encoded by the pipeline.
- It processes tasks inline (no parallel threads here); run multiple workers for concurrency.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

from scripts.analysis.analyze_transcript import OllamaAnalyzer
from scripts.analysis.drills import DrillExecutor
from scripts.analysis.scheduler.gpu_scheduler import GPUScheduler
from scripts.analysis.llm.hot_targets import HotTargetRunner


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="GPU worker for shared queue")
    parser.add_argument("--gpu-scheduler-config", required=True, help="Path to GPU scheduler config JSON")
    parser.add_argument("--log-mode", default="quiet", choices=["quiet", "progress", "verbose"])
    args = parser.parse_args()

    config = load_config(args.gpu_scheduler_config)
    scheduler = GPUScheduler(config=config, log_mode=args.log_mode)

    def executor(task: Dict[str, Any]) -> Any:
        ttype = task.get("task_type")
        if ttype == "chunk_analysis":
            chunk_obj = task["chunk"]
            model = task["model"]
            endpoint = task["endpoint"]
            opts = task.get("options") or {}
            req = task.get("custom_request") or ""
            analyzer = OllamaAnalyzer(
                model=model,
                base_url=endpoint,
                options=opts,
                custom_request=req,
                log_mode=task.get("log_mode") or "quiet",
                category_suggestions=task.get("category_suggestions") or [],
                category_map=task.get("category_map") or {},
            )
            res = analyzer.analyze_chunk(chunk_obj["text"], chunk_obj["chunk_id"])
            res["model_used"] = model
            return res

        if ttype == "summary":
            kind = task.get("kind")
            model = task["model"]
            endpoint = task["endpoint"]
            opts = task.get("options") or {}
            req = task.get("request_text") or ""
            analyzer = OllamaAnalyzer(
                model=model,
                base_url=endpoint,
                options=opts,
                custom_request=req,
                log_mode=task.get("log_mode") or "quiet",
                category_suggestions=task.get("category_suggestions") or [],
                category_map=task.get("category_map") or {},
            )
            if kind == "speaker":
                return {"speaker": analyzer.summarize_speaker(task.get("final_analysis") or {})}
            return {"main": analyzer.summarize(task.get("final_analysis") or {})}

        if ttype == "hot_target":
            target = task["target"]
            chunks = task["chunks"]
            runner = HotTargetRunner(
                model=target.get("model") or task.get("model"),
                base_url=target.get("endpoint") or task.get("endpoint"),
                options=target.get("options") or {},
                log_mode=task.get("log_mode") or "quiet",
            )
            res = runner.run_targets([target], chunks)
            return res

        if ttype == "drill":
            drill = task["drill"]
            executor = DrillExecutor(
                drills=[drill],
                base_model=task.get("model"),
                base_url=task.get("endpoint"),
                options=task.get("options") or {},
                log_mode=task.get("log_mode") or "quiet",
                delay_between=0,
            )
            chunk_texts = {c.get("chunk_id"): c.get("text") or "" for c in task.get("chunks") or []}
            res, summ, emitted = executor.run(task.get("chunks") or [], chunk_texts=chunk_texts)
            return {"results": res, "summary": summ, "emitted": emitted}

        raise ValueError(f"Unknown task type: {ttype}")

    # Main loop: process until queue is empty
    results = scheduler.run_tasks([], executor)  # no tasks enqueued here; we rely on dequeue internally
    # In this simplified worker, run_tasks will exit immediately because no tasks were enqueued locally.
    # For a true remote worker, we'd implement a blocking dequeue loop. For now, exit.
    if args.log_mode != "quiet":
        print("Worker exit (no local tasks enqueued).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
