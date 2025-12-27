#!/usr/bin/env python3
"""Analysis configuration models for the transcript analysis pipeline.

This module defines a first-class, versioned configuration object that
describes which passes to run, how to chunk, and which hot targets to use.

The goal is for *all* analysis behavior to be describable as an
AnalysisConfig instance, which can be stored in the DB or on disk as JSON.

This code is written to be tolerant of three environments:

- pydantic v1 installed
- pydantic v2 installed
- no pydantic installed at all

In the latter case, the models behave as light-weight containers with
attribute access, but without strict validation.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pathlib import Path

try:
    # Try pydantic v1 / v2
    from pydantic import BaseModel, Field, ValidationError  # type: ignore
    _HAS_PYDANTIC = True
except Exception:  # pragma: no cover
    _HAS_PYDANTIC = False

    class BaseModel:  # type: ignore
        """Very small stand-in for pydantic.BaseModel when pydantic is unavailable."""
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

        def dict(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
            return self.__dict__.copy()

    def Field(default=None, **kwargs):  # type: ignore
        return default

    class ValidationError(Exception):
        pass


class AnalysisPass(BaseModel):
    """A single analysis pass.

    phase:
      - "chunk"       – runs per transcript chunk
      - "aggregation" – runs once, after all chunks are processed
    """

    id: str
    phase: Literal["chunk", "aggregation"]
    enabled: bool = True
    order: int = 0
    requires: List[str] = Field(default_factory=list)
    params: Dict[str, Any] = Field(default_factory=dict)


class HotTargetRule(BaseModel):
    """Configuration for hot-target detection and drill-down behavior."""

    id: str
    description: str = ""
    pattern_type: Literal["keyword", "regex", "llm_tag"]
    # str or list[str]; interpreted by trigger code
    pattern: Any
    threshold: Optional[float] = None
    drill_pass_id: Optional[str] = None
    enabled: bool = True


class SpeakerFilterConfig(BaseModel):
    """Speaker filtering configuration for the pipeline.

    Allows filtering analysis to specific speakers or excluding certain speakers.
    Useful for podcasts where you only want the host's content analyzed.
    """

    # Filter mode: 'all' (no filtering), 'include' (only these speakers), 'exclude' (these speakers only)
    mode: Literal["all", "include", "exclude"] = "all"

    # List of speaker names to include or exclude
    speakers: List[str] = Field(default_factory=list)

    # Case-insensitive speaker matching
    case_insensitive: bool = True


class ChunkParams(BaseModel):
    """Chunking parameters for the pipeline."""

    max_words: int = 1000
    overlap_words: int = 150
    per_speaker_tracks: bool = False
    speaker_filter: Optional[SpeakerFilterConfig] = None




class Drill(BaseModel):
    """Configuration for a single drill in the analysis pipeline.

    Drills are used by the DrillExecutor to run targeted LLM passes over
    chunks or spans. They are now stored in the dedicated 'drills' database table
    and loaded at runtime. This model is used for API serialization and backward compatibility.

    NOTE: Drills are now managed via the database, not stored in AnalysisConfig.
    """

    # Database fields (populated when loading from database)
    id: Optional[int] = None  # NEW: Unique database ID
    is_local: bool = False  # NEW: True if local to a config, False if global

    # Unique identifier for this drill within a given AnalysisConfig.
    name: str

    # Human readable description of what the drill does.
    description: str = ""

    # Prompt that will be sent to the LLM for this drill.
    prompt: str

    # What logical unit this drill runs on.
    scope: Literal["chunks", "spans", "subchunks"] = "chunks"

    # Other drills that must have run before this one.
    depends_on: List[str] = Field(default_factory=list)

    # Shape of the output this drill produces/stores.
    output_shape: Literal["span", "chunk"] = "span"

    # If True, run on every eligible unit regardless of triggers.
    always: bool = False

    # Minimum number of keyword/category hits required to trigger.
    min_hits: int = 0

    # Keyword triggers for this drill.
    keywords: List[str] = Field(default_factory=list)

    # Category tags to match against for triggering.
    match: List[str] = Field(default_factory=list)

    # Category for drill output / downstream routing.
    category: Optional[str] = None

    # Cooldown in seconds between runs for the same logical entity.
    cooldown: int = 0

    # Per-drill LLM configuration override. If any of these values are
    # omitted, the executor should fall back to the parent config defaults.
    detail_pass: Dict[str, Any] = Field(default_factory=dict)

    # Metadata fields (populated from database)
    created_at: Optional[str] = None  # NEW: ISO format timestamp
    updated_at: Optional[str] = None  # NEW: ISO format timestamp

class AnalysisConfig(BaseModel):
    """Top-level configuration for an analysis run.

    This object is intended to be the single source of truth for
    chunking, passes, aggregation, and hot target behavior.

    NOTE: Drills are now stored in a separate database table and loaded at runtime.
    The 'drills' field below is maintained for backward compatibility but is typically
    empty for new configs. Drills are managed via the /drills API endpoints.
    """

    id: str
    name: str
    version: int = 1
    description: str = ""
    analysis_type: str = "normal"
    model: Optional[str] = None
    model_profile_id: Optional[int] = None
    transcription_machine: Optional[str] = None
    diarized: bool = False

    chunk_params: ChunkParams = ChunkParams()
    passes: List[AnalysisPass] = Field(default_factory=list)
    hot_targets: List[HotTargetRule] = Field(default_factory=list)
    drills: List[Drill] = Field(default_factory=list)
    # If True, unknown pass IDs in `passes` will cause the pipeline to error out.
    # If False (default), unknown pass IDs are logged and skipped.
    strict_pass_validation: bool = False

    # Custom output shapes defined for this config (name -> JSON schema).
    # Built-in shapes are always available: "span", "chunk", "presence".
    # Users can define additional shapes here for specialized drill outputs.
    output_shapes: Dict[str, Any] = Field(default_factory=dict)

    # Free-form backend parameters; callers may interpret these as needed.
    backend_params: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        # For pydantic v1
        arbitrary_types_allowed = True

    @classmethod
    def from_file(cls, path: Path) -> "AnalysisConfig":
        """Load an AnalysisConfig from a JSON file."""
        import json

        data = json.loads(Path(path).read_text(encoding="utf-8"))
        # pydantic v2 / v1 compatibility
        if _HAS_PYDANTIC and hasattr(cls, "model_validate"):
            return cls.model_validate(data)  # type: ignore[attr-defined]
        if _HAS_PYDANTIC and hasattr(cls, "parse_obj"):
            return cls.parse_obj(data)  # type: ignore[attr-defined]
        # No pydantic: construct directly
        obj = cls.__new__(cls)  # type: ignore[call-arg]
        for k, v in data.items():
            setattr(obj, k, v)
        return obj  # type: ignore[return-value]

    def to_file(self, path: Path) -> None:
        """Write this config to a JSON file."""
        import json

        if _HAS_PYDANTIC and hasattr(self, "model_dump"):
            as_dict = self.model_dump()  # type: ignore[attr-defined]
        elif _HAS_PYDANTIC and hasattr(self, "dict"):
            as_dict = self.dict()  # type: ignore[attr-defined]
        else:
            as_dict = self.__dict__  # type: ignore[assignment]

        Path(path).write_text(
            json.dumps(as_dict, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def load_config_from_file(path: Path) -> AnalysisConfig:
    """Convenience wrapper to load an AnalysisConfig from disk."""
    return AnalysisConfig.from_file(path)


def save_config_to_file(config: AnalysisConfig, path: Path) -> None:
    """Save an AnalysisConfig instance to disk as JSON."""
    config.to_file(path)


def default_config(config_id: str = "default_normal") -> AnalysisConfig:
    """Return a default configuration approximating current behavior.

    This version enumerates the main built-in passes so that callers can
    see and override the pipeline behavior explicitly via the config.
    """
    return AnalysisConfig(
        id=config_id,
        name="Default (normal)",
        version=1,
        description="Default analysis config approximating existing hard-coded behavior.",
        analysis_type="normal",
        chunk_params=ChunkParams(),
        passes=[
            # Chunk-level passes
            AnalysisPass(id="chunk_analysis",   phase="chunk",       enabled=True, order=10),
            AnalysisPass(id="subchunks",        phase="chunk",       enabled=True, order=20),
            AnalysisPass(id="sentiment_pass",   phase="chunk",       enabled=True, order=30),
            AnalysisPass(id="categories_pass",  phase="chunk",       enabled=True, order=40),

            # Aggregation / post-processing passes
            AnalysisPass(id="aggregate_results", phase="aggregation", enabled=True, order=100),
            AnalysisPass(id="hot_targets",       phase="aggregation", enabled=True, order=110),
            AnalysisPass(id="drills",            phase="aggregation", enabled=True, order=120),
            AnalysisPass(id="db_store",          phase="aggregation", enabled=True, order=130),
            AnalysisPass(id="local_json",        phase="aggregation", enabled=True, order=140),
            AnalysisPass(id="markdown_report",   phase="aggregation", enabled=True, order=150),
        ],
        hot_targets=[],
        strict_pass_validation=False,
        backend_params={},
    )
