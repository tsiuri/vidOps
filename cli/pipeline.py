# vidops/cli/pipeline.py

import click
import uuid
import sys
from typing import Optional, NamedTuple, Any, List

from services import (
    get_download_service,
    get_transcription_service,
    get_diarization_service,
)
from services.reference_registry import ReferenceRegistry
from dal.analysis_task_repository import AnalysisDatabase
from dal import JobRepository, VideoRepository
from models import Video, Job, JobStatus
from configuration import load_config
from scripts.analysis.analysis_config import AnalysisConfig
from db import get_connection


class ReferenceSelection(NamedTuple):
    reference_name: Optional[str]
    build_if_missing: bool
    skip_diarization: bool


class AnalysisSelection(NamedTuple):
    config_id: Optional[str]
    name: Optional[str]
    skip_analysis: bool


@click.group()
def pipeline():
    """Enqueue and manage multi-stage processing pipelines."""
    pass


@pipeline.command("enqueue")
@click.argument("ytid_or_url")
@click.option(
    "--skip-download",
    is_flag=True,
    help="Skip download (video already present)",
)
@click.option("--skip-transcription", is_flag=True, help="Skip transcription")
@click.option("--skip-diarization", is_flag=True, help="Skip diarization")
@click.option("--skip-analysis", is_flag=True, help="Skip analysis")
@click.option(
    "--transcription-model",
    default=None,
    help="Whisper model (default from config.yaml)",
)
@click.option(
    "--transcription-language",
    default=None,
    help="Transcription language (default from config.yaml)",
)
@click.option(
    "--diarization-device",
    default=None,
    help="Diarization device (default from config.yaml)",
)
@click.option(
    "--reference-name",
    default=None,
    help="Diarization reference to reuse/build before enqueueing (defaults to ytid). Aborts enqueue if build is cancelled or fails.",
)
@click.option(
    "--force-from",
    type=click.Choice(["download", "transcription", "diarization", "analysis"], case_sensitive=False),
    default=None,
    help="Skip earlier stages and start from this stage (e.g., rerun from diarization).",
)
@click.option(
    "--force",
    is_flag=True,
    help="Force re-run: enables download force and transcription force flags.",
)
@click.option("--analysis-config-id", type=str, default=None, help="Analysis config ID (prompts if omitted and analysis is included).")
@click.option("--priority", type=int, default=50, help="Pipeline priority")
def enqueue_pipeline(
    ytid_or_url: str,
    skip_download: bool,
    skip_transcription: bool,
    skip_diarization: bool,
    skip_analysis: bool,
    transcription_model: str | None,
    transcription_language: str | None,
    diarization_device: str | None,
    reference_name: str | None,
    force_from: str | None,
    force: bool,
    analysis_config_id: str | None,
    priority: int,
):
    """
    Enqueue a full processing pipeline for a video.

    All jobs are created immediately with dependency tracking.
    Jobs wait in PENDING until prerequisites complete.
    """
    # ------------------------------------------------------------------
    # Stage selection / gating
    # ------------------------------------------------------------------
    if force_from:
        # Normalize and skip all stages before the chosen entry point
        force_from = force_from.lower()
        stage_order = ["download", "transcription", "diarization", "analysis"]
        if force_from not in stage_order:
            raise click.ClickException(f"Unknown stage for --force-from: {force_from}")
        idx = stage_order.index(force_from)
        skip_download = skip_download or idx > 0
        skip_transcription = skip_transcription or idx > 1
        skip_diarization = skip_diarization or idx > 2
        skip_analysis = skip_analysis or idx > 3

    config = load_config()
    job_repo = JobRepository()
    video_repo = VideoRepository()
    diarization_service = get_diarization_service()
    cfg = config

    # Generate unique pipeline ID
    pipeline_id = f"pipe_{uuid.uuid4().hex[:8]}"

    # Extract YTID
    if ytid_or_url.startswith("http"):
        ytid = ytid_or_url.split("watch?v=")[-1].split("&")[0]
        url = ytid_or_url
    else:
        ytid = ytid_or_url
        url = f"https://youtube.com/watch?v={ytid}"

    click.echo(f"Enqueuing pipeline {pipeline_id} for {ytid}")

    # Resolve commonly reused defaults
    trans_model = transcription_model or config.transcription.model
    trans_lang = transcription_language or config.transcription.language
    transcript_kind = f"words_whisper_{trans_model}"

    # Collect configuration interactively before any DB writes
    reference_selection: Optional[ReferenceSelection] = None
    analysis_selection: Optional[AnalysisSelection] = None

    # Step 1: Diarization reference selection (menu to pick which reference to use/build)
    if not skip_diarization:
        try:
            if reference_name:
                reference_selection = ReferenceSelection(reference_name, True, False)
            else:
                reference_selection = _select_diarization_reference_for_pipeline(
                    ytid=ytid,
                    transcript_kind=transcript_kind,
                )
        except Exception as exc:  # noqa: BLE001
            click.echo(click.style(f"✗ Failed to select diarization reference: {exc}", fg="red"), err=True)
            click.echo("No jobs enqueued.")
            return
        if reference_selection.skip_diarization:
            skip_diarization = True
            click.echo(click.style("  ↷ Diarization skipped per selection.", fg="yellow"))
        elif not reference_selection.reference_name:
            click.echo("Diarization configuration cancelled; no jobs enqueued.")
            return

    # Step 2: Build/prepare diarization reference (interactive UIs for clip selection, params, speaker name if needed)
    reference_dir_rel: Optional[str] = None
    ref_name: Optional[str] = None
    if not skip_diarization:
        ref_name = (reference_selection.reference_name if reference_selection else reference_name) or ytid
        try:
            if reference_selection and reference_selection.build_if_missing:
                click.echo(f"  … Preparing diarization reference '{ref_name}'")
                reference_dir_rel = diarization_service.build_shared_reference(
                    ytids=[ytid],
                    transcript_kind=transcript_kind,
                    reference_name=ref_name,
                    clips_count=50,
                )
                click.echo(click.style(f"  ✓ Reference ready at {reference_dir_rel}", fg="green"))
            elif reference_selection and not reference_selection.build_if_missing:
                reference_dir_rel = f"data/references/{ref_name}"
                ref_path = diarization_service.fs_cache.get_central_path(reference_dir_rel)
                if not ref_path.exists():
                    raise FileNotFoundError(f"Reference not found: {reference_dir_rel}")
                click.echo(click.style(f"  ✓ Using existing reference '{ref_name}'", fg="green"))
            else:
                # Should not happen, but guard to keep behavior predictable
                reference_dir_rel = f"data/references/{ref_name}"
        except Exception as exc:
            click.echo(click.style(f"✗ Reference setup failed/cancelled: {exc}", fg="red"), err=True)
            click.echo("No jobs enqueued.")
            return

    # Step 3: Analysis config selection (after diarization is fully configured)
    if not skip_analysis and not analysis_config_id:
        db = AnalysisDatabase()
        db.connect()
        try:
            analysis_selection = _select_analysis_config_for_pipeline(db)
        except Exception as exc:  # noqa: BLE001
            click.echo(click.style(f"✗ Analysis config selection failed: {exc}", fg="red"), err=True)
            click.echo("No jobs enqueued.")
            return
        finally:
            db.disconnect()

        if analysis_selection.skip_analysis:
            skip_analysis = True
            click.echo(click.style("  ↷ Analysis skipped per selection.", fg="yellow"))
        elif analysis_selection.config_id:
            analysis_config_id = analysis_selection.config_id
            click.echo(f"  ✓ Selected analysis config: {analysis_selection.name or analysis_config_id}")
        else:
            click.echo("Analysis configuration cancelled; no jobs enqueued.")
            return

    # Create placeholder video record so dependent stages can reference it
    placeholder_video = Video(
        ytid=ytid,
        url=url,
        title=f"[Pending] {ytid}"  # Placeholder title, will be updated by download job
    )
    video_repo.upsert(placeholder_video)

    # Track created jobs
    jobs_created = []
    prev_job_id: Optional[str] = None
    transcription_skipped = False

    # Stage 1: Download
    if not skip_download:
        download_service = get_download_service()
        download_job = download_service.enqueue_download(
            url=url,
            priority=priority,
            force_download=force,
            ytdlp_overrides={"force_download": True, "no_overwrites": False} if force else None,
        )

        download_job.config["pipeline_id"] = pipeline_id
        download_job.config["pipeline_stage"] = "download"
        job_repo.update_config(download_job.job_id, download_job.config)

        jobs_created.append(("download", download_job.job_id))
        prev_job_id = download_job.job_id
        click.echo(f"  ✓ Download job created: {download_job.job_id}")

    # Stage 2: Transcription
    if not skip_transcription:
        transcription_service = get_transcription_service()
        try:
            trans_job = transcription_service.enqueue_video(
                ytid=ytid,
                model=trans_model,
                language=trans_lang,
                priority=priority,
                force=force,
                force_job=force,
            )

            trans_job.config["pipeline_id"] = pipeline_id
            trans_job.config["pipeline_stage"] = "transcription"
            if prev_job_id:
                trans_job.config["depends_on"] = prev_job_id
            job_repo.update_config(trans_job.job_id, trans_job.config)

            jobs_created.append(("transcription", trans_job.job_id))
            prev_job_id = trans_job.job_id

            dep_msg = (
                f" (depends on {trans_job.config['depends_on'][:8]}...)"
                if "depends_on" in trans_job.config
                else ""
            )
            click.echo(f"  ✓ Transcription job created: {trans_job.job_id}{dep_msg}")
        except ValueError as exc:
            transcription_skipped = True
            click.echo(click.style(f"  ↷ Skipping transcription: {exc}", fg="yellow"))

    # Stage 3: Diarization
    if not skip_diarization:
        diar_job = diarization_service.enqueue_diarization_job(
            ytid=ytid,
            transcript_kind=transcript_kind,
            device=diarization_device,  # None = use config default
            priority=priority,
            force_enqueue=True,  # Allow enqueue before transcript exists; worker resolves paths at runtime
            reference_name=ref_name if reference_dir_rel else None,
        )

        diar_job.config["pipeline_id"] = pipeline_id
        diar_job.config["pipeline_stage"] = "diarization"
        if prev_job_id and not transcription_skipped:
            diar_job.config["depends_on"] = prev_job_id
        if reference_dir_rel:
            diar_job.config["reference_dir"] = reference_dir_rel
        job_repo.update_config(diar_job.job_id, diar_job.config)

        jobs_created.append(("diarization", diar_job.job_id))
        prev_job_id = diar_job.job_id

        dep_msg = (
            f" (depends on {diar_job.config['depends_on'][:8]}...)"
            if "depends_on" in diar_job.config
            else ""
        )
        click.echo(f"  ✓ Diarization job created: {diar_job.job_id}{dep_msg}")

    # Stage 4: Analysis
    if not skip_analysis:
        analysis_dep = prev_job_id if not skip_diarization else (prev_job_id if prev_job_id else None)
        analysis_model_name = cfg.analysis.ollama.model if cfg.analysis else "llama3"
        required_vram_gb = float(cfg.analysis.default_vram_gb or 0) if cfg.analysis else 0.0
        if analysis_config_id:
            analysis_db = AnalysisDatabase()
            analysis_db.connect()
            try:
                config_row = analysis_db.get_analysis_config(analysis_config_id)
                if config_row:
                    config_obj = AnalysisConfig.model_validate(config_row.get("config_json", {}))
                    config_model = getattr(config_obj, "model", None)
                    if config_model:
                        analysis_model_name = config_model
                    # Try to get VRAM from model profile if available
                    model_profile_id = getattr(config_obj, "model_profile_id", None)
                    if model_profile_id:
                        try:
                            profile = analysis_db.get_analysis_model_profile(model_profile_id)
                            if profile and profile.get("required_vram_gb"):
                                required_vram_gb = float(profile["required_vram_gb"])
                        except Exception:  # noqa: BLE001
                            pass  # Use default
            except Exception as exc:  # noqa: BLE001
                click.echo(click.style(f"  ⚠ Failed to read analysis model override: {exc}", fg="yellow"))
            finally:
                analysis_db.disconnect()
        analysis_job_config = {
            "ytid": ytid,
            "config_id": analysis_config_id,
            "transcript_kind": transcript_kind,
            "model_url": cfg.analysis.ollama.url,
            "model_name": analysis_model_name,
        }
        analysis_job = job_repo.create(
            Job(
                job_type="analysis-distributed",
                ytid=ytid,
                config=analysis_job_config,
                priority=priority,
                status=JobStatus.PENDING,
                required_vram_gb=required_vram_gb,
            )
        )
        # pipeline metadata
        analysis_job.config["pipeline_id"] = pipeline_id
        analysis_job.config["pipeline_stage"] = "analysis"
        if analysis_dep:
            analysis_job.config["depends_on"] = analysis_dep
        job_repo.update_config(analysis_job.job_id, analysis_job.config)

        jobs_created.append(("analysis", analysis_job.job_id))
        prev_job_id = analysis_job.job_id

        dep_msg = (
            f" (depends on {analysis_job.config['depends_on'][:8]}...)"
            if "depends_on" in analysis_job.config
            else ""
        )
        click.echo(f"  ✓ Analysis job created: {analysis_job.job_id}{dep_msg}")

    click.echo(
        f"\n✓ Pipeline {pipeline_id} enqueued with {len(jobs_created)} stages"
    )
    click.echo("\nPipeline flow:")
    for idx, (stage, job_id) in enumerate(jobs_created):
        prefix = "  " + ("└─" if idx == len(jobs_created) - 1 else "├─")
        click.echo(f"{prefix} {stage}: {job_id}")

    click.echo(f"\nMonitor progress:")
    click.echo(f"  vo status jobs")
    click.echo(f"  vo pipeline status {pipeline_id}")


