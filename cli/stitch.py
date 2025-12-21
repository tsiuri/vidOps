# vidops/cli/stitch.py

import csv
import logging
from pathlib import Path
from typing import List

import click

from services import get_stitching_service

logger = logging.getLogger(__name__)


@click.group()
def stitch():
    """Manage stitch manifests and enqueue jobs."""
    pass


@stitch.command("enqueue")
@click.option("--clips-file", type=click.Path(exists=True), help="Manifest of clip asset paths (TSV or newline-delimited).")
@click.option("--clip", "clip_assets", multiple=True, help="Individual clip asset path (relative to storage).")
@click.option("--output-name", required=True, help="Name for the stitched file (e.g. highlight.mp4).")
@click.option("--method", default="batch", show_default=True, help="ffmpeg concat strategy (batch|cfr|simple).")
@click.option("--priority", type=int, default=50, show_default=True, help="Job priority.")
def stitch_enqueue(clips_file: str, clip_assets: List[str], output_name: str, method: str, priority: int):
    """Enqueue a stitching job that concatenates existing clip assets."""
    sources: List[str] = []
    if clips_file:
        sources.extend(_read_clip_manifest(Path(clips_file)))
    if clip_assets:
        sources.extend(list(clip_assets))

    sources = [src.strip() for src in sources if src.strip()]
    if not sources:
        click.echo(click.style("✗ Provide at least one clip via --clip or --clips-file.", fg="red"), err=True)
        return

    service = get_stitching_service()
    try:
        job = service.enqueue_stitch_job(
            input_ytids_or_clip_paths=sources,
            output_filename=output_name,
            stitch_method=method,
            priority=priority
        )
        click.echo(click.style(f"✓ Stitch job enqueued: {job.job_id} ({len(sources)} inputs)", fg="green"))
    except Exception as exc:
        logger.error("Unable to enqueue stitch job: %s", exc, exc_info=True)
        click.echo(click.style(f"✗ Failed to enqueue stitch job: {exc}", fg="red"), err=True)


def _read_clip_manifest(path: Path) -> List[str]:
    """
    Read clip paths from TSV (expects clip_path/path column) or newline file.
    """
    if path.suffix.lower() in {".tsv", ".csv"}:
        with path.open("r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            column = "clip_path"
            if column not in reader.fieldnames and "path" in reader.fieldnames:
                column = "path"
            if column not in reader.fieldnames:
                raise ValueError(f"Manifest missing 'clip_path' column: {path}")
            return [row[column] for row in reader if row.get(column)]

    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
