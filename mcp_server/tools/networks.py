"""Network-scoped Meraki MCP tools (read-only)."""

from typing import Optional

from fabric_aiops.governance import governed_tool
from fabric_aiops.ops import networks as ops
from fabric_aiops.ops.networks import DEFAULT_ALERT_LIMIT, DEFAULT_APP_LIMIT
from mcp_server._shared import _get_connection, mcp, tool_errors


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def network_list(org_id: Optional[str] = None, target: Optional[str] = None) -> list:
    """[READ] Networks in the organization (id, name, productTypes, tags).

    Args:
        org_id: Meraki organization id; omit to use the target's default org.
        target: Target name from config; omit for the default.
    """
    return ops.list_networks(_get_connection(target), org_id)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def network_get(network_id: str, target: Optional[str] = None) -> dict:
    """[READ] One network by id (name, product types, timezone, bound template).

    Args:
        network_id: Meraki network id (from network_list).
        target: Target name from config; omit for the default.
    """
    return ops.get_network(_get_connection(target), network_id)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def network_vlans(network_id: str, target: Optional[str] = None) -> list:
    """[READ] Appliance VLANs configured on a network (id, subnet, appliance IP).

    Args:
        network_id: Meraki network id (from network_list).
        target: Target name from config; omit for the default.
    """
    return ops.list_vlans(_get_connection(target), network_id)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def network_alerts(
    network_id: str, limit: int = DEFAULT_ALERT_LIMIT, target: Optional[str] = None
) -> dict:
    """[READ] Current network health alerts, summarised by severity.

    Args:
        network_id: Meraki network id (from network_list).
        limit: Max rows in the returned list (default 200). The result carries
            'returned'/'limit'/'truncated'; re-run with a higher limit when
            'truncated' is true rather than treating the list as complete.
        target: Target name from config; omit for the default.
    """
    return ops.network_alerts(_get_connection(target), network_id, limit=limit)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def network_traffic(
    network_id: str,
    timespan: int = 86400,
    limit: int = DEFAULT_APP_LIMIT,
    target: Optional[str] = None,
) -> dict:
    """[READ] Application/protocol traffic mix for a network, top apps by bytes.

    Args:
        network_id: Meraki network id (from network_list).
        timespan: Look-back window in seconds (7200..2592000, default 86400).
        limit: Max rows in the returned list (default 25). The result carries
            'returned'/'limit'/'truncated'; re-run with a higher limit when
            'truncated' is true rather than treating the list as complete.
        target: Target name from config; omit for the default.
    """
    return ops.traffic_summary(_get_connection(target), network_id, timespan, limit=limit)
