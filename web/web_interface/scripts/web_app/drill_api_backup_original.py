#!/usr/bin/env python3
"""
Drill configuration and test API.

Provides CRUD operations for drills stored inside analysis_configs.config_json["drills"]
and simple test/dry-run utilities.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from flask import Blueprint, request, jsonify

from scripts.analysis.analysis_config import Drill  # type: ignore
from scripts.analysis.db_storage import AnalysisDatabase  # used via get_db in factory
from scripts.analysis.drills import DrillExecutor


def _validate_drill_graph(drills: List[Dict[str, Any]]) -> Tuple[bool, str | None]:
    """Validate drills dependency graph: unknown deps and cycles."""
    name_map = {d.get("name"): d for d in drills if d.get("name")}
    # Unknown deps
    unknown: List[str] = []
    for d in drills:
        for dep in d.get("depends_on") or []:
            if dep not in name_map:
                unknown.append(dep)
    if unknown:
        return False, f"Unknown drill dependency/dependencies: {', '.join(sorted(set(unknown)))}"

    # Cycle detection (DFS)
    visiting = set()
    visited = set()

    def dfs(name: str, stack: List[str]) -> Tuple[bool, List[str]]:
        if name in visiting:
            # cycle: find position in stack
            if name in stack:
                idx = stack.index(name)
                return True, stack[idx:] + [name]
            return True, stack + [name]
        if name in visited:
            return False, []
        visiting.add(name)
        stack.append(name)
        spec = name_map.get(name) or {}
        for dep in spec.get("depends_on") or []:
            has_cycle, path = dfs(dep, stack)
            if has_cycle:
                return True, path
        stack.pop()
        visiting.remove(name)
        visited.add(name)
        return False, []

    for n in name_map:
        has_cycle, path = dfs(n, [])
        if has_cycle:
            cycle_str = " -> ".join(path)
            return False, f"Cycle detected in drill dependencies: {cycle_str}"
    return True, None


def _load_config(db: "AnalysisDB", config_id: str) -> Dict[str, Any] | None:
    row = db.get_analysis_config(config_id)
    if not row:
        return None
    cfg = row.get("config_json") or {}
    if not isinstance(cfg, dict):
        try:
            cfg = json.loads(cfg)
        except Exception:
            cfg = {}
    return {"row": row, "cfg": cfg}


def _save_config(db: "AnalysisDB", row: Dict[str, Any], cfg: Dict[str, Any]) -> None:
    db.upsert_analysis_config(
        config_id=row["id"],
        name=row["name"],
        analysis_type=row["analysis_type"],
        version=row["version"],
        config_json=cfg,
        is_default=row.get("is_default", False),
    )
    if db.conn:
        db.conn.commit()


def create_drill_blueprint(get_db) -> Blueprint:
    bp = Blueprint("drills_api", __name__)

    # ---- Basic CRUD ---------------------------------------------------------

    @bp.route("/drills", methods=["GET"])
    def list_drills():
        config_id = request.args.get("config_id")
        if not config_id:
            return jsonify({"error": "config_id query parameter is required"}), 400
        db = get_db()
        loaded = _load_config(db, config_id)
        if not loaded:
            return jsonify({"error": f"No config found with id={config_id}"}), 404
        cfg = loaded["cfg"]
        drills = cfg.get("drills") or []
        # Normalize: ensure list of dicts
        if not isinstance(drills, list):
            drills = []
        return jsonify({"drills": drills})

    @bp.route("/drills/<name>", methods=["GET"])
    def get_drill(name: str):
        config_id = request.args.get("config_id")
        if not config_id:
            return jsonify({"error": "config_id query parameter is required"}), 400
        db = get_db()
        loaded = _load_config(db, config_id)
        if not loaded:
            return jsonify({"error": f"No config found with id={config_id}"}), 404
        cfg = loaded["cfg"]
        drills = cfg.get("drills") or []
        for d in drills:
            if d.get("name") == name:
                return jsonify(d)
        return jsonify({"error": f"Drill '{name}' not found"}), 404

    @bp.route("/drills", methods=["POST"])
    def create_drill():
        config_id = request.args.get("config_id")
        if not config_id:
            return jsonify({"error": "config_id query parameter is required"}), 400
        db = get_db()
        loaded = _load_config(db, config_id)
        if not loaded:
            return jsonify({"error": f"No config found with id={config_id}"}), 404
        row = loaded["row"]
        cfg = loaded["cfg"]
        drills = cfg.get("drills") or []
        if not isinstance(drills, list):
            drills = []
        payload = request.get_json(force=True, silent=True) or {}

        name = (payload.get("name") or "").strip()
        prompt = (payload.get("prompt") or "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400
        if not prompt:
            return jsonify({"error": "prompt is required"}), 400

        # Unique name
        if any(d.get("name") == name for d in drills):
            return jsonify({"error": f"Drill with name '{name}' already exists"}), 400

        # Build drill dict with defaults
        spec: Dict[str, Any] = {
            "name": name,
            "description": payload.get("description") or "",
            "prompt": prompt,
            "scope": payload.get("scope") or "chunks",
            "depends_on": payload.get("depends_on") or [],
            "output_shape": payload.get("output_shape") or "span",
            "always": bool(payload.get("always", False)),
            "min_hits": int(payload.get("min_hits") or 0),
            "keywords": payload.get("keywords") or [],
            "match": payload.get("match") or [],
            "category": payload.get("category") or None,
            "cooldown": int(payload.get("cooldown") or 0),
            "detail_pass": payload.get("detail_pass") or {},
        }

        candidate = drills + [spec]
        ok, err = _validate_drill_graph(candidate)
        if not ok:
            return jsonify({"error": err}), 400

        drills.append(spec)
        cfg["drills"] = drills
        _save_config(db, row, cfg)
        return jsonify(spec)

    @bp.route("/drills/<name>", methods=["POST"])
    def update_drill(name: str):
        config_id = request.args.get("config_id")
        if not config_id:
            return jsonify({"error": "config_id query parameter is required"}), 400
        db = get_db()
        loaded = _load_config(db, config_id)
        if not loaded:
            return jsonify({"error": f"No config found with id={config_id}"}), 404
        row = loaded["row"]
        cfg = loaded["cfg"]
        drills = cfg.get("drills") or []
        if not isinstance(drills, list):
            drills = []

        payload = request.get_json(force=True, silent=True) or {}
        target = None
        for d in drills:
            if d.get("name") == name:
                target = d
                break
        if not target:
            return jsonify({"error": f"Drill '{name}' not found"}), 404

        # Compute new name first (support rename)
        new_name = (payload.get("name") or target.get("name") or "").strip()
        if not new_name:
            return jsonify({"error": "name is required"}), 400

        if new_name != name and any(d.get("name") == new_name for d in drills):
            return jsonify({"error": f"Another drill already has name '{new_name}'"}), 400

        # Update fields
        target["name"] = new_name
        for field in [
            "description",
            "prompt",
            "scope",
            "depends_on",
            "output_shape",
            "always",
            "min_hits",
            "keywords",
            "match",
            "category",
            "cooldown",
            "detail_pass",
        ]:
            if field in payload:
                target[field] = payload[field]

        # Rewrite depends_on references if renamed
        if new_name != name:
            for d in drills:
                deps = d.get("depends_on") or []
                d["depends_on"] = [new_name if dep == name else dep for dep in deps]

        ok, err = _validate_drill_graph(drills)
        if not ok:
            return jsonify({"error": err}), 400

        cfg["drills"] = drills
        _save_config(db, row, cfg)
        return jsonify(target)

    @bp.route("/drills/<name>", methods=["DELETE"])
    def delete_drill(name: str):
        config_id = request.args.get("config_id")
        if not config_id:
            return jsonify({"error": "config_id query parameter is required"}), 400
        db = get_db()
        loaded = _load_config(db, config_id)
        if not loaded:
            return jsonify({"error": f"No config found with id={config_id}"}), 404
        row = loaded["row"]
        cfg = loaded["cfg"]
        drills = cfg.get("drills") or []
        if not isinstance(drills, list):
            drills = []

        # Check dependents
        dependents = [d.get("name") for d in drills if name in (d.get("depends_on") or [])]
        if dependents:
            return (
                jsonify(
                    {
                        "error": f"Cannot delete drill '{name}' because other drills depend on it.",
                        "dependents": dependents,
                    }
                ),
                400,
            )

        new_drills = [d for d in drills if d.get("name") != name]
        if len(new_drills) == len(drills):
            return jsonify({"error": f"Drill '{name}' not found"}), 404

        cfg["drills"] = new_drills
        _save_config(db, row, cfg)
        return jsonify({"status": "deleted", "name": name})

    # ---- Test single drill -----------------------------------------------

    def _resolve_model_and_endpoint(drill: Dict[str, Any]) -> Tuple[str | None, str | None, Dict[str, Any]]:
        dp = drill.get("detail_pass") or {}
        model = dp.get("model")
        endpoint = dp.get("endpoint")
        options = dp.get("options") or {}
        return model, endpoint, options

    def _build_sample_chunks(sample: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[Any, str]]:
        text = sample.get("text") or ""
        chunk_id = sample.get("chunk_id", 0)
        categories = sample.get("categories") or []
        topics = sample.get("topics") or []
        start_sec = sample.get("start_sec")
        end_sec = sample.get("end_sec")
        chunk = {
            "chunk_id": chunk_id,
            "text": text,
            "categories": categories,
            "topics": topics,
            "start_sec": start_sec,
            "end_sec": end_sec,
        }
        chunks = [chunk]
        chunk_texts = {chunk_id: text}
        return chunks, chunk_texts

    def _dependency_closure(drills: List[Dict[str, Any]], target_name: str) -> List[Dict[str, Any]]:
        name_map = {d.get("name"): d for d in drills}
        closure = set()

        def visit(n: str):
            if n in closure:
                return
            closure.add(n)
            d = name_map.get(n)
            if not d:
                return
            for dep in d.get("depends_on") or []:
                visit(dep)

        visit(target_name)
        return [d for d in drills if d.get("name") in closure]

    @bp.route("/drills/<name>/test", methods=["POST"])
    def test_drill(name: str):
        """
        Execute a single drill (plus its dependency chain for span-scoped drills)
        against a sample chunk JSON.

        Request JSON:
          {
            "sample": { "text": "...", "categories": [...], "topics": [...], ... },
            "scope": "chunks|spans|subchunks"   # optional override
          }
        """
        config_id = request.args.get("config_id")
        if not config_id:
            return jsonify({"error": "config_id query parameter is required"}), 400
        body = request.get_json(force=True, silent=True) or {}
        sample = body.get("sample") or {}
        scope_override = body.get("scope")

        db = get_db()
        loaded = _load_config(db, config_id)
        if not loaded:
            return jsonify({"error": f"No config found with id={config_id}"}), 404
        cfg = loaded["cfg"]
        drills = cfg.get("drills") or []
        if not isinstance(drills, list):
            drills = []

        target = None
        for d in drills:
            if d.get("name") == name:
                target = d
                break
        if not target:
            return jsonify({"error": f"Drill '{name}' not found"}), 404

        model, endpoint, options = _resolve_model_and_endpoint(target)
        if not model or not endpoint:
            return (
                jsonify(
                    {
                        "error": "Test requires detail_pass.model and detail_pass.endpoint on the drill.",
                        "hint": "Set these in the drill editor under LLM Configuration.",
                    }
                ),
                400,
            )

        scope = scope_override or target.get("scope") or "chunks"

        # Select drills to include: for span drills, include deps as well
        if scope == "spans" or target.get("scope") == "spans":
            selected = _dependency_closure(drills, name)
        else:
            selected = [target]

        chunks, chunk_texts = _build_sample_chunks(sample)

        try:
            executor = DrillExecutor(
                drills=selected,
                base_model=model,
                base_url=endpoint,
                options=options,
                log_mode="quiet",
                delay_between=0,
            )
            results, summary, emitted = executor.run(chunks, chunk_texts=chunk_texts, scope="chunks")
        except Exception as e:
            return jsonify({"error": f"Drill execution failed: {e!r}"}), 500

        return jsonify(
            {
                "config_id": config_id,
                "drill": name,
                "included_drills": [d.get("name") for d in selected],
                "scope": scope,
                "sample": sample,
                "results": results,
                "summary": summary,
                "emitted_spans": emitted,
            }
        )

    # ---- Import / Export ---------------------------------------------------

    @bp.route("/drills/export", methods=["GET"])
    def export_drills():
        config_id = request.args.get("config_id")
        fmt = request.args.get("format", "json").lower()
        if not config_id:
            return jsonify({"error": "config_id query parameter is required"}), 400
        db = get_db()
        loaded = _load_config(db, config_id)
        if not loaded:
            return jsonify({"error": f"No config found with id={config_id}"}), 404
        cfg = loaded["cfg"]
        drills = cfg.get("drills") or []
        if not isinstance(drills, list):
            drills = []

        if fmt == "json":
            return jsonify({"drills": drills})
        elif fmt == "yaml":
            try:
                import yaml  # type: ignore
            except Exception:
                return (
                    jsonify(
                        {"error": "YAML export requested but PyYAML is not installed on server.", "supported": ["json"]}
                    ),
                    400,
                )
            text = yaml.safe_dump({"drills": drills}, sort_keys=False)  # type: ignore
            return (
                text,
                200,
                {
                    "Content-Type": "text/yaml",
                    "Content-Disposition": f'attachment; filename="drills_{config_id}.yaml"',
                },
            )
        else:
            return jsonify({"error": f"Unsupported format '{fmt}'. Use 'json' or 'yaml'."}), 400

    @bp.route("/drills/import", methods=["POST"])
    def import_drills():
        """
        Import drills into a config.

        Request JSON:
          {
            "drills": [...],   # required
            "mode": "merge" | "replace"   # default: merge
          }
        """
        config_id = request.args.get("config_id")
        if not config_id:
            return jsonify({"error": "config_id query parameter is required"}), 400
        body = request.get_json(force=True, silent=True) or {}
        incoming = body.get("drills")
        mode = (body.get("mode") or "merge").lower()

        if not isinstance(incoming, list):
            return jsonify({"error": "drills must be a list"}), 400

        db = get_db()
        loaded = _load_config(db, config_id)
        if not loaded:
            return jsonify({"error": f"No config found with id={config_id}"}), 404
        row = loaded["row"]
        cfg = loaded["cfg"]
        existing = cfg.get("drills") or []
        if not isinstance(existing, list):
            existing = []

        name_to_drill: Dict[str, Dict[str, Any]] = {d.get("name"): d for d in existing if d.get("name")}
        imported_count = 0
        updated_count = 0

        for spec in incoming:
            if not isinstance(spec, dict):
                continue
            name = (spec.get("name") or "").strip()
            if not name:
                continue
            # Normalize fields with defaults similar to create_drill
            spec.setdefault("description", "")
            spec.setdefault("prompt", "")
            spec.setdefault("scope", "chunks")
            spec.setdefault("depends_on", [])
            spec.setdefault("output_shape", "span")
            spec.setdefault("always", False)
            spec["min_hits"] = int(spec.get("min_hits") or 0)
            spec.setdefault("keywords", [])
            spec.setdefault("match", [])
            spec.setdefault("category", None)
            spec["cooldown"] = int(spec.get("cooldown") or 0)
            spec.setdefault("detail_pass", spec.get("detail_pass") or {})

            if mode == "replace" or name not in name_to_drill:
                name_to_drill[name] = spec
                imported_count += 1
            else:
                # merge: overwrite by name
                name_to_drill[name] = spec
                updated_count += 1

        merged = list(name_to_drill.values())
        ok, err = _validate_drill_graph(merged)
        if not ok:
            return jsonify({"error": err}), 400

        cfg["drills"] = merged
        _save_config(db, row, cfg)
        return jsonify(
            {
                "status": "ok",
                "imported": imported_count,
                "updated": updated_count,
                "total": len(merged),
            }
        )

    # ---- DAG dry-run -------------------------------------------------------

    def _dry_should_fire(spec: Dict[str, Any], sample: Dict[str, Any]) -> bool:
        """Pure-Python approximation of trigger logic (no LLM)."""
        if spec.get("always"):
            return True
        min_hits = int(spec.get("min_hits") or 0)
        match_tokens = [str(t).strip().lower() for t in (spec.get("match") or []) if t]
        keywords = [str(k).strip().lower() for k in (spec.get("keywords") or []) if k]
        if not match_tokens and not keywords and not min_hits:
            return False
        hits = 0
        cats = [str(c).strip().lower() for c in (sample.get("categories") or []) if c]
        topics = [str(t).strip().lower() for t in (sample.get("topics") or []) if t]
        text = (sample.get("text") or "").lower()
        for tok in match_tokens:
            if tok in cats or tok in topics:
                hits += 1
        for k in keywords:
            if k and k in text:
                hits += 1
        if min_hits > 0:
            return hits >= min_hits
        return hits >= 1

    @bp.route("/drills/dry-run", methods=["POST"])
    def dry_run_dag():
        """
        Dry-run the entire drill DAG on a sample chunk without calling the LLM.

        Request JSON:
          {
            "sample": { "text": "...", "categories": [...], "topics": [...] }
          }

        Response JSON:
          {
            "nodes": [
              {
                "name": "...",
                "scope": "chunks|spans|subchunks",
                "depends_on": [...],
                "would_run": true|false,
                "reason": "..."
              },
              ...
            ]
          }
        """
        config_id = request.args.get("config_id")
        if not config_id:
            return jsonify({"error": "config_id query parameter is required"}), 400
        body = request.get_json(force=True, silent=True) or {}
        sample = body.get("sample") or {}

        db = get_db()
        loaded = _load_config(db, config_id)
        if not loaded:
            return jsonify({"error": f"No config found with id={config_id}"}), 404
        cfg = loaded["cfg"]
        drills = cfg.get("drills") or []
        if not isinstance(drills, list):
            drills = []

        ok, err = _validate_drill_graph(drills)
        if not ok:
            return jsonify({"error": err}), 400

        name_map = {d.get("name"): d for d in drills if d.get("name")}
        order = []

        # Simple toposort
        visited = set()

        def visit(n: str):
            if n in visited:
                return
            visited.add(n)
            d = name_map.get(n)
            if not d:
                return
            for dep in d.get("depends_on") or []:
                visit(dep)
            order.append(d)

        for n in name_map:
            visit(n)

        fired = set()
        nodes = []
        for d in order:
            name = d.get("name")
            scope = d.get("scope") or "chunks"
            deps = d.get("depends_on") or []
            would_run = False
            reason = ""
            if scope in ("chunks", "subchunks"):
                if _dry_should_fire(d, sample):
                    would_run = True
                    reason = "Triggers matched (keywords/match/min_hits)."
                else:
                    reason = "Triggers did not match and 'always' is false."
            else:  # spans
                # spans run if any dependency 'fired'
                if any(dep in fired for dep in deps) or d.get("always"):
                    would_run = True
                    reason = "At least one dependency fired or 'always' is true."
                else:
                    reason = "No dependencies fired and 'always' is false."

            if would_run:
                fired.add(name)
            nodes.append(
                {
                    "name": name,
                    "scope": scope,
                    "depends_on": deps,
                    "would_run": would_run,
                    "reason": reason,
                }
            )

        return jsonify({"nodes": nodes})

    return bp
