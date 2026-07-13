"""``fabric-aiops client`` — client-scoped reads."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from fabric_aiops.cli._common import TargetOption, cli_errors, console, get_connection

client_app = typer.Typer(
    name="client",
    help="Clients on a network: list, detail, usage, connectivity.",
    no_args_is_help=True,
)

NetIdArg = Annotated[str, typer.Argument(help="Network id (from 'network list')")]
ClientIdArg = Annotated[str, typer.Argument(help="Client id/MAC (from 'client list')")]


@client_app.command("list")
@cli_errors
def client_list(
    network_id: NetIdArg,
    timespan: Annotated[int, typer.Option(help="Look-back seconds (7200..2592000)")] = 86400,
    target: TargetOption = None,
) -> None:
    """List clients seen on a network."""
    from fabric_aiops.ops import clients as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.list_clients(conn, network_id, timespan)))


@client_app.command("get")
@cli_errors
def client_get(network_id: NetIdArg, client_id: ClientIdArg, target: TargetOption = None) -> None:
    """Show one client's detail."""
    from fabric_aiops.ops import clients as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.get_client(conn, network_id, client_id)))


@client_app.command("usage")
@cli_errors
def client_usage(network_id: NetIdArg, client_id: ClientIdArg, target: TargetOption = None) -> None:
    """A client's usage history rolled up to total sent/received KB."""
    from fabric_aiops.ops import clients as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.client_usage(conn, network_id, client_id)))


@client_app.command("connectivity")
@cli_errors
def client_connectivity(
    network_id: NetIdArg, client_id: ClientIdArg, target: TargetOption = None
) -> None:
    """A client's connection-quality stats (assoc/auth/dhcp/dns/success)."""
    from fabric_aiops.ops import clients as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.client_connectivity(conn, network_id, client_id)))
