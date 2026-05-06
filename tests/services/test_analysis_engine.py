"""Tests for services.analysis_engine.AnalysisEngine."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from services.analysis_engine import AnalysisEngine, JobContext
from scripts.analysis.analysis_config import AnalysisConfig
from scripts.analysis.analysis_task import AnalysisTask, TaskStatus


def _make_task(
    *,
    pass_id: str,
    chunk_id: int = 0,
    chunk_text: str = "hello world",
    job_id: str = "test_yt:test_cfg:123",
    task_id: int = 1,
    chunk_metadata: dict | None = None,
) -> AnalysisTask:
    return AnalysisTask(
        task_id=task_id,
        job_id=job_id,
        ytid="test_yt",
        chunk_id=chunk_id,
        pass_id=pass_id,
        chunk_text=chunk_text,
        chunk_metadata=chunk_metadata or {"config_id": "test_cfg", "total_chunks": 1},
        status=TaskStatus.CLAIMED,
        model_profile_id=1,
    )


def test_engine_construct_and_shutdown(analysis_db_test, mock_ollama):
    """Engine constructs cleanly, connects to DB, shutdown is idempotent."""
    with patch("services.analysis_engine.OllamaAnalyzer", return_value=mock_ollama):
        engine = AnalysisEngine(
            model_name="qwen2.5:7b-instruct",
            model_url="http://localhost:11434",
            model_profile_id=1,
        )
        assert engine.db is not None
        assert engine.task_repo is not None
        assert engine.results_repo is not None
        engine.shutdown()
        engine.shutdown()  # idempotent


def test_run_task_chunk_analysis_ok(engine_no_db):
    """run_task on a chunk_analysis task returns ok with the analyzer's payload. No DB."""
    task = _make_task(pass_id="chunk_analysis", task_id=999)
    result = engine_no_db.run_task(task, worker_id="test-worker-1")
    assert result.status == "ok"
    assert result.pass_id == "chunk_analysis"
    assert result.payload is not None
    assert result.payload.get("status") == "completed"
    analysis = result.payload.get("analysis") or {}
    assert analysis.get("summary") == "stub summary"
    assert result.duration_s >= 0.0
    assert result.error_category is None


def test_run_task_sentiment_ok(engine_no_db):
    """run_task on a sentiment_pass task returns ok with sentiment label."""
    task = _make_task(
        pass_id="sentiment_pass",
        task_id=998,
        chunk_text="great news today",
    )
    result = engine_no_db.run_task(task, worker_id="test-worker-1")
    assert result.status == "ok"
    assert result.pass_id == "sentiment_pass"
    assert result.payload is not None
    assert "sentiment" in result.payload


def test_run_task_categories_ok(engine_no_db):
    """run_task on a categories_pass task returns ok with categories list."""
    task = _make_task(
        pass_id="categories_pass",
        task_id=997,
        chunk_text="The senate voted on the inflation policy.",
    )
    result = engine_no_db.run_task(task, worker_id="test-worker-1")
    assert result.status == "ok"
    assert result.pass_id == "categories_pass"
    assert result.payload is not None
    assert "categories" in result.payload
    assert isinstance(result.payload["categories"], list)


def test_run_task_subchunks_ok(engine_no_db):
    """run_task on a subchunks task returns ok with subchunks list."""
    task = _make_task(
        pass_id="subchunks",
        task_id=996,
        chunk_text="This is the first sentence. This is the second one. And a third.",
    )
    result = engine_no_db.run_task(task, worker_id="test-worker-1")
    assert result.status == "ok"
    assert result.pass_id == "subchunks"
    assert result.payload is not None
    assert "subchunks" in result.payload
    assert isinstance(result.payload["subchunks"], list)
    assert len(result.payload["subchunks"]) >= 1


def test_run_task_data_error_classified(engine_no_db, mock_ollama):
    """KeyError / ValueError get classified as 'data' error_category."""
    mock_ollama.analyze_chunk.side_effect = KeyError("chunk_text")
    task = _make_task(pass_id="chunk_analysis", task_id=995)
    result = engine_no_db.run_task(task, worker_id="test-worker-1")
    assert result.status == "failed"
    assert result.error_category == "data"
    assert "chunk_text" in (result.error_message or "")


