"""Remediation writes (guarded).

Every state-changing operation that *can* be reversed reads the controller's
current state **before** it changes anything, so the harness records a faithful
undo / audit trail (the before-state is fetched, never guessed). Irreversible
ops (reboot, blink) record the prior state for audit but offer no undo.

These are the only writes in the tool; each is gated at the MCP layer by the
governance harness (risk tier + audit + undo) and at the CLI layer by dry-run +
double-confirm.

One write refuses outright: ``bind_network_to_template`` will not bind a
network whose local VLANs the bind would overwrite, because nothing in this
tool can put them back — an operation must not destroy the state its own undo
depends on. See :func:`guard_bind_network_to_template`.

Write support is per-platform: the reference platform (meraki) maps every
write; unifi maps device restart only (a devmgr command envelope); on
platforms that map none (catalyst, cvp — their change models are
task/configlet workflows that do not map onto these canonical writes), each
function fails fast with a teaching ``PlatformUnsupported`` BEFORE any
controller call — writes are never silently no-oped.
"""

from __future__ import annotations

from typing import Any

from fabric_aiops.ops import networks as network_ops
from fabric_aiops.ops._util import (
    clean,
    op_get,
    op_post,
    op_put,
    platform_of,
    require_support,
)

# Canonical ops each write needs the target's platform to map. Declared once,
# here, so the *preview* can run the same support check as the real call without
# restating the key list at the MCP layer — a second copy would drift, and the
# failure mode of drift is a preview that says a write is available on a
# platform that will refuse it.
WRITE_SUPPORT: dict[str, tuple[str, ...]] = {
    "reboot_device": ("devices.reboot",),
    "blink_device_leds": ("devices.blink_leds",),
    "update_device": ("devices.update", "devices.get"),
    "update_network_vlan": ("networks.vlan_update", "networks.vlan_get"),
    "claim_devices_into_network": ("networks.claim_devices",),
    "remove_device_from_network": ("networks.remove_device",),
    "bind_network_to_template": ("networks.bind_template", "networks.get"),
    "unbind_network_from_template": ("networks.unbind_template", "networks.get"),
}


def require_write_support(conn: Any, action: str) -> None:
    """Fail fast with ``PlatformUnsupported`` when the target cannot do ``action``.

    A local descriptor lookup — no controller call — so it is safe to run on the
    dry-run path, which is the point: "this platform does not map that write" is
    the answer the preview exists to give.
    """
    require_support(conn, *WRITE_SUPPORT[action])


def reboot_device(conn: Any, serial: str) -> dict:
    """[WRITE] Reboot a device, capturing its prior status. No safe inverse."""
    require_write_support(conn, "reboot_device")
    prior = _device_status_safe(conn, serial)
    op_post(conn, "devices.reboot", serial=serial)
    return {
        "action": "reboot_device",
        "serial": serial,
        "priorState": {"status": prior.get("status")},
    }


def blink_device_leds(conn: Any, serial: str, duration: int = 20) -> dict:
    """[WRITE] Blink a device's locator LEDs (physical-locate aid). No state change."""
    require_write_support(conn, "blink_device_leds")
    duration = max(5, min(int(duration), 120))
    op_post(conn, "devices.blink_leds", json_body={"duration": duration}, serial=serial)
    return {"action": "blink_device_leds", "serial": serial, "durationSeconds": duration}


