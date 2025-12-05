# vidops/cli/quickclip.py

import logging
from typing import Optional

import click

from vidops.dal import VideoRepository, QuickClipRepository, FilesystemCache, JobRepository
from vidops.services.download import DownloadService
from vidops.services.clipping import ClippingService
from vidops.services.quickclip import QuickClipService

logger = logging.getLogger(__name__)


def get_quickclip_service() -> QuickClipService:
    """Initialize and return QuickClipService with all dependencies."""
    video_repo = VideoRepository()
    quickclip_repo = QuickClipRepository()
    job_repo = JobRepository()
    fs_cache = FilesystemCache()

    download_service = DownloadService(video_repo, job_repo, fs_cache)
    clipping_service = ClippingService(video_repo, job_repo, fs_cache)

    return QuickClipService(
        video_repo=video_repo,
        quickclip_repo=quickclip_repo,
        download_service=download_service,
        clipping_service=clipping_service,
        fs_cache=fs_cache,
    )


@click.group()
def quickclip():
    """QuickClip - Fast video clip capture with timestamps and metadata."""
    pass


@quickclip.command("create")
@click.argument("url")
@click.argument("spans", nargs=-1, required=True)
@click.option("-d", "--description", help="Session description (what this collection is about)")
@click.option("--tags", help="Comma-separated tags for searchability")
@click.option("--clips-only", is_flag=True, help="Skip downloading full video, only extract clips")
@click.option("--quality", default="best", help="Quality profile: best (default), 1080p, 720p, audio-only")
@click.option("--name", help="Custom session name (auto-generated if not provided)")
@click.option("--output", help="Custom output directory (default: generated/quickclips/<ytid>)")
@click.option("--force", is_flag=True, help="Force re-download even if video exists")
@click.option("--priority", type=int, default=10, help="Job priority (default: 10)")
def create_quickclip(
    url: str,
    spans: tuple,
    description: Optional[str],
    tags: Optional[str],
    clips_only: bool,
    quality: str,
    name: Optional[str],
    output: Optional[str],
    force: bool,
    priority: int,
):
    """
    Create a QuickClip session with video clips.

    \b
    Examples:
        # Basic usage
        vo quickclip create "https://youtube.com/watch?v=ABC123" "120-145" "300-320" -d "Highlights"

        # With labels
        vo quickclip create "URL" "120-145:intro" "300-320:main" -d "Video breakdown"

        # Clips-only mode
        vo quickclip create "URL" "60-90" "200-250" --clips-only -d "Just the highlights"

        # Using (start) and (end) placeholders
        vo quickclip create "URL" "(start)-60" "300-(end)" -d "Beginning and end"
    """
    click.echo(f"Creating QuickClip session for: {url}")
    click.echo(f"Clips: {len(spans)}")

    try:
        service = get_quickclip_service()
        tags_list = [t.strip() for t in tags.split(",")] if tags else None

        result = service.create_quickclip(
            url=url,
            spans=list(spans),
            description=description,
            tags=tags_list,
            clips_only=clips_only,
            quality=quality,
            session_name=name,
            output_dir=output,
            force=force,
            priority=priority,
        )

        click.echo(click.style(f"\n✓ QuickClip session created: {result['session_id']}", fg="green"))
        click.echo(f"  Session directory: {result['session_dir']}")
        click.echo(f"  Clips: {result['clips_count']}")

        if result.get('download_job_id'):
            click.echo(f"  Download job: {result['download_job_id']}")
        click.echo(f"  Clipping job: {result['clip_job_id']}")

        click.echo(click.style("\n✓ Jobs enqueued. Clips will be created by worker.", fg="green"))
        click.echo(f"\nClips will be saved to: {result['session_dir']}/")

        # Show individual clip info
        click.echo("\nClips:")
        for clip in result['clips']:
            label_str = f" ({clip.get('label')})" if clip.get('label') else ""
            duration = clip['end'] - clip['start']
            click.echo(f"  {clip['index']}. {clip['start']:.1f}s - {clip['end']:.1f}s ({duration:.1f}s){label_str}")

    except Exception as e:
        logger.error(f"Failed to create QuickClip session: {e}", exc_info=True)
        click.echo(click.style(f"✗ Failed: {e}", fg="red"), err=True)
        raise click.Abort()


