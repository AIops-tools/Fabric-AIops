"""Shared plumbing for the ACI-native reads (platform gate + GET)."""

from __future__ import annotations

from typing import Any

from fabric_aiops.connection import FabricApiError
from fabric_aiops.ops._util import platform_of
from fabric_aiops.platform import ACI


def require_aci(conn: Any) -> None:
    """Fail fast — before any request — when the target is not an ACI APIC.

    The ACI reads take ACI references (tenant, DN, MAC/IP) that mean nothing
    to a Meraki / Catalyst / CVP / UniFi controller.
    """
    platform = platform_of(conn)
    if platform.name != ACI:
        raise ValueError(
            f"This is a Cisco ACI read, but the target is {platform.label} "
            f"(platform '{platform.name}'). Point --target at a target with "
            f"'platform: aci' in config.yaml."
        )


def aci_get(conn: Any, path: str, params: dict[str, str] | None = None) -> dict:
    """GET an APIC ``/api/node/class`` or ``/api/mo`` URL; returns parsed JSON.

    Every APIC read answers with an ``imdata`` list. A 2xx without one — an
    HTML login page, a proxy error page, anything that is not the APIC — is
    raised, never passed on: the connection layer folds a non-JSON body into
    ``{}``, and every reader here would take ``{}`` for "no endpoints, no
    faults" and report a clean fabric.
    """
    payload = conn.get(path, params=params) if params else conn.get(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("imdata"), list):
        raise FabricApiError(
            f"APIC answered {path} without an 'imdata' list — this is not an APIC "
            f"response. Check that base_url points at the APIC itself, not a proxy "
            f"or login page.",
            path=path,
        )
    return payload


def probe_tenants(conn: Any) -> int | None:
    """Connectivity probe for ``doctor``: log in and read the tenant count.

    One ``fvTenant`` row is requested; APIC's reported ``totalCount`` gives
    the number visible to this account (None when APIC did not report it).
    """
    from fabric_aiops.ops import aci_mit as mit

    payload = aci_get(conn, mit.class_path("fvTenant"), {"page": "0", "page-size": "1"})
    return mit.total_count(payload)
