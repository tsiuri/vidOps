# vidops/cli/diarization.py

import click
from vidops.services import get_diarization_service
import logging

logger = logging.getLogger(__name__)

@click.group()
def diarize():
    """Enqueue and manage diarization jobs."""
    pass

@diarize.command("enqueue")
@click.argument("ytid")
@click.option("--transcript-kind", required=True, help="The kind of transcript to use for diarization (e.g., 'words_whisper_medium'). Use 'best' to auto-select the highest quality available.")
@click.option("--model", default="resemblyzer", help="The diarization model to use (e.g., 'resemblyzer').")
@click.option("--priority", type=int, default=0, help="Job priority.")
@click.option(
    "--reference-dir",
    help="Directory under storage containing reference.json and clips (default: generated/diary_reference/<ytid>).",
)
@click.option(
    "--words-path",
    help="Relative path to words TSV under storage (default: transcript path for transcript-kind).",
)
@click.option("--device", default="auto", show_default=True, help="Device hint for the legacy diarization script.")
@click.option(
    "--chunk-seconds",
    type=float,
    default=6.0,
    show_default=True,
    help="Chunk size passed to the legacy diarization runner.",
)
@click.option(
    "--overlap-seconds",
    type=float,
    default=1.0,
    show_default=True,
    help="Chunk overlap passed to the legacy diarization runner.",
)
@click.option(
    "--similarity-threshold",
    type=float,
    default=0.6,
    show_default=True,
    help="Speaker similarity threshold for the legacy runner.",
)
@click.option(
    "--gap-threshold",
    type=float,
    default=0.15,
    show_default=True,
    help="Gap tolerance when aligning words to speaker spans.",
)
@click.option(
    "--output-dir",
    help="Relative output directory under storage (default: generated/diarization_resemblyzer/<ytid>).",
)
@click.option(
    "--build-reference",
    is_flag=True,
    help="Generate reference clips interactively if missing before diarization.",
)
def enqueue_diarization(
    ytid: str,
    transcript_kind: str,
    model: str,
    priority: int,
    reference_dir: str | None,
    words_path: str | None,
    device: str,
    chunk_seconds: float,
    overlap_seconds: float,
    similarity_threshold: float,
    gap_threshold: float,
    output_dir: str | None,
    build_reference: bool,
):
    """
    Enqueue a single diarization job.

    Examples:
        # Use specific transcript kind
        vo diarize enqueue 7TdvWiRdhqk --transcript-kind words_whisper_medium

        # Auto-select best available transcript
        vo diarize enqueue 7TdvWiRdhqk --transcript-kind best

        # With custom reference directory
        vo diarize enqueue 7TdvWiRdhqk --transcript-kind best \\
            --reference-dir generated/diary_reference/7TdvWiRdhqk
    """
    click.echo(
        f"Enqueuing diarization job for YTID: {ytid} "
        f"(Transcript Kind: {transcript_kind}, Model: {model}, Priority: {priority})..."
    )

    try:
        service = get_diarization_service()
        job = service.enqueue_diarization_job(
            ytid=ytid,
            transcript_kind=transcript_kind,
            diarization_model=model,
            priority=priority,
            reference_dir=reference_dir,
            words_path=words_path,
            device=device,
            chunk_seconds=chunk_seconds,
            overlap_seconds=overlap_seconds,
            similarity_threshold=similarity_threshold,
            gap_threshold=gap_threshold,
            output_dir=output_dir,
            build_reference=build_reference,
        )
        # Show which transcript was actually used (helpful when using "best")
        actual_kind = job.config.get("transcript_kind", transcript_kind)
        if transcript_kind.lower() == "best":
            click.echo(f"  Using transcript: {actual_kind}")
        click.echo(click.style(f"✓ Diarization job enqueued: {job.job_id}", fg="green"))
    except ValueError as e:
        click.echo(click.style(f"✗ Failed to enqueue diarization job: {e}", fg="yellow"), err=True)
    except Exception as e:
        logger.error(f"Error enqueuing diarization job for {ytid}: {e}", exc_info=True)
        click.echo(click.style(f"✗ Failed to enqueue diarization job: {e}", fg="red"), err=True)


