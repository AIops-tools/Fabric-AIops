"""Client-scoped Meraki MCP tools (read-only)."""

from typing import Optional

from fabric_aiops.governance import governed_tool
from fabric_aiops.ops import clients as ops
from mcp_server._shared import _get_connection, mcp, tool_errors


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def client_list(
    network_id: str, timespan: int = 86400, target: Optional[str] = None
) -> list:
    """[READ] Clients seen on a network within a look-back window.

    Args:
        network_id: Meraki network id (from network_list).
        timespan: Look-back window in seconds (7200..2592000, default 86400).
        target: Target name from config; omit for the default.
    """
    return ops.list_clients(_get_connection(target), network_id, timespan)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def client_get(network_id: str, client_id: str, target: Optional[str] = None) -> dict:
    """[READ] One client's detail (description, MAC, IP, VLAN, manufacturer).

    Args:
        network_id: Meraki network id.
        client_id: Client id/MAC (from client_list).
        target: Target name from config; omit for the default.
    """
    return ops.get_client(_get_connection(target), network_id, client_id)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def client_usage(network_id: str, client_id: str, target: Optional[str] = None) -> dict:
    """[READ] A client's usage history rolled up to total sent/received KB.

    Args:
        network_id: Meraki network id.
        client_id: Client id/MAC (from client_list).
        target: Target name from config; omit for the default.
    """
    return ops.client_usage(_get_connection(target), network_id, client_id)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def client_connectivity(
    network_id: str, client_id: str, target: Optional[str] = None
) -> dict:
    """[READ] A client's connection-quality stats (assoc/auth/dhcp/dns/success).

    Args:
        network_id: Meraki network id.
        client_id: Client id/MAC (from client_list).
        target: Target name from config; omit for the default.
    """
    return ops.client_connectivity(_get_connection(target), network_id, client_id)