def test_run_task_drill_pass_invokes_drill_executor(engine_no_db, mock_ollama):
    """run_task on a job-level drills task calls DrillExecutor and returns its results."""
    # Build a context that's "ready for aggregation" (chunk_results populated)
    config = AnalysisConfig(id="cfg-1", name="cfg", passes=[])
    config.drills = [MagicMock(name="DrillSpec")]
    ctx = JobContext(
        job_id="job-2001",
        ytid="testytid",
        config_id="cfg-1",
        config=config,
        total_chunks=1,
    )
    ctx.chunk_results[0] = {"summary": "stub", "categories": ["politics"], "topics": ["x"]}
    ctx.chunk_texts[0] = "the chunk text"
    ctx.chunk_metadata_map[0] = {"start_sec": 0.0, "end_sec": 10.0}
    engine_no_db._get_job_context = MagicMock(return_value=ctx)

    fake_results = {"test-drill": {"hits": 1}}
    fake_summary = [{"drill": "test-drill", "count": 1}]
    fake_emitted = {
        "test-drill": [
            {
                "chunk_id": 0,
                "label": "found X",
                "context": "found X in text",
                "start_sec": 1.0,
                "end_sec": 2.0,
            }
        ]
    }

    with patch("services.analysis_engine.DrillExecutor") as MockExec:
        MockExec.return_value.run.return_value = (fake_results, fake_summary, fake_emitted)

        task = AnalysisTask(
            task_id=999,
            job_id="job-2001",
            ytid="testytid",
            chunk_id=0,
            pass_id="drills",
            chunk_text="",
            chunk_metadata={"config_id": "cfg-1", "is_job_level": True, "total_chunks": 1},
            status=TaskStatus.CLAIMED,
            model_profile_id=1,
        )
        result = engine_no_db.run_task(task, worker_id="test-worker-1")

    assert result.status == "ok", f"expected ok, got {result.status}: {result.error_message}"
    assert result.pass_id == "drills"
    assert result.payload is not None
    assert result.payload.get("status") == "completed"
    assert result.payload.get("drill_results") == fake_results
    # Verify the drill spans got normalized into the context
    assert ctx.drill_spans, "engine should have populated context.drill_spans"


def test_run_task_hot_targets_pattern_mode(engine_no_db, mock_ollama):
    """run_task on a hot_targets pass returns pattern detections for matching keywords."""
    # Build an AnalysisConfig with one keyword-pattern hot target
    config = AnalysisConfig(id="cfg-1", name="cfg", passes=[])
    target = MagicMock()
    target.enabled = True
    target.id = "test-target"
    target.description = "looks for X"
    target.prompt = None
    target.instruction = None
    target.pattern_type = "keyword"
    target.pattern = ["politics"]
    target.mode = "keyword"
    config.hot_targets = [target]

    ctx = JobContext(
        job_id="job-2002",
        ytid="testytid",
        config_id="cfg-1",
        config=config,
        total_chunks=1,
    )
    engine_no_db._get_job_context = MagicMock(return_value=ctx)

    # The runner should never be called for a keyword-pattern target — track it anyway
    fake_runner = MagicMock(name="HotTargetRunner")
    fake_runner.run_targets.return_value = {}
    engine_no_db.hot_target_runner = fake_runner

    task = AnalysisTask(
        task_id=998,
        job_id="job-2002",
        ytid="testytid",
        chunk_id=0,
        pass_id="hot_targets",
        chunk_text="The senate voted on the politics question today.",
        chunk_metadata={"config_id": "cfg-1", "is_job_level": False, "total_chunks": 1},
        status=TaskStatus.CLAIMED,
        model_profile_id=1,
    )
    result = engine_no_db.run_task(task, worker_id="test-worker-1")
    assert result.status == "ok", f"expected ok, got {result.status}: {result.error_message}"
    assert result.pass_id == "hot_targets"
    assert result.payload is not None
    assert result.payload.get("status") == "completed"
    targets = result.payload.get("targets") or []
    assert len(targets) == 1
    assert targets[0]["target_id"] == "test-target"
    assert "politics" in targets[0]["matches"]
    # Pattern path; LLM runner should not have been touched
    fake_runner.run_targets.assert_not_called()


def test_process_job_end_to_end(engine_no_db):
    """process_job runs all tasks for a job, marks them, aggregates."""
    fake_tasks = [
        AnalysisTask(
            task_id=1,
            job_id="job-3001",
            ytid="testytid",
            chunk_id=0,
            pass_id="chunk_analysis",
            chunk_text="hello world",
            chunk_metadata={"config_id": "cfg-1", "total_chunks": 3},
            status=TaskStatus.PENDING,
            model_profile_id=1,
        ),
        AnalysisTask(
            task_id=2,
            job_id="job-3001",
            ytid="testytid",
            chunk_id=1,
            pass_id="chunk_analysis",
            chunk_text="more text",
            chunk_metadata={"config_id": "cfg-1", "total_chunks": 3},
            status=TaskStatus.PENDING,
            model_profile_id=1,
        ),
        # An already-completed task — process_job should skip it
        AnalysisTask(
            task_id=3,
            job_id="job-3001",
            ytid="testytid",
            chunk_id=2,
            pass_id="chunk_analysis",
            chunk_text="extra text",
            chunk_metadata={"config_id": "cfg-1", "total_chunks": 3},
            status=TaskStatus.COMPLETED,
            model_profile_id=1,
        ),
        AnalysisTask(
            task_id=4,
            job_id="job-3001",
            ytid="testytid",
            chunk_id=-1,
            pass_id="aggregate_results",
            chunk_text="",
            chunk_metadata={"config_id": "cfg-1", "is_job_level": True, "total_chunks": 3},
            status=TaskStatus.PENDING,
            model_profile_id=1,
        ),
    ]
    engine_no_db.task_repo = MagicMock()
    engine_no_db.task_repo.get_job_tasks.return_value = fake_tasks
    engine_no_db.task_repo.mark_completed = MagicMock(return_value=True)
    engine_no_db.task_repo.mark_failed = MagicMock(return_value=True)
    # Stub aggregate_results so we don't go through results_repo
    engine_no_db.aggregate_results = MagicMock(return_value={"summary": "ok"})

    # Force every run_task to "ok"
    fake_run_task = MagicMock(
        return_value=MagicMock(status="ok", payload={"pass_id": "stub", "status": "completed"}, error_message=None)
    )
    engine_no_db.run_task = fake_run_task

    result = engine_no_db.process_job("job-3001", worker_id="test-worker-1")

    assert result.job_id == "job-3001"
    assert result.tasks_total == 4
    # Task 3 was already completed, so 1 ok from skip + 3 ok from run_task
    assert result.tasks_ok == 4
    assert result.tasks_failed == 0
    assert result.aggregate_status == "ok"
    # mark_completed should have been called for the 3 non-completed tasks
    assert engine_no_db.task_repo.mark_completed.call_count == 3
    engine_no_db.aggregate_results.assert_called_once_with("job-3001")
    # chunk_analysis tasks should run before aggregate_results
    pass_order = [call.args[0].pass_id for call in fake_run_task.call_args_list]
    assert pass_order.index("aggregate_results") == len(pass_order) - 1


