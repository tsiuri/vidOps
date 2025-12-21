import csv
import logging
from pathlib import Path
from typing import List, Dict, Optional

import click

from dal import WordRepository, VideoRepository, HitsRepository
from services import get_clipping_service

logger = logging.getLogger(__name__)

HIT_HEADERS = [
    "ytid",
    "start_sec",
    "end_sec",
    "duration_sec",
    "label",
    "phrase",
    "source",
    "media_asset_path",
    "run_name",
]


@click.group()
def clips():
    """Manage video clips and search operations."""
    pass


@clips.command("hits")
@click.option("-q", "--query", help="Comma-separated phrases to search for.")
@click.option("--timestamp", help="Timestamp range in format 'START-END' (seconds). Requires --ytid.")
@click.option("--source", default=None, show_default=True, help="Word source to search (e.g. whisper-medium). If omitted, will choose automatically when only one source exists for the target videos.")
@click.option("--limit", type=int, default=100, show_default=True, help="Maximum hits per phrase.")
@click.option("--exact/--fuzzy", default=False, show_default=True, help="Exact token match or substring match.")
@click.option("--name", required=True, help="Required name for this hits run (used for output paths).")
@click.option("-o", "--output", type=click.Path(), help="Optional TSV output path (default: generated/hits/<name>/hits.tsv).")
@click.option("--ytid", multiple=True, help="Limit search to one or more specific YTIDs (required when using --timestamp).")
def clips_hits(query: Optional[str], timestamp: Optional[str], source: Optional[str], limit: int, exact: bool, name: str, output: Optional[str], ytid: tuple):
    """Search the words table for matching phrases and emit a TSV manifest."""

    # Validate input: must have either query or timestamp
    if not query and not timestamp:
        click.echo(click.style("✗ Must provide either --query or --timestamp", fg="red"), err=True)
        return

    if query and timestamp:
        click.echo(click.style("✗ Cannot use both --query and --timestamp at the same time", fg="red"), err=True)
        return

    # If using timestamp mode, require ytid
    if timestamp and not ytid:
        click.echo(click.style("✗ --timestamp requires at least one --ytid", fg="red"), err=True)
        return

    video_repo = VideoRepository()
    hits_repo = HitsRepository()
    rows: List[Dict[str, str]] = []

    # Timestamp mode: create direct hits from timestamp range
    if timestamp:
        try:
            parts = timestamp.split("-")
            if len(parts) != 2:
                raise ValueError("Timestamp must be in format START-END (e.g., '10.5-20.3', '(start)-30', '60-(end)')")
        except ValueError as e:
            click.echo(click.style(f"✗ Invalid timestamp format: {e}", fg="red"), err=True)
            return

        start_str = parts[0].strip()
        end_str = parts[1].strip()

        click.echo(f"Creating timestamp-based hit(s) for {len(ytid)} YTID(s) from {start_str} to {end_str}...")
        for vid_id in ytid:
            # Resolve (start) and (end) placeholders
            if start_str == "(start)" or end_str == "(end)":
                video = video_repo.get(vid_id)
                if not video:
                    click.echo(click.style(f"✗ Video {vid_id} not found in database", fg="red"), err=True)
                    return
                video_duration = video.duration_sec
                if not video_duration:
                    click.echo(click.style(f"✗ Video {vid_id} has no duration metadata", fg="red"), err=True)
                    return

            try:
                if start_str == "(start)":
                    start_sec = 0.0
                else:
                    start_sec = float(start_str)

                if end_str == "(end)":
                    end_sec = float(video_duration)
                else:
                    end_sec = float(end_str)

                if start_sec >= end_sec:
                    raise ValueError("Start time must be less than end time")
            except ValueError as e:
                click.echo(click.style(f"✗ Invalid timestamp values: {e}", fg="red"), err=True)
                return

            media_asset = video_repo.get_primary_asset(vid_id, "media")
            duration = end_sec - start_sec
            rows.append({
                "ytid": vid_id,
                "start_sec": f"{start_sec:.3f}",
                "end_sec": f"{end_sec:.3f}",
                "duration_sec": f"{duration:.3f}",
                "label": f"timestamp_{start_sec}-{end_sec}",
                "phrase": f"Manual timestamp range {start_sec}-{end_sec}",
                "source": "manual",
                "media_asset_path": media_asset.path if media_asset else "",
                "run_name": name,
            })

    # Query mode: search for phrases
    else:
        phrases = [token.strip() for token in query.split(",") if token.strip()]
        if not phrases:
            click.echo(click.style("✗ No phrases provided.", fg="red"), err=True)
            return

        repo = WordRepository()

        # Auto-resolve source if not provided and we have scoped ytids
        resolved_source = source
        if not source and ytid:
            resolved_source = repo.auto_resolve_source(list(ytid))
        elif not source:
            resolved_source = repo.auto_resolve_source(None)

        if not resolved_source:
            click.echo(click.style("✗ No word source available for the requested scope.", fg="red"), err=True)
            return

        if resolved_source != source and source:
            click.echo(click.style(f"⚠ Source '{source}' not found for given ytids; using '{resolved_source}'", fg="yellow"), err=True)
        source = resolved_source

        click.echo(f"Searching {len(phrases)} phrase(s) against source '{source}'...")
        for phrase in phrases:
            tokens = phrase.split()
            hits = repo.find_phrase_hits(tokens, source=source, limit=limit, exact=exact, ytids=list(ytid) if ytid else None)
            for hit in hits:
                media_asset = video_repo.get_primary_asset(hit["ytid"], "media")
                duration = float(hit["end_sec"]) - float(hit["start_sec"])
                rows.append(
                    {
                        "ytid": hit["ytid"],
                        "start_sec": f"{hit['start_sec']:.3f}",
                        "end_sec": f"{hit['end_sec']:.3f}",
                        "duration_sec": f"{max(duration, 0):.3f}",
                        "label": phrase,
                        "phrase": hit["phrase"],
                        "source": hit["source"],
                        "media_asset_path": media_asset.path if media_asset else "",
                        "run_name": name,
                    }
                )

    if not rows:
        click.echo(click.style("No hits found.", fg="yellow"))
        return

    dest = Path(output) if output else Path("generated") / "hits" / name / "hits.tsv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=HIT_HEADERS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    click.echo(click.style(f"✓ Wrote {len(rows)} hits to {dest}", fg="green"))

    # Persist hits metadata in DB
    try:
        inserted = hits_repo.bulk_insert(rows)
        click.echo(click.style(f"✓ Recorded {inserted} hits in DB (run: {name})", fg="green"))
    except Exception as exc:
        logger.warning("Failed to insert hits into DB: %s", exc)
        click.echo(click.style("⚠ Failed to record hits in DB", fg="yellow"))


