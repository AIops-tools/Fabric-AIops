"""``fabric-aiops remediate`` — guarded Meraki writes (dry-run + double-confirm).

Real writes are delegated to the ``@governed_tool``-decorated functions in
``mcp_server.tools.remediation`` so every CLI write is audited, budget-counted,
risk-tiered and undo-recorded by the governance harness — the same path the MCP
tools take. The dry-run preview and double-confirm gate stay in the CLI.

Every governed result goes through ``governed()``, which turns the ``{"error":
...}`` envelope ``@tool_errors`` produces into exit code 1. Printing a refusal
and exiting 0 tells a CI job or a shell ``&&`` chain that a blocked write
landed — the write is stopped either way, but the exit code is what a caller
actually branches on.

``--dry-run`` runs the SAME governed twin with ``dry_run=True`` rather than
printing a hand-written guess. Three things follow. Every guard the real call
would hit — a platform that does not map the write, a bind that would destroy
its own undo — fires on the preview too, and reaches the operator as a refusal
with exit code 1 instead of a green banner. The preview lands the same audit
row the MCP preview always did; the CLI skipping it was the outlier. And the
banner's parameters are the twin's own resolved values, so they cannot drift
from what the command actually does.
"""

from __future__ import annotations

import json
from typing import Annotated

import typer

from fabric_aiops.cli._common import (
    DryRunOption,
    TargetOption,
    cli_errors,
    console,
    double_confirm,
    dry_run_print,
    governed,
)

remediate_app = typer.Typer(
    name="remediate",
    help="Guarded writes: reboot/blink/update/claim/remove/bind/unbind.",
    no_args_is_help=True,
)

SerialArg = Annotated[str, typer.Argument(help="Device serial")]
NetIdArg = Annotated[str, typer.Argument(help="Network id")]


@remediate_app.command("reboot")
@cli_errors
def reboot(serial: SerialArg, target: TargetOption = None, dry_run: DryRunOption = False) -> None:
    """Reboot a device (no undo; dry-run + confirm)."""
    from mcp_server.tools import remediation as gov

    if dry_run:
        preview = governed(gov.reboot_device(serial=serial, dry_run=True, target=target))
        dry_run_print(
            operation="reboot_device",
            api_call=f"POST /devices/{serial}/reboot",
            parameters=preview.get("wouldReboot", {}),
        )
        return
    double_confirm("reboot", serial)
    console.print_json(json.dumps(governed(gov.reboot_device(serial=serial, target=target))))


@remediate_app.command("blink-leds")
@cli_errors
def blink_leds(
    serial: SerialArg,
    duration: Annotated[int, typer.Option(help="Blink seconds (5..120)")] = 20,
    target: TargetOption = None,
) -> None:
    """Blink a device's locator LEDs (low risk; no confirm needed)."""
    from mcp_server.tools import remediation as gov

    console.print_json(
        json.dumps(governed(gov.blink_device_leds(serial=serial, duration=duration, target=target)))
    )


