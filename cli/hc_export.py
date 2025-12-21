"""vidops/cli/hc_export.py

Phase 7: Explicit export for Hits & Clips projects.

This command is intentionally explicit and side-effect free w.r.t. DB.
It reads the current finalized artifact set (Phase 5) and packages it.
"""

from __future__ import annotations

import click

from services.hc_export import HCProjectExportService


@click.command(name="hc-export")
@click.argument("project_id")
@click.option("--output-root", default=None, help="Base directory to write exports under (default: <workspace>/exports)")
@click.option("--allow-nonfinalized", is_flag=True, help="Allow exporting latest versions even if project is not finalized")
@click.option("--no-assets", is_flag=True, help="Do not copy clip asset files; export manifest only")
def hc_export(project_id: str, output_root: str | None, allow_nonfinalized: bool, no_assets: bool):
    """Export a Hits & Clips project to a zip + manifest.

    By default this requires the project be finalized (Phase 5), so the
    exported set is exactly frozen and auditably defined.
    """
    svc = HCProjectExportService()
    res = svc.export_project(
        project_id,
        actor="cli",
        output_root=output_root,
        require_finalized=not allow_nonfinalized,
        include_assets=not no_assets,
    )
    click.echo(f"export_id: {res.export_id}")
    click.echo(f"export_dir: {res.export_dir}")
    click.echo(f"zip_path:   {res.zip_path}")
