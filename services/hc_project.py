# vidops/services/hc_project.py

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import hashlib
import json

from dal import (
    HCProjectRepository,
    HCDagRepository,
    HCHitRepository,
    HCClipRepository,
    HCSuggestionRepository,
    AnalysisTaskResultsRepository,
    AnalysisResultsReadRepository,
    WordRepository,
    QuickClipRepository,
)


logger = logging.getLogger(__name__)


def _merge_spans(spans: List[Tuple[float, float, Optional[str]]], merge_within_sec: float) -> List[Tuple[float, float, Optional[str]]]:
    """Merge overlapping/nearby spans.

    Speaker merge rule: if speakers disagree, keep the earlier speaker label.
    """
    if not spans:
        return []
    spans_sorted = sorted(spans, key=lambda x: (x[0], x[1]))
    out: List[Tuple[float, float, Optional[str]]] = []
    cur_s, cur_e, cur_sp = spans_sorted[0]
    for s, e, sp in spans_sorted[1:]:
        if s <= cur_e + merge_within_sec:
            cur_e = max(cur_e, e)
            # keep cur_sp
        else:
            out.append((float(cur_s), float(cur_e), cur_sp))
            cur_s, cur_e, cur_sp = s, e, sp
    out.append((float(cur_s), float(cur_e), cur_sp))
    return out


@dataclass
class HCDagContext:
    project_id: str
    run_id: str
    created_by: Optional[str] = None
    run_spec: Optional[Dict[str, Any]] = None
    run_mode: str = "normal"  # normal | rebuild
    allow_invalid_upstream: bool = False


