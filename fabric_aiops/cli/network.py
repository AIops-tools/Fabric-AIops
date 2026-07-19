"""``fabric-aiops network`` — network-scoped reads."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from fabric_aiops.cli._common import (
    LimitOption,
    OrgOption,
    TargetOption,
    cli_errors,
    console,
    get_connection,
    limit_kwargs,
    print_result,
)

network_app = typer.Typer(
    name="network",
    help="Networks: list, get, VLANs, health alerts, traffic summary.",
    no_args_is_help=True,
)

NetIdArg = Annotated[str, typer.Argument(help="Network id (from 'network list')")]


@network_app.command("list")
@cli_errors
def network_list(org_id: OrgOption = None, target: TargetOption = None) -> None:
    """List networks in the organization."""
    from fabric_aiops.ops import networks as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.list_networks(conn, org_id)))


@network_app.command("get")
@cli_errors
def network_get(network_id: NetIdArg, target: TargetOption = None) -> None:
    """Show one network by id."""
    from fabric_aiops.ops import networks as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.get_network(conn, network_id)))


@network_app.command("vlans")
@cli_errors
def network_vlans(network_id: NetIdArg, target: TargetOption = None) -> None:
    """List appliance VLANs configured on a network."""
    from fabric_aiops.ops import networks as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.list_vlans(conn, network_id)))


@network_app.command("alerts")
@cli_errors
def network_alerts(
    network_id: NetIdArg, limit: LimitOption = None, target: TargetOption = None
) -> None:
    """Current network health alerts, summarised by severity."""
    from fabric_aiops.ops import networks as ops

    conn, _ = get_connection(target)
    print_result(ops.network_alerts(conn, network_id, **limit_kwargs(limit)))


@network_app.command("traffic")
@cli_errors
def network_traffic(
    network_id: NetIdArg,
    timespan: Annotated[int, typer.Option(help="Look-back seconds (7200..2592000)")] = 86400,
    limit: LimitOption = None,
    target: TargetOption = None,
) -> None:
    """Application/protocol traffic mix for a network, top apps by bytes."""
    from fabric_aiops.ops import networks as ops

    conn, _ = get_connection(target)
    print_result(ops.traffic_summary(conn, network_id, timespan, **limit_kwargs(limit)))
