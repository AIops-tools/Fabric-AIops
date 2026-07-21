"""Meraki remediation MCP tools (guarded writes).

The only state-changing tools in the package. Every one is wrapped with the
governance harness (audit + risk tier) and takes a ``dry_run``
preview. Reversible writes pass an ``undo=`` callback that turns the fetched
before-state into an inverse descriptor the harness records; irreversible ones
(reboot, blink) record none. An undo builder that cannot verify what it would
be restoring declines (returns None) rather than record a token whose replay
would report success having restored nothing.

``bind_network_to_template`` additionally REFUSES outright when the bind would
overwrite local VLANs this tool cannot recreate. The refusal check is a read
and runs on the preview path too, so ``dry_run=True`` never shows green for a
bind the real call would refuse.

Risk tiers: reboot / claim / remove / bind / unbind = high (destructive or
fleet-affecting); update_device / update_network_vlan = medium (mutating,
reversible); blink = low (a locator aid, no config change).
"""

from typing import Any, Optional

from fabric_aiops.governance import governed_tool
from fabric_aiops.ops import remediation as ops
from mcp_server._shared import _get_connection, mcp, tool_errors

# ── undo descriptors (built from the fetched before-state) ──────────────────


def _update_device_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    if not isinstance(result, dict) or not result.get("priorState"):
        return None
    return {
        "tool": "update_device",
        "params": {"serial": params.get("serial"), "attrs": result["priorState"]},
        "skill": "fabric-aiops",
        "note": "Inverse of update_device: restore the attributes captured before.",
    }


def _update_vlan_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    if not isinstance(result, dict) or not result.get("priorState"):
        return None
    return {
        "tool": "update_network_vlan",
        "params": {
            "network_id": params.get("network_id"),
            "vlan_id": params.get("vlan_id"),
            "attrs": result["priorState"],
        },
        "skill": "fabric-aiops",
        "note": "Inverse of update_network_vlan: restore the VLAN values captured before.",
    }


def _claim_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    if not isinstance(result, dict):
        return None
    serials = (result.get("priorState") or {}).get("claimedSerials") or []
    if not serials:
        return None
    return {
        "tool": "remove_device_from_network",
        "params": {"network_id": params.get("network_id"), "serials": serials},
        "skill": "fabric-aiops",
        "note": "Inverse of claim: remove the devices that were just claimed.",
    }


def _remove_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    if not isinstance(result, dict):
        return None
    prior = result.get("priorState") or {}
    serials = prior.get("serials") or ([prior["serial"]] if prior.get("serial") else [])
    if not serials:
        return None
    return {
        "tool": "claim_devices_into_network",
        "params": {"network_id": prior.get("networkId"), "serials": serials},
        "skill": "fabric-aiops",
        "note": "Inverse of remove: claim the device(s) back into the prior network.",
    }


def _bind_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    """Inverse of a bind — offered only where it really restores something.

    Binding overwrites the network's VLAN / firewall configuration with the
    template's, and unbinding does not put the old configuration back. So the
    unbind branch is offered only when the prior VLAN set was actually read and
    found empty; when it could not be read this declines (None) rather than
    hand back a token whose replay would report success having restored nothing.
    """
    if not isinstance(result, dict):
        return None
    prior = result.get("priorState") or {}
    prior_template = prior.get("configTemplateId")
    if prior_template:
        return {
            "tool": "bind_network_to_template",
            "params": {"network_id": params.get("network_id"), "template_id": prior_template},
            "skill": "fabric-aiops",
            "note": "Inverse of bind: rebind to the template that was bound before. The "
            "configuration was template-derived both before and after, so this restores it.",
        }
    if prior.get("vlanCapture") != ops.VLAN_CAPTURE_CAPTURED:
        return None  # prior local configuration unverified → claim no restoration
    return {
        "tool": "unbind_network_from_template",
        "params": {"network_id": params.get("network_id")},
        "skill": "fabric-aiops",
        "note": "Inverse of bind: unbind (the network had no template and no local VLANs "
        "before). Restores the binding state only — per-network firewall rules, group "
        "policies and static routes are not restored.",
    }