@diarize.command("enqueue-file")
@click.argument("ytids_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--transcript-kind", required=True, help="Transcript kind to use, or 'best' to auto-select highest quality.")
@click.option("--model", default="resemblyzer", show_default=True, help="Diarization model (e.g., 'resemblyzer').")
@click.option("--priority", type=int, default=0, show_default=True, help="Job priority.")
@click.option("--reference-dir", help="Reference dir under storage (default: generated/diary_reference/<ytid>).")
@click.option("--words-path", help="Words TSV path under storage (default: transcript path for transcript-kind).")
@click.option("--device", default="auto", show_default=True, help="Device hint for the legacy diarization script.")
@click.option("--chunk-seconds", type=float, default=6.0, show_default=True, help="Chunk size for diarization.")
@click.option("--overlap-seconds", type=float, default=1.0, show_default=True, help="Chunk overlap for diarization.")
@click.option("--similarity-threshold", type=float, default=0.6, show_default=True, help="Speaker similarity threshold.")
@click.option("--gap-threshold", type=float, default=0.15, show_default=True, help="Gap tolerance when aligning words.")
@click.option("--output-dir", help="Output dir under storage (default: generated/diarization_resemblyzer/<ytid>).")
@click.option("--build-reference", is_flag=True, help="Generate reference clips if missing before diarization.")
@click.option(
    "--shared-reference-name",
    help="Build/use a shared reference (50 clips sampled across the list) stored under generated/diary_reference/<name>.",
)
def enqueue_diarization_file(
    ytids_file: str,
    transcript_kind: str,
    model: str,
    priority: int,
    reference_dir: str | None,
    words_path: str | None,
    device: str,
    chunk_seconds: float,
    overlap_seconds: float,
    similarity_threshold: float,
    gap_threshold: float,
    output_dir: str | None,
    build_reference: bool,
    shared_reference_name: str | None,
):
    """
    Enqueue diarization jobs from a TSV/CSV list (first column must be YTID, header allowed).

    Example:
        python vo_cli.py diarize enqueue-file generated/query_ids/pop_trigger_ytids.tsv --transcript-kind best --priority 5
    """
    import csv

    ytids: list[str] = []
    with open(ytids_file, "r", encoding="utf-8") as fh:
        # auto-detect tab vs comma by checking first line
        sample = fh.read(1024)
        fh.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters="\t,")
        reader = csv.reader(fh, dialect)
        rows = list(reader)
    if not rows:
        click.echo(click.style("No rows found in ytids file.", fg="yellow"), err=True)
        return

    # Drop header if present (first cell not like YTID)
    def looks_like_ytid(val: str) -> bool:
        return len(val) == 11 and all(ch.isalnum() or ch in "-_" for ch in val)

    for idx, row in enumerate(rows):
        if not row:
            continue
        first = row[0].strip()
        if idx == 0 and not looks_like_ytid(first):
            continue
        if looks_like_ytid(first):
            ytids.append(first)

    if not ytids:
        click.echo(click.style("No YTIDs detected in file.", fg="yellow"), err=True)
        return

    service = get_diarization_service()
    reference_override: str | None = None

    if shared_reference_name:
        click.echo(f"Building shared reference '{shared_reference_name}' from {len(ytids)} YTIDs...")
        try:
            reference_override = service.build_shared_reference(
                ytids=ytids,
                transcript_kind=transcript_kind,
                reference_name=shared_reference_name,
                clips_count=50,
            )
            click.echo(click.style(f"✓ Shared reference ready: {reference_override}", fg="green"))
        except Exception as exc:  # noqa: BLE001
            click.echo(click.style(f"✗ Failed to build shared reference: {exc}", fg="red"), err=True)
            return

    click.echo(f"Enqueuing diarization for {len(ytids)} YTIDs from {ytids_file}...")
    successes: list[tuple[str, str]] = []
    failures: list[tuple[str, str]] = []

    for ytid in ytids:
        try:
            job = service.enqueue_diarization_job(
                ytid=ytid,
                transcript_kind=transcript_kind,
                diarization_model=model,
                priority=priority,
                reference_dir=reference_override or reference_dir,
                words_path=words_path,
                device=device,
                chunk_seconds=chunk_seconds,
                overlap_seconds=overlap_seconds,
                similarity_threshold=similarity_threshold,
                gap_threshold=gap_threshold,
                output_dir=output_dir,
                build_reference=bool(reference_override is None and build_reference),
            )
            successes.append((ytid, job.job_id))
        except Exception as exc:  # noqa: BLE001
            failures.append((ytid, str(exc)))

    if successes:
        click.echo(click.style(f"✓ Enqueued {len(successes)} diarization jobs.", fg="green"))
    if failures:
        click.echo(click.style(f"✗ Failed {len(failures)} YTIDs:", fg="yellow"), err=True)
        for ytid, msg in failures[:20]:
            click.echo(f"  {ytid}: {msg}", err=True)
        if len(failures) > 20:
            click.echo(f"  ... and {len(failures) - 20} more", err=True)
