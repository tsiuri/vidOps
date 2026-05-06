from unittest.mock import MagicMock

from services.analysis_engine import AnalysisEngine
from scripts.analysis.analysis_config import AnalysisConfig


def _make_engine():
    engine = AnalysisEngine.__new__(AnalysisEngine)
    engine.analyzer = MagicMock()
    return engine


def test_attach_summaries_generates_fields():
    engine = _make_engine()
    engine.analyzer.summarize.return_value = {
        "tldr_one_sentence": "You are ready to go.",
        "summary_paragraph": "You are explaining the situation clearly.",
        "political_overview": "You are outlining policies.",
        "controversies": "You are facing controversies.",
    }
    engine.analyzer.summarize_speaker.return_value = {
        "speaker_overview": "You are the host.",
        "personal_themes": ["theme"],
        "noteworthy_statements": ["quote"],
        "personal_conflicts": ["conflict"],
    }
    aggregated = {"analysis": {}, "metadata": {}}
    config = AnalysisConfig(id="cfg", name="Cfg", passes=[])

    engine._attach_summaries(aggregated, config)

    summaries = aggregated["summaries"]
    assert "the speaker is ready to go." in summaries["tldr_one_sentence"].lower()
    assert summaries["personal_themes"] == ["theme"]
    assert summaries["noteworthy_statements"] == ["quote"]
    assert summaries["personal_conflicts"] == ["conflict"]


def test_attach_summaries_respects_skip_flag():
    engine = _make_engine()
    aggregated = {"analysis": {}, "metadata": {}}
    config = AnalysisConfig(
        id="cfg",
        name="Cfg",
        passes=[],
        backend_params={"skip_summaries": True},
    )

    engine._attach_summaries(aggregated, config)

    engine.analyzer.summarize.assert_not_called()
    engine.analyzer.summarize_speaker.assert_not_called()