@quickclip.command("list")
@click.option("--limit", type=int, default=20, help="Number of sessions to show")
def list_sessions(limit: int):
    """List recent QuickClip sessions."""
    try:
        quickclip_repo = QuickClipRepository()
        sessions = quickclip_repo.list_recent_sessions(limit=limit)

        if not sessions:
            click.echo("No QuickClip sessions found.")
            return

        click.echo(f"\nRecent QuickClip sessions ({len(sessions)}):\n")
        for session in sessions:
            session_id = session['session_id']
            title = session.get('title') or session['ytid']
            desc = session.get('description') or "(no description)"
            clips_count = session.get('clips_count', 0)
            created = session['created_at'].strftime("%Y-%m-%d %H:%M") if session.get('created_at') else "unknown"

            # Truncate description for display
            if len(desc) > 50:
                desc = desc[:47] + "..."

            click.echo(f"  {click.style(session_id, fg='cyan')}")
            click.echo(f"    Video: {title}")
            click.echo(f"    Description: {desc}")
            click.echo(f"    Clips: {clips_count} | Created: {created}")
            if session.get('tags'):
                click.echo(f"    Tags: {session['tags']}")
            click.echo()

    except Exception as e:
        logger.error(f"Failed to list sessions: {e}", exc_info=True)
        click.echo(click.style(f"✗ Failed: {e}", fg="red"), err=True)


@quickclip.command("show")
@click.argument("session_id")
def show_session(session_id: str):
    """Show details of a specific QuickClip session."""
    try:
        quickclip_repo = QuickClipRepository()
        session = quickclip_repo.get_session(session_id)

        if not session:
            click.echo(click.style(f"✗ Session not found: {session_id}", fg="red"), err=True)
            return

        clips = quickclip_repo.get_session_clips(session_id)

        # Display session info
        click.echo(f"\n{click.style('Session:', bold=True)} {session['session_id']}")
        click.echo(f"Video: {session['url']}")
        click.echo(f"Created: {session['created_at']}")

        if session.get('description'):
            click.echo(f"\n{click.style('Description:', bold=True)}")
            click.echo(f"  {session['description']}")

        if session.get('tags'):
            click.echo(f"\n{click.style('Tags:', bold=True)} {session['tags']}")

        click.echo(f"\n{click.style('Quality:', bold=True)} {session.get('quality_profile', 'unknown')}")
        click.echo(f"{click.style('Full video saved:', bold=True)} {'Yes' if session.get('full_video_saved') else 'No'}")

        # Display clips
        click.echo(f"\n{click.style(f'Clips ({len(clips)}):', bold=True)}")
        for clip in clips:
            label_str = f" - {clip['label']}" if clip.get('label') else ""
            duration = float(clip['duration_sec'])
            click.echo(
                f"  {clip['clip_index']}. {float(clip['start_sec']):.1f}s - {float(clip['end_sec']):.1f}s "
                f"({duration:.1f}s){label_str}"
            )
            if clip.get('asset_path'):
                click.echo(f"     Path: {clip['asset_path']}")

        # Show directory
        if session.get('session_dir'):
            click.echo(f"\n{click.style('Directory:', bold=True)} {session['session_dir']}/")

    except Exception as e:
        logger.error(f"Failed to show session: {e}", exc_info=True)
        click.echo(click.style(f"✗ Failed: {e}", fg="red"), err=True)


@quickclip.command("search")
@click.argument("query")
def search_sessions(query: str):
    """Search QuickClip sessions by description, tags, or video title."""
    try:
        quickclip_repo = QuickClipRepository()
        sessions = quickclip_repo.search_sessions(query)

        if not sessions:
            click.echo(f"No sessions found matching: {query}")
            return

        click.echo(f"\nFound {len(sessions)} session(s) matching '{query}':\n")
        for session in sessions:
            session_id = session['session_id']
            title = session.get('title') or session['ytid']
            desc = session.get('description') or "(no description)"
            clips_count = session.get('clips_count', 0)

            # Truncate description
            if len(desc) > 50:
                desc = desc[:47] + "..."

            click.echo(f"  {click.style(session_id, fg='cyan')}")
            click.echo(f"    Video: {title}")
            click.echo(f"    Description: {desc}")
            click.echo(f"    Clips: {clips_count}")
            click.echo()

    except Exception as e:
        logger.error(f"Failed to search sessions: {e}", exc_info=True)
        click.echo(click.style(f"✗ Failed: {e}", fg="red"), err=True)


@quickclip.command("browse")
@click.option("--host", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)")
@click.option("--port", type=int, default=5000, help="Port to bind to (default: 5000)")
@click.option("--debug", is_flag=True, help="Enable debug mode")
def browse_web(host: str, port: int, debug: bool):
    """Launch web browser for QuickClip sessions."""
    try:
        from vidops.web.app import app

        url = f"http://{host}:{port}"
        click.echo(click.style(f"\n🎬 Starting QuickClip Browser...", fg="green", bold=True))
        click.echo(f"   Open your browser to: {click.style(url, fg='cyan', underline=True)}\n")
        click.echo("   Press Ctrl+C to stop the server\n")

        app.run(host=host, port=port, debug=debug)

    except ImportError as e:
        click.echo(click.style(f"✗ Failed to import Flask: {e}", fg="red"), err=True)
        click.echo("\nInstall Flask to use the web browser:")
        click.echo("  pip install flask")
    except Exception as e:
        logger.error(f"Failed to start web server: {e}", exc_info=True)
        click.echo(click.style(f"✗ Failed: {e}", fg="red"), err=True)