def test_process_job_failed_aggregate_status(engine_no_db):
    """If every task fails, aggregate_status is 'failed' and aggregate_results not called."""
    fake_tasks = [
        AnalysisTask(
            task_id=1,
            job_id="job-3003",
            ytid="testytid",
            chunk_id=0,
            pass_id="chunk_analysis",
            chunk_text="x",
            chunk_metadata={"config_id": "cfg-1", "total_chunks": 1},
            status=TaskStatus.PENDING,
            model_profile_id=1,
        ),
    ]
    engine_no_db.task_repo = MagicMock()
    engine_no_db.task_repo.get_job_tasks.return_value = fake_tasks
    engine_no_db.task_repo.mark_completed = MagicMock(return_value=True)
    engine_no_db.task_repo.mark_failed = MagicMock(return_value=True)
    engine_no_db.aggregate_results = MagicMock()
    engine_no_db.run_task = MagicMock(
        return_value=MagicMock(status="failed", payload=None, error_message="boom")
    )

    result = engine_no_db.process_job("job-3003", worker_id="test-worker-1")

    assert result.tasks_total == 1
    assert result.tasks_ok == 0
    assert result.tasks_failed == 1
    assert result.aggregate_status == "failed"
    engine_no_db.aggregate_results.assert_not_called()
    engine_no_db.task_repo.mark_failed.assert_called_once()


def test_aggregate_results_groups_by_pass(engine_no_db):
    """aggregate_results groups completed task results by pass_id and upserts."""
    fake_tasks = [
        AnalysisTask(
            task_id=1,
            job_id="job-3002",
            ytid="testytid",
            chunk_id=0,
            pass_id="chunk_analysis",
            chunk_text="t0",
            chunk_metadata={"config_id": "cfg-2", "total_chunks": 2},
            status=TaskStatus.COMPLETED,
            result_json={"analysis": {"summary": "s0"}},
            model_profile_id=1,
        ),
        AnalysisTask(
            task_id=2,
            job_id="job-3002",
            ytid="testytid",
            chunk_id=1,
            pass_id="chunk_analysis",
            chunk_text="t1",
            chunk_metadata={"config_id": "cfg-2", "total_chunks": 2},
            status=TaskStatus.COMPLETED,
            result_json={"analysis": {"summary": "s1"}},
            model_profile_id=1,
        ),
        AnalysisTask(
            task_id=3,
            job_id="job-3002",
            ytid="testytid",
            chunk_id=-1,
            pass_id="aggregate_results",
            chunk_text="",
            chunk_metadata={"config_id": "cfg-2", "is_job_level": True, "total_chunks": 2},
            status=TaskStatus.COMPLETED,
            result_json={"analysis_summary": {"summary": "agg"}},
            model_profile_id=1,
        ),
    ]
    engine_no_db.task_repo = MagicMock()
    engine_no_db.task_repo.get_job_tasks.return_value = fake_tasks
    engine_no_db.task_repo.get_job_progress.return_value = {
        "total": 3,
        "pending": 0,
        "claimed": 0,
        "completed": 3,
        "failed": 0,
    }
    engine_no_db.results_repo = MagicMock()

    output = engine_no_db.aggregate_results("job-3002")

    assert output is not None
    engine_no_db.results_repo.upsert_results.assert_called_once()
    kwargs = engine_no_db.results_repo.upsert_results.call_args.kwargs
    assert kwargs["job_id"] == "job-3002"
    assert kwargs["ytid"] == "testytid"
    assert kwargs["config_id"] == "cfg-2"
    assert kwargs["status"] == "completed"
    assert kwargs["total_tasks"] == 3
    assert kwargs["completed_tasks"] == 3
    assert kwargs["failed_tasks"] == 0
    by_pass = kwargs["results_by_pass"]
    assert "chunk_analysis" in by_pass
    assert len(by_pass["chunk_analysis"]) == 2
    assert "aggregate_results" in by_pass