@clips.command("cut")
@click.argument("hits_file", type=click.Path(exists=True))
@click.option("--name", help="Run name; if omitted, derived from hits_file run_name column.")
@click.option("--priority", type=int, default=50, show_default=True, help="Job priority for enqueued clips.")
@click.option("--limit", type=int, default=None, help="Max rows to enqueue from the TSV.")
@click.option("--mode", type=click.Choice(["net", "local"]), default="net", show_default=True, help="Use legacy cut-net (default) or cut-local.")
def clips_cut(hits_file: str, name: Optional[str], priority: int, limit: Optional[int], mode: str):
    """
    Enqueue a single clipping job for a TSV manifest generated by 'clips hits'.
    """
    rows = _read_hits_file(Path(hits_file))
    if limit:
        rows = rows[:limit]

    run_names = {r.get("run_name") for r in rows if r.get("run_name")}
    if name:
        run_name = name
    elif len(run_names) == 1:
        run_name = run_names.pop()
    else:
        raise click.UsageError("Run name is required (use --name or include run_name column with a single value).")

    # Use first ytid for job ytid context
    first_ytid = rows[0]["ytid"] if rows else None
    if not first_ytid:
        raise click.UsageError("Hits file missing ytid values.")

    # Default output alongside the hits manifest
    output_dir = Path("generated") / "hits" / run_name
    service = get_clipping_service()
    try:
        job = service.enqueue_manifest_job(
            manifest_path=str(Path(hits_file).resolve()),
            run_name=run_name,
            output_dir=str(output_dir),
            ytid=first_ytid,
            priority=priority,
            mode=mode,
        )
        click.echo(click.style(f"✓ Enqueued clipping job {job.job_id} for manifest {hits_file}", fg="green"))
    except Exception as exc:
        logger.error("Failed to enqueue clipping job: %s", exc, exc_info=True)
        click.echo(click.style(f"✗ Failed to enqueue clipping job: {exc}", fg="red"), err=True)


@clips.command("enqueue")
@click.argument("ytid")
@click.option("--start", type=float, required=True, help="Start time of the clip in seconds.")
@click.option("--end", type=float, required=True, help="End time of the clip in seconds.")
@click.option("--label", default="manual_clip", help="A label for the clip (e.g., search term, event name).")
@click.option("--priority", type=int, default=50, help="Job priority.")
@click.option("--media-asset", type=str, help="Optional relative path to the source media asset.")
def enqueue_clip_command(ytid: str, start: float, end: float, label: str, priority: int, media_asset: Optional[str]):
    """Enqueue a single video clipping job."""
    click.echo(f"Enqueuing clipping job for YTID: {ytid} from {start}s to {end}s (Label: {label}, Priority: {priority})...")

    if start >= end:
        click.echo(click.style("✗ Error: Start time must be less than end time.", fg="red"), err=True)
        return

    try:
        service = get_clipping_service()
        job = service.enqueue_clip_job(
            ytid=ytid,
            start_sec=start,
            end_sec=end,
            label=label,
            priority=priority,
            media_relative_path=media_asset
        )
        click.echo(click.style(f"✓ Clipping job enqueued: {job.job_id}", fg="green"))
    except ValueError as e:
        click.echo(click.style(f"✗ Failed to enqueue clipping job: {e}", fg="yellow"), err=True)
    except Exception as e:
        logger.error(f"Error enqueuing clipping job for {ytid}: {e}", exc_info=True)
        click.echo(click.style(f"✗ Failed to enqueue clipping job: {e}", fg="red"), err=True)


def _read_hits_file(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        rows = [row for row in reader]
    headers = [h.strip().lower() for h in reader.fieldnames or []]
    new_required = {"ytid", "start_sec", "end_sec"}
    legacy_required = {"url", "start", "end"}
    if new_required.issubset(headers):
        return rows
    if legacy_required.issubset(headers):
        # normalize legacy headers to new keys so downstream can consume them
        norm_rows = []
        for r in rows:
            norm_rows.append(
                {
                    "ytid": r.get("ytid", ""),
                    "url": r.get("url", ""),
                    "start_sec": r.get("start") or r.get("start_sec"),
                    "end_sec": r.get("end") or r.get("end_sec"),
                    "label": r.get("label"),
                    "phrase": r.get("caption") or r.get("label"),
                    "run_name": r.get("run_name"),
                }
            )
        return norm_rows
    raise ValueError("Hits file missing required columns (expected ytid/start_sec/end_sec or url/start/end).")


    
