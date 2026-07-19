"""Flagship Meraki health-analysis MCP tools (read-only)."""

from typing import Any, Optional

from fabric_aiops.governance import governed_tool
from fabric_aiops.ops import health as ops
from fabric_aiops.ops.health import MAX_ROWS
from mcp_server._shared import _get_connection, mcp, tool_errors


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def uplink_loss_and_latency_rca(
    loss_pct: float = 5.0,
    latency_ms: float = 150.0,
    records: Optional[list[dict[str, Any]]] = None,
    org_id: Optional[str] = None,
    limit: int = MAX_ROWS,
    target: Optional[str] = None,
) -> dict:
    """[READ] Rank the worst MX WAN uplinks by loss + latency, map cause + action.

    The flagship uplink RCA: pulls per-uplink loss/latency across the org (MX
    security appliances), ranks the worst uplinks by a composite of average loss
    and latency, flags each degraded uplink against the thresholds, and attaches
    a likely cause and a recommended action. Every ranking carries its numbers,
    not a black-box verdict. Pass 'records' for pure analysis, or an org/target
    to pull live.

    Args:
        loss_pct: Avg loss %% at/above which an uplink is degraded (default 5.0).
        latency_ms: Avg latency ms at/above which an uplink is degraded (default 150).
        records: Injected uplink series — {serial, networkId, uplink, ip,
            timeSeries:[{lossPercent, latencyMs}]}; skips live collection.
        org_id: Meraki organization id for live pull; omit to use target default.
        limit: Max rows in the ranked list (default 100). The result carries
            'returned'/'limit'/'truncated'; re-run with a higher limit when
            'truncated' is true rather than treating the list as complete.
        target: Target name from config; omit for the default.

    Returns dict: {uplinksEvaluated, degradedCount, thresholds, worst:[{serial,
        networkId, uplink, ip, avgLossPct, maxLossPct, avgLatencyMs, maxLatencyMs,
        degraded, cause, action}], returned, limit, truncated, note}.
    """
    if records is None:
        records = ops.pull_uplink_loss_latency(_get_connection(target), org_id)
    return ops.uplink_loss_and_latency_rca(
        records, loss_pct=loss_pct, latency_ms=latency_ms, limit=limit
    )


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def network_health_score(
    device_statuses: list[dict[str, Any]],
    uplinks: Optional[list[dict[str, Any]]] = None,
    alerts: Optional[list[dict[str, Any]]] = None,
    limit: int = MAX_ROWS,
) -> dict:
    """[READ] Composite fleet health score per network (0-100), worst-first.

    Folds device online %%, uplink health %%, and an alert-severity penalty into
    one weighted score per network (0.5 / 0.3 / 0.2), every component returned so
    the number is explainable. Pure analysis over injected rows — no live pull.

    Args:
        device_statuses: rows {serial, networkId, status, productType} (e.g. from
            org_device_statuses' 'devices').
        uplinks: optional rows {networkId, status} (active/ready = healthy).
        alerts: optional rows {networkId, severity} (critical/warning/info).
        limit: Max rows in the ranked list (default 100). The result carries
            'returned'/'limit'/'truncated'; re-run with a higher limit when
            'truncated' is true rather than treating the list as complete.

    Returns dict: {networksEvaluated, fleetScore, summary:{healthy, degraded,
        critical}, weights, worst:[{networkId, score, band, devicesOnline,
        devicesTotal, onlinePct, uplinkHealthPct, alertPenalty}], returned,
        limit, truncated, note}.

    Example: network_health_score(device_statuses=[
        {"networkId":"N1","status":"online"},
        {"networkId":"N1","status":"offline"}]).
    """
    return ops.network_health_score(
        device_statuses, uplinks=uplinks, alerts=alerts, limit=limit
    )


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def config_template_drift(
    template: dict[str, Any],
    networks: list[dict[str, Any]],
    limit: int = MAX_ROWS,
) -> dict:
    """[READ] For networks bound to a config template, list drifted settings.

    Compares each network bound to the template against the template's settings
    by exact value and reports expected-vs-actual for every drifted key. Pure
    analysis over injected data — no live pull.

    Args:
        template: {id, name, settings:{key: value}} — the config template.
        networks: rows {networkId, name, boundTemplateId, settings:{key: value}};
            only those whose boundTemplateId matches template['id'] are checked.
        limit: Max rows in the drifted list (default 100). The result carries
            'returned'/'limit'/'truncated'; re-run with a higher limit when
            'truncated' is true rather than treating the list as complete.

    Returns dict: {templateId, templateName, boundNetworks, driftedCount,
        compliantCount, settingsChecked, driftedNetworks:[{networkId, name,
        deviations:[{setting, expected, actual}]}], returned, limit, truncated,
        note}.

    Example: config_template_drift(
        template={"id":"T1","name":"branch","settings":{"timezone":"UTC"}},
        networks=[{"networkId":"N1","boundTemplateId":"T1",
                   "settings":{"timezone":"PST"}}]).
    """
    return ops.config_template_drift(template, networks, limit=limit)