class HitsClipsProjectService:
    """Executes a project run using the hc_* tables.

    This intentionally implements a *minimal executable DAG* consistent with the spec:
    - keyword_hit_generator
    - clip_projection

    Analysis-driven nodes and richer hybrid logic are left for follow-on phases.
    """

    def __init__(self):
        self.projects = HCProjectRepository()
        self.dag = HCDagRepository()
        self.hits = HCHitRepository()
        self.clips = HCClipRepository()
        self.suggestions = HCSuggestionRepository()
        self.analysis_tasks = AnalysisTaskResultsRepository()
        self.analysis_results = AnalysisResultsReadRepository()
        self.words = WordRepository()

    def process_job(self, job) -> None:
        """Entry point invoked by GenericWorker.

        Expects job.config to include:
        - project_id (required)
        - run_id (optional; if absent, created)
        - created_by (optional)
        """
        cfg = job.config or {}
        project_id = cfg.get("project_id")
        if not project_id:
            raise ValueError("hc_project_run job requires config.project_id")
        run_id = cfg.get("run_id")
        created_by = cfg.get("created_by")
        if not run_id:
            run_id = self.projects.create_run(project_id, created_by=created_by, run_spec={"job_id": job.job_id})
        run_row = self.projects.get_run(run_id) or {}
        run_spec = run_row.get("run_spec") or {}
        ctx = HCDagContext(
            project_id=project_id,
            run_id=run_id,
            created_by=created_by,
            run_spec=run_spec,
            run_mode=str(run_spec.get("mode") or "normal"),
            allow_invalid_upstream=bool(run_spec.get("allow_invalid_upstream", False)),
        )

        self.projects.update_run_status(run_id, "running")
        try:
            self.execute_run(ctx)
            self.projects.update_run_status(run_id, "completed")
        except Exception as exc:
            logger.error("HC project run failed: %s", exc, exc_info=True)
            self.projects.update_run_status(run_id, "failed", error_message=str(exc))
            raise

    def execute_run(self, ctx: HCDagContext) -> None:
        """Execute a project DAG run.

        Phase 4: distinguishes normal vs rebuild runs via ctx.run_mode.
        Rebuild runs may ignore manual invalidation blocks only when
        ctx.allow_invalid_upstream is true.
        """
        nodes = self.dag.list_nodes(ctx.project_id)
        edges = self.dag.list_edges(ctx.project_id)

        node_by_id = {str(n["node_id"]): n for n in nodes}
        indeg = {str(n["node_id"]): 0 for n in nodes}
        adj = {str(n["node_id"]): [] for n in nodes}
        for e in edges:
            frm = str(e["from_node_id"])
            to = str(e["to_node_id"])
            if frm in adj and to in indeg:
                adj[frm].append(to)
                indeg[to] += 1

        queue = [nid for nid, d in indeg.items() if d == 0]
        ordered = []
        while queue:
            nid = queue.pop(0)
            ordered.append(nid)
            for nxt in adj.get(nid, []):
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    queue.append(nxt)

        if len(ordered) != len(nodes):
            ordered = [str(n["node_id"]) for n in nodes]

        upstreams = {nid: [] for nid in ordered}
        for e in edges:
            frm = str(e["from_node_id"])
            to = str(e["to_node_id"])
            if to in upstreams:
                upstreams[to].append(frm)

        run_status = {}
        run_output_fp = {}

        produced_hits = 0
        produced_clips = 0
        produced_suggestions = 0

        def stable_json(obj):
            try:
                return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            except Exception:
                return str(obj)

        for node_id in ordered:
            node = node_by_id.get(str(node_id))
            if not node:
                continue

            cfg = node.get("config_json") or {}
            config_fp = self._hash_fingerprint(
                "hc_node_cfg",
                node.get("node_type"),
                node.get("node_key"),
                stable_json(cfg),
                node.get("invalidation_mode"),
                node.get("zero_results_mode"),
                bool(node.get("enabled", True)),
            )
            upstream_fp = self._hash_fingerprint(
                "hc_upstream",
                *[(u, run_output_fp.get(u)) for u in sorted(upstreams.get(str(node_id)) or [])],
            )
            input_fp = self._hash_fingerprint("hc_input", config_fp, upstream_fp)

            # Upstream blockers in this run
            upstream_blockers = [u for u in (upstreams.get(str(node_id)) or []) if run_status.get(u) in ("failed", "blocked")]
            if upstream_blockers:
                exec_id = self.dag.create_node_execution(
                    ctx.run_id,
                    str(node_id),
                    input_summary={"node_key": node.get("node_key"), "blocked_by": upstream_blockers, "run_mode": ctx.run_mode},
                    config_fingerprint=config_fp,
                    upstream_fingerprint=upstream_fp,
                    input_fingerprint=input_fp,
                )
                self.dag.update_node_execution(
                    exec_id,
                    "blocked",
                    output_summary={"reason": "upstream failed/blocked", "blocked_by": upstream_blockers},
                    error_message="blocked by upstream",
                    output_fingerprint=None,
                )
                run_status[str(node_id)] = "blocked"
                run_output_fp[str(node_id)] = None
                continue

            prev_exec = self.dag.get_latest_node_execution(str(node_id))
            # Phase 4B: rebuild runs are user-initiated and must not perform automatic invalidation.
            if ctx.run_mode == "normal" and prev_exec and prev_exec.get("input_fingerprint") and str(prev_exec.get("input_fingerprint")) != str(input_fp):
                mode = node.get("invalidation_mode", "soft") or "soft"
                reason = "dependency or config fingerprint changed"
                source = {
                    "node_id": str(node_id),
                    "node_key": str(node.get("node_key")),
                    "prev_exec_id": str(prev_exec.get("exec_id")),
                    "prev_input_fingerprint": str(prev_exec.get("input_fingerprint")),
                    "new_input_fingerprint": str(input_fp),
                }
                try:
                    if node.get("node_type") in ("suggestion_emitter", "analysis_suggestion_emitter"):
                        self.suggestions.invalidate_by_node_id(
                            project_id=ctx.project_id,
                            node_id=str(node_id),
                            mode=mode,
                            actor="system",
                            reason=reason,
                            source=source,
                        )
                    elif node.get("node_type") in ("keyword_hit_generator", "analysis_hit_generator", "promoted_suggestions_to_hits"):
                        self.hits.invalidate_by_node_key(
                            project_id=ctx.project_id,
                            node_key=str(node.get("node_key")),
                            mode=mode,
                            actor="system",
                            reason=reason,
                            source=source,
                        )
                    elif node.get("node_type") == "clip_projection":
                        self.clips.invalidate_by_node_key(
                            project_id=ctx.project_id,
                            node_key=str(node.get("node_key")),
                            mode=mode,
                            actor="system",
                            reason=reason,
                            source=source,
                        )
                except Exception:
                    logger.warning("Invalidation step failed for node %s", node.get("node_key"), exc_info=True)

            exec_id = self.dag.create_node_execution(
                ctx.run_id,
                str(node_id),
                input_summary={"node_key": node.get("node_key"), "node_type": node.get("node_type"), "run_mode": ctx.run_mode},
                config_fingerprint=config_fp,
                upstream_fingerprint=upstream_fp,
                input_fingerprint=input_fp,
            )
            self.dag.update_node_execution(exec_id, "running")

            try:
                if not node.get("enabled", True):
                    self.dag.update_node_execution(exec_id, "skipped", output_summary={"reason": "disabled"}, output_fingerprint=input_fp)
                    run_status[str(node_id)] = "skipped"
                    run_output_fp[str(node_id)] = input_fp
                    continue

                # Manual invalidation blocks can be bypassed only on explicit rebuild runs.
                if node.get("invalidation_mode") == "manual" and not (ctx.run_mode == "rebuild" and ctx.allow_invalid_upstream):
                    invalid_count = 0
                    nt = node.get("node_type")
                    if nt in ("keyword_hit_generator", "analysis_hit_generator", "promoted_suggestions_to_hits"):
                        invalid_count = self.hits.count_invalid_for_node(ctx.project_id, str(node.get("node_key")))
                    elif nt in ("suggestion_emitter", "analysis_suggestion_emitter"):
                        invalid_count = self.suggestions.count_invalid_for_node(ctx.project_id, str(node.get('node_key')))
                    elif nt == "clip_projection":
                        invalid_count = self.clips.count_invalid_for_node(ctx.project_id, str(node.get("node_key")))
                    if invalid_count > 0:
                        self.dag.update_node_execution(
                            exec_id,
                            "blocked",
                            output_summary={"reason": "manual invalidation mode: invalid artifacts exist", "invalid_count": invalid_count},
                            error_message="blocked by manual invalidation",
                            output_fingerprint=None,
                        )
                        run_status[str(node_id)] = "blocked"
                        run_output_fp[str(node_id)] = None
                        continue

                # Execute node
                node_type = node.get("node_type")
                produced = 0
                if node_type == "keyword_hit_generator":
                    produced = self._exec_keyword_hit_generator(ctx, node)
                    produced_hits += int(produced)
                elif node_type == "analysis_suggestion_emitter":
                    produced = self._exec_analysis_suggestion_emitter(ctx, node)
                    produced_suggestions += int(produced)
                elif node_type == "analysis_hit_generator":
                    produced = self._exec_analysis_hit_generator(ctx, node)
                    produced_hits += int(produced)
                elif node_type == "promoted_suggestions_to_hits":
                    produced = self._exec_promoted_suggestions_to_hits(ctx, node)
                    produced_hits += int(produced)
                elif node_type == "clip_projection":
                    produced = self._exec_clip_projection(ctx, node)
                    produced_clips += int(produced)
                else:
                    # unknown node types are treated as skipped
                    self.dag.update_node_execution(exec_id, "skipped", output_summary={"reason": f"unknown node_type: {node_type}"}, output_fingerprint=input_fp)
                    run_status[str(node_id)] = "skipped"
                    run_output_fp[str(node_id)] = input_fp
                    continue

                out_summary = {"produced": int(produced), "node_key": node.get("node_key"), "node_type": node_type, "run_mode": ctx.run_mode}
                output_fp = self._hash_fingerprint("hc_output", input_fp, stable_json(out_summary))
                self.dag.update_node_execution(
                    exec_id,
                    "completed",
                    output_summary=out_summary,
                    config_fingerprint=config_fp,
                    upstream_fingerprint=upstream_fp,
                    input_fingerprint=input_fp,
                    output_fingerprint=output_fp,
                )
                run_status[str(node_id)] = "completed"
                run_output_fp[str(node_id)] = output_fp
            except Exception as exc:
                self.dag.update_node_execution(exec_id, "failed", error_message=str(exc), output_fingerprint=None)
                run_status[str(node_id)] = "failed"
                run_output_fp[str(node_id)] = None
                logger.error("Node %s failed: %s", node.get("node_key"), exc, exc_info=True)

        # Record high-level summary in the run record for traceability.
        try:
            summary = {
                "run_mode": ctx.run_mode,
                "allow_invalid_upstream": bool(ctx.allow_invalid_upstream),
                "produced_hits": produced_hits,
                "produced_clips": produced_clips,
                "produced_suggestions": produced_suggestions,
            }
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE hc_project_runs SET run_spec = COALESCE(run_spec,'{}'::jsonb) || %s::jsonb, updated_at = NOW() WHERE run_id = %s",
                        (psycopg2.extras.Json({"last_run_summary": summary}), ctx.run_id),
                    )
        except Exception:
            logger.debug("Unable to persist run summary", exc_info=True)


    def _hash_fingerprint(*parts: Any) -> str:
        h = hashlib.sha256()
        for p in parts:
            if p is None:
                h.update(b"<null>")
            elif isinstance(p, (bytes, bytearray)):
                h.update(p)
            else:
                h.update(str(p).encode("utf-8", errors="replace"))
            h.update(b"\x00")
        return h.hexdigest()

    def _exec_analysis_suggestion_emitter(self, ctx: HCDagContext, node: Dict[str, Any]) -> int:
        """Emit suggestions by consuming analysis outputs.

        This node is intentionally *buffering*: it never writes hits directly.
        It transforms analysis outputs into hc_suggestions with full provenance.

        Supported sources:
        - analysis_tasks.result_json (chunk-level)
        - analysis_results.results_by_pass (aggregated)

        Minimal config:
          {
            "analysis_job_id": "...",
            "source": "analysis_tasks" | "analysis_results",
            "result_list_path": "suggestions",   // dotpath
            "suggestion_type": "analysis",
            "ytids": ["..."],
            "pass_ids": ["..."],                 // for analysis_tasks
            "config_ids": ["..."],               // for analysis_results
            "field_map": {
              "label": "label",
              "confidence": "confidence",
              "rationale": "rationale",
              "ytid": "ytid"
            },
            "spans_path": "spans"                // item dotpath (expects [[start,end,speaker?],...])
          }
        """

        cfg = node.get("config_json") or {}
        job_id = cfg.get("analysis_job_id") or cfg.get("job_id")
        if not job_id:
            raise ValueError("analysis_suggestion_emitter requires config.analysis_job_id")

        source = (cfg.get("source") or "analysis_tasks").strip().lower()
        suggestion_type = cfg.get("suggestion_type") or "analysis"
        result_list_path = cfg.get("result_list_path") or "suggestions"
        spans_path = cfg.get("spans_path") or "spans"
        field_map: Dict[str, str] = cfg.get("field_map") or {}

        ytids = cfg.get("ytids")
        pass_ids = cfg.get("pass_ids")
        config_ids = cfg.get("config_ids")
        limit = int(cfg.get("limit", 5000))

        created = 0

        if source == "analysis_results":
            rows = self.analysis_results.list_completed(job_id=job_id, ytids=ytids, config_ids=config_ids, limit=limit)
            for row in rows:
                ytid_row = row.get("ytid")
                results_by_pass = row.get("results_by_pass") or {}
                # results_by_pass is usually a dict keyed by pass_id
                for pass_id, pass_payload in (results_by_pass.items() if isinstance(results_by_pass, dict) else []):
                    items = self._get_path(pass_payload, result_list_path)
                    if not items:
                        continue
                    if isinstance(items, dict):
                        items = [items]
                    for idx, item in enumerate(items if isinstance(items, list) else []):
                        created += self._emit_one_analysis_suggestion(
                            ctx,
                            node,
                            suggestion_type=suggestion_type,
                            job_id=job_id,
                            source_table="analysis_results",
                            source_row_id=str(row.get("id")),
                            ytid_default=ytid_row,
                            pass_id=str(pass_id),
                            chunk_id=None,
                            task_id=None,
                            item_index=idx,
                            item=item,
                            field_map=field_map,
                            spans_path=spans_path,
                        )

        elif source == "analysis_tasks":
            rows = self.analysis_tasks.list_completed(job_id=job_id, pass_ids=pass_ids, ytids=ytids, limit=limit)
            for row in rows:
                result_json = row.get("result_json") or {}
                items = self._get_path(result_json, result_list_path)
                if not items:
                    continue
                if isinstance(items, dict):
                    items = [items]
                for idx, item in enumerate(items if isinstance(items, list) else []):
                    created += self._emit_one_analysis_suggestion(
                        ctx,
                        node,
                        suggestion_type=suggestion_type,
                        job_id=job_id,
                        source_table="analysis_tasks",
                        source_row_id=str(row.get("task_id")),
                        ytid_default=row.get("ytid"),
                        pass_id=row.get("pass_id"),
                        chunk_id=row.get("chunk_id"),
                        task_id=row.get("task_id"),
                        item_index=idx,
                        item=item,
                        field_map=field_map,
                        spans_path=spans_path,
                    )
        else:
            raise ValueError(f"analysis_suggestion_emitter unsupported source: {source}")

        return created

    def _emit_one_analysis_suggestion(
        self,
        ctx: HCDagContext,
        node: Dict[str, Any],
        *,
        suggestion_type: str,
        job_id: str,
        source_table: str,
        source_row_id: str,
        ytid_default: Optional[str],
        pass_id: Optional[str],
        chunk_id: Optional[int],
        task_id: Optional[int],
        item_index: int,
        item: Any,
        field_map: Dict[str, str],
        spans_path: str,
    ) -> int:
        if not isinstance(item, dict):
            # wrap non-dicts
            item_obj: Dict[str, Any] = {"value": item}
        else:
            item_obj = item

        # Resolve fields with optional per-item dotpaths
        def f(name: str, default: Any = None) -> Any:
            p = field_map.get(name) or name
            return self._get_path(item_obj, p) if p else default

        ytid = f("ytid", ytid_default) or ytid_default
        label = f("label")
        rationale = f("rationale")
        confidence = f("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except Exception:
            confidence = None

        # spans parsing
        spans_raw = self._get_path(item_obj, spans_path) or []
        spans: List[Tuple[float, float, Optional[str]]] = []
        if isinstance(spans_raw, list):
            for sp in spans_raw:
                if not sp:
                    continue
                try:
                    if isinstance(sp, dict):
                        s = float(sp.get("start", sp.get("start_sec")))
                        e = float(sp.get("end", sp.get("end_sec")))
                        speaker = sp.get("speaker") or sp.get("speaker_name")
                    else:
                        # list/tuple
                        s = float(sp[0])
                        e = float(sp[1])
                        speaker = sp[2] if len(sp) > 2 else None
                    spans.append((s, e, speaker))
                except Exception:
                    continue

        # Fingerprint must treat the *analysis output* as non-deterministic.
        # We include the raw item content; if a re-run produces the same item,
        # the suggestion won't duplicate. If it produces a different item,
        # it becomes a distinct suggestion (new fingerprint).
        raw_item_norm = json.dumps(item_obj, sort_keys=True, ensure_ascii=False, default=str)
        fingerprint = self._hash_fingerprint(
            "hc_suggestion",
            ctx.project_id,
            job_id,
            source_table,
            source_row_id,
            pass_id,
            chunk_id,
            item_index,
            raw_item_norm,
        )

        payload: Dict[str, Any] = {
            "analysis": {
                "job_id": job_id,
                "source": source_table,
                "source_row_id": source_row_id,
                "pass_id": pass_id,
                "chunk_id": chunk_id,
                "task_id": task_id,
                "item_index": item_index,
            },
            "raw_item": item_obj,
        }

        res = self.suggestions.create_suggestion_if_absent(
            project_id=ctx.project_id,
            run_id=ctx.run_id,
            node_id=node.get("node_id"),
            source_fingerprint=fingerprint,
            ytid=ytid,
            suggestion_type=suggestion_type,
            spans=spans,
            label=label,
            confidence=confidence,
            rationale=rationale,
            payload=payload,
        )
        return 1 if res.get("created") else 0


    # ------------------------------------------------------------------
    # Analysis-backed hits (Phase 2)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_confidence(val: Any) -> Optional[float]:
        """Normalize heterogeneous confidence inputs into 0..1 (or None)."""
        if val is None:
            return None
        # common patterns: {"score": 0.7}, {"confidence": 70}
        if isinstance(val, dict):
            for k in ("confidence", "score", "prob", "p"):
                if k in val:
                    val = val.get(k)
                    break
        try:
            f = float(val)
        except Exception:
            return None
        if f != f:  # NaN
            return None
        # heuristics: allow percentages
        if f > 1.0 and f <= 100.0:
            f = f / 100.0
        # clamp
        if f < 0.0:
            f = 0.0
        if f > 1.0:
            f = 1.0
        return f

    def _exec_analysis_hit_generator(self, ctx: HCDagContext, node: Dict[str, Any]) -> int:
        """Generate *hits* from analysis outputs or from explicitly promoted suggestions.

        Supported sources (config.source):
          - "promoted_suggestions"  (default): consume hc_suggestions where status='promoted' AND promoted_hit_id IS NULL
          - "analysis_tasks": consume analysis_tasks.result_json
          - "analysis_results": consume analysis_results.results_by_pass

        This node never changes a suggestion's status. It will only link promoted suggestions
        to their created hit via hc_suggestions.promoted_hit_id.
        """
        cfg = node.get("config_json") or {}
        source = (cfg.get("source") or "promoted_suggestions").strip().lower()

        if source == "promoted_suggestions":
            return self._exec_promoted_suggestions_to_hits(ctx, node)

        job_id = cfg.get("analysis_job_id") or cfg.get("job_id")
        if not job_id:
            raise ValueError("analysis_hit_generator requires config.analysis_job_id when source is analysis_tasks/analysis_results")

        hit_type = cfg.get("hit_type") or "analysis"
        result_list_path = cfg.get("result_list_path") or "hits"
        spans_path = cfg.get("spans_path") or "spans"
        field_map: Dict[str, str] = cfg.get("field_map") or {}
        ytids = cfg.get("ytids")
        pass_ids = cfg.get("pass_ids")
        config_ids = cfg.get("config_ids")
        limit = int(cfg.get("limit", 5000))
        merge_within = float(cfg.get("merge_within_sec", 0.5))

        # Include node config in fingerprint to guarantee per-project+config idempotency.
        cfg_norm = json.dumps(cfg, sort_keys=True, ensure_ascii=False, default=str)
        cfg_hash = hashlib.sha256(cfg_norm.encode("utf-8", errors="replace")).hexdigest()

        created = 0

        if source == "analysis_results":
            rows = self.analysis_results.list_completed(job_id=job_id, ytids=ytids, config_ids=config_ids, limit=limit)
            for row in rows:
                ytid_row = row.get("ytid")
                results_by_pass = row.get("results_by_pass") or {}
                for pass_id, pass_payload in (results_by_pass.items() if isinstance(results_by_pass, dict) else []):
                    items = self._get_path(pass_payload, result_list_path)
                    if not items:
                        continue
                    if isinstance(items, dict):
                        items = [items]
                    for idx, item in enumerate(items if isinstance(items, list) else []):
                        created += self._emit_one_analysis_hit(
                            ctx,
                            node,
                            job_id=job_id,
                            cfg_hash=cfg_hash,
                            source_table="analysis_results",
                            source_row_id=str(row.get("id")),
                            ytid_default=ytid_row,
                            pass_id=str(pass_id),
                            chunk_id=None,
                            task_id=None,
                            item_index=idx,
                            item=item,
                            hit_type=hit_type,
                            field_map=field_map,
                            spans_path=spans_path,
                            merge_within_sec=merge_within,
                        )

        elif source == "analysis_tasks":
            rows = self.analysis_tasks.list_completed(job_id=job_id, pass_ids=pass_ids, ytids=ytids, limit=limit)
            for row in rows:
                result_json = row.get("result_json") or {}
                items = self._get_path(result_json, result_list_path)
                if not items:
                    continue
                if isinstance(items, dict):
                    items = [items]
                for idx, item in enumerate(items if isinstance(items, list) else []):
                    created += self._emit_one_analysis_hit(
                        ctx,
                        node,
                        job_id=job_id,
                        cfg_hash=cfg_hash,
                        source_table="analysis_tasks",
                        source_row_id=str(row.get("task_id")),
                        ytid_default=row.get("ytid"),
                        pass_id=row.get("pass_id"),
                        chunk_id=row.get("chunk_id"),
                        task_id=row.get("task_id"),
                        item_index=idx,
                        item=item,
                        hit_type=hit_type,
                        field_map=field_map,
                        spans_path=spans_path,
                        merge_within_sec=merge_within,
                    )
        else:
            raise ValueError(f"analysis_hit_generator unsupported source: {source}")

        return created

    def _emit_one_analysis_hit(
        self,
        ctx: HCDagContext,
        node: Dict[str, Any],
        *,
        job_id: str,
        cfg_hash: str,
        source_table: str,
        source_row_id: str,
        ytid_default: Optional[str],
        pass_id: Optional[str],
        chunk_id: Optional[int],
        task_id: Optional[int],
        item_index: int,
        item: Any,
        hit_type: str,
        field_map: Dict[str, str],
        spans_path: str,
        merge_within_sec: float,
    ) -> int:
        if not isinstance(item, dict):
            item_obj: Dict[str, Any] = {"value": item}
        else:
            item_obj = item

        def f(name: str, default: Any = None) -> Any:
            p = field_map.get(name) or name
            return self._get_path(item_obj, p) if p else default

        ytid = f("ytid", ytid_default) or ytid_default
        label = f("label")
        rationale = f("rationale")
        confidence = self._normalize_confidence(f("confidence"))

        spans_raw = self._get_path(item_obj, spans_path) or []
        spans: List[Tuple[float, float, Optional[str]]] = []
        if isinstance(spans_raw, list):
            for sp in spans_raw:
                if not sp:
                    continue
                try:
                    if isinstance(sp, dict):
                        s = float(sp.get("start", sp.get("start_sec")))
                        e = float(sp.get("end", sp.get("end_sec")))
                        speaker = sp.get("speaker") or sp.get("speaker_name")
                    else:
                        s = float(sp[0])
                        e = float(sp[1])
                        speaker = sp[2] if len(sp) > 2 else None
                    spans.append((s, e, speaker))
                except Exception:
                    continue

        spans = _merge_spans(spans, merge_within_sec) if spans else []
        if not spans:
            return 0

        raw_item_norm = json.dumps(item_obj, sort_keys=True, ensure_ascii=False, default=str)
        fingerprint = self._hash_fingerprint(
            "hc_hit",
            ctx.project_id,
            cfg_hash,
            job_id,
            source_table,
            source_row_id,
            pass_id,
            chunk_id,
            item_index,
            raw_item_norm,
        )

        logical_key = f"analysis:{cfg_hash}:{job_id}:{source_table}:{source_row_id}:{pass_id or 'na'}:{chunk_id if chunk_id is not None else 'na'}:{item_index}"

        provenance: Dict[str, Any] = {
            "generator": "analysis_hit_generator",
            "node_key": node.get("node_key"),
            "analysis": {
                "job_id": job_id,
                "source": source_table,
                "source_row_id": source_row_id,
                "pass_id": pass_id,
                "chunk_id": chunk_id,
                "task_id": task_id,
                "item_index": item_index,
            },
            "source_fingerprint": fingerprint,
        }

        payload: Dict[str, Any] = {
            "analysis": provenance["analysis"],
            "raw_item": item_obj,
        }

        res = self.hits.create_hit_version_if_absent(
            project_id=ctx.project_id,
            logical_key=logical_key,
            source_fingerprint=fingerprint,
            hit_type=hit_type,
            ytid=ytid,
            spans=spans,
            label=label,
            tags=(node.get("config_json") or {}).get("tags") or [],
            confidence=confidence,
            rationale=rationale,
            provenance=provenance,
            payload=payload,
            status="active",
        )
        return 1 if res.get("created") else 0

    def _exec_promoted_suggestions_to_hits(self, ctx: HCDagContext, node: Dict[str, Any]) -> int:
        """Convert explicitly-promoted suggestions into hits (only if not yet linked)."""
        cfg = node.get("config_json") or {}
        limit = int(cfg.get("limit", 500))
        default_hit_type = cfg.get("hit_type") or "analysis"
        created = 0

        rows = self.suggestions.list_promoted_unconverted(ctx.project_id, limit=limit)
        for sug in rows:
            suggestion_id = str(sug["suggestion_id"])
            spans_rows = self.suggestions.get_suggestion_spans(suggestion_id)
            spans = [(float(s["start_sec"]), float(s["end_sec"]), s.get("speaker_name")) for s in spans_rows]
            if not spans:
                continue

            confidence = self._normalize_confidence(sug.get("confidence"))
            hit_type = default_hit_type
            try:
                if sug.get("suggestion_type") and str(sug.get("suggestion_type")).strip().lower() != "analysis":
                    # keep explicit type if provided, otherwise default
                    hit_type = default_hit_type
            except Exception:
                pass

            provenance = {
                "generator": "analysis_hit_generator",
                "node_key": node.get("node_key"),
                "suggestion_id": suggestion_id,
                "suggestion": {
                    "node_id": sug.get("node_id"),
                    "run_id": sug.get("run_id"),
                    "suggestion_type": sug.get("suggestion_type"),
                },
            }

            # If the suggestion carries analysis metadata in payload.analysis, bubble it up.
            try:
                payload_obj = sug.get("payload") or {}
                if isinstance(payload_obj, dict) and isinstance(payload_obj.get("analysis"), dict):
                    provenance["analysis"] = payload_obj.get("analysis")
            except Exception:
                pass

            # Fingerprint uses suggestion_id + stored payload (non-deterministic safety)
            sug_payload_norm = json.dumps(sug.get("payload") or {}, sort_keys=True, ensure_ascii=False, default=str)
            fingerprint = self._hash_fingerprint("hc_hit_from_suggestion", ctx.project_id, suggestion_id, sug_payload_norm)

            payload = {
                "suggestion": sug,
                "suggestion_spans": spans_rows,
            }

            res = self.hits.create_hit_version_if_absent(
                project_id=ctx.project_id,
                logical_key=f"suggestion:{suggestion_id}",
                source_fingerprint=fingerprint,
                hit_type=hit_type,
                ytid=sug.get("ytid"),
                spans=spans,
                label=sug.get("label"),
                confidence=confidence,
                rationale=sug.get("rationale"),
                provenance=provenance,
                payload=payload,
                status="active",
            )
            if res.get("created"):
                self.suggestions.set_promoted_hit(suggestion_id, res["hit_id"])
                created += 1

        return created

    

    # ------------------------------------------------------------------
    # Node implementations
    # ------------------------------------------------------------------

    def _exec_keyword_hit_generator(self, ctx: HCDagContext, node: Dict[str, Any]) -> int:
        cfg = node.get("config_json") or {}
        phrases: List[str] = cfg.get("phrases") or []
        if isinstance(phrases, str):
            phrases = [phrases]
        ytids: Optional[List[str]] = cfg.get("ytids")
        source: Optional[str] = cfg.get("words_source")
        exact: bool = bool(cfg.get("exact", False))
        limit: int = int(cfg.get("limit", 5000))
        padding_pre = float(cfg.get("padding_pre", 0.0))
        padding_post = float(cfg.get("padding_post", 0.0))
        merge_within = float(cfg.get("merge_within_sec", 0.5))
        label = cfg.get("label") or ("keywords:" + ",".join(phrases[:3]) if phrases else "keywords")

        total_created = 0
        for phrase in phrases:
            tokens = str(phrase).split()
            hits = self.words.find_phrase_hits(tokens=tokens, source=source, limit=limit, exact=exact, ytids=ytids)
            for h in hits:
                s = max(0.0, float(h["start_sec"]) - padding_pre)
                e = float(h["end_sec"]) + padding_post
                spans = _merge_spans([(s, e, None)], merge_within)
                self.hits.create_hit_version(
                    project_id=ctx.project_id,
                    hit_type="keyword",
                    ytid=h.get("ytid"),
                    spans=spans,
                    label=label,
                    tags=cfg.get("tags") or [],
                    confidence=None,
                    rationale=f"phrase match: {phrase}",
                    provenance={
                        "generator": "keyword_hit_generator",
                        "node_key": node.get("node_key"),
                        "words_source": h.get("source"),
                        "exact": exact,
                    },
                    payload={"phrase": phrase, "match": h},
                )
                total_created += 1
        return total_created

    def _exec_clip_projection(self, ctx: HCDagContext, node: Dict[str, Any]) -> int:
        cfg = node.get("config_json") or {}
        profile_name = cfg.get("profile_name", "default")
        profile_version = cfg.get("profile_version")
        profile_json = cfg.get("profile_json") or {
            "padding_pre": float(cfg.get("padding_pre", 2.0)),
            "padding_post": float(cfg.get("padding_post", 2.0)),
        }

        # Ensure a profile exists (versioned)
        profile_id = None
        if profile_version is None:
            profile_id = self.clips.create_profile(ctx.project_id, profile_name, profile_json)
            prof = self.clips.get_profile(profile_id)
            profile_version = prof.get("version") if prof else None
        else:
            profiles = [p for p in self.clips.list_profiles(ctx.project_id, name=profile_name) if int(p.get("version")) == int(profile_version)]
            if profiles:
                profile_id = profiles[0]["profile_id"]
            else:
                profile_id = self.clips.create_profile(ctx.project_id, profile_name, profile_json, version=int(profile_version))

        padding_pre = float(profile_json.get("padding_pre", 2.0))
        padding_post = float(profile_json.get("padding_post", 2.0))

        # Project latest active hits into planned clips
        hits = self.hits.list_hits(ctx.project_id, status="active", latest_only=True, limit=int(cfg.get("hit_limit", 1000)))
        created = 0
        for hit in hits:
            spans = self.hits.get_hit_spans(hit["hit_id"])
            if not spans:
                continue
            start = max(0.0, float(spans[0]["start_sec"]) - padding_pre)
            end = float(spans[-1]["end_sec"]) + padding_post
            snap = {
                "hit_id": hit["hit_id"],
                "hit_logical_id": hit["hit_logical_id"],
                "hit_version": hit["hit_version"],
                "label": hit.get("label"),
                "confidence": hit.get("confidence"),
                "spans": spans,
            }
            self.clips.create_clip(
                project_id=ctx.project_id,
                run_id=ctx.run_id,
                hit_id=hit["hit_id"],
                hit_snapshot=snap,
                profile_id=profile_id,
                profile_name=profile_name,
                profile_version=int(profile_version) if profile_version is not None else None,
                ytid=hit.get("ytid"),
                start_sec=start,
                end_sec=end,
                label=hit.get("label"),
                status="planned",
                provenance={"generator": "clip_projection", "node_key": node.get("node_key")},
            )
            created += 1

        if cfg.get("quickclip", False):
            self._emit_quickclip_from_project(ctx.project_id)

        return created

    def _emit_quickclip_from_project(self, project_id: str) -> None:
        """Best-effort: create QuickClip sessions/clips for planned clips.

        This is opt-in and does not download/video-render by itself.
        """
        clips = self.clips.list_clips(project_id, limit=5000)
        by_ytid: Dict[str, List[Dict[str, Any]]] = {}
        for c in clips:
            ytid = c.get("ytid")
            if not ytid:
                continue
            by_ytid.setdefault(ytid, []).append(c)

        for ytid, rows in by_ytid.items():
            session_id = f"hc_{project_id}_{ytid}"[:96]
            url = f"https://www.youtube.com/watch?v={ytid}"
            with QuickClipRepository() as qc:
                try:
                    qc.create_session(session_id=session_id, ytid=ytid, url=url, description=f"HC project {project_id}", tags="hc")
                except Exception:
                    pass

                idx = 1
                for r in sorted(rows, key=lambda x: float(x.get("start_sec", 0))):
                    clip_id = f"hc_{r['clip_id']}"[:96]
                    try:
                        qc.create_clip(
                            clip_id=clip_id,
                            session_id=session_id,
                            ytid=ytid,
                            start_sec=float(r["start_sec"]),
                            end_sec=float(r["end_sec"]),
                            label=r.get("label"),
                            clip_index=idx,
                        )
                        idx += 1
                    except Exception:
                        continue
