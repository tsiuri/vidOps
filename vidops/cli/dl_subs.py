import click
import logging
from pathlib import Path

from vidops.services import get_subtitle_service

logger = logging.getLogger(__name__)


@click.group()
def dl_subs():
    """Download subtitles via legacy dl-subs (queued)."""
    pass


@dl_subs.command("enqueue")
@click.argument("ytid")
@click.option("--lang", default="en", show_default=True, help="Subtitle language to request.")
@click.option("--format", "subtitle_format", default="vtt", show_default=True, help="Subtitle format (vtt, srt, ...).")
@click.option("--priority", type=int, default=50, show_default=True, help="Job priority.")
def enqueue_subtitle_download(ytid: str, lang: str, subtitle_format: str, priority: int):
    """Enqueue a single subtitle download job."""
    click.echo(f"Enqueuing dl-subs for {ytid} ({subtitle_format}, lang={lang})...")
    service = get_subtitle_service()
    try:
        job = service.enqueue_dl_subs(ytid=ytid, lang=lang, subtitle_format=subtitle_format, priority=priority)
    except Exception as exc:
        logger.error("Failed to enqueue dl-subs for %s: %s", ytid, exc, exc_info=True)
        click.echo(click.style(f"✗ Failed to enqueue: {exc}", fg="red"), err=True)
        return
    click.echo(click.style(f"✓ dl-subs job enqueued: {job.job_id}", fg="green"))


@dl_subs.command("enqueue-list")
@click.argument("list_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--lang", default="en", show_default=True, help="Subtitle language to request.")
@click.option("--format", "subtitle_format", default="vtt", show_default=True, help="Subtitle format (vtt, srt, ...).")
@click.option("--priority", type=int, default=50, show_default=True, help="Job priority.")
def enqueue_from_list(list_file: Path, lang: str, subtitle_format: str, priority: int):
    """Enqueue dl-subs jobs from a file of ytids (one per line)."""
    click.echo(f"Enqueuing dl-subs jobs from {list_file} ...")
    service = get_subtitle_service()
    jobs = service.enqueue_dl_subs_from_list(list_file, lang=lang, subtitle_format=subtitle_format, priority=priority)
    click.echo(click.style(f"✓ Enqueued {len(jobs)} jobs", fg="green"))