def _unbind_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    if not isinstance(result, dict):
        return None
    prior_template = (result.get("priorState") or {}).get("configTemplateId")
    if not prior_template:
        return None  # nothing was bound → no inverse
    return {
        "tool": "bind_network_to_template",
        "params": {"network_id": params.get("network_id"), "template_id": prior_template},
        "skill": "fabric-aiops",
        "note": "Inverse of unbind: rebind to the template that was bound before.",
    }


# ── tools ────────────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="high")
@tool_errors("dict")
def reboot_device(serial: str, dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE][risk=high] Reboot a device (no safe inverse).

    Records the device's prior status for the audit trail; a reboot cannot be
    undone, so no undo descriptor is offered. Pass dry_run=True to preview.

    Args:
        serial: Device serial to reboot.
        dry_run: If True, preview without rebooting.
        target: Target name from config; omit for the default.
    """
    conn = _get_connection(target)
    ops.require_write_support(conn, "reboot_device")
    if dry_run:
        return {"dryRun": True, "wouldReboot": {"serial": serial}}
    return ops.reboot_device(conn, serial)


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def blink_device_leds(
    serial: str, duration: int = 20, target: Optional[str] = None
) -> dict:
    """[WRITE][risk=medium] Blink a device's locator LEDs to find it physically.

    No configuration change (a locate aid), so no undo is recorded. It is still
    a POST to the controller, so it is tiered as a write: ``risk_level="low"``
    is what marks a tool as a *read*. Tiering this "low" would misreport a POST
    as a read in the audit trail, contradicting its own [WRITE] tag.

    Args:
        serial: Device serial.
        duration: Blink duration in seconds (5..120, default 20).
        target: Target name from config; omit for the default.
    """
    return ops.blink_device_leds(_get_connection(target), serial, duration)


@mcp.tool()
@governed_tool(risk_level="medium", undo=_update_device_undo)
@tool_errors("dict")
def update_device(
    serial: str,
    attrs: dict[str, Any],
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=medium] Update device attributes (name/tags/address/notes).

    Captures the changed keys' prior values before the change, so the harness
    records an undo (restore the prior values) and a faithful audit trail. Pass
    dry_run=True to preview.

    Args:
        serial: Device serial.
        attrs: Attributes to set — allowed keys: name, tags, address, notes,
            lat, lng, floorPlanId.
        dry_run: If True, preview without changing.
        target: Target name from config; omit for the default.
    """
    conn = _get_connection(target)
    ops.require_write_support(conn, "update_device")
    if dry_run:
        return {"dryRun": True, "wouldUpdate": {"serial": serial, "attrs": attrs}}
    return ops.update_device(conn, serial, attrs)


@mcp.tool()
@governed_tool(risk_level="medium", undo=_update_vlan_undo)
@tool_errors("dict")
def update_network_vlan(
    network_id: str,
    vlan_id: str,
    attrs: dict[str, Any],
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=medium] Update an appliance VLAN, capturing its prior values.

    Captures the changed keys' prior values before the change (undo restores
    them). Pass dry_run=True to preview.

    Args:
        network_id: Meraki network id.
        vlan_id: VLAN id to update.
        attrs: Attributes to set — allowed keys: name, subnet, applianceIp,
            groupPolicyId, dhcpHandling.
        dry_run: If True, preview without changing.
        target: Target name from config; omit for the default.
    """
    conn = _get_connection(target)
    ops.require_write_support(conn, "update_network_vlan")
    if dry_run:
        return {"dryRun": True, "wouldUpdate": {"vlanId": vlan_id, "attrs": attrs}}
    return ops.update_network_vlan(conn, network_id, vlan_id, attrs)


@mcp.tool()
@governed_tool(risk_level="high", undo=_claim_undo)
@tool_errors("dict")
def claim_devices_into_network(
    network_id: str,
    serials: list[str],
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=high] Claim devices into a network. Inverse: remove them.

    Records the claimed serials so the harness can offer an undo (remove them).
    Pass dry_run=True to preview.

    Args:
        network_id: Meraki network id to claim into.
        serials: Device serials to claim.
        dry_run: If True, preview without claiming.
        target: Target name from config; omit for the default.
    """
    conn = _get_connection(target)
    ops.require_write_support(conn, "claim_devices_into_network")
    if dry_run:
        return {"dryRun": True, "wouldClaim": {"networkId": network_id, "serials": serials}}
    return ops.claim_devices_into_network(conn, network_id, serials)


