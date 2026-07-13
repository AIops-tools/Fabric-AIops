"""Organization-scoped Meraki MCP tools (read-only) + the fleet overview."""

from typing import Optional

from fabric_aiops.governance import governed_tool
from fabric_aiops.ops import organizations as ops
from fabric_aiops.ops import overview as ov
from mcp_server._shared import _get_connection, mcp, tool_errors


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def overview(org_id: Optional[str] = None, target: Optional[str] = None) -> dict:
    """[READ] One-shot fabric fleet health: networks + device status/product rollup.

    Call this first to triage a Meraki organization before drilling into a
    specific network or device.

    Args:
        org_id: Meraki organization id; omit to use the target's default org.
        target: Target name from config; omit for the default.
    """
    return ov.fleet_overview(_get_connection(target), org_id)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def org_list(target: Optional[str] = None) -> list:
    """[READ] Organizations visible to the API key (id, name, url, apiEnabled).

    Args:
        target: Target name from config; omit for the default.
    """
    return ops.list_organizations(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def org_get(org_id: Optional[str] = None, target: Optional[str] = None) -> dict:
    """[READ] One organization by id (name, url, api access, cloud region).

    Args:
        org_id: Meraki organization id; omit to use the target's default org.
        target: Target name from config; omit for the default.
    """
    return ops.get_organization(_get_connection(target), org_id)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def org_licensing(org_id: Optional[str] = None, target: Optional[str] = None) -> dict:
    """[READ] Org licensing overview: status, expiration, per-device-type counts.

    Args:
        org_id: Meraki organization id; omit to use the target's default org.
        target: Target name from config; omit for the default.
    """
    return ops.licensing_overview(_get_connection(target), org_id)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def org_admins(org_id: Optional[str] = None, target: Optional[str] = None) -> list:
    """[READ] Dashboard administrators for the org (name, email, access level).

    Args:
        org_id: Meraki organization id; omit to use the target's default org.
        target: Target name from config; omit for the default.
    """
    return ops.list_admins(_get_connection(target), org_id)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def org_device_statuses(org_id: Optional[str] = None, target: Optional[str] = None) -> dict:
    """[READ] Org-wide device availability rolled up by status + product type.

    Args:
        org_id: Meraki organization id; omit to use the target's default org.
        target: Target name from config; omit for the default.
    """
    return ops.device_statuses(_get_connection(target), org_id)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def org_api_requests(org_id: Optional[str] = None, target: Optional[str] = None) -> dict:
    """[READ] Org API-request usage overview (response-code counts, 429 rate-limits).

    Args:
        org_id: Meraki organization id; omit to use the target's default org.
        target: Target name from config; omit for the default.
    """
    return ops.api_request_usage(_get_connection(target), org_id)
