"""``fabric-aiops remediate`` — guarded Meraki writes (dry-run + double-confirm)."""

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
    get_connection,
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
    from fabric_aiops.ops import remediation as ops

    if dry_run:
        dry_run_print(operation="reboot_device", api_call=f"POST /devices/{serial}/reboot")
        return
    double_confirm("reboot", serial)
    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.reboot_device(conn, serial)))


@remediate_app.command("blink-leds")
@cli_errors
def blink_leds(
    serial: SerialArg,
    duration: Annotated[int, typer.Option(help="Blink seconds (5..120)")] = 20,
    target: TargetOption = None,
) -> None:
    """Blink a device's locator LEDs (low risk; no confirm needed)."""
    from fabric_aiops.ops import remediation as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.blink_device_leds(conn, serial, duration)))


@remediate_app.command("update-device")
@cli_errors
def update_device(
    serial: SerialArg,
    attrs_json: Annotated[str, typer.Argument(help='JSON of attrs, e.g. {"name":"ap1"}')],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Update device attributes (captures before-state; dry-run + confirm)."""
    from fabric_aiops.ops import remediation as ops

    attrs = json.loads(attrs_json)
    if dry_run:
        dry_run_print(
            operation="update_device", api_call=f"PUT /devices/{serial}", parameters=attrs
        )
        return
    double_confirm("update attributes on", serial)
    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.update_device(conn, serial, attrs)))


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
    from fabric_aiops.ops import remediation as ops

    attrs = json.loads(attrs_json)
    if dry_run:
        dry_run_print(
            operation="update_network_vlan",
            api_call=f"PUT /networks/{network_id}/appliance/vlans/{vlan_id}",
            parameters=attrs,
        )
        return
    double_confirm(f"update VLAN {vlan_id} on", network_id)
    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.update_network_vlan(conn, network_id, vlan_id, attrs)))


@remediate_app.command("claim")
@cli_errors
def claim(
    network_id: NetIdArg,
    serials: Annotated[list[str], typer.Argument(help="Device serials to claim")],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Claim devices into a network (dry-run + confirm)."""
    from fabric_aiops.ops import remediation as ops

    if dry_run:
        dry_run_print(
            operation="claim_devices_into_network",
            api_call=f"POST /networks/{network_id}/devices/claim",
            parameters={"serials": serials},
        )
        return
    double_confirm(f"claim {len(serials)} device(s) into", network_id)
    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.claim_devices_into_network(conn, network_id, serials)))


@remediate_app.command("remove")
@cli_errors
def remove(
    network_id: NetIdArg,
    serial: SerialArg,
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Remove a device from a network (dry-run + confirm)."""
    from fabric_aiops.ops import remediation as ops

    if dry_run:
        dry_run_print(
            operation="remove_device_from_network",
            api_call=f"POST /networks/{network_id}/devices/remove",
            parameters={"serial": serial},
        )
        return
    double_confirm(f"remove device {serial} from", network_id)
    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.remove_device_from_network(conn, network_id, serial)))


@remediate_app.command("bind")
@cli_errors
def bind(
    network_id: NetIdArg,
    template_id: Annotated[str, typer.Argument(help="Config template id")],
    auto_bind: Annotated[bool, typer.Option(help="Auto-bind switch/AP profiles")] = False,
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Bind a network to a config template (captures before-state; dry-run + confirm)."""
    from fabric_aiops.ops import remediation as ops

    if dry_run:
        dry_run_print(
            operation="bind_network_to_template",
            api_call=f"POST /networks/{network_id}/bind",
            parameters={"configTemplateId": template_id, "autoBind": auto_bind},
        )
        return
    double_confirm(f"bind to template {template_id}", network_id)
    conn, _ = get_connection(target)
    console.print_json(
        json.dumps(ops.bind_network_to_template(conn, network_id, template_id, auto_bind))
    )


@remediate_app.command("unbind")
@cli_errors
def unbind(
    network_id: NetIdArg,
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Unbind a network from its config template (captures before-state; dry-run + confirm)."""
    from fabric_aiops.ops import remediation as ops

    if dry_run:
        dry_run_print(
            operation="unbind_network_from_template",
            api_call=f"POST /networks/{network_id}/unbind",
        )
        return
    double_confirm("unbind from its config template", network_id)
    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.unbind_network_from_template(conn, network_id)))
