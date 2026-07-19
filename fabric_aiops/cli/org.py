"""``fabric-aiops org`` — organization-scoped reads."""

from __future__ import annotations

import json

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

org_app = typer.Typer(
    name="org",
    help="Organizations: list/get, licensing, admins, device statuses, API usage.",
    no_args_is_help=True,
)


@org_app.command("list")
@cli_errors
def org_list(target: TargetOption = None) -> None:
    """List organizations visible to the API key."""
    from fabric_aiops.ops import organizations as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.list_organizations(conn)))


@org_app.command("get")
@cli_errors
def org_get(org_id: OrgOption = None, target: TargetOption = None) -> None:
    """Show one organization by id."""
    from fabric_aiops.ops import organizations as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.get_organization(conn, org_id)))


@org_app.command("licensing")
@cli_errors
def org_licensing(org_id: OrgOption = None, target: TargetOption = None) -> None:
    """Org licensing overview."""
    from fabric_aiops.ops import organizations as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.licensing_overview(conn, org_id)))


@org_app.command("admins")
@cli_errors
def org_admins(org_id: OrgOption = None, target: TargetOption = None) -> None:
    """List dashboard administrators for the org."""
    from fabric_aiops.ops import organizations as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.list_admins(conn, org_id)))


@org_app.command("device-statuses")
@cli_errors
def org_device_statuses(
    org_id: OrgOption = None, limit: LimitOption = None, target: TargetOption = None
) -> None:
    """Org-wide device availability rolled up by status and product type."""
    from fabric_aiops.ops import organizations as ops

    conn, _ = get_connection(target)
    print_result(ops.device_statuses(conn, org_id, **limit_kwargs(limit)))


@org_app.command("api-usage")
@cli_errors
def org_api_usage(org_id: OrgOption = None, target: TargetOption = None) -> None:
    """Org API-request usage overview (response-code counts, 429 rate-limits)."""
    from fabric_aiops.ops import organizations as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.api_request_usage(conn, org_id)))
