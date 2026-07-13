"""``fabric-aiops overview`` — one-shot fabric fleet health."""

from __future__ import annotations

import json

from fabric_aiops.cli._common import OrgOption, TargetOption, cli_errors, console, get_connection


@cli_errors
def overview_cmd(org_id: OrgOption = None, target: TargetOption = None) -> None:
    """One-shot fabric fleet health: networks + device status/product rollup."""
    from fabric_aiops.ops import overview as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.fleet_overview(conn, org_id)))
