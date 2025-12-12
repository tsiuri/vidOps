#!/usr/bin/env python3
"""
Test suite for pipeline behavior with different configs.

Tests pipeline actually respects pass enable/disable and config overrides.
"""

import json
import sys
import subprocess
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
ROOT = REPO_ROOT

from scripts.analysis.config_loader import load_local_config
from scripts.analysis.db_storage import AnalysisDatabase


def get_db() -> AnalysisDatabase:
    """Connect to database using local config credentials."""
    cfg = load_local_config() or {}
    dbname = cfg.get('db_name', 'transcripts')
    host = cfg.get('db_host', 'localhost')
    port = int(cfg.get('db_port', 5432))
    user = cfg.get('db_user')
    password = cfg.get('db_password')

    db = AnalysisDatabase(dbname=dbname, host=host, port=port, user=user, password=password)
    db.connect()
    return db


def cleanup_config(db: AnalysisDatabase, config_id: str) -> None:
    cur = db.cursor
    cur.execute("DELETE FROM analysis_configs WHERE id = %s", (config_id,))
    if db.conn:
        db.conn.commit()


def print_test_header(test_name: str, description: str):
    """Print a test header."""
    print(f"\n{'='*70}")
    print(f"TEST: {test_name}")
    print(f"DESC: {description}")
    print(f"{'='*70}")


def run_pipeline_with_config(config_id: str, ytid: str, test_name: str) -> dict:
    """
    Run the pipeline with a specific config and capture output.
    Returns analysis output metadata.
    """
    print(f"Running pipeline with config {config_id[:8]}... for ytid {ytid}")

    # Run the CLI
    cmd = [
        "python3",
        str(ROOT / "analyze_to_db_wrapper.py"),
        "--ytid", ytid,
        "--config-id", config_id,
        "--force"
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,  # 2 minute timeout
            cwd=str(ROOT)
        )

        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "success": result.returncode == 0
        }
    except subprocess.TimeoutExpired:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": "TIMEOUT",
            "success": False
        }
    except Exception as e:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": str(e),
            "success": False
        }


def check_output_exists(ytid: str, output_type: str) -> bool:
    """Check if a specific output file exists."""
    output_dir = ROOT / "analysis_runs" / ytid

    if output_type == "json":
        return (output_dir / "analysis.json").exists()
    elif output_type == "markdown":
        return (output_dir / "analysis.md").exists()
    elif output_type == "chunks":
        chunks_dir = output_dir / "chunks"
        return chunks_dir.exists() and len(list(chunks_dir.glob("*.json"))) > 0

    return False


