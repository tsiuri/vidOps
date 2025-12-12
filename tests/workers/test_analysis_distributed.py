from unittest.mock import MagicMock

from workers.analysis_distributed import AnalysisWorker
from scripts.analysis.analysis_config import AnalysisConfig


def _make_worker():
    worker = AnalysisWorker.__new__(AnalysisWorker)
    worker.analyzer = MagicMock()
    return worker


def test_attach_summaries_generates_fields():
    worker = _make_worker()
    worker.analyzer.summarize.return_value = {
        "tldr_one_sentence": "You are ready to go.",
        "summary_paragraph": "You are explaining the situation clearly.",
        "political_overview": "You are outlining policies.",
        "controversies": "You are facing controversies.",
    }
    worker.analyzer.summarize_speaker.return_value = {
        "speaker_overview": "You are the host.",
        "personal_themes": ["theme"],
        "noteworthy_statements": ["quote"],
        "personal_conflicts": ["conflict"],
    }
    aggregated = {"analysis": {}, "metadata": {}}
    config = AnalysisConfig(id="cfg", name="Cfg", passes=[])

    worker._attach_summaries(aggregated, config)

    summaries = aggregated["summaries"]
    assert "the speaker is ready to go." in summaries["tldr_one_sentence"].lower()
    assert summaries["personal_themes"] == ["theme"]
    assert summaries["noteworthy_statements"] == ["quote"]
    assert summaries["personal_conflicts"] == ["conflict"]


def test_attach_summaries_respects_skip_flag():
    worker = _make_worker()
    aggregated = {"analysis": {}, "metadata": {}}
    config = AnalysisConfig(
        id="cfg",
        name="Cfg",
        passes=[],
        backend_params={"skip_summaries": True},
    )

    worker._attach_summaries(aggregated, config)

    worker.analyzer.summarize.assert_not_called()
    worker.analyzer.summarize_speaker.assert_not_called()
