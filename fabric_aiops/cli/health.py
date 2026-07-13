"""``fabric-aiops health`` — the flagship signature analyses."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from fabric_aiops.cli._common import OrgOption, TargetOption, cli_errors, console, get_connection

health_app = typer.Typer(
    name="health",
    help="Flagship analyses: uplink loss/latency RCA, fleet health score.",
    no_args_is_help=True,
)


@health_app.command("uplink-rca")
@cli_errors
def health_uplink_rca(
    loss_pct: Annotated[float, typer.Option(help="Avg loss %% = degraded")] = 5.0,
    latency_ms: Annotated[float, typer.Option(help="Avg latency ms = degraded")] = 150.0,
    org_id: OrgOption = None,
    target: TargetOption = None,
) -> None:
    """Rank the worst MX WAN uplinks by loss + latency and map cause + action."""
    from fabric_aiops.ops import health as ops

    conn, _ = get_connection(target)
    records = ops.pull_uplink_loss_latency(conn, org_id)
    result = ops.uplink_loss_and_latency_rca(records, loss_pct=loss_pct, latency_ms=latency_ms)
    console.print_json(json.dumps(result))


@health_app.command("score")
@cli_errors
def health_score(org_id: OrgOption = None, target: TargetOption = None) -> None:
    """Composite per-network health score from live org device + uplink status."""
    from fabric_aiops.ops import devices as dev
    from fabric_aiops.ops import health as ops
    from fabric_aiops.ops import organizations as org

    conn, _ = get_connection(target)
    device_rows = org.device_statuses(conn, org_id).get("devices", [])
    uplinks = dev.uplink_status(conn, org_id)
    console.print_json(json.dumps(ops.network_health_score(device_rows, uplinks=uplinks)))
