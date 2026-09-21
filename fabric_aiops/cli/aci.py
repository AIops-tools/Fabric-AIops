"""``fabric-aiops aci`` — Cisco ACI (APIC) reads with ACI-native references."""

from __future__ import annotations

from typing import Annotated

import typer

from fabric_aiops.cli._common import (
    LimitOption,
    TargetOption,
    audited,
    cli_errors,
    get_connection,
    limit_kwargs,
    print_result,
)

aci_app = typer.Typer(
    name="aci",
    help="Cisco ACI (APIC): endpoint path trace, EPG segment, faults (read-only).",
    no_args_is_help=True,
)


@aci_app.command("trace")
@cli_errors
@audited
def aci_trace_cmd(
    mac: Annotated[str | None, typer.Option("--mac", help="Endpoint MAC address")] = None,
    ip: Annotated[str | None, typer.Option("--ip", help="Endpoint IP address")] = None,
    limit: LimitOption = None,
    target: TargetOption = None,
) -> None:
    """Reconstruct an endpoint's static path: leaf/interface → EPG → BD → VRF → contracts."""
    from fabric_aiops.ops import aci_trace as ops

    conn, _ = get_connection(target)
    print_result(ops.endpoint_trace(conn, mac=mac, ip=ip, **limit_kwargs(limit)))


@aci_app.command("segment")
@cli_errors
@audited
def aci_segment_cmd(
    tenant: Annotated[str, typer.Argument(help="Tenant name")],
    app: Annotated[str, typer.Argument(help="Application profile name")],
    epg: Annotated[str, typer.Argument(help="EPG name")],
    target: TargetOption = None,
) -> None:
    """An EPG's bridge domain, VRF and contracts (down to filter entries)."""
    from fabric_aiops.ops import aci_segment as ops

    conn, _ = get_connection(target)
    print_result(ops.segment_for(conn, tenant, app, epg))


@aci_app.command("faults")
@cli_errors
@audited
def aci_faults_cmd(
    severity: Annotated[
        str | None, typer.Option(help="critical/major/minor/warning/info/cleared")
    ] = None,
    include_cleared: Annotated[
        bool, typer.Option("--include-cleared", help="Also list cleared faults")
    ] = False,
    limit: LimitOption = None,
    target: TargetOption = None,
) -> None:
    """Fabric faults, worst severity first (records, not incidents)."""
    from fabric_aiops.ops import aci_faults as ops

    conn, _ = get_connection(target)
    print_result(ops.list_faults(
        conn, severity=severity, include_cleared=include_cleared, **limit_kwargs(limit)
    ))
