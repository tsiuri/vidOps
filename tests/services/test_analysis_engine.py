"""Tests for services.analysis_engine.AnalysisEngine."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from services.analysis_engine import AnalysisEngine


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