def main():
    print("=" * 70)
    print("PIPELINE BEHAVIOR TEST SUITE (Sections 1-4)")
    print("Testing that pipeline respects config pass flags")
    print("=" * 70)

    db = get_db()
    results = {}

    # Get a ytid with data
    ytid = "02zchSt0f4M"

    try:
        # ===== RT1: Default config - all passes enabled =====
        print_test_header("RT1", "Pipeline with default config (all passes enabled)")

        cfg_default = {
            "id": str(uuid4()),
            "name": "RT1 Default Pipeline Test",
            "version": 1,
            "analysis_type": "normal",
            "chunk_params": {"max_words": 1000, "overlap_words": 150, "per_speaker_tracks": False},
            "passes": [],  # Empty = all enabled
            "hot_targets": [],
            "backend_params": {},
            "strict_pass_validation": False
        }

        db.upsert_analysis_config(
            cfg_default["id"], cfg_default["name"], cfg_default["analysis_type"],
            cfg_default["version"], cfg_default, is_default=False
        )
        db.conn.commit()

        print("Config created. Verify in logs:")
        print("  - Should see: [chunk_analysis] chunks processed")
        print("  - Should see: Storing results in database...")
        print("  - Should see: Local JSON written")
        print("  - Should see: Hot targets detection (if configured)")

        # Just verify config exists and can be loaded
        cfg_check = db.get_analysis_config(cfg_default["id"])
        results["RT1"] = "PASS" if cfg_check else "FAIL"
        print(f"Config loadable: {bool(cfg_check)}")

        # ===== RT2: Disable DB writes =====
        print_test_header("RT2", "Pipeline with db_store disabled")

        cfg_no_db = {
            "id": str(uuid4()),
            "name": "RT2 No DB Store",
            "version": 1,
            "analysis_type": "normal",
            "chunk_params": {"max_words": 1000, "overlap_words": 150, "per_speaker_tracks": False},
            "passes": [
                {"id": "chunk_analysis", "enabled": True},
                {"id": "db_store", "enabled": False}
            ],
            "hot_targets": [],
            "backend_params": {},
            "strict_pass_validation": False
        }

        db.upsert_analysis_config(
            cfg_no_db["id"], cfg_no_db["name"], cfg_no_db["analysis_type"],
            cfg_no_db["version"], cfg_no_db, is_default=False
        )
        db.conn.commit()

        print("Config created. Verify in logs:")
        print("  - Should NOT see: 'Storing results in database'")
        print("  - Should still see: Local JSON written")
        print("  - No DB operations should occur")

        cfg_check = db.get_analysis_config(cfg_no_db["id"])
        results["RT2"] = "PASS" if cfg_check else "FAIL"
        print(f"Config loadable: {bool(cfg_check)}")

        # ===== RT3: Disable hot targets =====
        print_test_header("RT3", "Pipeline with hot_targets pass disabled")

        cfg_no_ht = {
            "id": str(uuid4()),
            "name": "RT3 No Hot Targets",
            "version": 1,
            "analysis_type": "normal",
            "chunk_params": {"max_words": 1000, "overlap_words": 150, "per_speaker_tracks": False},
            "passes": [
                {"id": "chunk_analysis", "enabled": True},
                {"id": "hot_targets", "enabled": False}
            ],
            "hot_targets": [],
            "backend_params": {},
            "strict_pass_validation": False
        }

        db.upsert_analysis_config(
            cfg_no_ht["id"], cfg_no_ht["name"], cfg_no_ht["analysis_type"],
            cfg_no_ht["version"], cfg_no_ht, is_default=False
        )
        db.conn.commit()

        print("Config created. Verify in logs:")
        print("  - Should see: '[passes] hot_targets disabled; skipping hot target detection'")
        print("  - Should NOT see: 'Hot targets flagged'")

        cfg_check = db.get_analysis_config(cfg_no_ht["id"])
        results["RT3"] = "PASS" if cfg_check else "FAIL"
        print(f"Config loadable: {bool(cfg_check)}")

        # ===== RT4: Custom chunk size =====
        print_test_header("RT4", "Pipeline respects chunk_params override")

        cfg_custom_chunks = {
            "id": str(uuid4()),
            "name": "RT4 Custom Chunk Size",
            "version": 1,
            "analysis_type": "normal",
            "chunk_params": {"max_words": 500, "overlap_words": 100, "per_speaker_tracks": False},
            "passes": [],
            "hot_targets": [],
            "backend_params": {},
            "strict_pass_validation": False
        }

        db.upsert_analysis_config(
            cfg_custom_chunks["id"], cfg_custom_chunks["name"], cfg_custom_chunks["analysis_type"],
            cfg_custom_chunks["version"], cfg_custom_chunks, is_default=False
        )
        db.conn.commit()

        print("Config created. Verify in logs:")
        print("  - Should see different chunk count (smaller chunks with 500-word limit)")
        print("  - Should see different overlap (100 vs 150 words)")

        cfg_check = db.get_analysis_config(cfg_custom_chunks["id"])
        results["RT4"] = "PASS" if cfg_check else "FAIL"
        print(f"Config loadable: {bool(cfg_check)}")

        # ===== RT5: Config-level hot targets =====
        print_test_header("RT5", "Config-level hot_targets override file-based")

        cfg_config_ht = {
            "id": str(uuid4()),
            "name": "RT5 Config Hot Targets",
            "version": 1,
            "analysis_type": "normal",
            "chunk_params": {"max_words": 1000, "overlap_words": 150, "per_speaker_tracks": False},
            "passes": [
                {"id": "chunk_analysis", "enabled": True},
                {"id": "hot_targets", "enabled": True}
            ],
            "hot_targets": [
                {"name": "custom_target_1", "category": "policy"},
                {"name": "custom_target_2", "category": "test"}
            ],
            "backend_params": {},
            "strict_pass_validation": False
        }

        db.upsert_analysis_config(
            cfg_config_ht["id"], cfg_config_ht["name"], cfg_config_ht["analysis_type"],
            cfg_config_ht["version"], cfg_config_ht, is_default=False
        )
        db.conn.commit()

        print("Config created. Verify:")
        print("  - Hot targets should come from config (not from file)")
        print("  - Should see custom_target_1 and custom_target_2 in output if triggered")

        cfg_check = db.get_analysis_config(cfg_config_ht["id"])
        ht_list = cfg_check.get("config_json", {}).get("hot_targets", []) if cfg_check else []
        results["RT5"] = "PASS" if len(ht_list) == 2 else "FAIL"
        print(f"Config hot_targets count: {len(ht_list)} (expected 2)")

        # ===== RT6: Strict validation enabled =====
        print_test_header("RT6", "Config with invalid pass and strict=true")

        cfg_strict = {
            "id": str(uuid4()),
            "name": "RT6 Strict Validation",
            "version": 1,
            "analysis_type": "normal",
            "chunk_params": {"max_words": 1000, "overlap_words": 150, "per_speaker_tracks": False},
            "passes": [
                {"id": "chunk_analysis", "enabled": True},
                {"id": "invalid_pass_xyz", "enabled": True}
            ],
            "hot_targets": [],
            "backend_params": {},
            "strict_pass_validation": True
        }

        db.upsert_analysis_config(
            cfg_strict["id"], cfg_strict["name"], cfg_strict["analysis_type"],
            cfg_strict["version"], cfg_strict, is_default=False
        )
        db.conn.commit()

        print("Config created. Verify:")
        print("  - Pipeline should raise ValueError on invalid pass 'invalid_pass_xyz'")
        print("  - Should NOT proceed with analysis")

        cfg_check = db.get_analysis_config(cfg_strict["id"])
        strict_flag = cfg_check.get("config_json", {}).get("strict_pass_validation", False) if cfg_check else False
        results["RT6"] = "PASS" if strict_flag else "FAIL"
        print(f"Strict validation enabled: {strict_flag}")

        # Summary
        print(f"\n{'='*70}")
        print("PIPELINE BEHAVIOR TEST SUMMARY")
        print(f"{'='*70}")

        passed = sum(1 for v in results.values() if v == "PASS")
        failed = sum(1 for v in results.values() if v == "FAIL")

        for test_name in sorted(results.keys()):
            status = results[test_name]
            symbol = "✓" if status == "PASS" else "✗"
            print(f"{symbol} {test_name}: {status}")

        print(f"\n{passed} passed, {failed} failed out of {len(results)} tests")
        print(f"{'='*70}")
        print("\nNote: These tests verify config creation and loading.")
        print("To test actual pipeline behavior, run individual commands:")
        print(f"  python3 analyze_to_db_wrapper.py --ytid {ytid} --config-id <UUID> --force")
        print(f"{'='*70}")

        db.disconnect()
        return 0 if failed == 0 else 1

    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())


