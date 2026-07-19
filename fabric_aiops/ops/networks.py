"""Network-scoped reads over the Meraki Dashboard API (read-only).

A network is the mid-tier of the Meraki hierarchy (organizations → networks →
devices): a site/branch that groups the devices bound to it. These reads answer
"what networks does the org have, and for a given network — its VLANs, health
alerts, and traffic mix". All controller text is sanitized at the read boundary.
"""

from __future__ import annotations

from typing import Any

from fabric_aiops.ops._util import (
    bounded,
    clean,
    clean_list,
    op_get,
    op_get_pages,
    require_org,
)

#: Default cap on the raw rows a summarising read returns alongside its rollup.
DEFAULT_ALERT_LIMIT = 200
DEFAULT_APP_LIMIT = 25


def list_networks(conn: Any, org_id: str | None = None) -> list[dict]:
    """[READ] Networks in the organization (id, name, productTypes, tags)."""
    oid = require_org(conn, org_id)
    return clean_list(op_get_pages(conn, "networks.list", org_id=oid))


def get_network(conn: Any, network_id: str) -> dict:
    """[READ] One network by id (name, product types, timezone, bound template)."""
    return clean(op_get(conn, "networks.get", network_id=network_id))


def list_vlans(conn: Any, network_id: str) -> list[dict]:
    """[READ] Appliance VLANs configured on a network (id, subnet, appliance IP)."""
    return clean_list(op_get(conn, "networks.vlans", network_id=network_id))


def network_alerts(
    conn: Any, network_id: str, limit: int = DEFAULT_ALERT_LIMIT
) -> dict:
    """[READ] Current network health alerts, summarised by severity.

    Pulls ``/networks/{id}/health/alerts`` and rolls the open alerts up by
    severity (critical/warning/info) so an agent sees the blast radius before
    reading individual alerts. The raw alert rows are returned too, capped at
    ``limit`` — and the cap announces itself via ``truncated`` rather than
    leaving the consumer to infer it. ``total`` is always the full count.
    """
    rows = clean_list(op_get(conn, "networks.alerts", network_id=network_id))
    by_severity: dict[str, int] = {}
    for r in rows:
        sev = str(r.get("severity") or "unknown")
        by_severity[sev] = by_severity.get(sev, 0) + 1
    return {
        "networkId": network_id,
        "total": len(rows),
        "bySeverity": dict(sorted(by_severity.items(), key=lambda kv: kv[1], reverse=True)),
        **bounded(rows, limit, "alerts"),
    }


def traffic_summary(
    conn: Any, network_id: str, timespan: int = 86400, limit: int = DEFAULT_APP_LIMIT
) -> dict:
    """[READ] Application/protocol traffic mix for a network over ``timespan`` seconds.

    Pulls ``/networks/{id}/traffic`` and ranks applications by total bytes
    (sent + received), so an agent can answer "what's using the pipe" without
    parsing the whole series. The ranked rows are capped at ``limit`` and the
    cap announces itself via ``truncated``; ``applicationCount`` is the full count.
    """
    span = max(7200, min(int(timespan), 2592000))  # Meraki bounds: 2h .. 30d
    rows = clean_list(
        op_get(conn, "networks.traffic", params={"timespan": span}, network_id=network_id)
    )
    ranked = []
    for r in rows:
        sent = r.get("sent") or 0
        recv = r.get("recv") or 0
        total = (sent if isinstance(sent, (int, float)) else 0) + (
            recv if isinstance(recv, (int, float)) else 0
        )
        ranked.append({
            "application": r.get("application"),
            "protocol": r.get("protocol"),
            "destination": r.get("destination"),
            "sentKb": sent,
            "recvKb": recv,
            "totalKb": total,
        })
    ranked.sort(key=lambda x: x["totalKb"], reverse=True)
    return {
        "networkId": network_id,
        "timespanSeconds": span,
        "applicationCount": len(ranked),
        **bounded(ranked, limit, "topApplications"),
    }
