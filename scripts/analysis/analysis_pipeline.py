#!/usr/bin/env python3
"""
Modular transcript analysis pipeline.

This module wraps parsing, chunking, LLM calls, aggregation, and persistence so
callers can drive analysis consistently for both database and local JSON
storage.
"""

from __future__ import annotations

import concurrent.futures
import json
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .analyze_transcript import (
    VTTParser,
    TranscriptChunker,
    OllamaAnalyzer,
    AnalysisAggregator,
    generate_markdown_report,
)
from .llm.hot_targets import HotTargetRunner
from .scheduler.gpu_scheduler import GPUScheduler
from .drills import DrillExecutor, load_drills
from .scheduler.gpu_scheduler import GPUScheduler
from .drills import DrillExecutor, load_drills
from .llm.hot_target_trigger import HotTargetTriggerEvaluator
from .llm.hot_target_scheduler import HotTargetScheduler, ExecutionMode
from .db_storage import AnalysisDatabase


@dataclass
class PipelineSettings:
    model: str = "qwen2.5:7b-instruct"
    quality: str = "thorough"
    chunk_size: int = 1000
    overlap: int = 150
    chunk_parallel: int = 4
    request_text: str = ""
    ollama_url: str = "http://localhost:11434"
    chunk_url_a: Optional[str] = None
    chunk_model_a: Optional[str] = None
    chunk_url_b: Optional[str] = None
    chunk_model_b: Optional[str] = None
    speaker_url: Optional[str] = None
    speaker_model: Optional[str] = None
    speaker_alias: str = "Hasan"
    write_local: bool = True
    output_dir: Path = Path("analysis_runs")
    include_chunk_text: bool = False
    write_markdown: bool = False
    skip_summaries: bool = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    num_predict: Optional[int] = None
    num_ctx: Optional[int] = None
    repeat_penalty: Optional[float] = None
    log_mode: str = "quiet"  # quiet | progress | verbose
    diarized: bool = False
    transcription_machine: Optional[str] = None
    hot_targets_path: Optional[Path] = None
    hot_targets: List[Dict[str, Any]] = field(default_factory=list)
    hot_target_scheduler_config: Optional[Dict[str, Any]] = None  # Scheduler configuration
    alias_map_path: Optional[str] = None
    category_cache_path: Optional[str] = None
    category_cache: Dict[str, str] = field(default_factory=dict)  # normalized -> canonical
    category_cache_writeback: bool = True
    category_map_path: Optional[str] = None
    category_map: Dict[str, str] = field(default_factory=dict)  # alias -> canonical
    drills_path: Optional[Path] = None
    drills: List[Dict[str, Any]] = field(default_factory=list)
    drills_scheduler_config: Optional[Dict[str, Any]] = None
    gpu_scheduler_config: Optional[Dict[str, Any]] = None
    merge_gap_sec: float = 10.0
    subchunks_enabled: bool = True


@dataclass
class PipelineRunResult:
    metadata: Dict[str, Any]
    final_analysis: Dict[str, Any]
    chunk_analyses: List[Dict[str, Any]]
    local_paths: Dict[str, Any] = field(default_factory=dict)