@pipeline.command("status")
@click.argument("pipeline_id")
def pipeline_status(pipeline_id: str):
    """Show status of all jobs in a pipeline."""
    try:
        from tabulate import tabulate
    except ImportError:
        click.echo(
            "tabulate module not found. Install with: pip install tabulate",
            err=True,
        )
        return

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT job_id,
                       job_type,
                       status,
                       priority,
                       config->>'depends_on' as depends_on,
                       config->>'pipeline_stage' as pipeline_stage,
                       error_message,
                       created_at,
                       completed_at
                FROM jobs
                WHERE config->>'pipeline_id' = %s
                ORDER BY created_at ASC
                """,
                (pipeline_id,),
            )
            rows = cur.fetchall()

    if not rows:
        click.echo(f"No jobs found for pipeline {pipeline_id}")
        return

    headers = ["Job ID", "Stage", "Status", "Depends On", "Created", "Completed", "Error"]
    table_data = []
    for row in rows:
        job_id, job_type, status, priority, depends_on, pipeline_stage, error_message, created_at, completed_at = row
        stage = pipeline_stage or job_type
        err_snippet = (error_message or "")[:60] + ("…" if error_message and len(error_message) > 60 else "")
        table_data.append(
            [
                job_id[:12],
                stage,
                status,
                depends_on[:8] if depends_on else "-",
                created_at.strftime("%H:%M:%S"),
                completed_at.strftime("%H:%M:%S") if completed_at else "-",
                err_snippet or "-",
            ]
        )

    click.echo(f"\nPipeline: {pipeline_id}")
    click.echo(tabulate(table_data, headers=headers, tablefmt="simple"))

    # Emit summary and exit non-zero if there were failures
    status_counts = {}
    failed_rows = []
    for row in rows:
        status_counts[row[2]] = status_counts.get(row[2], 0) + 1  # status at index 2
        if row[2] == "failed":
            failed_rows.append(row)

    click.echo("\nStatus summary: " + ", ".join(f"{k}={v}" for k, v in sorted(status_counts.items())))
    if failed_rows:
        click.echo(click.style("Detected failed jobs:", fg="red"), err=True)
        for row in failed_rows:
            job_id, job_type, status, priority, depends_on, pipeline_stage, error_message, created_at, completed_at = row
            stage = pipeline_stage or job_type
            click.echo(
                f"  {job_id} [{stage}] -> {error_message or 'no error_message recorded'}",
                err=True,
            )
        # Non-zero exit so callers/shell users notice failure immediately
        ctx = click.get_current_context()
        ctx.exit(1)


# ---------------------------------------------------------------------------
# Interactive helpers
# ---------------------------------------------------------------------------
def _select_diarization_reference_for_pipeline(
    ytid: str,
    transcript_kind: str,
) -> ReferenceSelection:
    """
    Interactive picker for diarization references. Returns the chosen reference
    name, whether it should be built if missing, or a skip signal.
    """
    from dal import FilesystemCache

    registry = ReferenceRegistry()
    records: List[Any] = []
    try:
        records = registry.list_records(limit=50) or []
    except Exception as exc:  # noqa: BLE001
        click.echo(
            f"Warning: Could not load diarization reference registry ({exc}); defaulting to new reference.",
            err=True,
        )
        records = []

    default_name = ytid
    # Check if default_name reference already exists
    fs_cache = FilesystemCache()
    default_ref_path = fs_cache.get_central_path(f"data/references/{default_name}")
    default_ref_exists = (default_ref_path / "reference.json").exists()

    if not sys.stdin.isatty():
        return _prompt_reference_text(default_name, records, default_ref_exists)

    try:
        import curses
    except Exception:  # noqa: BLE001
        click.echo("Curses UI unavailable; falling back to text prompt for reference selection.", err=True)
        return _prompt_reference_text(default_name, records, default_ref_exists)

    def _menu(stdscr):
        curses.curs_set(0)
        stdscr.nodelay(False)
        stdscr.keypad(True)
        options = []

        # First option: build or rebuild the default reference
        if default_ref_exists:
            options.append(("build", default_name, f"Rebuild reference '{default_name}' from transcript {transcript_kind}"))
        else:
            options.append(("build", default_name, f"Build reference '{default_name}' from transcript {transcript_kind}"))

        for rec in records:
            label = f"Use existing {rec.name}"
            if rec.clip_count:
                label += f" ({rec.clip_count} clips)"
            options.append(("existing", rec.name, label))
        options.append(("skip", None, "Skip diarization for this pipeline"))
        options.append(("cancel", None, "Cancel (abort enqueue)"))

        idx = 0
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            header = "↑/↓ move  Enter select  s skip diarization  q cancel"
            stdscr.addnstr(0, 0, header.ljust(w - 1)[: w - 1], w - 1)
            for i, (_, _, label) in enumerate(options):
                prefix = ">" if i == idx else " "
                stdscr.addnstr(i + 2, 0, f"{prefix} {label}".ljust(w - 1)[: w - 1], w - 1)
            ch = stdscr.getch()
            if ch in (curses.KEY_UP, ord("k")):
                idx = (idx - 1) % len(options)
            elif ch in (curses.KEY_DOWN, ord("j")):
                idx = (idx + 1) % len(options)
            elif ch in (ord("s"), ord("S")):
                return ReferenceSelection(None, False, True)
            elif ch in (ord("q"), 27):
                return ReferenceSelection(None, False, False)
            elif ch in (curses.KEY_ENTER, 10, 13, ord(" ")):
                action, name, _ = options[idx]
                if action == "build":
                    return ReferenceSelection(name, True, False)
                if action == "existing":
                    return ReferenceSelection(name, False, False)
                if action == "skip":
                    return ReferenceSelection(None, False, True)
                return ReferenceSelection(None, False, False)

    return curses.wrapper(_menu)


def _prompt_reference_text(default_name: str, records: List[Any], default_ref_exists: bool = False) -> ReferenceSelection:
    print("\nDiarization reference options:")
    if default_ref_exists:
        print(f"  1. Rebuild reference '{default_name}' from this video")
    else:
        print(f"  1. Build reference '{default_name}' from this video")
    for idx, rec in enumerate(records, start=2):
        meta = []
        if getattr(rec, "clip_count", None):
            meta.append(f"{rec.clip_count} clips")
        if getattr(rec, "transcript_kind", None):
            meta.append(rec.transcript_kind)
        suffix = f" ({', '.join(meta)})" if meta else ""
        print(f"  {idx}. Use existing {rec.name}{suffix}")
    print("  s. Skip diarization for this pipeline")
    print("  0. Cancel")

    try:
        choice = input("Select reference [default=build new]: ").strip().lower()
    except EOFError:
        choice = ""

    if choice in {"s", "skip"}:
        return ReferenceSelection(None, False, True)
    if choice in {"0", "q", "quit", "cancel"}:
        return ReferenceSelection(None, False, False)
    if not choice:
        return ReferenceSelection(default_name, True, False)
    try:
        num = int(choice)
    except ValueError:
        return ReferenceSelection(default_name, True, False)
    if num == 1:
        return ReferenceSelection(default_name, True, False)
    idx = num - 2
    if 0 <= idx < len(records):
        return ReferenceSelection(records[idx].name, False, False)
    return ReferenceSelection(default_name, True, False)


def _select_analysis_config_for_pipeline(db: AnalysisDatabase) -> AnalysisSelection:
    """
    Interactive analysis-config picker with an explicit skip option.
    """
    configs = db.list_analysis_configs()
    if not configs:
        click.echo("No analysis configs found; skipping analysis.", err=True)
        return AnalysisSelection(None, None, True)

    default = next((c for c in configs if c.get("is_default")), None)
    if not sys.stdin.isatty():
        return _prompt_analysis_config_text(configs, default)

    try:
        import curses
    except Exception:  # noqa: BLE001
        click.echo("Curses UI unavailable; falling back to text prompt for analysis config selection.", err=True)
        return _prompt_analysis_config_text(configs, default)

    def _menu(stdscr):
        curses.curs_set(0)
        stdscr.nodelay(False)
        stdscr.keypad(True)
        idx = 0
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            header = "↑/↓ move  Enter select  s skip analysis  q cancel"
            stdscr.addnstr(0, 0, header.ljust(w - 1)[: w - 1], w - 1)
            max_rows = max(1, h - 3)
            start = max(0, min(idx - max_rows + 1, len(configs) - max_rows))
            end = min(len(configs), start + max_rows)
            view = configs[start:end]
            for line_idx, cfg in enumerate(view, start=0):
                i = start + line_idx
                prefix = ">" if i == idx else " "
                name = cfg.get("name") or cfg["id"]
                meta = f"{cfg.get('analysis_type','?')} v{cfg.get('version','?')}"
                if cfg.get("is_default"):
                    meta += " [default]"
                line = f"{prefix} {cfg['id']} :: {name} :: {meta}"
                stdscr.addnstr(line_idx + 2, 0, line.ljust(w - 1)[: w - 1], w - 1)
            ch = stdscr.getch()
            if ch in (curses.KEY_UP, ord("k")):
                idx = (idx - 1) % len(configs)
            elif ch in (curses.KEY_DOWN, ord("j")):
                idx = (idx + 1) % len(configs)
            elif ch in (ord("s"), ord("S")):
                return AnalysisSelection(None, None, True)
            elif ch in (ord("q"), 27):
                return AnalysisSelection(None, None, False)
            elif ch in (curses.KEY_ENTER, 10, 13, ord(" ")):
                sel = configs[idx]
                return AnalysisSelection(sel["id"], sel.get("name") or sel["id"], False)

    return curses.wrapper(_menu)


def _prompt_analysis_config_text(
    configs: List[dict[str, Any]],
    default: Optional[dict[str, Any]],
) -> AnalysisSelection:
    print("\nAvailable analysis configs:")
    for idx, cfg in enumerate(configs, 1):
        meta = f"{cfg.get('analysis_type','?')} v{cfg.get('version','?')}"
        if cfg.get("is_default"):
            meta += " [default]"
        print(f"  {idx}. {cfg['id']} :: {cfg.get('name') or cfg['id']} :: {meta}")
    print("  s. Skip analysis for this pipeline")
    print("  0. Cancel")
    try:
        choice = input("Select config (number) [default uses first/default]: ").strip().lower()
    except EOFError:
        choice = ""
    if choice in {"s", "skip"}:
        return AnalysisSelection(None, None, True)
    if choice in {"0", "q", "quit", "cancel"}:
        return AnalysisSelection(None, None, False)
    if not choice:
        chosen = default or configs[0]
        return AnalysisSelection(chosen["id"], chosen.get("name") or chosen["id"], False)
    try:
        num = int(choice)
    except ValueError:
        chosen = default or configs[0]
        return AnalysisSelection(chosen["id"], chosen.get("name") or chosen["id"], False)
    if num <= 0:
        return AnalysisSelection(None, None, False)
    if 1 <= num <= len(configs):
        cfg = configs[num - 1]
        return AnalysisSelection(cfg["id"], cfg.get("name") or cfg["id"], False)
    chosen = default or configs[0]
    return AnalysisSelection(chosen["id"], chosen.get("name") or chosen["id"], False)
