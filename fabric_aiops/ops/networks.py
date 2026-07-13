"""Network-scoped reads over the Meraki Dashboard API (read-only).

A network is the mid-tier of the Meraki hierarchy (organizations → networks →
devices): a site/branch that groups the devices bound to it. These reads answer
"what networks does the org have, and for a given network — its VLANs, health
alerts, and traffic mix". All controller text is sanitized at the read boundary.
"""

from __future__ import annotations

from typing import Any

from fabric_aiops.ops._util import clean, clean_list, require_org


def list_networks(conn: Any, org_id: str | None = None) -> list[dict]:
    """[READ] Networks in the organization (id, name, productTypes, tags)."""
    oid = require_org(conn, org_id)
    return clean_list(conn.get_pages(f"/organizations/{oid}/networks"))


def get_network(conn: Any, network_id: str) -> dict:
    """[READ] One network by id (name, product types, timezone, bound template)."""
    return clean(conn.get(f"/networks/{network_id}"))


def list_vlans(conn: Any, network_id: str) -> list[dict]:
    """[READ] Appliance VLANs configured on a network (id, subnet, appliance IP)."""
    return clean_list(conn.get(f"/networks/{network_id}/appliance/vlans"))


def network_alerts(conn: Any, network_id: str) -> dict:
    """[READ] Current network health alerts, summarised by severity.

    Pulls ``/networks/{id}/health/alerts`` and rolls the open alerts up by
    severity (critical/warning/info) so an agent sees the blast radius before
    reading individual alerts. The raw alert rows are returned too (bounded).
    """
    rows = clean_list(conn.get(f"/networks/{network_id}/health/alerts"))
    by_severity: dict[str, int] = {}
    for r in rows:
        sev = str(r.get("severity") or "unknown")
        by_severity[sev] = by_severity.get(sev, 0) + 1
    return {
        "networkId": network_id,
        "total": len(rows),
        "bySeverity": dict(sorted(by_severity.items(), key=lambda kv: kv[1], reverse=True)),
        "alerts": rows[:200],
    }


def traffic_summary(conn: Any, network_id: str, timespan: int = 86400) -> dict:
    """[READ] Application/protocol traffic mix for a network over ``timespan`` seconds.

    Pulls ``/networks/{id}/traffic`` and ranks applications by total bytes
    (sent + received), so an agent can answer "what's using the pipe" without
    parsing the whole series.
    """
    span = max(7200, min(int(timespan), 2592000))  # Meraki bounds: 2h .. 30d
    rows = clean_list(conn.get(f"/networks/{network_id}/traffic", params={"timespan": span}))
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
        "topApplications": ranked[:25],
    }
