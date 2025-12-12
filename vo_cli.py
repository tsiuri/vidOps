# vo_cli.py

import click
from db import close_pool
from cli.status import status
from cli.worker import worker
from cli.download import download
from cli.transcribe import transcribe
from cli.clipping import clip
from cli.overlord import overlord
from cli.analysis import analyze
from cli.diarization import diarize
from cli.clips import clips
from cli.dl_subs import dl_subs
from cli.stitch import stitch
from cli.voice import voice
from cli.query_ids import query_ids
from cli.convert_captions import convert_captions
from cli.dates import dates
from cli.extra_utils import extra_utils
from cli.quickclip import quickclip
from __init__ import __version__  # Import the version from the package

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
cli.add_command(quickclip)

if __name__ == '__main__':
    try:
        cli()
    finally:
        # Ensure database connections are closed on exit
        close_pool()
