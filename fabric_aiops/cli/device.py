"""``fabric-aiops device`` — device-scoped reads."""

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

device_app = typer.Typer(
    name="device",
    help="Devices: inventory (by model MX/MS/MR/MV/MG), status, uplinks, ports, SSIDs.",
    no_args_is_help=True,
)

SerialArg = Annotated[str, typer.Argument(help="Device serial (from 'device inventory')")]


@device_app.command("inventory")
@cli_errors
def device_inventory(
    org_id: OrgOption = None,
    model: Annotated[str | None, typer.Option("--model", help="Model family/prefix")] = None,
    limit: LimitOption = None,
    target: TargetOption = None,
) -> None:
    """Org device inventory, optionally filtered by model family."""
    from fabric_aiops.ops import devices as ops

    conn, _ = get_connection(target)
    print_result(ops.inventory(conn, org_id, model, **limit_kwargs(limit)))


@device_app.command("status")
@cli_errors
def device_status(serial: SerialArg, org_id: OrgOption = None, target: TargetOption = None) -> None:
    """Show one device's availability status."""
    from fabric_aiops.ops import devices as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.device_status(conn, serial, org_id)))


@device_app.command("uplinks")
@cli_errors
def device_uplinks(org_id: OrgOption = None, target: TargetOption = None) -> None:
    """Appliance/gateway uplink statuses across the org."""
    from fabric_aiops.ops import devices as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.uplink_status(conn, org_id)))


@device_app.command("switch-ports")
@cli_errors
def device_switch_ports(serial: SerialArg, target: TargetOption = None) -> None:
    """Switch (MS) port configuration for a device."""
    from fabric_aiops.ops import devices as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.switch_ports(conn, serial)))


@device_app.command("ssids")
@cli_errors
def device_ssids(
    network_id: Annotated[str, typer.Argument(help="Network id the APs belong to")],
    target: TargetOption = None,
) -> None:
    """Wireless (MR) SSIDs configured on a network."""
    from fabric_aiops.ops import devices as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.wireless_ssids(conn, network_id)))
