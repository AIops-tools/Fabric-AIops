"""Organization-scoped reads over the Meraki Dashboard API (read-only).

The organization is the top of the Meraki hierarchy (organizations → networks →
devices). These reads answer "what orgs can this key see, how are they licensed,
who administers them, and how healthy/available are their devices right now".
All controller text is sanitized at the read boundary.
"""

from __future__ import annotations

from typing import Any

from fabric_aiops.ops._util import (
    as_list,
    bounded,
    clean,
    clean_list,
    op_get,
    op_get_pages,
    require_org,
)

#: Default cap on the raw device rows returned alongside the status rollup.
DEFAULT_DEVICE_LIMIT = 500


def list_organizations(conn: Any) -> list[dict]:
    """[READ] Organizations visible to the API key."""
    return clean_list(op_get_pages(conn, "orgs.list"))


def get_organization(conn: Any, org_id: str | None = None) -> dict:
    """[READ] One organization by id (name, url, api enabled, cloud region)."""
    oid = require_org(conn, org_id)
    return clean(op_get(conn, "orgs.get", org_id=oid))


def licensing_overview(conn: Any, org_id: str | None = None) -> dict:
    """[READ] Org licensing overview: status, expiration, per-device-type counts."""
    oid = require_org(conn, org_id)
    return clean(op_get(conn, "orgs.licensing", org_id=oid))


def list_admins(conn: Any, org_id: str | None = None) -> list[dict]:
    """[READ] Dashboard administrators for the org (name, email, access level)."""
    oid = require_org(conn, org_id)
    return clean_list(op_get(conn, "orgs.admins", org_id=oid))


def device_statuses(
    conn: Any, org_id: str | None = None, limit: int = DEFAULT_DEVICE_LIMIT
) -> dict:
    """[READ] Org-wide device availability, rolled up by status and product type.

    Pulls ``/organizations/{id}/devices/statuses`` and summarises: how many
    devices are online / offline / alerting / dormant, and the breakdown by
    product type (appliance/switch/wireless/camera/cellularGateway). The raw
    per-device rows are returned too, capped at ``limit``, with ``truncated``
    saying so explicitly. ``total`` is always the full count.
    """
    oid = require_org(conn, org_id)
    rows = clean_list(op_get_pages(conn, "orgs.device_statuses", org_id=oid))
    by_status: dict[str, int] = {}
    by_product: dict[str, int] = {}
    for r in rows:
        status = str(r.get("status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        product = str(r.get("productType") or "unknown")
        by_product[product] = by_product.get(product, 0) + 1
    return {
        "organizationId": oid,
        "total": len(rows),
        "byStatus": dict(sorted(by_status.items(), key=lambda kv: kv[1], reverse=True)),
        "byProductType": dict(sorted(by_product.items(), key=lambda kv: kv[1], reverse=True)),
        **bounded(rows, limit, "devices"),
    }


def api_request_usage(conn: Any, org_id: str | None = None) -> dict:
    """[READ] Org API-request usage overview (response-code counts) for rate insight."""
    oid = require_org(conn, org_id)
    raw = op_get(conn, "orgs.api_requests", org_id=oid)
    data = clean(raw) if isinstance(raw, dict) else {}
    counts = data.get("responseCodeCounts") if isinstance(data, dict) else None
    codes = counts if isinstance(counts, dict) else {}
    total = sum(v for v in codes.values() if isinstance(v, int))
    rate_limited = int(codes.get("429", 0) or 0)
    return {
        "organizationId": oid,
        "totalRequests": total,
        "rateLimited429": rate_limited,
        "responseCodeCounts": codes,
    }


def summarize_organizations(rows: list[dict]) -> list[dict]:
    """[READ] Fold raw org records to {id, name, url, apiEnabled} (pure helper)."""
    out = []
    for r in as_list(rows):
        api = r.get("api") if isinstance(r.get("api"), dict) else {}
        out.append({
            "id": r.get("id"),
            "name": r.get("name"),
            "url": r.get("url"),
            "apiEnabled": bool(api.get("enabled")) if api else None,
        })
    return out
