"""Device-scoped Meraki MCP tools (read-only)."""

from typing import Optional

from fabric_aiops.governance import governed_tool
from fabric_aiops.ops import devices as ops
from fabric_aiops.ops.devices import DEFAULT_DEVICE_LIMIT
from mcp_server._shared import _get_connection, mcp, tool_errors


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def device_inventory(
    org_id: Optional[str] = None,
    model: Optional[str] = None,
    limit: int = DEFAULT_DEVICE_LIMIT,
    target: Optional[str] = None,
) -> dict:
    """[READ] Org device inventory, optionally filtered by model family.

    Buckets devices by Meraki model prefix (MX security appliance, MS switch, MR
    wireless AP, MV camera, MG cellular gateway) and returns per-family counts.

    Args:
        org_id: Meraki organization id; omit to use the target's default org.
        model: Model family/prefix to filter (e.g. 'MS', 'MR46'); omit for all.
        limit: Max rows in the returned list (default 500). The result carries
            'returned'/'limit'/'truncated'; re-run with a higher limit when
            'truncated' is true rather than treating the list as complete.
        target: Target name from config; omit for the default.
    """
    return ops.inventory(_get_connection(target), org_id, model, limit=limit)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def device_status(
    serial: str, org_id: Optional[str] = None, target: Optional[str] = None
) -> dict:
    """[READ] One device's availability status (online/offline/alerting/dormant).

    Args:
        serial: Device serial (e.g. Q2XX-XXXX-XXXX).
        org_id: Meraki organization id; omit to use the target's default org.
        target: Target name from config; omit for the default.
    """
    return ops.device_status(_get_connection(target), serial, org_id)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def device_uplinks(org_id: Optional[str] = None, target: Optional[str] = None) -> list:
    """[READ] Appliance/gateway uplink statuses across the org (WAN interfaces).

    Args:
        org_id: Meraki organization id; omit to use the target's default org.
        target: Target name from config; omit for the default.
    """
    return ops.uplink_status(_get_connection(target), org_id)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def switch_ports(serial: str, target: Optional[str] = None) -> list:
    """[READ] Switch (MS) port configuration for a device by serial.

    Args:
        serial: MS switch serial.
        target: Target name from config; omit for the default.
    """
    return ops.switch_ports(_get_connection(target), serial)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def wireless_ssids(network_id: str, target: Optional[str] = None) -> list:
    """[READ] Wireless (MR) SSIDs configured on a network (number, name, enabled).

    Args:
        network_id: Meraki network id (from network_list).
        target: Target name from config; omit for the default.
    """
    return ops.wireless_ssids(_get_connection(target), network_id)
