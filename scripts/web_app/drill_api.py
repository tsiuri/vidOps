#!/usr/bin/env python3
"""
Drill configuration and test API (Refactored for Database Storage).

Provides CRUD operations for drills stored in the dedicated drills table.
Supports both global drills (visible to all configs) and local drills (config-specific).

Migration from config_json JSONB to database completed.
See DRILL_API_MIGRATION_PLAN.md for details.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from flask import Blueprint, request, jsonify

from scripts.analysis.analysis_config import Drill  # type: ignore
from scripts.analysis.db_storage import AnalysisDatabase  # used via get_db in factory
from scripts.analysis.drills import DrillExecutor


def _validate_drill_graph(drills: List[Dict[str, Any]]) -> Tuple[bool, str | None]:
    """Validate drills dependency graph: unknown deps and cycles.

    Used by test and dry-run endpoints to validate DAG before execution.
    """
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


def create_drill_blueprint(get_db) -> Blueprint:
    bp = Blueprint("drills_api", __name__)

    # ========== BASIC CRUD =========================================================

    @bp.route("/drills", methods=["GET"])
    def list_drills():
        """List all drills visible to a config (global + local).

        Returns drills ordered: local first, then global, all alphabetical.
        """
        config_id = request.args.get("config_id")
        if not config_id:
            return jsonify({"error": "config_id query parameter is required"}), 400

        db = get_db()

        # Verify config exists
        cfg = db.get_analysis_config(config_id)
        if not cfg:
            return jsonify({"error": f"No config found with id={config_id}"}), 404

        # Try to load drills from database, fall back to config JSON
        try:
            drills = db.list_drills_for_config(config_id)
        except Exception as e:
            # Database table doesn't exist or other error - fall back to config JSON
            if "drills" not in cfg:
                drills = []
            else:
                drills = cfg.get("drills", [])
            # Add is_local field if missing (for backward compatibility)
            for d in drills:
                if "is_local" not in d:
                    d["is_local"] = False

        return jsonify({"drills": drills})

    @bp.route("/drills/<name>", methods=["GET"])
    def get_drill(name: str):
        """Get a specific drill by name (local takes precedence over global)."""
        config_id = request.args.get("config_id")
        if not config_id:
            return jsonify({"error": "config_id query parameter is required"}), 400

        db = get_db()

        # Verify config exists
        cfg = db.get_analysis_config(config_id)
        if not cfg:
            return jsonify({"error": f"No config found with id={config_id}"}), 404

        try:
            drill = db.get_drill(name, config_id)
        except Exception:
            # Database table doesn't exist - fall back to config JSON
            drills = cfg.get("drills", [])
            drill = next((d for d in drills if d.get("name") == name), None)
            if drill and "is_local" not in drill:
                drill["is_local"] = False

        if not drill:
            return jsonify({"error": f"Drill '{name}' not found"}), 404

        return jsonify(drill)

    @bp.route("/drills", methods=["POST"])
    def create_drill():
        """Create a new drill (global or local).

        Accepts is_local boolean (default: False = global).
        Dependencies validated at database insertion time.
        Falls back to config JSON storage if database table doesn't exist.
        """
        config_id = request.args.get("config_id")
        if not config_id:
            return jsonify({"error": "config_id query parameter is required"}), 400

        db = get_db()

        # Verify config exists
        cfg = db.get_analysis_config(config_id)
        if not cfg:
            return jsonify({"error": f"No config found with id={config_id}"}), 404

        payload = request.get_json(force=True, silent=True) or {}

        name = (payload.get("name") or "").strip()
        prompt = (payload.get("prompt") or "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400
        if not prompt:
            return jsonify({"error": "prompt is required"}), 400

        # Determine if local or global
        is_local = bool(payload.get("is_local", False))
        drill_config_id = config_id if is_local else None

        try:
            # Check if drill with this name already exists (local or global)
            existing = db.get_drill(name, config_id)
            if existing:
                return jsonify({"error": f"Drill with name '{name}' already exists"}), 400

            drill_id = db.create_drill(
                name=name,
                prompt=prompt,
                config_id=drill_config_id,
                description=payload.get("description", ""),
                scope=payload.get("scope", "chunks"),
                output_shape=payload.get("output_shape", "span"),
                category=payload.get("category"),
                always=bool(payload.get("always", False)),
                min_hits=int(payload.get("min_hits", 0)),
                keywords=payload.get("keywords", []),
                match=payload.get("match", []),
                cooldown=int(payload.get("cooldown", 0)),
                detail_pass=payload.get("detail_pass", {}),
                depends_on=payload.get("depends_on", [])
            )

            if db.conn:
                db.conn.commit()

            # Return created drill
            drill = db.get_drill(name, config_id)
            return jsonify(drill)

        except Exception as e:
            if db.conn:
                db.conn.rollback()

            # Database table doesn't exist - fall back to config JSON storage
            if "drills" not in cfg:
                cfg["drills"] = []

            # Check if drill already exists in config JSON
            if any(d.get("name") == name for d in cfg.get("drills", [])):
                return jsonify({"error": f"Drill with name '{name}' already exists"}), 400

            # Create drill in config JSON (temporary, until migration is run)
            new_drill = {
                "name": name,
                "prompt": prompt,
                "description": payload.get("description", ""),
                "scope": payload.get("scope", "chunks"),
                "output_shape": payload.get("output_shape", "span"),
                "category": payload.get("category"),
                "always": bool(payload.get("always", False)),
                "min_hits": int(payload.get("min_hits", 0)),
                "keywords": payload.get("keywords", []),
                "match": payload.get("match", []),
                "cooldown": int(payload.get("cooldown", 0)),
                "detail_pass": payload.get("detail_pass", {}),
                "depends_on": payload.get("depends_on", []),
                "is_local": is_local
            }

            cfg["drills"].append(new_drill)
            # Save back to database
            db.upsert_analysis_config(
                config_id=config_id,
                name=cfg.get("name", f"config-{config_id}"),
                analysis_type=cfg.get("analysis_type", "normal"),
                version=cfg.get("version", 1),
                config_json=cfg
            )

            return jsonify(new_drill), 201

    @bp.route("/drills/<name>", methods=["POST"])
    def update_drill(name: str):
        """Update an existing drill.

        Supports renaming, updating all fields, and changing dependencies.
        Falls back to config JSON storage if database table doesn't exist.
        """
        config_id = request.args.get("config_id")
        if not config_id:
            return jsonify({"error": "config_id query parameter is required"}), 400

        db = get_db()

        # Verify config exists
        cfg = db.get_analysis_config(config_id)
        if not cfg:
            return jsonify({"error": f"No config found with id={config_id}"}), 404

        payload = request.get_json(force=True, silent=True) or {}

        try:
            drill = db.get_drill(name, config_id)
            if not drill:
                return jsonify({"error": f"Drill '{name}' not found"}), 404

            # Handle name changes (unique constraint on (name, config_id))
            if "name" in payload:
                new_name = payload["name"].strip()
                if new_name != name:
                    # Check if new name conflicts
                    existing = db.get_drill(new_name, config_id)
                    if existing:
                        return jsonify({"error": f"Another drill already has name '{new_name}'"}), 400

            # Prepare update parameters
            update_params = {}

            # Handle name change
            if "name" in payload:
                new_name = payload["name"].strip()
                if new_name and new_name != name:
                    update_params["name"] = new_name

            # Map updatable fields
            field_map = [
                ("description", str),
                ("prompt", str),
                ("scope", str),
                ("output_shape", str),
                ("category", str),
                ("always", bool),
                ("min_hits", int),
                ("keywords", list),
                ("match", list),
                ("cooldown", int),
                ("detail_pass", dict),
                ("depends_on", list),
            ]

            for field, field_type in field_map:
                if field in payload:
                    if field == "always":
                        update_params[field] = bool(payload[field])
                    elif field == "min_hits" or field == "cooldown":
                        update_params[field] = int(payload[field])
                    else:
                        update_params[field] = payload[field]

            update_params["config_id_for_deps"] = config_id

            db.update_drill(drill["id"], **update_params)

            if db.conn:
                db.conn.commit()

            # Return updated drill
            updated_name = update_params.get("name", name)
            drill = db.get_drill(updated_name, config_id)
            return jsonify(drill)

        except Exception as e:
            if db.conn:
                db.conn.rollback()

            # Database table doesn't exist - fall back to config JSON storage
            if "drills" not in cfg:
                return jsonify({"error": f"Drill '{name}' not found"}), 404

            # Find drill in config JSON
            drills = cfg.get("drills", [])
            drill_idx = next((i for i, d in enumerate(drills) if d.get("name") == name), None)
            if drill_idx is None:
                return jsonify({"error": f"Drill '{name}' not found"}), 404

            drill = drills[drill_idx]

            # Handle name change in config JSON
            if "name" in payload:
                new_name = payload["name"].strip()
                if new_name and new_name != name:
                    # Check for conflicts
                    if any(d.get("name") == new_name for d in drills):
                        return jsonify({"error": f"Another drill already has name '{new_name}'"}), 400
                    drill["name"] = new_name

            # Update fields
            field_map = [
                "description", "prompt", "scope", "output_shape", "category",
                "always", "min_hits", "keywords", "match", "cooldown", "detail_pass", "depends_on"
            ]

            for field in field_map:
                if field in payload:
                    if field == "always":
                        drill[field] = bool(payload[field])
                    elif field in ("min_hits", "cooldown"):
                        drill[field] = int(payload[field])
                    else:
                        drill[field] = payload[field]

            # Save back to database
            db.upsert_analysis_config(
                config_id=config_id,
                name=cfg.get("name", f"config-{config_id}"),
                analysis_type=cfg.get("analysis_type", "normal"),
                version=cfg.get("version", 1),
                config_json=cfg
            )

            # Ensure is_local is present
            if "is_local" not in drill:
                drill["is_local"] = False

            return jsonify(drill)

    @bp.route("/drills/<name>", methods=["DELETE"])
    def delete_drill(name: str):
        """Delete a drill.

        Checks for dependents (will fail if other drills depend on it).
        Global drills require ?force=true parameter.
        """
        config_id = request.args.get("config_id")
        if not config_id:
            return jsonify({"error": "config_id query parameter is required"}), 400

        db = get_db()

        # Verify config exists
        if not db.get_analysis_config(config_id):
            return jsonify({"error": f"No config found with id={config_id}"}), 404

        drill = db.get_drill(name, config_id)
        if not drill:
            return jsonify({"error": f"Drill '{name}' not found"}), 404

        # Check for dependents
        dependents = db.get_drill_dependents(drill["id"])
        if dependents:
            return jsonify({
                "error": f"Cannot delete drill '{name}' because other drills depend on it.",
                "dependents": dependents
            }), 400

        # For global drills, require ?force=true
        if not drill["is_local"]:
            force = request.args.get("force") == "true"
            if not force:
                return jsonify({
                    "error": "This is a global drill. Use ?force=true to confirm deletion.",
                    "warning": "Deleting this drill will affect all configurations."
                }), 400

        try:
            db.delete_drill(drill["id"])
            if db.conn:
                db.conn.commit()
            return jsonify({"status": "deleted", "name": name})
        except Exception as e:
            if db.conn:
                db.conn.rollback()
            return jsonify({"error": str(e)}), 500

    # ========== TEST & DRY-RUN ====================================================

    @bp.route("/drills/<name>/test", methods=["POST"])
    def test_drill(name: str):
        """Test a single drill against sample data.

        Runs the drill through the pipeline with a test transcript.
        """
        config_id = request.args.get("config_id")
        if not config_id:
            return jsonify({"error": "config_id query parameter is required"}), 400

        db = get_db()

        # Verify config exists
        if not db.get_analysis_config(config_id):
            return jsonify({"error": f"No config found with id={config_id}"}), 404

        # Get drill from database
        drill = db.get_drill(name, config_id)
        if not drill:
            return jsonify({"error": f"Drill '{name}' not found"}), 404

        # Convert to dict format for DrillExecutor
        drill_dict = {
            "name": drill["name"],
            "description": drill.get("description", ""),
            "prompt": drill["prompt"],
            "scope": drill["scope"],
            "depends_on": drill.get("depends_on", []),
            "output_shape": drill["output_shape"],
            "always": drill["always"],
            "min_hits": drill["min_hits"],
            "keywords": drill["keywords"],
            "match": drill["match"],
            "category": drill.get("category"),
            "cooldown": drill["cooldown"],
            "detail_pass": drill["detail_pass"],
        }

        payload = request.get_json(force=True, silent=True) or {}
        sample_text = payload.get("sample_text") or ""

        if not sample_text.strip():
            return jsonify({"error": "sample_text is required"}), 400

        try:
            executor = DrillExecutor(
                backend=payload.get("backend") or "claude-opus-4-5-20251101",
                detail_backend=payload.get("detail_backend"),
                options=payload.get("options") or {},
            )

            # Create minimal chunk dict for test
            chunk = {
                "id": "test_chunk",
                "text": sample_text,
                "metadata": {},
            }

            result = executor.execute_drill(drill_dict, chunk)

            return jsonify({
                "drill": name,
                "result": result,
                "sample_text": sample_text[:200] + "..." if len(sample_text) > 200 else sample_text,
            })

        except Exception as e:
            return jsonify({"error": f"Test failed: {str(e)}"}), 500

    @bp.route("/drills/export", methods=["GET"])
    def export_drills():
        """Export all drills visible to a config (global + local)."""
        config_id = request.args.get("config_id")
        if not config_id:
            return jsonify({"error": "config_id query parameter is required"}), 400

        db = get_db()

        # Verify config exists
        if not db.get_analysis_config(config_id):
            return jsonify({"error": f"No config found with id={config_id}"}), 404

        # Get all drills from database
        drills = db.list_drills_for_config(config_id)

        return jsonify({
            "drills": drills,
            "meta": {
                "config_id": config_id,
                "count": len(drills),
                "format_version": "2.0",
                "note": "Includes both global and local drills. 'is_local' field indicates scope."
            }
        })

    @bp.route("/drills/import", methods=["POST"])
    def import_drills():
        """Import drills from JSON.

        Creates drills in database. Defaults to local (is_local=true) for safety.
        Reports created and failed drills.
        """
        config_id = request.args.get("config_id")
        if not config_id:
            return jsonify({"error": "config_id query parameter is required"}), 400

        db = get_db()

        # Verify config exists
        if not db.get_analysis_config(config_id):
            return jsonify({"error": f"No config found with id={config_id}"}), 404

        payload = request.get_json(force=True, silent=True) or {}
        new_drills = payload.get("drills") or []

        if not isinstance(new_drills, list):
            return jsonify({"error": "drills must be a list"}), 400

        created_ids = []
        errors = []

        try:
            for drill_spec in new_drills:
                try:
                    if not drill_spec.get("name"):
                        errors.append({"name": "unknown", "error": "name is required"})
                        continue

                    if not drill_spec.get("prompt"):
                        errors.append({"name": drill_spec.get("name"), "error": "prompt is required"})
                        continue

                    # Default to local for imported drills (is_local defaults to true)
                    is_local = bool(drill_spec.get("is_local", True))
                    drill_config_id = config_id if is_local else None

                    drill_id = db.create_drill(
                        name=drill_spec["name"],
                        prompt=drill_spec["prompt"],
                        config_id=drill_config_id,
                        description=drill_spec.get("description", ""),
                        scope=drill_spec.get("scope", "chunks"),
                        output_shape=drill_spec.get("output_shape", "span"),
                        category=drill_spec.get("category"),
                        always=bool(drill_spec.get("always", False)),
                        min_hits=int(drill_spec.get("min_hits", 0)),
                        keywords=drill_spec.get("keywords", []),
                        match=drill_spec.get("match", []),
                        cooldown=int(drill_spec.get("cooldown", 0)),
                        detail_pass=drill_spec.get("detail_pass", {}),
                        depends_on=drill_spec.get("depends_on", [])
                    )
                    created_ids.append(drill_id)

                except Exception as e:
                    errors.append({
                        "name": drill_spec.get("name", "unknown"),
                        "error": str(e)
                    })

            if db.conn:
                db.conn.commit()

            return jsonify({
                "created": len(created_ids),
                "created_ids": created_ids,
                "errors": errors,
                "warning": "Imported drills default to local (is_local=true). Set is_local=false in payload to create global drills."
            })

        except Exception as e:
            if db.conn:
                db.conn.rollback()
            return jsonify({"error": f"Import failed: {str(e)}"}), 500

    @bp.route("/drills/dry-run", methods=["POST"])
    def dry_run():
        """Dry-run: validate drill DAG and optionally execute against sample data.

        Does NOT save results. Useful for testing drill configurations.
        """
        config_id = request.args.get("config_id")
        if not config_id:
            return jsonify({"error": "config_id query parameter is required"}), 400

        db = get_db()

        # Verify config exists
        if not db.get_analysis_config(config_id):
            return jsonify({"error": f"No config found with id={config_id}"}), 404

        payload = request.get_json(force=True, silent=True) or {}

        # Load all drills for this config from database
        all_drills = db.list_drills_for_config(config_id)

        # Convert to dict format for validation
        drill_dicts = [
            {
                "name": d["name"],
                "description": d.get("description", ""),
                "prompt": d["prompt"],
                "scope": d["scope"],
                "depends_on": d.get("depends_on", []),
                "output_shape": d["output_shape"],
                "always": d["always"],
                "min_hits": d["min_hits"],
                "keywords": d["keywords"],
                "match": d["match"],
                "category": d.get("category"),
                "cooldown": d["cooldown"],
                "detail_pass": d["detail_pass"],
            }
            for d in all_drills
        ]

        # Validate DAG
        valid, err = _validate_drill_graph(drill_dicts)
        if not err:
            return jsonify({
                "status": "valid",
                "message": "Drill dependency graph is valid",
                "drill_count": len(drill_dicts),
                "drills": [d["name"] for d in drill_dicts],
            })
        else:
            return jsonify({
                "status": "invalid",
                "message": err,
                "drill_count": len(drill_dicts),
                "drills": [d["name"] for d in drill_dicts],
            }), 400

    return bp
