#!/usr/bin/env python3
"""
Database-integrated transcript analyzer using the modular pipeline.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .analysis_pipeline import PipelineSettings, TranscriptAnalysisPipeline
from .config_loader import load_hot_targets, load_local_config, log_effective_config
from .drills import load_drills
from .db_storage import AnalysisDatabase
from .analysis_config import AnalysisConfig, load_config_from_file, default_config


def load_ytids_from_file(path: Path) -> List[str]:
    """Read ytids (one per line, comments allowed) from a text file."""
    ytids: List[str] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            ytids.append(s.split()[0])
    except Exception as exc:
        print(f"Warning: failed reading ytids from {path}: {exc}", file=sys.stderr)
    return ytids


def load_json_config(path_val: Any) -> Optional[Dict[str, Any]]:
    """Load JSON config if path is provided/existing; return None on failure/missing."""
    if not path_val:
        return None
    try:
        p = Path(path_val)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def fmt_time_vtt(seconds: float) -> str:
    """Format seconds as VTT timestamp."""
    if seconds < 0:
        seconds = 0.0
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis == 1000:
        secs += 1
        millis = 0
    return f"{hrs:02d}:{mins:02d}:{secs:02d}.{millis:03d}"


def detok(words: List[str]) -> str:
    """Minimal detokenization used when reconstructing VTT lines."""
    if not words:
        return ""
    s = " ".join(w.strip() for w in words if w and str(w).strip())
    for p in [" .", " ,", " ;", " :", " !", " ?"]:
        s = s.replace(p, p[1:])
    s = s.replace("( ", "(").replace(" )", ")")
    s = s.replace("[ ", "[").replace(" ]", "]")
    s = s.replace("{ ", "{").replace(" }", "}")
    return s.strip()


def group_words(rows: Sequence[Tuple[int, str, float, float]], gap: float):
    """Group consecutive words into caption lines based on a time gap."""
    group: List[str] = []
    group_start: Optional[float] = None
    group_end: Optional[float] = None
    last_end: Optional[float] = None
    for _idx, word, start, end in rows:
        s = float(start) if start is not None else 0.0
        e = float(end) if end is not None else s
        if last_end is None or (s - last_end) >= gap:
            if group:
                yield (
                    group_start if group_start is not None else s,
                    group_end if group_end is not None else e,
                    detok(group),
                )
            group = [word]
            group_start = s
            group_end = e
        else:
            group.append(word)
            group_end = e
        last_end = e
    if group:
        yield (
            group_start if group_start is not None else 0.0,
            group_end if group_end is not None else (group_start or 0.0),
            detok(group),
        )


def fetch_video_metadata(conn, ytid: str) -> Optional[Dict[str, Optional[str]]]:
    """Pull basic video metadata from the videos table if available."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT title, title_date, upload_date FROM videos WHERE ytid=%s LIMIT 1",
                (ytid,),
            )
            row = cur.fetchone()
            if not row:
                return None
            title, title_date, upload_date = row
            date_val = title_date or upload_date
            if date_val is not None:
                date_val = str(date_val)
            return {"title": title or ytid, "date": date_val}
    except Exception:
        return None


def export_vtt_from_db(
    conn,
    ytid: str,
    out_dir: Path,
    gap_sec: float = 1.0,
    speaker_name: Optional[str] = None,
    restrict_to_diarized: bool = False,
) -> Optional[Path]:
    """
    Reconstruct a VTT from the words table (or speaker_words view when speaker_name set).
    Returns the path to the written VTT, or None if no words were found.
    """
    try:
        with conn.cursor() as cur:
            if speaker_name:
                cur.execute(
                    """
                    SELECT idx, word, start_sec, end_sec
                    FROM speaker_words
                    WHERE ytid=%s AND speaker_name=%s
                    ORDER BY start_sec, idx
                    """,
                    (ytid, speaker_name),
                )
            elif restrict_to_diarized:
                cur.execute(
                    """
                    SELECT idx, word, start_sec, end_sec
                    FROM words w
                    WHERE ytid=%s
                      AND EXISTS (
                        SELECT 1 FROM diarized_timestamps dt
                        WHERE dt.ytid = w.ytid
                          AND w.start_sec >= dt.start_sec
                          AND w.start_sec < dt.end_sec
                      )
                    ORDER BY start_sec, idx
                    """,
                    (ytid,),
                )
            else:
                cur.execute(
                    "SELECT idx, word, start_sec, end_sec FROM words WHERE ytid=%s ORDER BY start_sec, idx",
                    (ytid,),
                )
            rows = cur.fetchall()
    except Exception as exc:
        print(f"Error: failed to fetch words for {ytid}: {exc}", file=sys.stderr)
        return None

    if not rows:
        source_msg = "speaker" if speaker_name else ("diarized window" if restrict_to_diarized else "DB")
        print(f"Warning: no words found in {source_msg} for {ytid}; skipping DB export.", file=sys.stderr)
        return None

    groups = list(group_words(rows, gap_sec))
    if not groups:
        print(f"Warning: no caption groups produced for {ytid}; skipping.", file=sys.stderr)
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    if speaker_name:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", speaker_name.strip()) or "speaker"
        vtt_path = out_dir / f"{ytid}__speaker_{safe}.vtt"
    elif restrict_to_diarized:
        vtt_path = out_dir / f"{ytid}__diarized.vtt"
    else:
        vtt_path = out_dir / f"{ytid}__from_db.vtt"
    with open(vtt_path, "w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n")
        cue_id = 1
        for start, end, text in groups:
            f.write(f"{cue_id}\n")
            f.write(f"{fmt_time_vtt(start)} --> {fmt_time_vtt(end)}\n{text}\n\n")
            cue_id += 1
    return vtt_path


