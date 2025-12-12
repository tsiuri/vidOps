"""Drill executor: configurable chained passes that can run on chunks or prior spans."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .llm.hot_targets import HotTargetRunner


def load_drills(path: str | Path | None) -> List[Dict[str, Any]]:
    """Load drill specs from JSON; return empty list on failure/missing."""
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        drills = data.get("drills") if isinstance(data, dict) else data
        if isinstance(drills, list):
            return [d for d in drills if isinstance(d, dict)]
    except Exception:
        return []
    return []


class DrillExecutor:
    """Execute drills in dependency order; supports chunk- and span-scoped drills."""

    def __init__(
        self,
        drills: List[Dict[str, Any]],
        base_model: str,
        base_url: str,
        options: Optional[Dict[str, Any]] = None,
        log_mode: str = "quiet",
        delay_between: float = 0.0,
        output_shapes: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.drills = drills or []
        self.base_model = base_model
        self.base_url = base_url
        self.options = options or {}
        self.log_mode = log_mode
        self.delay_between = delay_between
        self.output_shapes = output_shapes or {}

    def _toposort(self) -> List[Dict[str, Any]]:
        """Return drills sorted by dependencies."""
        name_map = {d.get("name"): d for d in self.drills if d.get("name")}
        visited: set[str] = set()
        order: List[Dict[str, Any]] = []

        def visit(name: str) -> None:
            if name in visited:
                return
            visited.add(name)
            spec = name_map.get(name)
            if not spec:
                return
            for dep in spec.get("depends_on", []) or []:
                visit(dep)
            order.append(spec)

        for n in name_map:
            visit(n)
        return order

    def _runner_for(self, spec: Dict[str, Any]) -> HotTargetRunner:
        model = spec.get("detail_pass", {}).get("model") or self.base_model
        endpoint = spec.get("detail_pass", {}).get("endpoint") or self.base_url
        opts = spec.get("detail_pass", {}).get("options") or self.options
        return HotTargetRunner(model=model, base_url=endpoint, options=opts, log_mode=self.log_mode, output_shapes=self.output_shapes)

    def _should_fire(self, spec: Dict[str, Any], chunk_analyses: Sequence[Dict[str, Any]]) -> bool:
        if spec.get("always"):
            return True
        min_hits = int(spec.get("min_hits") or 0)
        hits = 0
        match_tokens = [str(t).strip().lower() for t in (spec.get("match") or []) if t]
        keywords = [str(k).strip().lower() for k in (spec.get("keywords") or []) if k]
        for chunk in chunk_analyses:
            cats = [str(c).strip().lower() for c in (chunk.get("categories") or []) if c]
            topics = [str(t).strip().lower() for t in (chunk.get("topics") or []) if t]
            text = (chunk.get("text") or "").lower()
            if match_tokens and any(tok in cats or tok in topics for tok in match_tokens):
                hits += 1
            if keywords and any(k in text for k in keywords):
                hits += 1
            if hits >= min_hits > 0:
                break
        return hits >= max(1, min_hits) if (match_tokens or keywords or min_hits) else False

    def run(
        self,
        chunks: List[Dict[str, Any]],
        chunk_texts: Optional[Dict[Any, str]] = None,
        prior_spans: Optional[Dict[str, Any]] = None,
        scope: str = "chunks",
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
        """
        Execute drills and return (drill_results, drill_summary, emitted_spans_by_drill).
        drill_results mirrors hot_target_results: {name: [{chunk_id, spans/outputs, model_used, endpoint}]}
        emitted_spans_by_drill: {name: [span dicts]} for downstream persistence.
        scope: "chunks" (default), "spans", or "subchunks".
        """
        ordered = self._toposort()
        results: Dict[str, Any] = {}
        summary: List[Dict[str, Any]] = []
        chunk_texts = chunk_texts or {c.get("chunk_id"): c.get("text") or "" for c in chunks}
        chunk_meta = {c.get("chunk_id"): c for c in chunks}

        # map of drill -> emitted spans (for span-scoped follow-ups)
        emitted_spans: Dict[str, List[Dict[str, Any]]] = {}

        for spec in ordered:
            name = spec.get("name") or "drill"
            spec_scope = spec.get("scope") or scope or "chunks"
            deps = spec.get("depends_on") or []
            # If scope is spans, ensure deps emitted spans
            if spec_scope == "spans":
                dep_hits = []
                for dep in deps:
                    dep_hits.extend(emitted_spans.get(dep, []))
                if not dep_hits:
                    continue  # nothing to run on

            # Trigger check for chunk-scoped drills
            if spec_scope not in ("spans", "subchunks") and not self._should_fire(spec, chunks):
                continue

            runner = self._runner_for(spec)
            entries: List[Dict[str, Any]] = []
            if spec_scope == "spans":
                # Run once over aggregated span context
                dep_hits = []
                for dep in deps:
                    dep_hits.extend(emitted_spans.get(dep, []))
                context_text = "\n\n".join(
                    f"[span {i+1}] {s.get('context') or ''}" for i, s in enumerate(dep_hits)
                )
                synthetic_chunk = {"chunk_id": -1, "text": context_text}
                res = runner.run_targets([{"name": name, "prompt": spec.get("prompt"), "category": name}], [synthetic_chunk])
                entries = res.get(name, [])
            else:
                # Run per chunk/subchunk like hot targets
                drill_target = {
                    "name": name,
                    "prompt": spec.get("prompt"),
                    "category": spec.get("category") or name,
                    "output_shape": spec.get("output_shape") or "span",
                }
                res = runner.run_targets([drill_target], chunks)
                entries = res.get(name, [])

            # Filter spans: drop negatives in presence mode, map timings from chunk metadata
            cleaned_entries: List[Dict[str, Any]] = []
            for entry in entries:
                spans = entry.get("spans") or []
                filtered: List[Dict[str, Any]] = []
                for s in spans:
                    if not isinstance(s, dict):
                        continue
                    if "present" in s and not s.get("present"):
                        continue
                    start_sec = s.get("start_sec")
                    end_sec = s.get("end_sec")
                    meta = chunk_meta.get(entry.get("chunk_id")) or {}
                    if start_sec is None and meta.get("start_sec") is not None:
                        start_sec = meta.get("start_sec")
                    if end_sec is None and meta.get("end_sec") is not None:
                        end_sec = meta.get("end_sec")
                    filtered.append({**s, "start_sec": start_sec, "end_sec": end_sec})
                entry["spans"] = filtered
                if filtered:
                    cleaned_entries.append(entry)

            if cleaned_entries:
                results[name] = cleaned_entries
                total_spans = sum(len(e.get("spans") or []) for e in cleaned_entries)
                examples = []
                for e in cleaned_entries:
                    for s in e.get("spans") or []:
                        if len(examples) >= 5:
                            break
                        lbl = s.get("label") or s.get("description") or ""
                        examples.append(lbl)
                    if len(examples) >= 5:
                        break
                summary.append({"target": name, "chunks": len(cleaned_entries), "spans": total_spans, "examples": examples})
                # record emitted spans for downstream span-scoped drills
                emitted = []
                for e in cleaned_entries:
                    cid = e.get("chunk_id")
                    chunk_meta_entry = chunk_meta.get(cid) or {}
                    for s in e.get("spans") or []:
                        emitted.append(
                            {
                                "label": s.get("label") or s.get("description"),
                                "context": s.get("evidence") or e.get("context") or "",
                                "start_sec": s.get("start_sec") if s.get("start_sec") is not None else chunk_meta_entry.get("start_sec"),
                                "end_sec": s.get("end_sec") if s.get("end_sec") is not None else chunk_meta_entry.get("end_sec"),
                                "chunk_ids": [cid] if cid is not None else [],
                                "parties": s.get("parties"),
                            }
                        )
                emitted_spans[name] = emitted

            if self.delay_between > 0:
                import time

                time.sleep(self.delay_between)

        return results, summary, emitted_spans