class TranscriptAnalysisPipeline:
    """High-level pipeline for transcript analysis and persistence."""

    def __init__(self, settings: PipelineSettings, db: AnalysisDatabase | None = None, config: object | None = None):
        self.settings = settings
        self.config = config  # Optional AnalysisConfig instance
        self.db = db
        self.alias_map = self._load_alias_map()
        self.category_cache, self.category_cache_format = self._load_category_cache()
        self.category_map = self._load_category_map()
        self.drills = self._load_drills()

        # Apply configuration overrides that affect pipeline settings (e.g., chunking).
        self._apply_config_overrides()

        # Configure analysis pass registry and activation state
        self.pass_registry = self._build_pass_registry()
        self.enabled_passes: Set[str] = set()
        self.unknown_passes: Set[str] = set()
        self._resolve_enabled_passes_from_config()

        # Initialize hot target trigger evaluator and scheduler
        self.trigger_evaluator = HotTargetTriggerEvaluator()
        self.scheduler = self._init_scheduler()
        # Shared GPU scheduler (queue-backed) for hot targets/drills; currently used inline
        self.gpu_scheduler = GPUScheduler(config=self.settings.gpu_scheduler_config, log_mode=self.settings.log_mode)

    # --- Configuration / pass activation helpers ---------------------------------

    def _apply_config_overrides(self) -> None:
        """
        Apply overrides from the AnalysisConfig onto PipelineSettings.

        Currently this wires chunk_params (max_words, overlap_words,
        per_speaker_tracks) into the pipeline. It is intentionally tolerant
        of missing or partial configs.
        """
        cfg = self.config
        if cfg is None:
            return

        chunk_params = getattr(cfg, "chunk_params", None)
        if chunk_params is None:
            return

        max_words = getattr(chunk_params, "max_words", None)
        if isinstance(max_words, int) and max_words > 0:
            self.settings.chunk_size = max_words

        overlap_words = getattr(chunk_params, "overlap_words", None)
        if isinstance(overlap_words, int) and overlap_words >= 0:
            self.settings.overlap = overlap_words

        per_speaker_tracks = getattr(chunk_params, "per_speaker_tracks", None)
        if per_speaker_tracks is not None:
            # When per-speaker tracks are requested, we ensure subchunk logic is enabled.
            self.settings.subchunks_enabled = bool(per_speaker_tracks)
        # If the AnalysisConfig carries hot_targets inline, prefer those over any file-based config.
        hot_targets_cfg = getattr(cfg, "hot_targets", None)
        if hot_targets_cfg:
            normalized_hot_targets: List[Dict[str, Any]] = []
            for ht in hot_targets_cfg:
                if isinstance(ht, dict):
                    normalized_hot_targets.append(ht)
                else:
                    # Pydantic model or similar
                    if hasattr(ht, "model_dump"):
                        normalized_hot_targets.append(ht.model_dump())
                    elif hasattr(ht, "dict"):
                        normalized_hot_targets.append(ht.dict())
                    else:
                        # Fallback: shallow __dict__ copy
                        try:
                            normalized_hot_targets.append(
                                {k: v for k, v in vars(ht).items() if not k.startswith("_")}
                            )
                        except Exception:
                            continue
            if normalized_hot_targets:
                self.settings.hot_targets = normalized_hot_targets
                # When hot targets are provided by the config object, ignore any file path.
                self.settings.hot_targets_path = None

    def _build_pass_registry(self) -> Dict[str, Dict[str, Any]]:
        """
        Define the built-in analysis passes.

        This is intentionally simple; more metadata can be added over time.
        """
        return {
            # Chunk-level passes
            "chunk_analysis": {
                "phase": "chunk",
                "description": "Primary per-chunk LLM analysis.",
            },
            "subchunks": {
                "phase": "chunk",
                "description": "Speaker/subchunk-level breakdowns and summaries.",
            },
            "sentiment_pass": {
                "phase": "chunk",
                "description": "Sentiment-related per-chunk analysis (currently folded into chunk_analysis).",
            },
            "categories_pass": {
                "phase": "chunk",
                "description": "Category tagging (currently folded into chunk_analysis).",
            },

            # Aggregation / post-processing passes
            "aggregate_results": {
                "phase": "aggregation",
                "description": "Aggregate per-chunk analyses into a transcript-wide view.",
            },
            "hot_targets": {
                "phase": "aggregation",
                "description": "Hot-target detection and detail passes.",
            },
            "drills": {
                "phase": "aggregation",
                "description": "LLM drill passes driven by drills configuration.",
            },
            "db_store": {
                "phase": "aggregation",
                "description": "Persist full analysis, chunks, and spans into the database.",
            },
            "local_json": {
                "phase": "aggregation",
                "description": "Write analysis.json + per-chunk JSON locally.",
            },
            "markdown_report": {
                "phase": "aggregation",
                "description": "Generate analysis.md markdown report.",
            },
        }

    def _resolve_enabled_passes_from_config(self) -> None:
        """
        Compute the set of enabled passes based on the AnalysisConfig.

        - If no config or no passes are defined, all known passes are enabled.
        - Unknown pass IDs are logged; if config.strict_pass_validation is True,
          the pipeline will raise instead of silently skipping.
        """
        # Default: everything is enabled
        self.enabled_passes = set(self.pass_registry.keys())
        self.unknown_passes = set()

        cfg = self.config
        passes = getattr(cfg, "passes", None) if cfg is not None else None
        if not passes:
            # Keep default "all enabled" behavior for backwards compatibility.
            return

        strict = bool(getattr(cfg, "strict_pass_validation", False))

        enabled: Set[str] = set()
        unknown: Set[str] = set()

        for p in passes:
            pass_id = getattr(p, "id", None)
            if not pass_id or not getattr(p, "enabled", True):
                continue
            if pass_id in self.pass_registry:
                enabled.add(pass_id)
                # Also enable any declared requirements
                for req in getattr(p, "requires", []) or []:
                    if req in self.pass_registry:
                        enabled.add(req)
                    else:
                        unknown.add(req)
            else:
                unknown.add(pass_id)

        if not enabled:
            # Safety: never end up with an empty set; default back to all.
            enabled = set(self.pass_registry.keys())

        # Ensure the core chunk_analysis pass remains enabled for now so that
        # downstream logic relying on per-chunk analyses continues to work.
        if "chunk_analysis" in self.pass_registry and "chunk_analysis" not in enabled:
            if self.settings.log_mode in ("progress", "verbose"):
                print(
                    "[passes] warning: chunk_analysis was disabled in config but is currently required; re-enabling it",
                    flush=True,
                )
            enabled.add("chunk_analysis")

        self.enabled_passes = enabled
        self.unknown_passes = unknown

        if unknown:
            msg = f"Unknown analysis passes in config: {', '.join(sorted(unknown))}"
            print(f"Warning: {msg}", file=sys.stderr)
            if strict:
                raise ValueError(msg)

    def _is_pass_enabled(self, pass_id: str) -> bool:
        # If the pass is not known, fall back to "enabled" to avoid surprising behavior.
        if pass_id not in self.pass_registry:
            return True
        return pass_id in self.enabled_passes

    def _log_pass_plan(self) -> None:
        """Log which passes are enabled for this run (verbose/progress modes only)."""
        if self.settings.log_mode not in ("progress", "verbose"):
            return
        enabled = [p for p in self.pass_registry.keys() if p in self.enabled_passes]
        disabled = [p for p in self.pass_registry.keys() if p not in self.enabled_passes]
        print("[passes] enabled: " + (", ".join(enabled) if enabled else "<none>"), flush=True)
        if disabled:
            print("[passes] disabled: " + ", ".join(disabled), flush=True)

    def _init_scheduler(self) -> HotTargetScheduler:
        """Initialize hot target scheduler from settings."""
        if self.settings.hot_target_scheduler_config:
            return HotTargetScheduler.from_config(
                self.settings.hot_target_scheduler_config,
                log_mode=self.settings.log_mode
            )
        else:
            # Default scheduler: batch mode, no delays
            return HotTargetScheduler(
                mode=ExecutionMode.BATCH,
                delay_between=0,
                log_mode=self.settings.log_mode
            )

    def _build_options(self) -> Dict[str, Any]:
        presets = {
            "fast": {"temperature": 0.6, "top_p": 0.9, "top_k": 40, "num_predict": 256, "repeat_penalty": 1.05},
            "balanced": {"temperature": 0.3, "top_p": 0.9, "top_k": 40, "num_predict": 512, "repeat_penalty": 1.1},
            "thorough": {"temperature": 0.2, "top_p": 0.9, "top_k": 50, "num_predict": 1024, "repeat_penalty": 1.15},
        }
        opts = presets.get(self.settings.quality, {}).copy()
        if "num_ctx" not in opts:
            opts["num_ctx"] = 4096
        if self.settings.temperature is not None:
            opts["temperature"] = self.settings.temperature
        if self.settings.top_p is not None:
            opts["top_p"] = self.settings.top_p
        if self.settings.top_k is not None:
            opts["top_k"] = self.settings.top_k
        if self.settings.num_predict is not None:
            opts["num_predict"] = self.settings.num_predict
        if self.settings.num_ctx is not None:
            opts["num_ctx"] = self.settings.num_ctx
        if self.settings.repeat_penalty is not None:
            opts["repeat_penalty"] = self.settings.repeat_penalty
        return opts

    def _load_alias_map(self) -> Dict[str, str]:
        """Load alias map from disk if provided (JSON mapping alias -> canonical)."""
        amap: Dict[str, str] = {}
        path = self.settings.alias_map_path
        if not path:
            return amap
        try:
            p = Path(path)
            if not p.exists():
                print(f"Alias map path not found: {p}")
                return amap
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(k, str) and isinstance(v, str):
                        amap[k.strip().lower()] = v.strip()
            if amap:
                print(f"Loaded alias map ({len(amap)} entries) from {p}")
        except Exception as exc:
            print(f"Warning: failed to load alias map {path}: {exc}")
        return amap

    def _load_category_cache(self) -> Tuple[Dict[str, str], str]:
        """Load category cache from disk if provided (JSON list or mapping). Returns (cache, format)."""
        cache: Dict[str, str] = {}
        fmt = "mapping"
        path = self.settings.category_cache_path
        if not path:
            return cache, fmt
        try:
            p = Path(path)
            if not p.exists():
                print(f"Category cache path not found: {p}")
                return cache, fmt
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                fmt = "list"
                for cat in data:
                    if isinstance(cat, str) and cat.strip():
                        key = cat.strip().lower()
                        cache[key] = cat.strip()
            elif isinstance(data, dict):
                fmt = "mapping"
                for k, v in data.items():
                    if isinstance(k, str) and isinstance(v, str):
                        cache[k.strip().lower()] = v.strip()
            if cache:
                print(f"Loaded category cache ({len(cache)} entries) from {p}")
        except Exception as exc:
            print(f"Warning: failed to load category cache {path}: {exc}")
        return cache, fmt

    def _load_category_map(self) -> Dict[str, str]:
        """Load category synonym map (alias -> canonical)."""
        cmap: Dict[str, str] = {}
        path = self.settings.category_map_path
        if not path:
            return cmap
        try:
            p = Path(path)
            if not p.exists():
                print(f"Category map path not found: {p}")
                return cmap
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(k, str) and isinstance(v, str):
                        cmap[k.strip().lower()] = v.strip()
            if cmap:
                print(f"Loaded category map ({len(cmap)} entries) from {p}")
        except Exception as exc:
            print(f"Warning: failed to load category map {path}: {exc}")
        return cmap

    def _load_drills(self) -> List[Dict[str, Any]]:
        """Load drills config from database (if available) or file.

        Priority:
        1. If config has an ID and database is available, load from drills table
        2. Otherwise, load from drills_path file if available
        """
        # Try loading from database first
        if self.config and self.db:
            try:
                config_id = getattr(self.config, "id", None)
                if config_id:
                    db_drills = self.db.list_drills_for_config(config_id)
                    if db_drills:
                        # Convert database drills to dict format
                        drills = []
                        for d in db_drills:
                            drill_dict = {
                                "id": d.get("id"),
                                "name": d["name"],
                                "description": d.get("description", ""),
                                "prompt": d["prompt"],
                                "scope": d.get("scope", "chunks"),
                                "depends_on": d.get("depends_on", []),
                                "output_shape": d.get("output_shape", "span"),
                                "always": d.get("always", False),
                                "min_hits": d.get("min_hits", 0),
                                "keywords": d.get("keywords", []),
                                "match": d.get("match", []),
                                "category": d.get("category"),
                                "cooldown": d.get("cooldown", 0),
                                "detail_pass": d.get("detail_pass", {}),
                                "is_local": d.get("is_local", False),
                            }
                            drills.append(drill_dict)

                        if self.settings.log_mode in ("progress", "verbose"):
                            print(f"Loaded {len(drills)} drill(s) from database for config {config_id[:8]}...")
                        return drills
            except Exception as e:
                if self.settings.log_mode in ("progress", "verbose"):
                    print(f"Warning: Failed to load drills from database: {e}")

        # Fall back to file-based loading
        if not self.settings.drills_path:
            return []
        drills = load_drills(self.settings.drills_path)
        if drills:
            print(f"Loaded {len(drills)} drill spec(s) from {self.settings.drills_path}")
        return drills

    def _run_hot_targets(self, targets: List[Dict[str, Any]], chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Run targeted detail passes over chunks using the scheduler.

        The scheduler handles batching, rate-limiting, and priority-based execution.
        """
        if not targets:
            return {}

        # Prepare tasks for the GPU scheduler queue
        base_runner = HotTargetRunner(
            model=self.settings.chunk_model_a or self.settings.model,
            base_url=self.settings.chunk_url_a or self.settings.ollama_url,
            options=self._build_options(),
            log_mode=self.settings.log_mode,
        )

        def make_task(target: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "task_type": "hot_target",
                "target": target,
                "chunks": [{"chunk_id": c.get("chunk_id"), "text": c.get("text") or ""} for c in chunks],
                "priority_label": target.get("priority") or target.get("priority_label") or "normal",
            }

        tasks = [make_task(t) for t in targets]

        def executor(payload: Dict[str, Any]) -> Dict[str, Any]:
            target = payload["target"]
            local_chunks = payload["chunks"]
            tgt_model = target.get("model") or base_runner.model
            tgt_endpoint = target.get("endpoint") or base_runner.base_url
            if tgt_model == base_runner.model and tgt_endpoint == base_runner.base_url:
                exec_runner = base_runner
            else:
                exec_runner = HotTargetRunner(
                    model=tgt_model,
                    base_url=tgt_endpoint,
                    options=target.get("options") or base_runner.options,
                    log_mode=base_runner.log_mode,
                )
            name = target.get("name") or target.get("category") or "target"
            entries: List[Dict[str, Any]] = []
            for chunk in local_chunks:
                cid = chunk.get("chunk_id")
                text = chunk.get("text") or ""
                prompt = exec_runner._build_prompt(target, text, cid)
                data = exec_runner._call_model(prompt, cid)
                spans = data.get("spans") if isinstance(data, dict) else []
                entries.append(
                    {
                        "chunk_id": cid,
                        "spans": spans if isinstance(spans, list) else [],
                        "model_used": exec_runner.model,
                        "endpoint": exec_runner.base_url,
                    }
                )
            return {name: entries}

        results = self.gpu_scheduler.run_tasks(tasks, executor)
        merged_results: Dict[str, Any] = {}
        for res in results:
            if res["success"] and res["result"]:
                merged_results.update(res["result"])
        return merged_results

    def _maybe_update_category_cache(self, new_categories: Set[str]) -> None:
        """Persist newly seen categories into the cache file if configured."""
        if (
            not new_categories
            or not self.settings.category_cache_path
            or not self.settings.category_cache_writeback
        ):
            return
        try:
            path = Path(self.settings.category_cache_path)
            merged = dict(self.category_cache)
            added = 0
            for cat in new_categories:
                if not isinstance(cat, str):
                    continue
                canon = cat.strip()
                if not canon:
                    continue
                norm = canon.lower()
                if norm in merged:
                    continue
                merged[norm] = canon
                added += 1
            if not added:
                return
            # Write back preserving simple list format if that's what we loaded
            if self.category_cache_format == "list":
                out_payload = sorted(set(merged.values()))
            else:
                out_payload = {k: merged[k] for k in sorted(merged.keys())}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(out_payload, indent=2, ensure_ascii=False), encoding="utf-8")
            self.category_cache = merged
            print(f"Updated category cache at {path} (+{added} new)")
        except Exception as exc:
            print(f"Warning: failed to update category cache: {exc}")

    def _detect_hot_targets(self, chunk_analyses: List[Dict[str, Any]], chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Identify which hot targets should be triggered based on configurable rules.

        Uses the new HotTargetTriggerEvaluator for rich trigger logic including:
        - Min occurrence thresholds
        - Keyword matching with thresholds
        - OR/AND logic
        - Cooldown tracking
        """
        if not self.settings.hot_targets:
            return []

        return self.trigger_evaluator.evaluate_all_targets(
            self.settings.hot_targets,
            chunk_analyses,
            chunks
        )

    def _extract_metadata(self, input_path: Path) -> Dict[str, Any]:
        """Infer video_id/title/date from filename + optional info.json."""
        import json as _json

        filename = input_path.stem
        parts = filename.split("__")
        video_id = parts[0] if parts else "unknown"
        title = parts[1] if len(parts) > 1 else "Unknown Title"
        title = re.sub(r"\.(transcript\.[a-z]{2}|[a-z]{2})$", "", title)
        date = "unknown"

        try:
            cand = next((p for p in input_path.parent.glob(f"{video_id}__*.info.json")), None)
            if cand and cand.exists():
                with open(cand, "r", encoding="utf-8") as jf:
                    info = _json.load(jf)
                    title = info.get("title", title)
                    date = info.get("upload_date") or info.get("date") or date
                    if isinstance(date, str) and re.match(r"^\d{8}$", date):
                        date = f"{date[0:4]}-{date[4:6]}-{date[6:8]}"
        except Exception:
            pass

        if date == "unknown":
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", title)
            date = date_match.group(1) if date_match else "unknown"

        return {
            "video_id": video_id,
            "title": title,
            "date": date,
            "source_file": str(input_path),
            "vtt_path": str(input_path),
        }

    def _should_skip(self, video_id: str, force: bool) -> bool:
        """Return True if DB says analysis already exists and force is False."""
        if force or not self.db:
            return False
        status = self.db.get_analysis_status(video_id)
        if status and status.get("analyzed"):
            analyzed_at = status.get("analyzed_at")
            print(f"Skipping {video_id} - already analyzed on {analyzed_at}")
            print("  (use --force to re-run)")
            return True
        return False

    def _postprocess_chunk(self, raw: Dict[str, Any], chunk: Dict[str, Any]) -> Dict[str, Any]:
        """Clean model output and attach metadata."""
        analysis = dict(raw)
        chunk_text = chunk.get("text", "")
        chunk_lower = chunk_text.lower()
        # Apply alias map to people names if provided
        if self.alias_map:
            mapped_people: List[str] = []
            for person in analysis.get("people", []) or []:
                if not isinstance(person, str):
                    continue
                nm = person.strip()
                if not nm:
                    continue
                canonical = self.alias_map.get(nm.lower(), nm)
                mapped_people.append(canonical)
            analysis["people"] = mapped_people
        # Quotes: drop placeholders and ensure they exist in the chunk text
        quotes_in = analysis.get("notable_quotes", []) or []
        filtered_quotes = []
        for q in quotes_in:
            ql = str(q).strip()
            if not ql:
                continue
            qll = ql.lower()
            if qll in {"short direct quote", "none", "none found", "direct quote", "quote", "sample quote", "placeholder", "exact quote from the text"}:
                continue
            if "note confidence" in qll:
                continue
            if qll in chunk_lower:
                filtered_quotes.append(ql)
        analysis["notable_quotes"] = filtered_quotes[:5]

        # Categories: drop obvious template fillers when not present in text; normalize via cache if provided
        categories = []
        for c in analysis.get("categories") or []:
            cstr = str(c).strip().lower()
            if not cstr:
                continue
            if cstr in {"music", "classical", "review"} and cstr not in chunk_lower:
                continue
            if self.category_map:
                mapped = self.category_map.get(cstr)
                if mapped:
                    categories.append(mapped)
                    continue
            if self.category_cache:
                canonical = self.category_cache.get(cstr)
                if canonical:
                    categories.append(canonical)
                    continue
            categories.append(cstr)
        analysis["categories"] = categories

        # Summary fallback / cleanup
        analysis["summary"] = self._clean_summary(analysis.get("summary", ""), chunk_text, analysis)

        analysis["chunk_id"] = chunk.get("chunk_id")
        analysis["word_count"] = chunk.get("word_count")
        if self.settings.include_chunk_text:
            analysis["chunk_text"] = chunk_text
        return analysis

    def _clean_summary(self, summary: Any, chunk_text: str, analysis: Dict[str, Any]) -> str:
        placeholder_phrases = {
            "one or two sentence summary of this segment",
            "summary of this segment",
            "write a summary",
            "short summary",
            "tldr",
        }
        raw_sum = summary if isinstance(summary, str) else ""
        clower = chunk_text.lower()

        def split_sentences(text: str) -> List[str]:
            sents = re.split(r"(?<=[.!?])\s+", text.strip())
            return [s.strip() for s in sents if s.strip()]

        def synthesize_from_chunk() -> str:
            sents = split_sentences(chunk_text)
            chosen: List[str] = []
            for s in sents:
                wc = len(s.split())
                if 12 <= wc <= 35:
                    chosen.append(s)
                    if len(chosen) == 2:
                        break
            if not chosen and sents:
                chosen.append(sents[0][:400])
            return " ".join(chosen).strip()

        def needs_fallback(text: str) -> bool:
            if not text:
                return True
            sl = text.lower().strip()
            if sl in placeholder_phrases:
                return True
            words = [w for w in re.findall(r"[a-zA-Z]{4,}", sl)]
            if not words:
                return True
            unmatched = [w for w in words if w not in clower]
            return (len(unmatched) / max(1, len(words))) > 0.5

        if needs_fallback(raw_sum):
            kps = [str(k).strip() for k in (analysis.get("key_points") or []) if str(k).strip()]
            if kps:
                return " ".join(kps[:2])
            return synthesize_from_chunk()
        sents = split_sentences(raw_sum)
        return " ".join(sents[:2])

    def _analyze_chunks(
        self,
        chunks: Sequence[Dict[str, Any]],
        analyzer_a: OllamaAnalyzer,
        analyzer_b: OllamaAnalyzer | None,
    ) -> List[Dict[str, Any]]:
        """Run chunk analyses in parallel and preserve order."""
        results: List[Optional[Dict[str, Any]]] = [None] * len(chunks)

        def run_one(idx: int, chunk_obj: Dict[str, Any]) -> Dict[str, Any]:
            ana = analyzer_a if (analyzer_b is None or idx % 2 == 0) else analyzer_b
            res = ana.analyze_chunk(chunk_obj["text"], chunk_obj["chunk_id"])
            res["model_used"] = getattr(ana, "model", self.settings.model)
            processed = self._postprocess_chunk(res, chunk_obj)
            if self.settings.log_mode in {"progress", "verbose"}:
                cid = chunk_obj.get("chunk_id")
                wc = chunk_obj.get("word_count")
                print(f"[chunk {cid:04d}] done ({wc} words, model={res.get('model_used')})", flush=True)
            return processed

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, self.settings.chunk_parallel)) as ex:
            future_map = {ex.submit(run_one, idx, chunk): idx for idx, chunk in enumerate(chunks)}
            for fut in concurrent.futures.as_completed(future_map):
                idx = future_map[fut]
                try:
                    results[idx] = fut.result()
                except Exception:
                    results[idx] = self._postprocess_chunk(
                        {
                            "people": [],
                            "topics": [],
                            "sentiment": "unknown",
                            "key_points": [],
                            "notable_quotes": [],
                            "categories": [],
                            "summary": "",
                        },
                        chunks[idx],
                    )

        return [r for r in results if r is not None]

    def _build_spans(self, chunk_analyses: List[Dict[str, Any]], ytid: str) -> Dict[str, List[Dict[str, Any]]]:
        """
        Build topic and person spans from chunk analyses.
        Groups consecutive chunks mentioning the same entity into spans.
        Returns {topic_spans: [...], person_spans: [...]}
        """
        topic_spans: List[Dict[str, Any]] = []
        person_spans: List[Dict[str, Any]] = []

        # Build topic spans by grouping consecutive chunks with same normalized topic
        topic_to_chunks: Dict[str, List[int]] = {}
        for chunk in chunk_analyses:
            chunk_id = chunk.get("chunk_id", 0)
            for topic in chunk.get("topics", []):
                if not topic:
                    continue
                norm = str(topic).lower().strip()
                if norm not in topic_to_chunks:
                    topic_to_chunks[norm] = []
                topic_to_chunks[norm].append(chunk_id)

        # Create topic span records
        for norm_topic, chunk_ids in topic_to_chunks.items():
            # Get context from first chunk mentioning this topic
            first_chunk = next((c for c in chunk_analyses if c.get("chunk_id") in chunk_ids), None)
            context = (first_chunk.get("summary", "") if first_chunk else "")[:200]
            sentiment = (first_chunk.get("sentiment") if first_chunk else None)

            topic_spans.append({
                "ytid": ytid,
                "topic": norm_topic.title(),  # Capitalize for display
                "normalized_topic": norm_topic,
                "chunk_ids": sorted(chunk_ids),
                "context": context,
                "sentiment": sentiment if sentiment and sentiment != "unknown" else None,
            })

        # Build person spans by grouping consecutive chunks with same normalized name
        person_to_chunks: Dict[str, List[int]] = {}
        for chunk in chunk_analyses:
            chunk_id = chunk.get("chunk_id", 0)
            for person in chunk.get("people", []):
                if not person:
                    continue
                norm = str(person).lower().strip()
                if norm not in person_to_chunks:
                    person_to_chunks[norm] = []
                person_to_chunks[norm].append(chunk_id)

        # Create person span records
        for norm_name, chunk_ids in person_to_chunks.items():
            # Get context from first chunk mentioning this person
            first_chunk = next((c for c in chunk_analyses if c.get("chunk_id") in chunk_ids), None)
            context = (first_chunk.get("summary", "") if first_chunk else "")[:200]
            sentiment = (first_chunk.get("sentiment") if first_chunk else None)

            person_spans.append({
                "ytid": ytid,
                "person_name": norm_name.title(),  # Capitalize for display
                "normalized_name": norm_name,
                "chunk_ids": sorted(chunk_ids),
                "context": context,
                "sentiment": sentiment if sentiment and sentiment != "unknown" else None,
                "polarity": None,  # Not extracted yet; placeholder for future
            })

        return {
            "topic_spans": topic_spans,
            "person_spans": person_spans,
        }

    def _build_target_spans_from_hot_targets(
        self, hot_target_results: Dict[str, Any], ytid: str
    ) -> List[Dict[str, Any]]:
        """
        Convert hot_target_results to target_spans format.
        hot_target_results structure: {target_name: [{chunk_id, spans[], model_used, endpoint}]}
        Returns list of target span records ready for DB insertion.
        """
        target_spans: List[Dict[str, Any]] = []

        if not hot_target_results:
            return target_spans

        for target_name, entries in hot_target_results.items():
            if not isinstance(entries, list):
                continue

            for entry in entries:
                chunk_id = entry.get("chunk_id")
                spans = entry.get("spans", [])
                if not isinstance(spans, list):
                    continue

                for span in spans:
                    if not isinstance(span, dict):
                        continue

                    # Extract fields from hot target span
                    label = span.get("label", "")
                    parties = span.get("parties", [])
                    if not isinstance(parties, list):
                        parties = []
                    polarity = span.get("polarity")
                    sentiment = span.get("sentiment")
                    evidence = span.get("evidence", "")
                    start_sec = span.get("start_sec")
                    end_sec = span.get("end_sec")

                    # Map to target_spans schema
                    target_spans.append({
                        "ytid": ytid,
                        "parties": parties if parties else None,  # NULL if empty
                        "description": label,  # label -> description
                        "start_sec": start_sec,
                        "end_sec": end_sec,
                        "chunk_ids": [chunk_id] if chunk_id is not None else [],
                        "context": evidence,  # evidence -> context
                        "sentiment": sentiment if sentiment else None,
                        "polarity": polarity if polarity else None,
                        "target_name": target_name,  # Store which target detected this
                        "model_used": entry.get("model_used"),
                    })

        return target_spans

    def _merge_target_spans(self, spans: List[Dict[str, Any]], gap_sec: float) -> List[Dict[str, Any]]:
        """Merge consecutive spans for the same target when gaps are small."""
        filtered = [s for s in spans if s.get("start_sec") is not None and s.get("end_sec") is not None]
        if not filtered:
            return spans
        filtered.sort(key=lambda x: (float(x.get("start_sec") or 0), float(x.get("end_sec") or 0)))
        merged: List[Dict[str, Any]] = []
        current = None
        for s in filtered:
            if current is None:
                current = dict(s)
                continue
            same_target = (s.get("target_name") or s.get("source_pass")) == (current.get("target_name") or current.get("source_pass"))
            gap = float(s.get("start_sec") or 0) - float(current.get("end_sec") or 0)
            if same_target and gap >= 0 and gap <= gap_sec:
                current["end_sec"] = s.get("end_sec")
                # merge chunk_ids
                cur_chunks = current.get("chunk_ids") or []
                new_chunks = s.get("chunk_ids") or []
                current["chunk_ids"] = sorted(set(cur_chunks + new_chunks))
                # merge parties if missing
                if not current.get("parties"):
                    current["parties"] = s.get("parties")
                # keep first description/context
            else:
                merged.append(current)
                current = dict(s)
        if current:
            merged.append(current)
        # include spans without timings unchanged
        no_time = [s for s in spans if s not in filtered]
        return merged + no_time

    def _parse_vtt_cues(self, path: Path) -> List[Dict[str, Any]]:
        cues: List[Dict[str, Any]] = []
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            blocks = text.strip().split("\n\n")
            for blk in blocks:
                lines = [l for l in blk.split("\n") if l.strip()]
                if len(lines) < 2:
                    continue
                if "-->" in lines[0]:
                    timing_line = lines[0]
                    cue_id = None
                    cue_text = " ".join(lines[1:])
                else:
                    cue_id = lines[0].strip()
                    if len(lines) < 2 or "-->" not in lines[1]:
                        continue
                    timing_line = lines[1]
                    cue_text = " ".join(lines[2:]) if len(lines) > 2 else ""
                if "-->" not in timing_line:
                    continue
                parts = timing_line.split("-->")
                def parse_ts(ts: str) -> float:
                    ts = ts.strip()
                    h, m, s = ts.split(":")
                    s = float(s)
                    return int(h) * 3600 + int(m) * 60 + s
                try:
                    start = parse_ts(parts[0])
                    end = parse_ts(parts[1])
                except Exception:
                    continue
                cues.append({"cue_id": cue_id, "start_sec": start, "end_sec": end, "text": cue_text})
        except Exception:
            return []
        return cues

    def _apply_people_recount(self, final_analysis: Dict[str, Any], transcript_text: str) -> None:
        """Approximate recount of people mentions directly from transcript words."""
        try:
            people_list = final_analysis.get("analysis", {}).get("people_mentioned", []) or []
            if not people_list:
                return
            t_ascii = unicodedata.normalize("NFKD", transcript_text).encode("ascii", "ignore").decode("ascii").lower()
            words = re.findall(r"[a-z]+", t_ascii)

            def norm_token(s: str) -> str:
                s = s.lower()
                s = re.sub(r"[^a-z]", "", s)
                s = s.replace("ck", "k")
                s = re.sub(r"(.)\1+", r"\1", s)
                s = re.sub(r"[aeiou]+", "", s)
                return s

            from collections import Counter

            norm_counts = Counter(norm_token(w) for w in words if w)

            def count_mentions(name: str) -> int:
                full_re = re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE)
                cnt = len(full_re.findall(t_ascii))
                tokens = re.findall(r"[A-Za-z']+", name)
                if tokens:
                    last = tokens[-1]
                    if len(last) >= 4:
                        cnt = max(cnt, norm_counts.get(norm_token(last), 0))
                return cnt

            recounted = []
            for entry in people_list:
                nm = str(entry.get("name", "")).strip()
                if not nm:
                    continue
                recounted.append({"name": nm, "mentions": count_mentions(nm)})
            if any(e["mentions"] > 0 for e in recounted):
                recounted.sort(key=lambda x: x["mentions"], reverse=True)
                final_analysis["analysis"]["people_mentioned"] = recounted
        except Exception:
            return

    def _rewrite_third_person(self, text: str, alias: str) -> str:
        if not isinstance(text, str) or not text:
            return text
        parts = re.split(r'(".*?")', text, flags=re.DOTALL)
        out: List[str] = []
        for part in parts:
            if len(part) >= 2 and part.startswith('"') and part.endswith('"'):
                out.append(part)
                continue
            s = part
            repl = [
                (r"\byou\s+are\b", f"{alias} is"),
                (r"\byou\s+were\b", f"{alias} was"),
                (r"\byou're\b", f"{alias} is"),
                (r"\byoure\b", f"{alias} is"),
                (r"\byour\b", f"{alias}'s"),
                (r"\byours\b", f"{alias}'s"),
                (r"\byourself\b", f"{alias}"),
                (r"\byourselves\b", f"{alias}"),
                (r"\byou\b", f"{alias}"),
            ]
            for pat, rep in repl:
                s = re.sub(pat, rep, s, flags=re.IGNORECASE)
            out.append(s)
        return "".join(out)

    def _summarize_hot_targets(self, hot_results: Dict[str, Any] | None) -> List[Dict[str, Any]]:
        """Summarize hot target results into a compact structure for reporting."""
        if not hot_results:
            return []
        summaries: List[Dict[str, Any]] = []
        for tgt_name, entries in hot_results.items():
            total_spans = 0
            examples: List[str] = []
            for entry in entries or []:
                spans = entry.get("spans") or []
                total_spans += len(spans)
                for span in spans:
                    if len(examples) >= 5:
                        break
                    label = span.get("label") or span.get("description") or ""
                    parties = span.get("parties") or []
                    ptxt = f" parties={', '.join(parties)}" if parties else ""
                    examples.append(f"{label}{ptxt}".strip())
                if len(examples) >= 5:
                    break
            summaries.append(
                {
                    "target": tgt_name,
                    "chunks": len(entries),
                    "spans": total_spans,
                    "examples": examples,
                }
            )
        return summaries

    def _annotate_chunk_timings(self, ytid: Optional[str], chunks: List[Dict[str, Any]], chunk_analyses: List[Dict[str, Any]]) -> None:
        """Derive chunk start/end seconds from word timestamps when available."""
        if not ytid or not self.db:
            return
        try:
            self.db.cursor.execute("SELECT start_sec, end_sec FROM words WHERE ytid=%s ORDER BY idx", (ytid,))
            word_times = [(float(r[0]) if r[0] is not None else None, float(r[1]) if r[1] is not None else None) for r in self.db.cursor.fetchall()]
        except Exception:
            return
        if not word_times:
            return

        # Map chunk_id -> chunk analysis dict for timing injection
        analysis_map = {c.get("chunk_id"): c for c in chunk_analyses}

        overlap = max(0, int(self.settings.overlap))
        pointer = 0
        for chunk in chunks:
            wc = int(chunk.get("word_count") or 0)
            cid = chunk.get("chunk_id")
            start_idx = max(0, pointer)
            end_idx = max(start_idx, start_idx + wc - 1)
            if start_idx >= len(word_times):
                start_sec = end_sec = None
            else:
                start_sec = word_times[start_idx][0]
                end_idx = min(end_idx, len(word_times) - 1)
                end_sec = word_times[end_idx][1] or word_times[end_idx][0]
            chunk["start_sec"] = start_sec
            chunk["end_sec"] = end_sec
            if cid in analysis_map:
                analysis_map[cid]["start_sec"] = start_sec
                analysis_map[cid]["end_sec"] = end_sec
            # advance pointer accounting for overlap
            pointer = max(0, end_idx + 1 - overlap)

        # If no word timings, fallback to VTT cues when available
        missing = any(c.get("start_sec") is None for c in chunk_analyses)
        vtt_path = chunks[0].get("vtt_path") if chunks else None
        if missing and vtt_path:
            try:
                vtt_path_obj = Path(vtt_path)
                if vtt_path_obj.exists():
                    cues = self._parse_vtt_cues(vtt_path_obj)
                    if cues:
                        by_chunk = {c.get("chunk_id"): c for c in chunk_analyses}
                        # naive assignment: map sequentially if counts align
                        if len(cues) >= len(chunks):
                            for idx, ch in enumerate(chunks):
                                if idx < len(cues):
                                    cue = cues[idx]
                                    start_sec = cue.get("start_sec")
                                    end_sec = cue.get("end_sec")
                                    ch["start_sec"] = start_sec
                                    ch["end_sec"] = end_sec
                                    if ch.get("chunk_id") in by_chunk:
                                        by_chunk[ch.get("chunk_id")]["start_sec"] = start_sec
                                        by_chunk[ch.get("chunk_id")]["end_sec"] = end_sec
            except Exception:
                pass

    def _persist_local(
        self,
        metadata: Dict[str, Any],
        final_analysis: Dict[str, Any],
        chunk_analyses: List[Dict[str, Any]],
        batch_id: int,
        analysis_type: str,
        request_text: str,
    ) -> Dict[str, Any]:
        """Write aggregate + per-chunk JSON (and optional markdown) locally."""
        out_root = self.settings.output_dir
        video_dir = out_root / metadata["video_id"]
        chunk_dir = video_dir / "chunks"
        video_dir.mkdir(parents=True, exist_ok=True)
        chunk_dir.mkdir(parents=True, exist_ok=True)

        # Persist the reconstructed transcript alongside the analysis outputs for review.
        vtt_src = Path(metadata.get("vtt_path") or "")
        if vtt_src.exists():
            try:
                shutil.copyfile(vtt_src, video_dir / "transcript.vtt")
            except Exception:
                pass

        payload = {
            "metadata": metadata,
            "analysis": final_analysis.get("analysis", {}),
            "stats": final_analysis.get("stats", {}),
            "summaries": final_analysis.get("summaries", {}),
            "chunk_summaries": final_analysis.get("chunk_summaries", []),
            "hot_targets": {
                "config": final_analysis.get("metadata", {}).get("hot_targets_config"),
                "triggered": final_analysis.get("metadata", {}).get("hot_targets_triggered"),
                "results": final_analysis.get("hot_target_results", {}),
            },
            "hot_target_summary": self._summarize_hot_targets(final_analysis.get("hot_target_results")),
            "drill_results": final_analysis.get("drill_results", {}),
            "drill_summary": final_analysis.get("drill_summary", []),
            "run": {
                "batch_id": batch_id,
                "analysis_type": analysis_type,
                "request_text": request_text or "none",
                "model": self.settings.model,
                "quality": self.settings.quality,
            },
            "chunks": chunk_analyses,
        }

        main_path = video_dir / "analysis.json"
        with open(main_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        chunk_paths: List[Path] = []
        for chunk in chunk_analyses:
            cpath = chunk_dir / f"chunk_{int(chunk.get('chunk_id', 0)):04d}.json"
            with open(cpath, "w", encoding="utf-8") as cf:
                json.dump(
                    {
                        "metadata": {
                            **metadata,
                            "chunk_id": chunk.get("chunk_id"),
                            "word_count": chunk.get("word_count"),
                            "start_sec": chunk.get("start_sec"),
                            "end_sec": chunk.get("end_sec"),
                            "batch_id": batch_id,
                            "analysis_type": analysis_type,
                            "request_text": request_text or "none",
                        },
                        "analysis": chunk,
                    },
                    cf,
                    indent=2,
                    ensure_ascii=False,
                )
            chunk_paths.append(cpath)

        md_path = None
        if self.settings.write_markdown and self._is_pass_enabled("markdown_report"):
            md_path = video_dir / "analysis.md"
            generate_markdown_report(
                final_analysis,
                md_path,
                hot_target_summary=self._summarize_hot_targets(final_analysis.get("hot_target_results")),
            )

        return {
            "analysis_json": main_path,
            "chunk_dir": chunk_dir,
            "chunk_files": chunk_paths,
            "analysis_md": md_path,
        }

    def run_file(
        self,
        input_path: Path,
        batch_id: int,
        analysis_type: str,
        force: bool = False,
    ) -> PipelineRunResult | None:
        """Run the full pipeline for a single transcript path."""
        metadata = self._extract_metadata(input_path)
        request_text = (self.settings.request_text or "").strip()

        if self._should_skip(metadata["video_id"], force):
            return None

        print(f"Analyzing: {metadata['title']}")
        print(f"Video ID: {metadata['video_id']}")

        parser_obj = VTTParser()
        text = parser_obj.parse(input_path)
        total_words = len(text.split())
        print(f"Total words: {total_words:,}")

        chunker = TranscriptChunker(chunk_size=self.settings.chunk_size, overlap=self.settings.overlap)
        chunks = chunker.chunk(text)
        print(f"Created {len(chunks)} chunks")
        # Log which passes are active for this run (if verbose/progress)
        self._log_pass_plan()

        # attach host info for downstream metadata
        if self.settings.transcription_machine:
            metadata["transcription_machine"] = self.settings.transcription_machine

        subchunks = []
        if self.settings.subchunks_enabled and self._is_pass_enabled("subchunks") and metadata.get("vtt_path"):
            try:
                subchunks = self._parse_vtt_cues(Path(metadata["vtt_path"]))
                for idx, sc in enumerate(subchunks, start=1):
                    if sc.get("cue_id") is None:
                        sc["cue_id"] = idx
            except Exception:
                subchunks = []
        elif self.settings.subchunks_enabled and not self._is_pass_enabled("subchunks") and self.settings.log_mode in ("progress", "verbose"):
            print("[passes] subchunks disabled; skipping VTT subchunk parsing", flush=True)

        options = self._build_options()
        analyzer_a = OllamaAnalyzer(
            model=self.settings.chunk_model_a or self.settings.model,
            base_url=self.settings.chunk_url_a or self.settings.ollama_url,
            options=options,
            custom_request=request_text,
            log_mode=self.settings.log_mode,
            category_suggestions=sorted(set(self.category_cache.values() or []))[:50] if self.category_cache else None,
            category_map=self.category_map if self.category_map else None,
        )
        analyzer_b = None
        if self.settings.chunk_url_b and self.settings.chunk_model_b:
            analyzer_b = OllamaAnalyzer(
                model=self.settings.chunk_model_b,
                base_url=self.settings.chunk_url_b,
                options=options,
                custom_request=request_text,
                log_mode=self.settings.log_mode,
                category_suggestions=sorted(set(self.category_cache.values() or []))[:50] if self.category_cache else None,
                category_map=self.category_map if self.category_map else None,
            )

        print(f"Analyzing chunks with {analyzer_a.model} (queued)...")

        def make_chunk_task(chunk_obj: Dict[str, Any], use_b: bool) -> Dict[str, Any]:
            return {
                "task_type": "chunk_analysis",
                "chunk": chunk_obj,
                "model": analyzer_b.model if (analyzer_b and use_b) else analyzer_a.model,
                "endpoint": analyzer_b.base_url if (analyzer_b and use_b) else analyzer_a.base_url,
                "options": analyzer_b.options if (analyzer_b and use_b) else analyzer_a.options,
                "custom_request": self.settings.request_text,
                "log_mode": self.settings.log_mode,
                "priority_label": "critical",
                "category_suggestions": analyzer_a.category_suggestions,
                "category_map": analyzer_a.category_map,
            }

        tasks = [make_chunk_task(c, (idx % 2 == 1)) for idx, c in enumerate(chunks)]

        def chunk_executor(payload: Dict[str, Any]) -> Dict[str, Any]:
            chunk_obj = payload["chunk"]
            model = payload["model"]
            endpoint = payload["endpoint"]
            opts = payload.get("options") or {}
            req = payload.get("custom_request") or ""
            exec_analyzer = OllamaAnalyzer(
                model=model,
                base_url=endpoint,
                options=opts,
                custom_request=req,
                log_mode=payload.get("log_mode") or "quiet",
                category_suggestions=analyzer_a.category_suggestions,
                category_map=analyzer_a.category_map,
            )
            res = exec_analyzer.analyze_chunk(chunk_obj["text"], chunk_obj["chunk_id"])
            res["model_used"] = model
            return res

        chunk_results = self.gpu_scheduler.run_tasks(tasks, chunk_executor)
        chunk_by_id = {c.get("chunk_id"): c for c in chunks}
        processed_map: Dict[int, Dict[str, Any]] = {}
        successful_analyses = 0
        for res in chunk_results:
            payload = res.get("payload") or {}
            cid = None
            try:
                cid = payload.get("chunk", {}).get("chunk_id")
            except Exception:
                cid = None
            chunk_obj = chunk_by_id.get(cid)
            if not chunk_obj:
                continue
            if res.get("success") and res.get("result"):
                processed = self._postprocess_chunk(res["result"], chunk_obj)
                successful_analyses += 1
            else:
                processed = self._postprocess_chunk(
                    {
                        "people": [],
                        "topics": [],
                        "sentiment": "unknown",
                        "key_points": [],
                        "notable_quotes": [],
                        "categories": [],
                        "summary": "",
                    },
                    chunk_obj,
                )
            processed_map[cid] = processed

        # Fail fast if no chunk analyses succeeded
        if successful_analyses == 0 and len(chunks) > 0:
            print(f"Error: No chunk analyses succeeded (0/{len(chunks)} chunks). LLM service may be unavailable.", file=sys.stderr)
            return None

        # Preserve original chunk order
        chunk_analyses = []
        for c in chunks:
            cid = c.get("chunk_id")
            if cid in processed_map:
                chunk_analyses.append(processed_map[cid])
            else:
                # Fallback if missing
                chunk_analyses.append(
                    self._postprocess_chunk(
                        {
                            "people": [],
                            "topics": [],
                            "sentiment": "unknown",
                            "key_points": [],
                            "notable_quotes": [],
                            "categories": [],
                            "summary": "",
                        },
                        c,
                    )
                )

        # Derive chunk timings from word-level timestamps when available
        self._annotate_chunk_timings(metadata.get("video_id"), chunks, chunk_analyses)

        aggregator = AnalysisAggregator()
        final_analysis = aggregator.aggregate(chunk_analyses, metadata)

        self._apply_people_recount(final_analysis, text)

        if not self.settings.skip_summaries:
            print("Generating summaries (queued)...")

            def make_summary_task(kind: str) -> Dict[str, Any]:
                return {
                    "task_type": "summary",
                    "kind": kind,
                    "model": analyzer_a.model if kind == "main" else (self.settings.speaker_model or analyzer_a.model),
                    "endpoint": analyzer_a.base_url if kind == "main" else (self.settings.speaker_url or analyzer_a.base_url),
                    "options": options,
                    "request_text": request_text,
                    "log_mode": self.settings.log_mode,
                    "priority_label": "low",
                }

            summary_tasks = [make_summary_task("main"), make_summary_task("speaker")]

            def summary_executor(payload: Dict[str, Any]) -> Dict[str, Any]:
                kind = payload["kind"]
                model = payload["model"]
                endpoint = payload["endpoint"]
                opts = payload.get("options") or {}
                req = payload.get("request_text") or ""
                exec_analyzer = OllamaAnalyzer(
                    model=model,
                    base_url=endpoint,
                    options=opts,
                    custom_request=req,
                    log_mode=payload.get("log_mode") or "quiet",
                    category_suggestions=payload.get("category_suggestions") or analyzer_a.category_suggestions,
                    category_map=payload.get("category_map") or analyzer_a.category_map,
                )
                if kind == "speaker":
                    return {"speaker": exec_analyzer.summarize_speaker(final_analysis)}
                return {"main": exec_analyzer.summarize(final_analysis)}

            summary_results = self.gpu_scheduler.run_tasks(summary_tasks, summary_executor)
            for res in summary_results:
                if not res["success"] or not res["result"]:
                    continue
                payload = res["result"]
                if "main" in payload:
                    final_analysis["summaries"] = payload["main"]
                if "speaker" in payload:
                    sp = payload["speaker"]
                    final_analysis.setdefault("summaries", {})
                    final_analysis["summaries"]["speaker_overview"] = sp.get("speaker_overview", "")
                    final_analysis["summaries"]["personal_themes"] = sp.get("personal_themes", [])
                    if sp.get("noteworthy_statements"):
                        final_analysis["summaries"].setdefault("noteworthy_statements", [])
                        final_analysis["summaries"]["noteworthy_statements"].extend(sp["noteworthy_statements"])
                    if sp.get("personal_conflicts"):
                        final_analysis["summaries"].setdefault("personal_conflicts", [])
                        final_analysis["summaries"]["personal_conflicts"].extend(sp["personal_conflicts"])
                    for key in ["tldr_one_sentence", "summary_paragraph", "political_overview", "controversies", "speaker_overview"]:
                        if key in final_analysis["summaries"]:
                            final_analysis["summaries"][key] = self._rewrite_third_person(
                                final_analysis["summaries"][key], self.settings.speaker_alias
                            )
            if self.settings.log_mode in ("progress", "verbose"):
                print("[summary] aggregate summaries collected", flush=True)

        # Attach run context
        final_analysis.setdefault("metadata", {}).update(
            {
                "batch_id": batch_id,
                "analysis_type": analysis_type,
                "request_text": request_text or "none",
                "model": self.settings.model,
                "diarized": bool(self.settings.diarized),
                "transcription_machine": self.settings.transcription_machine,
            }
        )

        # Update category cache with any newly seen categories
        seen_categories: Set[str] = set()
        for chunk in chunk_analyses:
            for cat in chunk.get("categories") or []:
                if isinstance(cat, str) and cat.strip():
                    seen_categories.add(cat.strip())
        for cat in final_analysis.get("analysis", {}).get("categories") or []:
            if isinstance(cat, str) and cat.strip():
                seen_categories.add(cat.strip())
        self._maybe_update_category_cache(seen_categories)

        # Flag hot targets for downstream recursive/detail passes
        if self.settings.hot_targets:
            triggered = []
            names: List[str] = []
            hot_results: Dict[str, Any] = {}

            if self._is_pass_enabled("hot_targets"):
                triggered = self._detect_hot_targets(chunk_analyses, chunks)
                if triggered:
                    names = [
                        t.get("name") or t.get("category")
                        for t in triggered
                        if t.get("name") or t.get("category")
                    ]
                    print(f"Hot targets flagged: {', '.join(names)} -> running detail passes...")
                    print(f"Hot target tasks queued: {len(triggered)}", flush=True)
                    try:
                        hot_results = self._run_hot_targets(triggered, chunks)
                    except Exception as exc:
                        print(f"Warning: hot target passes failed: {exc}", flush=True)
                        hot_results = {}
                else:
                    names = []
                    hot_results = {}
            else:
                # Config explicitly disabled hot-target passes
                if self.settings.log_mode in ("progress", "verbose"):
                    print("[passes] hot_targets disabled; skipping hot target detection", flush=True)

            final_analysis.setdefault("metadata", {})["hot_targets_config"] = (
                str(self.settings.hot_targets_path) if self.settings.hot_targets_path else None
            )
            final_analysis["metadata"]["hot_targets_triggered"] = names
            final_analysis["metadata"]["hot_target_specs"] = triggered
            final_analysis["metadata"]["category_cache_path"] = self.settings.category_cache_path
            final_analysis["metadata"]["alias_map_path"] = self.settings.alias_map_path

            if hot_results:
                final_analysis["hot_target_results"] = hot_results
                # Log a brief summary to the console
                for tgt_name, entries in hot_results.items():
                    total_spans = sum(len(e.get("spans") or []) for e in entries)
                    print(f"[hot-target] {tgt_name}: {total_spans} span(s) across {len(entries)} chunk(s)")
                    if self.settings.log_mode in ("progress", "verbose"):
                        for entry in entries[:5]:
                            spans = entry.get("spans") or []
                            if not spans:
                                continue
                            lbl = spans[0].get("label") or spans[0].get("description") or ""
                            cid = entry.get("chunk_id")
                            print(f"  [hot-target] {tgt_name} chunk {cid}: {lbl[:120]}", flush=True)

# Drills: chained, configurable passes
        drill_results = {}
        drill_summary = []
        drill_emitted_spans: Dict[str, List[Dict[str, Any]]] = {}
        if self.settings.drills and self._is_pass_enabled("drills"):
            try:
                def make_drill_task(drill: Dict[str, Any]) -> Dict[str, Any]:
                    scope = drill.get("scope") or "chunks"
                    return {
                        "task_type": "drill",
                        "drill": drill,
                        "chunks": [{"chunk_id": c.get("chunk_id"), "text": c.get("text") or ""} for c in (subchunks if scope == "subchunks" else chunks)],
                        "priority_label": drill.get("priority") or drill.get("priority_label") or "normal",
                        "scope": scope,
                    }

                tasks = [make_drill_task(d) for d in self.settings.drills]
                if self.settings.log_mode in ("progress", "verbose"):
                    print(f"Drill tasks queued: {len(tasks)}", flush=True)

                def drill_executor(payload: Dict[str, Any]) -> Dict[str, Any]:
                    drill = payload["drill"]
                    scope = payload.get("scope") or drill.get("scope") or "chunks"
                    output_shapes = getattr(self.config, "output_shapes", {}) or {}
                    executor = DrillExecutor(
                        drills=[drill],
                        base_model=self.settings.chunk_model_a or self.settings.model,
                        base_url=self.settings.chunk_url_a or self.settings.ollama_url,
                        options=self._build_options(),
                        log_mode=self.settings.log_mode,
                        delay_between=(self.settings.drills_scheduler_config or {}).get("delay_between_targets", 0) if isinstance(self.settings.drills_scheduler_config, dict) else 0,
                        output_shapes=output_shapes,
                    )
                    if scope == "subchunks" and subchunks:
                        sub_texts = [
                            {"chunk_id": c.get("cue_id"), "text": c.get("text") or "", "start_sec": c.get("start_sec"), "end_sec": c.get("end_sec")}
                            for c in subchunks
                        ]
                        text_map = {s.get("cue_id"): s.get("text") or "" for s in subchunks}
                        res, summ, emitted = executor.run(sub_texts, chunk_texts=text_map, scope="subchunks")
                    else:
                        chunk_texts = {c.get("chunk_id"): c.get("text") or "" for c in payload["chunks"]}
                        res, summ, emitted = executor.run(payload["chunks"], chunk_texts=chunk_texts, scope="chunks")
                    return {"results": res, "summary": summ, "emitted": emitted}

                drill_task_results = self.gpu_scheduler.run_tasks(tasks, drill_executor)
                for res in drill_task_results:
                    if not res["success"] or not res["result"]:
                        continue
                    result_payload = res["result"]
                    drill_results.update(result_payload.get("results") or {})
                    drill_summary.extend(result_payload.get("summary") or [])
                    emitted = result_payload.get("emitted") or {}
                    for dname, spans_list in emitted.items():
                        drill_emitted_spans.setdefault(dname, []).extend(spans_list)
            except Exception as exc:
                print(f"Warning: drill execution failed: {exc}")
                if drill_results:
                    final_analysis["drill_results"] = drill_results
                    final_analysis["drill_summary"] = drill_summary
                    print(f"Drills executed: {', '.join(drill_results.keys())}")
                    if self.settings.log_mode in ("progress", "verbose"):
                        for ds in drill_summary:
                            print(
                                f"[drill] {ds.get('target')} spans={ds.get('spans',0)} chunks={ds.get('chunks',0)}",
                                flush=True,
                            )
                # Persist drill spans via target_spans with source_pass=drill_name
                if drill_emitted_spans and self.db:
                    all_spans = []
                    for dname, spans_list in drill_emitted_spans.items():
                        for span in spans_list:
                            parties = span.get("parties")
                            if parties and not isinstance(parties, list):
                                parties = [str(parties)]
                            if parties is not None and isinstance(parties, list) and len(parties) == 0:
                                parties = None
                            all_spans.append(
                                {
                                    "ytid": metadata["video_id"],
                                    "parties": parties,
                                    "description": span.get("label"),
                                    "start_sec": span.get("start_sec"),
                                    "end_sec": span.get("end_sec"),
                                    "chunk_ids": [span.get("chunk_ids")] if span.get("chunk_ids") is not None else [],
                                    "context": span.get("context"),
                                    "sentiment": None,
                                    "polarity": None,
                                    "target_name": dname,
                                    "model_used": None,
                                }
                            )
                    if all_spans:
                        all_spans = self._merge_target_spans(all_spans, gap_sec=self.settings.merge_gap_sec)
                        try:
                            print(f"Storing {len(all_spans)} drill span(s)...")
                            self.db.store_target_spans(
                                metadata["video_id"],
                                all_spans,
                                source_pass="drill",
                                pass_tier="drill",
                                analysis_type=analysis_type,
                                batch_id=batch_id,
                                diarized=self.settings.diarized,
                                transcription_machine=self.settings.transcription_machine,
                            )
                        except Exception as exc:
                            print(f"Warning: failed to persist drill spans: {exc}")

        if self.db and self._is_pass_enabled("db_store"):
            print("Storing results in database...")
            # Build spans from chunk analyses
            print("Building spans...")
            spans = self._build_spans(chunk_analyses, metadata["video_id"])

            # Build target spans from hot targets if present (persisted to target_spans table)
            target_spans = []
            hot_results = final_analysis.get("hot_target_results")
            if hot_results:
                print("Building targeted spans from hot targets...")
                target_spans = self._build_target_spans_from_hot_targets(
                    hot_results, metadata["video_id"]
                )
                if target_spans:
                    target_spans = self._merge_target_spans(target_spans, gap_sec=self.settings.merge_gap_sec)
                    print(f"  Built {len(target_spans)} targeted span(s)")

            self.db.store_full_analysis_with_chunks(
                final_analysis,
                chunk_analyses,
                spans=spans,
                model=self.settings.model,
                batch_id=batch_id,
                analysis_type=analysis_type,
                request_text=request_text or "none",
                diarized=self.settings.diarized,
                transcription_machine=self.settings.transcription_machine,
            )

            # Store hot-target spans separately (uses conflict_spans table)
            # Store hot-target spans separately (uses target_spans table)
            if target_spans:
                print("Storing targeted spans...")
                self.db.store_target_spans(
                    metadata["video_id"],
                    target_spans,
                    source_pass="hot_target",
                    pass_tier="detail",
                    analysis_type=analysis_type,
                    batch_id=batch_id,
                    diarized=self.settings.diarized,
                    transcription_machine=self.settings.transcription_machine,
                )

            print("✓ Database storage complete")

        local_paths: Dict[str, Any] = {}
        if self.settings.write_local and self._is_pass_enabled("local_json"):
            local_paths = self._persist_local(
                metadata,
                final_analysis,
                chunk_analyses,
                batch_id=batch_id,
                analysis_type=analysis_type,
                request_text=request_text,
            )
            print(f"✓ Local JSON written to {local_paths.get('analysis_json')}")

        return PipelineRunResult(
            metadata=metadata,
            final_analysis=final_analysis,
            chunk_analyses=chunk_analyses,
            local_paths=local_paths,
        )
