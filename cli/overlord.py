# vidops/cli/overlord.py

import click
from services import get_overlord_service
import logging

logger = logging.getLogger(__name__)

@click.group()
def overlord():
    """Manage and start the Overlord service."""
    pass

@overlord.command("start")
def start_overlord():
    """Starts the Overlord service."""
    click.echo("Starting Overlord service...")
    
    try:
        service = get_overlord_service()
        service.run()
    except Exception as e:
        logger.error(f"Error running Overlord service: {e}", exc_info=True)
        click.echo(click.style(f"✗ Overlord service failed: {e}", fg="red"), err=True)