def update_device(conn: Any, serial: str, attrs: dict) -> dict:
    """[WRITE] Update device attributes (name/tags/address/notes), capturing before.

    Reads the device first so the response carries ``priorState`` for the exact
    keys being changed (drives undo + audit); then PUTs the update.
    """
    require_write_support(conn, "update_device")
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
    require_write_support(conn, "update_network_vlan")
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
    require_write_support(conn, "claim_devices_into_network")
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

    The device's current network is exactly ``network_id``, captured for undo,
    so the undo token is genuinely applicable. It restores **membership only**:
    a removed device is reset to an unconfigured state, and claiming it back
    puts it in the network with the network's defaults, not with the per-device
    configuration it carried before (name, tags, address, notes, switch-port
    settings). Capture those with ``device_status`` / ``switch_ports`` first if
    you will need them.
    """
    require_write_support(conn, "remove_device_from_network")
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


class IrreversibleBind(ValueError):  # noqa: N818 — teaching error, reads as a statement
    """Refused: the bind would overwrite local config this tool cannot restore."""


# How ``priorState.vlanCapture`` reports what was learned about local VLANs.
VLAN_CAPTURE_TEMPLATE_DERIVED = "template-derived"  # already bound; no local VLANs exist
VLAN_CAPTURE_CAPTURED = "captured"  # the controller answered; the list is trustworthy
VLAN_CAPTURE_UNSUPPORTED = "unsupported"  # platform maps bind but not networks.vlans
VLAN_CAPTURE_UNAVAILABLE = "unavailable"  # the read failed (no appliance, API error)


def _prior_local_vlans(conn: Any, network_id: str) -> dict:
    """Read a network's local VLAN set for the bind guard (a read; never writes).

    Returns ``{"vlans": ..., "capture": ..., "error": ...}``. ``vlans`` is
    ``None`` whenever the set could not be read and ``[]`` only when the
    controller answered that there are none — the two are never collapsed. A
    failed probe that returned ``[]`` would look exactly like "there was nothing
    to lose", which is the hazard this guard exists to prevent.
    """
    platform = platform_of(conn)
    if not platform.supports("networks.vlans"):
        return {
            "vlans": None,
            "capture": VLAN_CAPTURE_UNSUPPORTED,
            "error": f"{platform.label} does not map the 'networks.vlans' read.",
        }
    try:
        return {
            "vlans": network_ops.list_vlans(conn, network_id),
            "capture": VLAN_CAPTURE_CAPTURED,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — reported as 'unavailable', never as []
        return {"vlans": None, "capture": VLAN_CAPTURE_UNAVAILABLE, "error": clean(str(exc))}


def guard_bind_network_to_template(conn: Any, network_id: str) -> dict:
    """Capture the bind's before-state; raise :class:`IrreversibleBind` when the
    bind would destroy the very state its undo needs. Reads only — never writes.

    Called by ``bind_network_to_template`` itself *and* by the MCP tool and the
    CLI command ahead of their ``dry_run`` early returns, so a preview of a
    destructive bind reports the refusal instead of a green ``wouldBind``. Every
    path runs this one function, so preview and real call can never disagree —
    the preview pays up to two GETs for that guarantee.

    Binding a Meraki network **overwrites** its VLAN / firewall configuration
    with the template's, and unbinding leaves the template-derived config in
    place rather than restoring what was there before. So the cases split:

      * already bound to a template — the config was template-derived before and
        after, so rebinding to the prior template genuinely restores it. Allowed.
      * unbound, VLAN set read and empty — nothing local to overwrite. Allowed.
      * unbound, VLAN set read and **non-empty** — the bind would delete VLANs
        that no operation in this tool can recreate (``networks.vlan_update``
        edits an existing VLAN; there is no create). **Refused**, because the
        undo would report success having restored nothing.
      * VLAN set unreadable (platform does not map the read, or the network has
        no appliance) — allowed, but recorded as unverified, and
        ``bind_network_to_template`` then offers no undo descriptor at all.
    """
    require_write_support(conn, "bind_network_to_template")
    prior = clean(op_get(conn, "networks.get", network_id=network_id))
    prior_template = prior.get("configTemplateId") if isinstance(prior, dict) else None
    if prior_template:
        return {
            "configTemplateId": prior_template,
            "vlans": None,
            "vlanCapture": VLAN_CAPTURE_TEMPLATE_DERIVED,
            "vlanCaptureError": None,
        }
    capture = _prior_local_vlans(conn, network_id)
    vlans = capture["vlans"]
    if vlans:
        raise IrreversibleBind(
            f"Refusing to bind network '{network_id}' to a config template: the network is "
            f"not bound to any template and carries {len(vlans)} local VLAN(s), which the "
            f"bind would overwrite with the template's. Unbinding does not put them back "
            f"and this tool has no VLAN-create operation, so its undo would report success "
            f"having restored nothing. Nothing was changed. Save the VLANs first "
            f"('fabric-aiops network vlans {network_id}'), then bind from the Meraki "
            f"dashboard, which spells out what the overwrite will replace."
        )
    return {
        "configTemplateId": None,
        "vlans": vlans,
        "vlanCapture": capture["capture"],
        "vlanCaptureError": capture["error"],
    }


def bind_is_reversible(prior_state: dict) -> bool:
    """True when a bind's recorded ``priorState`` supports a real restoration."""
    if prior_state.get("configTemplateId"):
        return True
    return prior_state.get("vlanCapture") == VLAN_CAPTURE_CAPTURED


def bind_network_to_template(
    conn: Any, network_id: str, template_id: str, auto_bind: bool = False
) -> dict:
    """[WRITE] Bind a network to a config template, capturing the prior binding.

    Guarded by :func:`guard_bind_network_to_template`: refuses when the network
    is unbound and carries local VLANs, because the bind overwrites them and
    nothing here can put them back.

    What the undo restores, precisely — the descriptor is built from
    ``priorState`` and claims no more than these:

      * **prior template present** — rebinding to it restores the network's
        configuration, which was template-derived both before and after.
      * **no prior template, VLAN set verified empty** — unbinding restores the
        unbound binding state. This is a **partial** restoration: per-network
        firewall rules, group policies and static routes are neither captured
        here nor restored by the unbind.
      * **VLAN set unreadable** (``vlanCapture`` is ``unsupported`` /
        ``unavailable``) — ``reversible`` is False and **no undo descriptor is
        offered at all**, because an undo token would claim a restoration that
        nobody verified was possible.
    """
    capture = guard_bind_network_to_template(conn, network_id)
    op_post(
        conn,
        "networks.bind_template",
        json_body={"configTemplateId": template_id, "autoBind": bool(auto_bind)},
        network_id=network_id,
    )
    prior_state = {
        "configTemplateId": capture["configTemplateId"],
        "vlans": capture["vlans"],
        "vlanCapture": capture["vlanCapture"],
        "vlanCaptureError": capture["vlanCaptureError"],
    }
    return {
        "action": "bind_network_to_template",
        "networkId": network_id,
        "templateId": template_id,
        "priorState": prior_state,
        "reversible": bind_is_reversible(prior_state),
        "note": _BIND_NOTES.get(capture["vlanCapture"], _BIND_NOTE_UNVERIFIED),
    }


_BIND_NOTE_UNVERIFIED = (
    "The prior local VLAN set could not be read, so no undo is recorded: unbinding "
    "would not restore whatever this bind overwrote."
)
_BIND_NOTES = {
    VLAN_CAPTURE_TEMPLATE_DERIVED: (
        "Undo rebinds to the prior template; the configuration was template-derived "
        "before and after, so that restores it."
    ),
    VLAN_CAPTURE_CAPTURED: (
        "The network had no local VLANs, so undo unbinds to restore the prior binding "
        "state. Per-network firewall rules, group policies and static routes are NOT "
        "captured or restored."
    ),
}


def unbind_network_from_template(conn: Any, network_id: str) -> dict:
    """[WRITE] Unbind a network from its config template, capturing the prior binding.

    Reads the network first so undo can rebind to the template it was bound to.
    """
    require_write_support(conn, "unbind_network_from_template")
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
