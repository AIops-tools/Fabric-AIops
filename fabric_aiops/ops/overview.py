"""One-shot fabric fleet overview (read-only).

A single call an agent can lead with before drilling into a specific
organization, network, or device: the organization, its network count, and its
device availability rolled up by status and product type. Resilient — a failing
sub-call degrades to a partial summary with an ``error`` field rather than a
raised traceback (a health probe must survive the thing it probes).
"""

from __future__ import annotations

from typing import Any

from fabric_aiops.ops import networks as net
from fabric_aiops.ops import organizations as orgs
from fabric_aiops.ops._util import require_org


def fleet_overview(conn: Any, org_id: str | None = None) -> dict:
    """[READ] Org summary: platform + network count + device status/product rollup."""
    try:
        oid = require_org(conn, org_id)
    except ValueError as exc:
        return {"error": str(exc)[:200]}

    result: dict[str, Any] = {
        "platform": getattr(getattr(conn, "target", None), "platform", "meraki"),
        "organizationId": oid,
    }
    try:
        result["networks"] = len(net.list_networks(conn, oid))
    except Exception as exc:  # noqa: BLE001 — partial summary, not a crash
        result["networksError"] = str(exc)[:200]
    try:
        statuses = orgs.device_statuses(conn, oid)
        result["devicesTotal"] = statuses.get("total")
        result["devicesByStatus"] = statuses.get("byStatus")
        result["devicesByProductType"] = statuses.get("byProductType")
    except Exception as exc:  # noqa: BLE001
        result["devicesError"] = str(exc)[:200]
    return result
