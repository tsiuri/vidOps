# vo_cli.py

import click
from vidops.db import close_pool
from vidops.cli.status import status
from vidops.cli.worker import worker
from vidops.cli.download import download
from vidops.cli.transcribe import transcribe
from vidops.cli.clipping import clip
from vidops.cli.overlord import overlord
from vidops.cli.analysis import analyze
from vidops.cli.diarization import diarize
from vidops.cli.clips import clips
from vidops.cli.dl_subs import dl_subs
from vidops.cli.stitch import stitch
from vidops.cli.voice import voice
from vidops.cli.query_ids import query_ids
from vidops.cli.convert_captions import convert_captions
from vidops.cli.dates import dates
from vidops.cli.extra_utils import extra_utils
from vidops import __version__ # Import the version from the package

@click.group(
    help="""
    VidOps - Unified Video Processing Toolkit (Overlord System)

    This CLI allows you to manage video processing tasks such as downloading,
    transcribing, clipping, analyzing, diarizing, and stitching. It interacts
    with a centralized PostgreSQL database for job management and worker coordination.

    Use 'vo_cli.py <command> --help' for more information on a specific command.
    """
)
@click.version_option(__version__, "-v", "--version", message="VidOps Version: %(version)s")
def cli():
    """VidOps - Unified Video Processing Toolkit"""
    pass

# Register commands
cli.add_command(status)
cli.add_command(worker)
cli.add_command(download)
cli.add_command(transcribe)
cli.add_command(clip)
cli.add_command(overlord)
cli.add_command(analyze)
cli.add_command(diarize)
cli.add_command(clips)
cli.add_command(stitch)
cli.add_command(dl_subs)
cli.add_command(voice)
cli.add_command(query_ids)
cli.add_command(dates)
cli.add_command(extra_utils)
cli.add_command(convert_captions)

if __name__ == '__main__':
    try:
        cli()
    finally:
        # Ensure database connections are closed on exit
        close_pool()
