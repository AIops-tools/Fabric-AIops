"""Remediation writes (guarded).

Every state-changing operation that *can* be reversed reads the controller's
current state **before** it changes anything, so the harness records a faithful
undo / audit trail (the before-state is fetched, never guessed). Irreversible
ops (reboot, blink) record the prior state for audit but offer no undo.

These are the only writes in the tool; each is gated at the MCP layer by the
governance harness (risk tier + audit + undo) and at the CLI layer by dry-run +
double-confirm.

Write support is per-platform: the reference platform (meraki) maps every
write; on platforms that do not (catalyst, cvp — their change models are
task/configlet workflows that do not map onto these canonical writes), each
function fails fast with a teaching ``PlatformUnsupported`` BEFORE any
controller call — writes are never silently no-oped.
"""

from __future__ import annotations

from typing import Any

from fabric_aiops.ops._util import clean, op_get, op_post, op_put, require_support


def reboot_device(conn: Any, serial: str) -> dict:
    """[WRITE] Reboot a device, capturing its prior status. No safe inverse."""
    require_support(conn, "devices.reboot")
    prior = _device_status_safe(conn, serial)
    op_post(conn, "devices.reboot", serial=serial)
    return {
        "action": "reboot_device",
        "serial": serial,
        "priorState": {"status": prior.get("status")},
    }


def blink_device_leds(conn: Any, serial: str, duration: int = 20) -> dict:
    """[WRITE] Blink a device's locator LEDs (physical-locate aid). No state change."""
    require_support(conn, "devices.blink_leds")
    duration = max(5, min(int(duration), 120))
    op_post(conn, "devices.blink_leds", json_body={"duration": duration}, serial=serial)
    return {"action": "blink_device_leds", "serial": serial, "durationSeconds": duration}


def update_device(conn: Any, serial: str, attrs: dict) -> dict:
    """[WRITE] Update device attributes (name/tags/address/notes), capturing before.

    Reads the device first so the response carries ``priorState`` for the exact
    keys being changed (drives undo + audit); then PUTs the update.
    """
    require_support(conn, "devices.update", "devices.get")
    allowed = {"name", "tags", "address", "notes", "lat", "lng", "floorPlanId"}
    payload = {k: v for k, v in (attrs or {}).items() if k in allowed}
    prior_full = clean(op_get(conn, "devices.get", serial=serial))
    prior = {k: prior_full.get(k) for k in payload}
    op_put(conn, "devices.update", json_body=payload, serial=serial)
    return {
        "action": "update_device",
        "serial": serial,
        "changed": payload,
        "priorState": prior,
    }


def update_network_vlan(conn: Any, network_id: str, vlan_id: str, attrs: dict) -> dict:
    """[WRITE] Update an appliance VLAN, capturing the changed keys' prior values."""
    require_support(conn, "networks.vlan_update", "networks.vlan_get")
    allowed = {"name", "subnet", "applianceIp", "groupPolicyId", "dhcpHandling"}
    payload = {k: v for k, v in (attrs or {}).items() if k in allowed}
    prior_full = clean(
        op_get(conn, "networks.vlan_get", network_id=network_id, vlan_id=vlan_id)
    )
    prior = {k: prior_full.get(k) for k in payload}
    op_put(
        conn,
        "networks.vlan_update",
        json_body=payload,
        network_id=network_id,
        vlan_id=vlan_id,
    )
    return {
        "action": "update_network_vlan",
        "networkId": network_id,
        "vlanId": vlan_id,
        "changed": payload,
        "priorState": prior,
    }


def claim_devices_into_network(conn: Any, network_id: str, serials: list[str]) -> dict:
    """[WRITE] Claim devices into a network. Inverse: remove each device.

    Records the claimed serials so the harness can offer an undo (remove them).
    """
    require_support(conn, "networks.claim_devices")
    serial_list = [str(s) for s in (serials or []) if s]
    op_post(
        conn,
        "networks.claim_devices",
        json_body={"serials": serial_list},
        network_id=network_id,
    )
    return {
        "action": "claim_devices_into_network",
        "networkId": network_id,
        "serials": serial_list,
        "priorState": {"claimedSerials": serial_list},
    }


def remove_device_from_network(conn: Any, network_id: str, serial: str) -> dict:
    """[WRITE] Remove a device from a network. Inverse: claim it back.

    The device's current network is exactly ``network_id``, captured for undo.
    """
    require_support(conn, "networks.remove_device")
    op_post(
        conn,
        "networks.remove_device",
        json_body={"serial": serial},
        network_id=network_id,
    )
    return {
        "action": "remove_device_from_network",
        "networkId": network_id,
        "serial": serial,
        "priorState": {"networkId": network_id, "serial": serial},
    }


def bind_network_to_template(
    conn: Any, network_id: str, template_id: str, auto_bind: bool = False
) -> dict:
    """[WRITE] Bind a network to a config template, capturing the prior binding.

    Reads the network first to capture any template it was already bound to, so
    undo can rebind to the prior template (or unbind when there was none).
    """
    require_support(conn, "networks.bind_template", "networks.get")
    prior = clean(op_get(conn, "networks.get", network_id=network_id))
    prior_template = prior.get("configTemplateId")
    op_post(
        conn,
        "networks.bind_template",
        json_body={"configTemplateId": template_id, "autoBind": bool(auto_bind)},
        network_id=network_id,
    )
    return {
        "action": "bind_network_to_template",
        "networkId": network_id,
        "templateId": template_id,
        "priorState": {"configTemplateId": prior_template},
    }


def unbind_network_from_template(conn: Any, network_id: str) -> dict:
    """[WRITE] Unbind a network from its config template, capturing the prior binding.

    Reads the network first so undo can rebind to the template it was bound to.
    """
    require_support(conn, "networks.unbind_template", "networks.get")
    prior = clean(op_get(conn, "networks.get", network_id=network_id))
    prior_template = prior.get("configTemplateId")
    op_post(conn, "networks.unbind_template", network_id=network_id)
    return {
        "action": "unbind_network_from_template",
        "networkId": network_id,
        "priorState": {"configTemplateId": prior_template},
    }


def _device_status_safe(conn: Any, serial: str) -> dict:
    """Best-effort device record for before-state capture (never raises)."""
    try:
        rec = clean(op_get(conn, "devices.get", serial=serial))
        return rec if isinstance(rec, dict) else {}
    except Exception:  # noqa: BLE001 — before-state is advisory for a reboot
        return {}