def write_info_json(out_path: Path, ytid: str, meta: Optional[Dict[str, Optional[str]]]) -> None:
    """Write a minimal info.json next to the generated VTT to improve metadata extraction."""
    payload = {"id": ytid}
    if meta:
        payload["title"] = meta.get("title") or ytid
        if meta.get("date"):
            payload["upload_date"] = meta["date"]
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        print(f"Warning: failed to write info JSON at {out_path}: {exc}", file=sys.stderr)


def fetch_ytids_from_query(conn, sql: str) -> List[str]:
    """Run a SQL query and return the first column as ytids (strings)."""
    ytids: List[str] = []
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            for row in cur.fetchall():
                if not row:
                    continue
                val = row[0]
                if val is None:
                    continue
                ytids.append(str(val).strip())
    except Exception as exc:
        print(f"Error: failed to fetch ytids from query: {exc}", file=sys.stderr)
    return ytids


def resolve_paths(paths: List[Path]) -> List[Path]:
    """Expand directories and globs into unique .vtt paths."""
    resolved: List[Path] = []
    for p in paths:
        if p.is_dir():
            vtts = sorted(list(p.glob("*.transcript.en.vtt")))
            if not vtts:
                vtts = sorted(list(p.glob("*.vtt")))
            resolved.extend(vtts)
        else:
            if any(ch in p.name for ch in ["*", "?", "["]):
                resolved.extend(sorted(m for m in p.parent.glob(p.name) if m.is_file()))
            elif p.exists() and p.is_file():
                resolved.append(p)
    seen = set()
    unique: List[Path] = []
    for x in resolved:
        if x not in seen:
            unique.append(x)
            seen.add(x)
    return unique


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze video transcripts with AI, persist to PostgreSQL, and cache local JSON outputs."
    )
    parser.add_argument("inputs", nargs="*", type=Path, help="Transcript files or directories (optional when using --ytid/--ytids-file)")
    parser.add_argument("--ytid", dest="ytids", action="append", help="Analyze by YouTube ID using words from the database (repeatable)")
    parser.add_argument("--ytids-file", type=Path, help="File containing ytids (one per line, # comments ok) for DB-driven analysis")
    parser.add_argument("--ytids-query", help="SQL query to fetch ytids directly from the database (first column is used)")
    parser.add_argument("--ytids-query-file", type=Path, help="File containing SQL query to fetch ytids")
    parser.add_argument("--db-gap-sec", type=float, default=1.0, help="Gap threshold in seconds when reconstructing VTT from DB words (default: 1.0)")
    parser.add_argument("--model", default="qwen2.5:7b-instruct", help="Ollama model to use")
    parser.add_argument("--chunk-size", type=int, default=1000, help="Words per chunk")
    parser.add_argument("--overlap", type=int, default=150, help="Overlap between chunks")
    parser.add_argument("--ollama-url", default="http://localhost:11434", help="Ollama API URL")
    parser.add_argument("--no-summaries", action="store_true", help="Skip generating summary variants")
    parser.add_argument(
        "--quality",
        choices=["fast", "balanced", "thorough"],
        default="thorough",
        help="Preset quality profile (default: thorough)",
    )
    parser.add_argument("--temperature", type=float, help="Sampling temperature")
    parser.add_argument("--top-p", type=float, dest="top_p", help="Top-p nucleus sampling")
    parser.add_argument("--top-k", type=int, dest="top_k", help="Top-k sampling")
    parser.add_argument("--num-predict", type=int, dest="num_predict", help="Max tokens to generate")
    parser.add_argument("--num-ctx", type=int, dest="num_ctx", help="Context window size (tokens)")
    parser.add_argument("--repeat-penalty", type=float, dest="repeat_penalty", help="Repetition penalty")
    # Batch and customization
    parser.add_argument("--batch-id", type=int, help="Optional batch number to tag this run (defaults to yyyymmddHHMMSS UTC)")
    parser.add_argument("--analysis-type", default="normal", help="Label for analysis type (default: normal)")
    # Analysis configuration selection
    parser.add_argument("--config-id", help="ID of AnalysisConfig to load from the database")
    parser.add_argument("--config-file", type=Path, help="Path to AnalysisConfig JSON file")
    parser.add_argument("--config-type", help="Analysis type to use when selecting a default config from the database")
    parser.add_argument("--config-version", type=int, help="Specific config version to use with --config-type")
    parser.add_argument("--request", dest="request", help="Additional instruction to include in LLM prompts (optional)")
    # Dual-endpoint options for chunk analysis
    parser.add_argument("--chunk-url-a", help="Primary endpoint for chunk analysis (defaults to --ollama-url)")
    parser.add_argument("--chunk-model-a", help="Primary model for chunk analysis (defaults to --model)")
    parser.add_argument("--chunk-url-b", help="Secondary endpoint for chunk analysis (optional)")
    parser.add_argument("--chunk-model-b", help="Secondary model for chunk analysis (optional)")
    parser.add_argument("--chunk-parallel", type=int, default=4, help="Parallel workers for chunk analysis (default: 4)")
    # Dual-pass / dual-endpoint options
    parser.add_argument("--speaker-url", help="Optional Ollama URL for speaker-focused second pass (defaults to --ollama-url)")
    parser.add_argument("--speaker-model", help="Optional model tag for speaker-focused second pass (defaults to --model)")
    parser.add_argument("--speaker-alias", default="Hasan", help="Alias to refer to the speaker in third-person rewrites (default: Hasan)")
    # Database options
    parser.add_argument("--db-name", default="transcripts", help="PostgreSQL database name")
    parser.add_argument("--db-host", default="localhost", help="PostgreSQL host")
    parser.add_argument("--db-port", type=int, default=5432, help="PostgreSQL port")
    parser.add_argument("--db-user", help="PostgreSQL user (default: system user)")
    parser.add_argument("--db-password", help="PostgreSQL password")
    # Processing options
    parser.add_argument("--force", action="store_true", help="Re-analyze even if already in database")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue processing other files if one fails")
    # Local output options
    parser.add_argument("--output-dir", type=Path, default=Path("analysis_runs"), help="Directory for local JSON outputs (default: analysis_runs/)")
    parser.add_argument("--output", dest="output_dir", type=Path, help=argparse.SUPPRESS)  # alias for compatibility
    parser.add_argument("--no-local-files", action="store_true", help="Skip writing local JSON outputs (default: write)")
    parser.add_argument("--include-chunk-text", action="store_true", help="Include raw chunk text in local JSON files")
    parser.add_argument("--write-markdown", action="store_true", help="Also write markdown reports alongside JSON")
    parser.add_argument("--save-json", action="store_true", help=argparse.SUPPRESS)  # legacy flag; now default behavior
    parser.add_argument(
        "--log-mode",
        choices=["quiet", "progress", "verbose"],
        help="Logging verbosity: quiet, progress (default; per-chunk completion), verbose (raw LLM responses)",
    )
    parser.add_argument(
        "--diarized",
        action="store_true",
        help="Tag this run as diarized in DB/metadata. No effect on text reconstruction (use --speaker-name or --restrict-to-diarized to actually filter).",
    )
    parser.add_argument(
        "--speaker-name",
        help="When set, rebuild transcript from diarized speaker_words for this speaker (DB-driven only). Implies --diarized.",
    )
    parser.add_argument(
        "--restrict-to-diarized",
        action="store_true",
        help="Include only words inside ANY diarized window (all speakers combined). Implies --diarized. Ignored if --speaker-name is set.",
    )
    parser.add_argument(
        "--transcription-machine",
        help="Host identifier for this run (e.g., laptop_razer); stored in DB metadata.",
    )
    parser.add_argument(
        "--drills-config",
        type=Path,
        help="Path to drills config JSON (defines drill passes). Defaults to config/drills.json if present.",
    )
    parser.add_argument(
        "--no-drills",
        action="store_true",
        help="Skip running drills even if a config is available.",
    )
    parser.add_argument(
        "--no-subchunks",
        action="store_true",
        help="Skip timing drill subchunk generation (VTT cue parsing).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    analysis_config: AnalysisConfig | None = None

    local_cfg = load_local_config()
    cfg_sections = local_cfg.get("__sections__", {}) if isinstance(local_cfg, dict) else {}

    def cfg_section(name: str) -> Dict[str, Any]:
        if isinstance(cfg_sections, dict) and name in cfg_sections and isinstance(cfg_sections[name], dict):
            return cfg_sections[name]
        val = local_cfg.get(name)
        return val if isinstance(val, dict) else {}

    def pick(keys: Sequence[Tuple[Optional[str], str]], fallback: Any = None) -> Any:
        """Pick the first non-empty config value from the provided section/key pairs."""
        for sec, key in keys:
            source = cfg_section(sec) if sec else local_cfg
            if isinstance(source, dict) and key in source and source[key] not in ("", None):
                return source[key]
        return fallback

    def resolve(current: Any, default: Any, keys: Sequence[Tuple[Optional[str], str]]) -> Any:
        if current not in (None, default):
            return current
        cfg_val = pick(keys, fallback=None)
        if cfg_val not in (None, ""):
            return cfg_val
        return default if current is None else current

    DEFAULT_DB_HOST = "localhost"
    DEFAULT_DB_PORT = 5432
    DEFAULT_DB_NAME = "transcripts"
    DEFAULT_OLLAMA_URL = "http://localhost:11434"
    DEFAULT_MODEL = "qwen2.5:7b-instruct"
    DEFAULT_CHUNK_SIZE = 1000
    DEFAULT_OVERLAP = 150
    DEFAULT_CHUNK_PARALLEL = 4
    DEFAULT_MERGE_GAP_SEC = 10.0

    args.db_host = resolve(args.db_host, DEFAULT_DB_HOST, [("shared", "db_host"), (None, "db_host")])
    args.db_port = resolve(args.db_port, DEFAULT_DB_PORT, [("shared", "db_port"), (None, "db_port")])
    args.db_name = resolve(args.db_name, DEFAULT_DB_NAME, [("shared", "db_name"), (None, "db_name")])
    args.db_user = resolve(args.db_user, None, [("shared", "db_user"), (None, "db_user")])
    args.db_password = resolve(args.db_password, None, [("shared", "db_password"), (None, "db_password")])
    args.ollama_url = resolve(
        args.ollama_url, DEFAULT_OLLAMA_URL, [("shared", "ollama_url"), ("llm", "ollama_url"), (None, "ollama_url")]
    )
    args.model = resolve(args.model, DEFAULT_MODEL, [("llm", "model"), ("shared", "model"), (None, "model")])
    args.chunk_size = resolve(args.chunk_size, DEFAULT_CHUNK_SIZE, [("chunking", "chunk_size"), (None, "chunk_size")])
    args.overlap = resolve(args.overlap, DEFAULT_OVERLAP, [("chunking", "overlap"), (None, "overlap")])
    args.chunk_parallel = resolve(
        args.chunk_parallel,
        DEFAULT_CHUNK_PARALLEL,
        [("chunking", "chunk_parallel"), ("llm", "chunk_parallel"), (None, "chunk_parallel")],
    )
    args.quality = resolve(args.quality, "thorough", [("llm", "quality"), ("shared", "quality"), (None, "quality")])
    merge_gap_sec = resolve(DEFAULT_MERGE_GAP_SEC, DEFAULT_MERGE_GAP_SEC, [("aggregation", "merge_gap_sec"), (None, "merge_gap_sec")])

    analyze_cfg = cfg_section("analyze_to_db.py vars") or cfg_section("analyze_to_db") or {}
    allowed_modes = {"quiet", "progress", "verbose"}
    log_mode = args.log_mode or analyze_cfg.get("logmode") or analyze_cfg.get("log_mode") or pick(
        [("llm", "log_mode"), ("shared", "log_mode")], fallback=None
    )
    log_mode = log_mode or "progress"
    if log_mode not in allowed_modes:
        log_mode = "progress"

    transcription_machine = args.transcription_machine or pick(
        [("shared", "transcription_machine"), (None, "transcription_machine")], fallback=None
    )
    category_cache_path = pick(
        [("ingestion", "category_cache_path"), ("validation", "category_cache_path"), ("shared", "category_cache_path")]
    )
    category_cache_writeback = bool(
        pick(
            [("ingestion", "category_cache_writeback"), ("shared", "category_cache_writeback")],
            fallback=True,
        )
    )
    category_map_path = pick(
        [("ingestion", "category_map_path"), ("validation", "category_map_path"), ("shared", "category_map_path")]
    )
    alias_map_path = pick([("validation", "alias_map_path"), ("shared", "alias_map_path"), (None, "alias_map_path")])
    hot_targets_path = pick(
        [
            ("aggregation", "hot_targets_config"),
            ("llm", "hot_targets_config"),
            ("shared", "hot_targets_config"),
            (None, "hot_targets_config"),
        ],
        fallback="config/hot_targets.json",
    )
    hot_targets = load_hot_targets(hot_targets_path)
    drills_path = pick(
        [
            ("llm", "drills_config"),
            ("shared", "drills_config"),
            ("drills", "config"),
            (None, "drills_config"),
        ],
        fallback=None,
    )
    # CLI override
    if getattr(args, "drills_config", None):
        drills_path = args.drills_config

    hot_target_scheduler_config = pick(
        [
            ("hot_targets", "scheduler"),
            ("llm", "hot_target_scheduler"),
            ("shared", "hot_target_scheduler"),
            (None, "hot_target_scheduler"),
        ],
        fallback=None,
    )
    drills_scheduler_config = pick(
        [
            ("drills", "scheduler"),
            ("llm", "drills_scheduler"),
            ("shared", "drills_scheduler"),
            (None, "drills_scheduler"),
        ],
        fallback=None,
    )
    gpu_scheduler_config = pick(
        [
            ("shared", "gpu_scheduler_config"),
            ("llm", "gpu_scheduler_config"),
            (None, "gpu_scheduler_config"),
        ],
        fallback=None,
    )
    gpu_scheduler_config_loaded = load_json_config(gpu_scheduler_config)
    if gpu_scheduler_config_loaded:
        gpu_scheduler_config = gpu_scheduler_config_loaded
    else:
        # Default to in-memory queue to avoid consuming stale tasks from a shared Postgres table.
        # Use a custom config (config.local.json -> shared.gpu_scheduler_config) to enable Postgres.
        gpu_scheduler_config = {
            "queue": {"backend": "memory"},
            "rate_limit": {"delay_between_targets": 0},
            "endpoints": [
                {
                    "name": "default",
                    "url": args.ollama_url,
                    "max_concurrency": 1,
                    "priority": "high",
                    "models": [args.model],
                }
            ],
        }

    effective_config = {
        "config_source": local_cfg.get("__source__"),
        "db": {
            "host": args.db_host,
            "port": args.db_port,
            "name": args.db_name,
            "user": args.db_user,
            "password": args.db_password,
        },
        "llm": {
            "model": args.model,
            "quality": args.quality,
            "ollama_url": args.ollama_url,
            "chunk_parallel": args.chunk_parallel,
        },
        "chunking": {"chunk_size": args.chunk_size, "overlap": args.overlap},
        "logging": {"mode": log_mode},
        "host": {"transcription_machine": transcription_machine},
        "paths": {
            "hot_targets_config": str(hot_targets_path) if hot_targets_path else None,
            "alias_map_path": alias_map_path,
            "category_cache_path": category_cache_path,
            "category_cache_writeback": category_cache_writeback,
            "category_map_path": category_map_path,
            "drills_config": str(drills_path) if drills_path else None,
        },
        "hot_targets_loaded": len(hot_targets),
        "hot_target_scheduler": bool(hot_target_scheduler_config),
        "drills_scheduler": bool(drills_scheduler_config),
        "gpu_scheduler_config": gpu_scheduler_config,
        "analysis_config": {
            "id": getattr(analysis_config, "id", None) if analysis_config is not None else None,
            "name": getattr(analysis_config, "name", None) if analysis_config is not None else None,
            "analysis_type": getattr(analysis_config, "analysis_type", None) if analysis_config is not None else None,
            "strict_pass_validation": bool(getattr(analysis_config, "strict_pass_validation", False)) if analysis_config is not None else False,
            "passes": [getattr(p, "id", None) for p in (getattr(analysis_config, "passes", []) or [])] if analysis_config is not None else [],
        },
        "drills_enabled": not args.no_drills and bool(drills_path),
        "drills_config": str(drills_path) if drills_path else None,
    }
    log_effective_config(
        "Effective configuration (CLI > config > default)", effective_config, redact_keys={"db_password", "password"}
    )
    if hot_targets_path:
        path_obj = Path(hot_targets_path)
        if hot_targets:
            print(f"Loaded {len(hot_targets)} hot target(s) from {path_obj}")
        else:
            print(f"Hot targets config {path_obj} missing or empty; no targeted detail passes will be flagged.")

    db_ytids: List[str] = args.ytids or []
    if args.ytids_file:
        db_ytids.extend(load_ytids_from_file(args.ytids_file))
    query_sql = None
    if args.ytids_query:
        query_sql = args.ytids_query
    elif args.ytids_query_file:
        try:
            query_sql = args.ytids_query_file.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"Error: could not read ytids query file: {exc}", file=sys.stderr)
            sys.exit(1)
    files = resolve_paths(args.inputs)

    if not files and not db_ytids and not query_sql:
        print("Error: No transcript inputs provided (files/dirs) and no ytids specified (direct or query).", file=sys.stderr)
        sys.exit(1)

    if args.batch_id is None:
        args.batch_id = int(datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))

    print(f"Batch ID: {args.batch_id} | Analysis type: {args.analysis_type} | Request: {args.request or 'none'}\n")

    try:
        db = AnalysisDatabase(
            dbname=args.db_name,
            host=args.db_host,
            port=args.db_port,
            user=args.db_user,
            password=args.db_password,
        )
        db.connect()
        print(f"✓ Connected to database: {args.db_name}@{args.db_host}\n")

        # Resolve analysis configuration (DB-backed or file-based)
        if args.config_file:
            analysis_config = load_config_from_file(args.config_file)
        elif args.config_id:
            row = db.get_analysis_config(args.config_id)
            if row and row.get("config_json"):
                cfg_data = row["config_json"]
                if hasattr(AnalysisConfig, "model_validate"):
                    analysis_config = AnalysisConfig.model_validate(cfg_data)  # type: ignore[attr-defined]
                elif hasattr(AnalysisConfig, "parse_obj"):
                    analysis_config = AnalysisConfig.parse_obj(cfg_data)      # type: ignore[attr-defined]
                else:
                    # No pydantic available; manual construction
                    obj = AnalysisConfig.__new__(AnalysisConfig)  # type: ignore[misc]
                    for k, v in cfg_data.items():
                        setattr(obj, k, v)
                    analysis_config = obj  # type: ignore[assignment]
            else:
                print(f"Warning: --config-id={args.config_id} not found in analysis_configs; falling back to defaults.")
                analysis_config = None
        elif args.config_type:
            row = db.get_default_analysis_config(args.config_type, version=args.config_version)
            if row and row.get("config_json"):
                cfg_data = row["config_json"]
                if hasattr(AnalysisConfig, "model_validate"):
                    analysis_config = AnalysisConfig.model_validate(cfg_data)  # type: ignore[attr-defined]
                elif hasattr(AnalysisConfig, "parse_obj"):
                    analysis_config = AnalysisConfig.parse_obj(cfg_data)      # type: ignore[attr-defined]
                else:
                    obj = AnalysisConfig.__new__(AnalysisConfig)  # type: ignore[misc]
                    for k, v in cfg_data.items():
                        setattr(obj, k, v)
                    analysis_config = obj  # type: ignore[assignment]
            else:
                print(f"Warning: No default config found for analysis_type={args.config_type}; falling back to defaults.")
                analysis_config = None
        else:
            # Use a minimal default config to attach metadata if desired
            analysis_config = default_config()
    except Exception as e:
        print(f"Error: Could not connect to database: {e}", file=sys.stderr)
        sys.exit(1)

    if query_sql:
        fetched = fetch_ytids_from_query(db.conn, query_sql)
        if fetched:
            db_ytids.extend(fetched)
            print(f"Fetched {len(fetched)} ytids from query.")
        else:
            print("Warning: ytids query returned no results.", file=sys.stderr)

    # Reconstruct VTTs from DB words when ytids were provided
    if db_ytids:
        out_dir = Path("generated/from-db")
        for ytid in db_ytids:
            # Validate diarization requirement before attempting analysis
            if (args.restrict_to_diarized or args.speaker_name) and not db.has_diarization(ytid):
                reason = "speaker filtering" if args.speaker_name else "diarized analysis"
                print(
                    f"Error: Cannot run {reason} on {ytid} - no diarization data found.\n"
                    f"       Run diarization first with: vo diarize enqueue {ytid}",
                    file=sys.stderr
                )
                sys.exit(1)

            vtt_path = export_vtt_from_db(
                db.conn,
                ytid,
                out_dir,
                gap_sec=args.db_gap_sec,
                speaker_name=args.speaker_name,
                restrict_to_diarized=args.restrict_to_diarized,
            )
            if not vtt_path:
                continue
            meta = fetch_video_metadata(db.conn, ytid)
            info_path = vtt_path.with_suffix(".info.json")
            write_info_json(info_path, ytid, meta)
            files.append(vtt_path)

    # De-duplicate files while preserving order
    seen_files = set()
    unique_files: List[Path] = []
    for f in files:
        if f not in seen_files:
            unique_files.append(f)
            seen_files.add(f)
    files = unique_files

    if not files:
        print("Error: No transcript files found to analyze after DB export.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(files)} transcript file(s) to process")

    diarized_flag = bool(args.diarized or args.speaker_name or args.restrict_to_diarized)

    settings = PipelineSettings(
        model=args.model,
        quality=args.quality,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
        chunk_parallel=args.chunk_parallel,
        request_text=args.request or "",
        ollama_url=args.ollama_url,
        chunk_url_a=args.chunk_url_a or args.ollama_url,
        chunk_model_a=args.chunk_model_a or args.model,
        chunk_url_b=args.chunk_url_b,
        chunk_model_b=args.chunk_model_b,
        speaker_url=args.speaker_url or args.ollama_url,
        speaker_model=args.speaker_model or args.model,
        speaker_alias=args.speaker_alias,
        write_local=(not args.no_local_files) or args.save_json,
        output_dir=args.output_dir,
        include_chunk_text=args.include_chunk_text,
        write_markdown=args.write_markdown,
        skip_summaries=args.no_summaries,
        temperature=args.temperature,
        top_p=args.top_p,
        top_k=args.top_k,
        num_predict=args.num_predict,
        num_ctx=args.num_ctx,
        repeat_penalty=args.repeat_penalty,
        log_mode=log_mode,
        diarized=diarized_flag,
        transcription_machine=transcription_machine,
        hot_targets_path=Path(hot_targets_path) if hot_targets_path else None,
        hot_targets=hot_targets,
        hot_target_scheduler_config=hot_target_scheduler_config,
        alias_map_path=alias_map_path,
        category_cache_path=category_cache_path,
        category_cache_writeback=category_cache_writeback,
        category_map_path=category_map_path,
        drills_path=Path(drills_path) if drills_path else None,
        drills=load_drills(drills_path) if not args.no_drills else [],
        drills_scheduler_config=drills_scheduler_config,
        gpu_scheduler_config=gpu_scheduler_config,
        merge_gap_sec=float(merge_gap_sec) if merge_gap_sec is not None else DEFAULT_MERGE_GAP_SEC,
    )

    pipeline = TranscriptAnalysisPipeline(settings=settings, db=db, config=analysis_config)

    total = len(files)
    processed = 0
    skipped = 0
    errors = 0
    aborted = False

    try:
        try:
            for idx, fpath in enumerate(files, 1):
                print(f"=== [{idx}/{total}] {fpath.relative_to(Path.cwd()) if fpath.is_absolute() else fpath} ===")
                try:
                    result = pipeline.run_file(
                        fpath,
                        batch_id=args.batch_id,
                        analysis_type=args.analysis_type,
                        force=args.force,
                    )
                    if result is None:
                        skipped += 1
                    else:
                        processed += 1
                except Exception as e:
                    errors += 1
                    print(f"Error analyzing {fpath}: {e}", file=sys.stderr)
                    if not args.continue_on_error:
                        raise
                    continue
        except KeyboardInterrupt:
            aborted = True
            print("\nAborted by user (Ctrl+C). Exiting gracefully...", file=sys.stderr)
    finally:
        db.disconnect()

    print(f"\n{'='*60}")
    print("Processing complete!" if not aborted else "Processing aborted by user.")
    print(f"  Processed: {processed}")
    print(f"  Skipped:   {skipped}")
    print(f"  Errors:    {errors}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()

# ---------------------------------------------------------------------------
# Distributed analysis helpers (job + task creation)
# ---------------------------------------------------------------------------

from typing import Any, Dict, List, Optional, Iterable

from .analysis_task import AnalysisTask
from .analysis_task_repository import AnalysisTaskRepository
from .analysis_config import AnalysisConfig


def _resolve_base_model(config: AnalysisConfig, model_name: Optional[str]) -> str:
    base_model = model_name or getattr(config, "model", None)
    backend = getattr(config, "backend_params", {}) or {}
    base_model = base_model or backend.get("model") or backend.get("model_name")
    if not base_model:
        raise ValueError("Analysis config missing model; set config.model or pass model_name")
    return str(base_model)

def _resolve_profile_options(config: AnalysisConfig) -> Dict[str, Any]:
    backend = getattr(config, "backend_params", {}) or {}
    options = backend.get("ollama_options") or backend.get("ollama") or {}
    return options if isinstance(options, dict) else {}

def _iter_hot_target_profile_ids(config: AnalysisConfig) -> Iterable[int]:
    for spec in getattr(config, "hot_targets", []) or []:
        if isinstance(spec, dict):
            profile_id = spec.get("model_profile_id")
        else:
            profile_id = getattr(spec, "model_profile_id", None)
        if profile_id:
            yield int(profile_id)

def _iter_drill_profile_ids(
    db: AnalysisDatabase,
    config_id: str,
) -> Iterable[str]:
    try:
        drills = db.list_drills_for_config(config_id) or []
    except Exception:
        drills = []
    for drill in drills:
        detail = drill.get("detail_pass") or {}
        profile_id = detail.get("model_profile_id")
        if profile_id:
            yield int(profile_id)


def _resolve_model_profile(
    db: AnalysisDatabase,
    config: AnalysisConfig,
    model_profile_id: Optional[int],
    model_name: Optional[str],
) -> Dict[str, Any]:
    backend = getattr(config, "backend_params", {}) or {}
    profile_id = model_profile_id or getattr(config, "model_profile_id", None) or backend.get("model_profile_id")
    if profile_id:
        profile = db.get_analysis_model_profile(int(profile_id))
        if not profile:
            raise ValueError(f"Model profile id '{profile_id}' not found in analysis_model_profiles")
        return profile

    base_model = _resolve_base_model(config, model_name)
    options = _resolve_profile_options(config)
    profile = db.get_analysis_model_profile_by_name_options(base_model, options)
    if not profile:
        raise ValueError(
            f"No model profile registered for model '{base_model}' with options {options}. "
            "Add a profile or set model_profile_id in the analysis config."
        )
    return profile


def _get_pass_required_vram_gb(
    pass_id: str,
    config: AnalysisConfig,
    config_id: str,
    base_profile: Dict[str, Any],
    db: AnalysisDatabase,
) -> float:
    llm_passes = {
        "chunk_analysis",
        "sentiment_pass",
        "aggregate_results",
        "hot_targets",
        "drills",
    }
    if pass_id not in llm_passes:
        return 0.0

    vram_values = [float(base_profile.get("required_vram_gb") or 0)]
    if pass_id == "hot_targets":
        for profile_id in _iter_hot_target_profile_ids(config):
            profile = db.get_analysis_model_profile(profile_id)
            if profile:
                vram_values.append(float(profile.get("required_vram_gb") or 0))
    elif pass_id == "drills":
        for profile_id in _iter_drill_profile_ids(db, config_id):
            profile = db.get_analysis_model_profile(profile_id)
            if profile:
                vram_values.append(float(profile.get("required_vram_gb") or 0))

    return max(vram_values) if vram_values else 0.0


def create_analysis_job(
    ytid: str,
    config_id: str,
    config: AnalysisConfig,
    chunks: List[Dict[str, Any]],
    db: AnalysisDatabase,
    model_name: Optional[str] = None,
    model_profile_id: Optional[int] = None,
) -> str:
    """
    Create a new analysis job and enqueue tasks for all enabled passes/chunks.

    Returns a job_id (currently derived from ytid + config_id + timestamp).
    """
    from datetime import datetime, timezone

    job_id = f"{ytid}:{config_id}:{int(datetime.now(timezone.utc).timestamp())}"
    repo = AnalysisTaskRepository(db)

    enabled_passes: List[str] = [
        p.id for p in getattr(config, "passes", []) if getattr(p, "enabled", True)
    ]

    total_chunks = len(chunks)
    base_profile = _resolve_model_profile(db, config, model_profile_id, model_name)

    pass_requirements = {
        pass_id: _get_pass_required_vram_gb(
            pass_id=pass_id,
            config=config,
            config_id=config_id,
            base_profile=base_profile,
            db=db,
        )
        for pass_id in enabled_passes
    }

    # Define job-level passes that should run once per job, not per chunk
    job_level_passes = {
        "aggregate_results",
        "hot_targets",
        "drills",
        "db_store",
        "local_json",
        "markdown_report",
    }

    # Separate chunk-level and job-level passes
    chunk_level_passes = [p for p in enabled_passes if p not in job_level_passes]
    job_passes = [p for p in enabled_passes if p in job_level_passes]

    # Create chunk-level tasks (one per chunk per pass)
    for chunk_id, chunk_obj in enumerate(chunks):
        # Extract text from chunk object
        chunk_text = (
            chunk_obj.get("text")
            or chunk_obj.get("content")
            or ""
        )

        # Base metadata cloned from chunk object
        chunk_metadata: Dict[str, Any] = dict(chunk_obj)
        chunk_metadata.setdefault("chunk_number", chunk_id)
        chunk_metadata.setdefault("total_chunks", total_chunks)
        chunk_metadata.setdefault("config_id", config_id)

        for pass_id in chunk_level_passes:
            required_vram_gb = pass_requirements.get(pass_id, 0.0)
            task = AnalysisTask(
                task_id=0,
                job_id=job_id,
                ytid=ytid,
                chunk_id=chunk_id,
                pass_id=pass_id,
                chunk_text=chunk_text,
                chunk_metadata=chunk_metadata,
                required_vram_gb=required_vram_gb,
                model_profile_id=int(base_profile.get("id") or 0) or None,
            )
            repo.create_task(task)

    # Create job-level tasks (one per job, not per chunk)
    # Use chunk_id=0 as a sentinel for job-level tasks
    if chunks:
        first_chunk = chunks[0]
        job_chunk_text = first_chunk.get("text") or first_chunk.get("content") or ""
        job_metadata: Dict[str, Any] = dict(first_chunk)
        job_metadata.update({
            "chunk_number": 0,
            "total_chunks": total_chunks,
            "config_id": config_id,
            "is_job_level": True,
        })

        for pass_id in job_passes:
            required_vram_gb = pass_requirements.get(pass_id, 0.0)
            task = AnalysisTask(
                task_id=0,
                job_id=job_id,
                ytid=ytid,
                chunk_id=0,  # Job-level tasks use chunk_id=0
                pass_id=pass_id,
                chunk_text=job_chunk_text,
                chunk_metadata=job_metadata,
                required_vram_gb=required_vram_gb,
                model_profile_id=int(base_profile.get("id") or 0) or None,
            )
            repo.create_task(task)

    return job_id
