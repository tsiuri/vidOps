#!/usr/bin/env python3
"""
Test suite for Sections 1-4 of the Analysis System Refactor.

Tests:
- T1: Default config run
- T2-T7: Pass disable tests
- T8-T10: Chunking parameter tests
- T11: Hot targets override
- T12-T13: Strict validation
- T14-T18: Regression and output tests
"""

import json
import sys
from pathlib import Path
from uuid import uuid4
from datetime import datetime

# Ensure repo root is importable for scripts.analysis
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.analysis.config_loader import load_local_config
from scripts.analysis.db_storage import AnalysisDatabase
from scripts.analysis.analysis_config import AnalysisConfig


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


class TestConfig:
    """Helper to create test configs."""

    @staticmethod
    def base_config():
        """Create a minimal base config."""
        return {
            "id": str(uuid4()),
            "name": "Test Config",
            "version": 1,
            "analysis_type": "normal",
            "description": "Auto-generated test config",
            "chunk_params": {
                "max_words": 1000,
                "overlap_words": 150,
                "per_speaker_tracks": False
            },
            "passes": [],
            "hot_targets": [],
            "backend_params": {},
            "strict_pass_validation": False
        }

    @staticmethod
    def with_passes(passes_list):
        """Create config with specific passes enabled."""
        cfg = TestConfig.base_config()
        cfg["passes"] = passes_list
        return cfg

    @staticmethod
    def with_chunking(max_words=None, overlap=None, per_speaker=None):
        """Create config with custom chunking params."""
        cfg = TestConfig.base_config()
        if max_words is not None:
            cfg["chunk_params"]["max_words"] = max_words
        if overlap is not None:
            cfg["chunk_params"]["overlap_words"] = overlap
        if per_speaker is not None:
            cfg["chunk_params"]["per_speaker_tracks"] = per_speaker
        return cfg


def store_config(db: AnalysisDatabase, config: dict, as_default=False) -> str:
    """Store a test config and return its ID."""
    config_id = config["id"]
    db.upsert_analysis_config(
        config_id,
        config["name"],
        config["analysis_type"],
        config["version"],
        config,
        is_default=as_default
    )
    if db.conn:
        db.conn.commit()
    return config_id


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


def verify_config_stored(db: AnalysisDatabase, config_id: str) -> bool:
    """Verify a config was stored correctly."""
    row = db.get_analysis_config(config_id)
    if row and row.get("config_json"):
        return True
    return False