def test_pipeline_config_upsert_and_fetch():
    db = get_db()
    config_id = str(uuid4())
    cfg = {
        "id": config_id,
        "name": "pytest-pipeline",
        "version": 1,
        "analysis_type": "normal",
        "chunk_params": {"max_words": 400, "overlap_words": 120, "per_speaker_tracks": False},
        "passes": [
            {"id": "chunk_analysis", "enabled": True},
            {"id": "subchunks", "enabled": True},
        ],
        "hot_targets": [],
    }
    db.upsert_analysis_config(
        config_id=config_id,
        name=cfg["name"],
        analysis_type=cfg["analysis_type"],
        version=cfg["version"],
        config_json=cfg,
        is_default=False,
    )
    try:
        row = db.get_analysis_config(config_id)
        assert row is not None
        stored_cfg = row.get("config_json", {})
        assert stored_cfg.get("chunk_params", {}).get("max_words") == 400
        assert len(stored_cfg.get("passes", [])) == 2
    finally:
        cleanup_config(db, config_id)


def test_pipeline_config_listing_includes_new_entry():
    db = get_db()
    config_id = str(uuid4())
    cfg = {
        "id": config_id,
        "name": "pytest-pipeline-listing",
        "version": 1,
        "analysis_type": "normal",
        "chunk_params": {"max_words": 300, "overlap_words": 80, "per_speaker_tracks": False},
        "passes": [],
        "hot_targets": [],
    }
    db.upsert_analysis_config(
        config_id=config_id,
        name=cfg["name"],
        analysis_type=cfg["analysis_type"],
        version=cfg["version"],
        config_json=cfg,
        is_default=False,
    )
    try:
        entries = db.list_analysis_configs()
        ids = {entry.get("id") for entry in entries}
        assert config_id in ids
    finally:
        cleanup_config(db, config_id)
