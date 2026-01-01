# cli/monitor.py
"""CLI wrapper for the curses-based jobs queue monitor."""

import click


@click.command(help="Launch the live curses-based jobs queue monitor")
@click.option("--interval", "-i", type=float, default=1.0, help="Refresh interval in seconds")
@click.option("--limit", "-l", type=int, default=10, help="Max rows per section")
def monitor(interval: float, limit: int) -> None:
    """
    Watch the jobs queue in a live curses UI.

    Controls:
      q         - quit
      ↑/↓       - move cursor
      ←/→       - horizontal scroll
      PgUp/PgDn - page scroll
      x         - cancel pending job under cursor (or all pending if on status row)
    """
    import curses
    from argparse import Namespace
    from scripts.management.watch_jobs import draw

    args = Namespace(interval=interval, limit=limit)
    curses.wrapper(draw, args)
