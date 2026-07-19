"""Device-scoped reads over the Meraki Dashboard API (read-only).

Devices are the leaves of the Meraki hierarchy. Meraki device models carry a
product-type prefix in their model string:

  * ``MX`` — security appliance / SD-WAN
  * ``MS`` — switch
  * ``MR`` — wireless access point
  * ``MV`` — smart camera
  * ``MG`` — cellular gateway

These reads answer "what devices does the org own (optionally by model family),
what's each one's status/uplink, and — for a switch — its ports, or — for a
wireless AP's network — its SSIDs". All controller text is sanitized.
"""

from __future__ import annotations

from typing import Any

from fabric_aiops.ops._util import bounded, clean_list, op_get, op_get_pages, require_org

#: Default cap on the device rows the inventory read returns.
DEFAULT_DEVICE_LIMIT = 500

# Recognised Meraki product-model prefixes.
MODEL_PREFIXES = ("MX", "MS", "MR", "MV", "MG")


def _model_prefix(model: Any) -> str | None:
    """Return the two-letter product prefix of a Meraki model, if recognised."""
    text = str(model or "").upper()
    for prefix in MODEL_PREFIXES:
        if text.startswith(prefix):
            return prefix
    return None


def inventory(
    conn: Any,
    org_id: str | None = None,
    model: str | None = None,
    limit: int = DEFAULT_DEVICE_LIMIT,
) -> dict:
    """[READ] Org device inventory, optionally filtered by model family.

    Pulls ``/organizations/{id}/devices`` and buckets devices by model prefix
    (MX/MS/MR/MV/MG). ``model`` filters to one family (e.g. ``MS`` for switches);
    an exact model string (e.g. ``MR46``) also works via prefix match. Returns
    the per-family counts and the (filtered) device rows, capped at ``limit``
    with ``truncated`` saying so explicitly. ``total`` and ``matched`` are
    always the full counts.
    """
    oid = require_org(conn, org_id)
    rows = clean_list(op_get_pages(conn, "devices.list", org_id=oid))
    wanted = str(model).upper() if model else None

    by_model: dict[str, int] = {}
    filtered: list[dict] = []
    for r in rows:
        prefix = _model_prefix(r.get("model"))
        key = prefix or "other"
        by_model[key] = by_model.get(key, 0) + 1
        if wanted is None or str(r.get("model") or "").upper().startswith(wanted):
            filtered.append(r)

    return {
        "organizationId": oid,
        "total": len(rows),
        "modelFilter": wanted,
        "byModelFamily": dict(sorted(by_model.items(), key=lambda kv: kv[1], reverse=True)),
        "matched": len(filtered),
        **bounded(filtered, limit, "devices"),
    }


def device_status(conn: Any, serial: str, org_id: str | None = None) -> dict:
    """[READ] One device's availability status (from the org status feed)."""
    oid = require_org(conn, org_id)
    rows = clean_list(
        op_get_pages(conn, "orgs.device_statuses", params={"serials[]": serial}, org_id=oid)
    )
    for r in rows:
        if str(r.get("serial")) == str(serial):
            return r
    # Fall back to the device record if the status feed did not include it.
    raise KeyError(f"No status for device '{serial}' in organization '{oid}'.")


def uplink_status(conn: Any, org_id: str | None = None) -> list[dict]:
    """[READ] Appliance/gateway uplink statuses across the org (WAN interfaces)."""
    oid = require_org(conn, org_id)
    return clean_list(op_get_pages(conn, "devices.uplinks", org_id=oid))


def switch_ports(conn: Any, serial: str) -> list[dict]:
    """[READ] Switch (MS) port configuration for a device by serial."""
    return clean_list(op_get(conn, "devices.switch_ports", serial=serial))


def wireless_ssids(conn: Any, network_id: str) -> list[dict]:
    """[READ] Wireless (MR) SSIDs configured on a network (number, name, enabled)."""
    return clean_list(op_get(conn, "devices.wireless_ssids", network_id=network_id))
