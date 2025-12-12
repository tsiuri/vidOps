import csv
import logging
from pathlib import Path
from typing import List, Dict, Optional

import click

from dal import WordRepository, VideoRepository, HitsRepository

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
def query_ids():
    """Batch phrase search against a list of YTIDs."""
    pass


@query_ids.command("run")
@click.option("-q", "--query", required=True, help="Comma-separated phrases to search for.")
@click.option("--name", required=True, help="Required name for this run (used for output paths).")
@click.option("--ytids-file", type=click.Path(exists=True), required=True, help="Path to a file containing one YTID per line.")
@click.option("--source", default=None, show_default=True, help="Word source to search (e.g. whisper-medium). If omitted, auto-resolves when only one source exists for the scoped videos.")
@click.option("--limit", type=int, default=100, show_default=True, help="Maximum hits per phrase per chunk.")
@click.option("--exact/--fuzzy", default=False, show_default=True, help="Exact token match or substring match.")
@click.option("--chunk-size", type=int, default=200, show_default=True, help="Process YTIDs in chunks to avoid oversized queries.")
@click.option("-o", "--output", type=click.Path(), help="Optional TSV output path (default: generated/query_ids/<name>/hits.tsv).")
def query_ids_run(query: str, name: str, ytids_file: str, source: Optional[str], limit: int, exact: bool, chunk_size: int, output: Optional[str]):
    """Run a phrase query against a list of YTIDs and emit a manifest."""
    phrases = [token.strip() for token in query.split(",") if token.strip()]
    if not phrases:
        click.echo(click.style("✗ No phrases provided.", fg="red"), err=True)
        return

    ytids = [ln.strip() for ln in Path(ytids_file).read_text().splitlines() if ln.strip()]
    if not ytids:
        click.echo(click.style("✗ No YTIDs found in ytids-file.", fg="red"), err=True)
        return

    repo = WordRepository()
    video_repo = VideoRepository()
    hits_repo = HitsRepository()
    rows: List[Dict[str, str]] = []

    # Resolve source if not provided
    resolved_source = source
    if not source:
        resolved_source = repo.auto_resolve_source(ytids)
    if not resolved_source:
        click.echo(click.style("✗ Unable to auto-resolve source for provided YTIDs; please specify --source.", fg="red"), err=True)
        return
    if resolved_source != source and source:
        click.echo(click.style(f"⚠ Source '{source}' not found for given ytids; using '{resolved_source}'", fg="yellow"), err=True)
    source = resolved_source

    click.echo(f"Searching {len(phrases)} phrase(s) against source '{source}' for {len(ytids)} ytids (chunk_size={chunk_size})...")

    def chunks(seq, size):
        for i in range(0, len(seq), size):
            yield seq[i:i + size]

    for phrase in phrases:
        tokens = phrase.split()
        for batch in chunks(ytids, chunk_size):
            hits = repo.find_phrase_hits(tokens, source=source, limit=limit, exact=exact, ytids=batch)
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

    dest = Path(output) if output else Path("generated") / "query_ids" / name / "hits.tsv"
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