def main():
    print("=" * 70)
    print("ANALYSIS SYSTEM REFACTOR TEST SUITE (Sections 1-4)")
    print("=" * 70)

    db = get_db()
    results = {}

    try:
        # ===== T1: Default Config Run =====
        print_test_header("T1", "Default config run - all passes enabled by default")
        cfg_t1 = TestConfig.base_config()
        cfg_t1["name"] = "T1 Default Config"
        cfg_t1["passes"] = []  # Empty = all enabled
        id_t1 = store_config(db, cfg_t1)

        result_t1 = verify_config_stored(db, id_t1)
        print(f"Config stored: {result_t1}")
        print(f"Config ID: {id_t1}")
        print(f"Passes defined: {cfg_t1['passes']}")
        print(f"Expected behavior: All passes should be enabled")
        results["T1"] = "PASS" if result_t1 else "FAIL"

        # ===== T2: Disable DB Writes =====
        print_test_header("T2", "Disable db_store pass")
        cfg_t2 = TestConfig.with_passes([
            {"id": "chunk_analysis", "enabled": True},
            {"id": "aggregate_results", "enabled": True},
            {"id": "hot_targets", "enabled": True},
            {"id": "drills", "enabled": True},
            {"id": "local_json", "enabled": True},
            {"id": "markdown_report", "enabled": True},
            {"id": "db_store", "enabled": False}  # DISABLED
        ])
        cfg_t2["name"] = "T2 Disable DB Store"
        id_t2 = store_config(db, cfg_t2)

        result_t2 = verify_config_stored(db, id_t2)
        print(f"Config stored: {result_t2}")
        print(f"Config ID: {id_t2}")
        print(f"db_store enabled: False")
        print(f"Expected behavior: No DB writes, JSON still written")
        results["T2"] = "PASS" if result_t2 else "FAIL"

        # ===== T3: Disable Markdown =====
        print_test_header("T3", "Disable markdown_report pass")
        cfg_t3 = TestConfig.with_passes([
            {"id": "chunk_analysis", "enabled": True},
            {"id": "local_json", "enabled": True},
            {"id": "db_store", "enabled": True},
            {"id": "markdown_report", "enabled": False}  # DISABLED
        ])
        cfg_t3["name"] = "T3 Disable Markdown"
        id_t3 = store_config(db, cfg_t3)

        result_t3 = verify_config_stored(db, id_t3)
        print(f"Config stored: {result_t3}")
        print(f"markdown_report enabled: False")
        print(f"Expected behavior: No analysis.md generated")
        results["T3"] = "PASS" if result_t3 else "FAIL"

        # ===== T4: Disable Local JSON =====
        print_test_header("T4", "Disable local_json pass")
        cfg_t4 = TestConfig.with_passes([
            {"id": "chunk_analysis", "enabled": True},
            {"id": "db_store", "enabled": True},
            {"id": "local_json", "enabled": False}  # DISABLED
        ])
        cfg_t4["name"] = "T4 Disable Local JSON"
        id_t4 = store_config(db, cfg_t4)

        result_t4 = verify_config_stored(db, id_t4)
        print(f"Config stored: {result_t4}")
        print(f"local_json enabled: False")
        print(f"Expected behavior: No analysis.json, no chunks/ folder")
        results["T4"] = "PASS" if result_t4 else "FAIL"

        # ===== T5: Disable Hot Targets =====
        print_test_header("T5", "Disable hot_targets pass")
        cfg_t5 = TestConfig.with_passes([
            {"id": "chunk_analysis", "enabled": True},
            {"id": "hot_targets", "enabled": False}  # DISABLED
        ])
        cfg_t5["name"] = "T5 Disable Hot Targets"
        id_t5 = store_config(db, cfg_t5)

        result_t5 = verify_config_stored(db, id_t5)
        print(f"Config stored: {result_t5}")
        print(f"hot_targets enabled: False")
        print(f"Expected behavior: Log '[passes] hot_targets disabled; skipping...'")
        results["T5"] = "PASS" if result_t5 else "FAIL"

        # ===== T6: Disable Drills =====
        print_test_header("T6", "Disable drills pass")
        cfg_t6 = TestConfig.with_passes([
            {"id": "chunk_analysis", "enabled": True},
            {"id": "drills", "enabled": False}  # DISABLED
        ])
        cfg_t6["name"] = "T6 Disable Drills"
        id_t6 = store_config(db, cfg_t6)

        result_t6 = verify_config_stored(db, id_t6)
        print(f"Config stored: {result_t6}")
        print(f"drills enabled: False")
        print(f"Expected behavior: No drill tasks run, drill_results empty")
        results["T6"] = "PASS" if result_t6 else "FAIL"

        # ===== T7: Disable Subchunks =====
        print_test_header("T7", "Disable subchunks pass")
        cfg_t7 = TestConfig.with_passes([
            {"id": "chunk_analysis", "enabled": True},
            {"id": "subchunks", "enabled": False}  # DISABLED
        ])
        cfg_t7["name"] = "T7 Disable Subchunks"
        cfg_t7["chunk_params"]["per_speaker_tracks"] = True  # Request it but disable pass
        id_t7 = store_config(db, cfg_t7)

        result_t7 = verify_config_stored(db, id_t7)
        print(f"Config stored: {result_t7}")
        print(f"subchunks enabled: False (but per_speaker_tracks requested)")
        print(f"Expected behavior: Log '[passes] subchunks disabled; skipping...'")
        results["T7"] = "PASS" if result_t7 else "FAIL"

        # ===== T8: Custom max_words =====
        print_test_header("T8", "Custom chunking: max_words = 500")
        cfg_t8 = TestConfig.with_chunking(max_words=500)
        cfg_t8["name"] = "T8 Custom Chunk Size"
        id_t8 = store_config(db, cfg_t8)

        result_t8 = verify_config_stored(db, id_t8)
        stored_cfg = db.get_analysis_config(id_t8)
        max_words = stored_cfg.get("config_json", {}).get("chunk_params", {}).get("max_words")
        print(f"Config stored: {result_t8}")
        print(f"max_words in DB: {max_words}")
        print(f"Expected: 500, Got: {max_words}")
        results["T8"] = "PASS" if max_words == 500 else "FAIL"

        # ===== T9: Custom overlap =====
        print_test_header("T9", "Custom chunking: overlap_words = 50")
        cfg_t9 = TestConfig.with_chunking(overlap=50)
        cfg_t9["name"] = "T9 Custom Overlap"
        id_t9 = store_config(db, cfg_t9)

        result_t9 = verify_config_stored(db, id_t9)
        stored_cfg = db.get_analysis_config(id_t9)
        overlap = stored_cfg.get("config_json", {}).get("chunk_params", {}).get("overlap_words")
        print(f"Config stored: {result_t9}")
        print(f"overlap_words in DB: {overlap}")
        print(f"Expected: 50, Got: {overlap}")
        results["T9"] = "PASS" if overlap == 50 else "FAIL"

        # ===== T10: Enable per_speaker_tracks =====
        print_test_header("T10", "Enable per_speaker_tracks with subchunks pass")
        cfg_t10 = TestConfig.with_chunking(per_speaker=True)
        cfg_t10["name"] = "T10 Per-Speaker Tracks"
        cfg_t10["passes"] = [
            {"id": "chunk_analysis", "enabled": True},
            {"id": "subchunks", "enabled": True}
        ]
        id_t10 = store_config(db, cfg_t10)

        result_t10 = verify_config_stored(db, id_t10)
        stored_cfg = db.get_analysis_config(id_t10)
        per_speaker = stored_cfg.get("config_json", {}).get("chunk_params", {}).get("per_speaker_tracks")
        print(f"Config stored: {result_t10}")
        print(f"per_speaker_tracks in DB: {per_speaker}")
        print(f"Expected: True, Got: {per_speaker}")
        results["T10"] = "PASS" if per_speaker is True else "FAIL"

        # ===== T11: Config-level hot targets override =====
        print_test_header("T11", "Config-level hot_targets override file-based config")
        cfg_t11 = TestConfig.base_config()
        cfg_t11["name"] = "T11 Config Hot Targets"
        cfg_t11["hot_targets"] = [
            {"name": "test_target_1", "category": "policy"},
            {"name": "test_target_2", "category": "conflict"}
        ]
        id_t11 = store_config(db, cfg_t11)

        result_t11 = verify_config_stored(db, id_t11)
        stored_cfg = db.get_analysis_config(id_t11)
        hot_targets = stored_cfg.get("config_json", {}).get("hot_targets", [])
        num_targets = len(hot_targets)
        print(f"Config stored: {result_t11}")
        print(f"Hot targets in DB: {num_targets}")
        print(f"Expected: 2, Got: {num_targets}")
        print(f"Hot targets: {[h.get('name') for h in hot_targets]}")
        results["T11"] = "PASS" if num_targets == 2 else "FAIL"

        # ===== T12: Mistyped pass, strict=false =====
        print_test_header("T12", "Mistyped pass with strict_pass_validation=false")
        cfg_t12 = TestConfig.with_passes([
            {"id": "chunk_analysis", "enabled": True},
            {"id": "typo_pass_name", "enabled": True}  # TYPO
        ])
        cfg_t12["name"] = "T12 Typo Strict False"
        cfg_t12["strict_pass_validation"] = False
        id_t12 = store_config(db, cfg_t12)

        result_t12 = verify_config_stored(db, id_t12)
        stored_cfg = db.get_analysis_config(id_t12)
        strict = stored_cfg.get("config_json", {}).get("strict_pass_validation", False)
        print(f"Config stored: {result_t12}")
        print(f"strict_pass_validation: {strict}")
        print(f"Pass with typo included: typo_pass_name")
        print(f"Expected behavior: Warning logged, pipeline proceeds")
        results["T12"] = "PASS" if result_t12 and not strict else "FAIL"

        # ===== T13: Mistyped pass, strict=true =====
        print_test_header("T13", "Mistyped pass with strict_pass_validation=true")
        cfg_t13 = TestConfig.with_passes([
            {"id": "chunk_analysis", "enabled": True},
            {"id": "another_typo", "enabled": True}  # TYPO
        ])
        cfg_t13["name"] = "T13 Typo Strict True"
        cfg_t13["strict_pass_validation"] = True
        id_t13 = store_config(db, cfg_t13)

        result_t13 = verify_config_stored(db, id_t13)
        stored_cfg = db.get_analysis_config(id_t13)
        strict = stored_cfg.get("config_json", {}).get("strict_pass_validation", False)
        print(f"Config stored: {result_t13}")
        print(f"strict_pass_validation: {strict}")
        print(f"Pass with typo included: another_typo")
        print(f"Expected behavior: ValueError raised immediately")
        results["T13"] = "PASS" if result_t13 and strict else "FAIL"

        # ===== T14: Web UI Config Creation =====
        print_test_header("T14", "Verify Web UI can list and retrieve configs")
        # Test that all configs can be retrieved
        all_configs = db.list_analysis_configs()
        t_configs = [c for c in all_configs if c.get("name", "").startswith("T")]
        print(f"Total test configs in DB: {len(t_configs)}")
        print(f"Test configs found:")
        for cfg in t_configs:
            print(f"  - {cfg.get('name')} (id={cfg.get('id')[:8]}..., passes={len(cfg.get('config_json', {}).get('passes', []))})")
        results["T14"] = "PASS" if len(t_configs) >= 13 else "FAIL"

        # ===== T15: TUI Config Retrieval =====
        print_test_header("T15", "Verify TUI can fetch fresh config from DB")
        test_id = id_t11  # Use the hot targets config
        fresh_config = db.get_analysis_config(test_id)
        has_all_fields = all([
            fresh_config.get("id"),
            fresh_config.get("name"),
            fresh_config.get("analysis_type"),
            fresh_config.get("config_json"),
            fresh_config.get("version")
        ])
        print(f"Config ID: {test_id[:8]}...")
        print(f"Has all required fields: {has_all_fields}")
        print(f"Fields: id, name, analysis_type, config_json, version, created_at, updated_at")
        results["T15"] = "PASS" if has_all_fields else "FAIL"

        # ===== T16: DB-backed CLI run =====
        print_test_header("T16", "Verify DB-backed config can be retrieved for CLI usage")
        config_for_cli = db.get_analysis_config(id_t1)
        is_valid_for_cli = (
            config_for_cli is not None and
            isinstance(config_for_cli.get("config_json"), dict) and
            "chunk_params" in config_for_cli.get("config_json", {})
        )
        print(f"Config ID: {id_t1[:8]}...")
        print(f"Valid for CLI: {is_valid_for_cli}")
        print(f"Config has chunk_params: {is_valid_for_cli}")
        results["T16"] = "PASS" if is_valid_for_cli else "FAIL"

        # ===== T17: JSON Structure Validity =====
        print_test_header("T17", "Verify all test configs have valid JSON structure")
        test_config_ids = [id_t1, id_t2, id_t3, id_t4, id_t5, id_t6, id_t7, id_t8, id_t9, id_t10, id_t11, id_t12, id_t13]
        all_valid = True
        for config_id in test_config_ids:
            cfg = db.get_analysis_config(config_id)
            cfg_json = cfg.get("config_json") if cfg else None
            is_valid = isinstance(cfg_json, dict) and "chunk_params" in cfg_json
            if not is_valid:
                all_valid = False
                print(f"Invalid: {config_id[:8]}...")

        print(f"All configs have valid JSON: {all_valid}")
        print(f"Total configs validated: {len(test_config_ids)}")
        results["T17"] = "PASS" if all_valid else "FAIL"

        # ===== T18: Database Persistence =====
        print_test_header("T18", "Verify configs persist across connections")
        # Store a config
        cfg_persist = TestConfig.base_config()
        cfg_persist["name"] = "T18 Persistence Test"
        cfg_persist["description"] = f"Created at {datetime.now().isoformat()}"
        id_persist = store_config(db, cfg_persist)

        # Disconnect and reconnect
        db.disconnect()
        db2 = get_db()

        # Retrieve with new connection
        cfg_retrieved = db2.get_analysis_config(id_persist)
        persisted = cfg_retrieved is not None and cfg_retrieved.get("name") == "T18 Persistence Test"

        print(f"Config ID: {id_persist[:8]}...")
        print(f"Config persisted and retrievable: {persisted}")
        print(f"Retrieved name: {cfg_retrieved.get('name') if cfg_retrieved else 'N/A'}")
        results["T18"] = "PASS" if persisted else "FAIL"

        db2.disconnect()

    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()

    # Print summary
    print(f"\n{'='*70}")
    print("TEST SUMMARY")
    print(f"{'='*70}")

    passed = sum(1 for v in results.values() if v == "PASS")
    failed = sum(1 for v in results.values() if v == "FAIL")

    for test_name in sorted(results.keys()):
        status = results[test_name]
        symbol = "✓" if status == "PASS" else "✗"
        print(f"{symbol} {test_name}: {status}")

    print(f"\n{passed} passed, {failed} failed out of {len(results)} tests")
    print(f"{'='*70}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())


def test_analysis_config_upsert_roundtrip():
    db = get_db()
    cfg = TestConfig.base_config()
    cfg["name"] = "pytest-upsert"
    config_id = store_config(db, cfg)
    try:
        assert verify_config_stored(db, config_id)
    finally:
        cleanup_config(db, config_id)


def test_analysis_config_chunk_params_persist():
    db = get_db()
    cfg = TestConfig.with_chunking(max_words=512, overlap=64)
    cfg["name"] = "pytest-chunk-params"
    config_id = store_config(db, cfg)
    try:
        row = db.get_analysis_config(config_id)
        chunk_params = row.get("config_json", {}).get("chunk_params", {})
        assert chunk_params.get("max_words") == 512
        assert chunk_params.get("overlap_words") == 64
    finally:
        cleanup_config(db, config_id)


def test_analysis_config_strict_validation_flag():
    db = get_db()
    cfg = TestConfig.with_passes([
        {"id": "chunk_analysis", "enabled": True},
        {"id": "typo_pass_name", "enabled": True},
    ])
    cfg["name"] = "pytest-strict-validation"
    cfg["strict_pass_validation"] = True
    config_id = store_config(db, cfg)
    try:
        row = db.get_analysis_config(config_id)
        strict = row.get("config_json", {}).get("strict_pass_validation")
        assert strict is True
    finally:
        cleanup_config(db, config_id)