@remediate_app.command("update-device")
@cli_errors
def update_device(
    serial: SerialArg,
    attrs_json: Annotated[str, typer.Argument(help='JSON of attrs, e.g. {"name":"ap1"}')],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Update device attributes (captures before-state; dry-run + confirm)."""
    from mcp_server.tools import remediation as gov

    attrs = json.loads(attrs_json)
    if dry_run:
        preview = governed(
            gov.update_device(serial=serial, attrs=attrs, dry_run=True, target=target)
        )
        dry_run_print(
            operation="update_device",
            api_call=f"PUT /devices/{serial}",
            parameters=preview.get("wouldUpdate", {}).get("attrs", attrs),
        )
        return
    double_confirm("update attributes on", serial)
    console.print_json(
        json.dumps(governed(gov.update_device(serial=serial, attrs=attrs, target=target)))
    )


@remediate_app.command("update-vlan")
@cli_errors
def update_vlan(
    network_id: NetIdArg,
    vlan_id: Annotated[str, typer.Argument(help="VLAN id")],
    attrs_json: Annotated[str, typer.Argument(help='JSON of attrs, e.g. {"name":"data"}')],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Update an appliance VLAN (captures before-state; dry-run + confirm)."""
    from mcp_server.tools import remediation as gov

    attrs = json.loads(attrs_json)
    if dry_run:
        preview = governed(
            gov.update_network_vlan(
                network_id=network_id, vlan_id=vlan_id, attrs=attrs, dry_run=True, target=target
            )
        )
        dry_run_print(
            operation="update_network_vlan",
            api_call=f"PUT /networks/{network_id}/appliance/vlans/{vlan_id}",
            parameters=preview.get("wouldUpdate", {}).get("attrs", attrs),
        )
        return
    double_confirm(f"update VLAN {vlan_id} on", network_id)
    console.print_json(
        json.dumps(
            governed(
                gov.update_network_vlan(
                    network_id=network_id, vlan_id=vlan_id, attrs=attrs, target=target
                )
            )
        )
    )


@remediate_app.command("claim")
@cli_errors
def claim(
    network_id: NetIdArg,
    serials: Annotated[list[str], typer.Argument(help="Device serials to claim")],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Claim devices into a network (dry-run + confirm)."""
    from mcp_server.tools import remediation as gov

    if dry_run:
        preview = governed(
            gov.claim_devices_into_network(
                network_id=network_id, serials=serials, dry_run=True, target=target
            )
        )
        dry_run_print(
            operation="claim_devices_into_network",
            api_call=f"POST /networks/{network_id}/devices/claim",
            parameters=preview.get("wouldClaim", {}),
        )
        return
    double_confirm(f"claim {len(serials)} device(s) into", network_id)
    console.print_json(
        json.dumps(
            governed(
                gov.claim_devices_into_network(
                    network_id=network_id, serials=serials, target=target
                )
            )
        )
    )


@remediate_app.command("remove")
@cli_errors
def remove(
    network_id: NetIdArg,
    serial: SerialArg,
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Remove a device from a network (dry-run + confirm)."""
    from mcp_server.tools import remediation as gov

    if dry_run:
        preview = governed(
            gov.remove_device_from_network(
                network_id=network_id, serial=serial, dry_run=True, target=target
            )
        )
        dry_run_print(
            operation="remove_device_from_network",
            api_call=f"POST /networks/{network_id}/devices/remove",
            parameters=preview.get("wouldRemove", {}),
        )
        return
    double_confirm(f"remove device {serial} from", network_id)
    console.print_json(
        json.dumps(
            governed(
                gov.remove_device_from_network(
                    network_id=network_id, serial=serial, target=target
                )
            )
        )
    )


@remediate_app.command("bind")
@cli_errors
def bind(
    network_id: NetIdArg,
    template_id: Annotated[str, typer.Argument(help="Config template id")],
    auto_bind: Annotated[bool, typer.Option(help="Auto-bind switch/AP profiles")] = False,
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Bind a network to a config template (captures before-state; dry-run + confirm).

    Refuses when the bind would overwrite local VLANs this tool cannot restore.
    """
    from mcp_server.tools import remediation as gov

    if dry_run:
        # Through the governed twin, which runs the refusal check (a read) before
        # it previews: --dry-run can never show green for a bind the confirmed
        # run would refuse. ``reversible`` is the twin's own verdict on whether
        # an undo could restore this network, which is the fact worth knowing
        # BEFORE binding, not after.
        preview = governed(
            gov.bind_network_to_template(
                network_id=network_id,
                template_id=template_id,
                auto_bind=auto_bind,
                dry_run=True,
                target=target,
            )
        )
        dry_run_print(
            operation="bind_network_to_template",
            api_call=f"POST /networks/{network_id}/bind",
            parameters={
                "configTemplateId": template_id,
                "autoBind": auto_bind,
                "reversible": preview.get("reversible"),
            },
        )
        return
    double_confirm(f"bind to template {template_id}", network_id)

    console.print_json(
        json.dumps(
            governed(
                gov.bind_network_to_template(
                    network_id=network_id,
                    template_id=template_id,
                    auto_bind=auto_bind,
                    target=target,
                )
            )
        )
    )


@remediate_app.command("unbind")
@cli_errors
def unbind(
    network_id: NetIdArg,
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Unbind a network from its config template (captures before-state; dry-run + confirm)."""
    from mcp_server.tools import remediation as gov

    if dry_run:
        preview = governed(
            gov.unbind_network_from_template(
                network_id=network_id, dry_run=True, target=target
            )
        )
        dry_run_print(
            operation="unbind_network_from_template",
            api_call=f"POST /networks/{network_id}/unbind",
            parameters=preview.get("wouldUnbind", {}),
        )
        return
    double_confirm("unbind from its config template", network_id)
    console.print_json(
        json.dumps(governed(gov.unbind_network_from_template(network_id=network_id, target=target)))
    )