@mcp.tool()
@governed_tool(risk_level="high", undo=_remove_undo)
@tool_errors("dict")
def remove_device_from_network(
    network_id: str,
    serial: Optional[str] = None,
    serials: Optional[list[str]] = None,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=high] Remove device(s) from a network. Inverse: claim back.

    The devices' current network is captured for undo, so the undo token is
    genuinely applicable — but claiming a device back restores MEMBERSHIP, not
    CONFIGURATION. A removed device is reset to an unconfigured state; it
    returns with the network's defaults, without the name, tags, address, notes
    or switch-port settings it carried before. Read those with device_status /
    switch_ports first if you will need them back.

    Pass dry_run=True to preview. Accepts a single ``serial`` or a ``serials``
    list — the list form is how claim_devices_into_network's undo replays.

    Args:
        network_id: Meraki network id the devices are bound to.
        serial: One device serial to remove (or use ``serials``).
        serials: Device serials to remove (mutually exclusive with ``serial``).
        dry_run: If True, preview without removing.
        target: Target name from config; omit for the default.
    """
    if serial and serials:
        raise ValueError("Pass either serial OR serials, not both.")
    batch = [str(x) for x in (serials or ([serial] if serial else [])) if str(x).strip()]
    if not batch:
        raise ValueError("remove_device_from_network requires serial or serials.")
    conn = _get_connection(target)
    ops.require_write_support(conn, "remove_device_from_network")
    if dry_run:
        return {"dryRun": True, "wouldRemove": {"networkId": network_id, "serials": batch}}
    if len(batch) == 1:
        return ops.remove_device_from_network(conn, network_id, batch[0])
    removed = [ops.remove_device_from_network(conn, network_id, x) for x in batch]
    return {
        "action": "remove_device_from_network",
        "networkId": network_id,
        "removed": removed,
        "priorState": {"networkId": network_id, "serials": batch},
    }


@mcp.tool()
@governed_tool(risk_level="high", undo=_bind_undo)
@tool_errors("dict")
def bind_network_to_template(
    network_id: str,
    template_id: str,
    auto_bind: bool = False,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=high] Bind a network to a config template, capturing prior binding.

    REFUSES when the network is unbound and carries local VLANs: the bind
    overwrites them with the template's, unbinding does not put them back, and
    this tool has no VLAN-create operation — so the undo would report success
    having restored nothing.

    Undo, precisely: rebind to the prior template when there was one (a faithful
    restore — the configuration was template-derived either way); unbind when
    the network was unbound with a VERIFIED empty VLAN set (restores the binding
    state only, NOT firewall rules / group policies / static routes); and NO
    undo at all when the prior VLAN set could not be read.

    Pass dry_run=True to preview. The preview runs the same refusal check
    (reads only), so it never previews green a bind the real call would refuse.

    Args:
        network_id: Meraki network id to bind.
        template_id: Config template id to bind to.
        auto_bind: Auto-bind switch/AP profiles (Meraki autoBind flag).
        dry_run: If True, preview without binding.
        target: Target name from config; omit for the default.
    """
    conn = _get_connection(target)
    capture = ops.guard_bind_network_to_template(conn, network_id)
    if dry_run:
        return {
            "dryRun": True,
            "wouldBind": {"networkId": network_id, "templateId": template_id},
            "priorState": capture,
            "reversible": ops.bind_is_reversible(capture),
        }
    return ops.bind_network_to_template(conn, network_id, template_id, auto_bind)


@mcp.tool()
@governed_tool(risk_level="high", undo=_unbind_undo)
@tool_errors("dict")
def unbind_network_from_template(
    network_id: str,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=high] Unbind a network from its config template. Inverse: rebind.

    Captures the template the network was bound to, so undo rebinds to it. Pass
    dry_run=True to preview.

    Args:
        network_id: Meraki network id to unbind.
        dry_run: If True, preview without unbinding.
        target: Target name from config; omit for the default.
    """
    conn = _get_connection(target)
    ops.require_write_support(conn, "unbind_network_from_template")
    if dry_run:
        return {"dryRun": True, "wouldUnbind": {"networkId": network_id}}
    return ops.unbind_network_from_template(conn, network_id)
