"""Top-level Typer app: assembles sub-apps and top-level commands."""

from __future__ import annotations

import typer

from fabric_aiops.cli._common import cli_errors
from fabric_aiops.cli.client import client_app
from fabric_aiops.cli.device import device_app
from fabric_aiops.cli.doctor import doctor_cmd
from fabric_aiops.cli.health import health_app
from fabric_aiops.cli.init import init_cmd
from fabric_aiops.cli.network import network_app
from fabric_aiops.cli.org import org_app
from fabric_aiops.cli.overview import overview_cmd
from fabric_aiops.cli.remediate import remediate_app
from fabric_aiops.cli.secret import secret_app
from fabric_aiops.cli.undo import undo_app

app = typer.Typer(
    name="fabric-aiops",
    help="Governed AI-ops for Cisco Meraki network fabrics (organizations / networks / devices).",
    no_args_is_help=True,
)

app.add_typer(org_app, name="org")
app.add_typer(network_app, name="network")
app.add_typer(device_app, name="device")
app.add_typer(client_app, name="client")
app.add_typer(health_app, name="health")
app.add_typer(remediate_app, name="remediate")
app.add_typer(secret_app, name="secret")
app.add_typer(undo_app, name="undo")
app.command("init")(init_cmd)
app.command("overview")(overview_cmd)
app.command("doctor")(doctor_cmd)


@app.command("mcp")
@cli_errors
def mcp_cmd() -> None:
    """Start the MCP server (stdio transport).

    Single-command entry point for MCP clients (does not go through uvx/PyPI
    resolution at launch):
        fabric-aiops mcp
    """
    import sys

    if sys.version_info < (3, 11):
        typer.echo(
            f"ERROR: fabric-aiops requires Python >= 3.11 "
            f"(got {sys.version_info.major}.{sys.version_info.minor}).\n"
            f"Fix: uv python install 3.12 && "
            f"uv tool install --python 3.12 --force fabric-aiops",
            err=True,
        )
        raise typer.Exit(2)

    from mcp_server.server import main as _mcp_main

    _mcp_main()


if __name__ == "__main__":
    app()
