"""Cisco Catalyst Center (formerly DNA Center) platform descriptor.

Read-only coverage mapped onto the canonical op keys; adapters fold Catalyst
Center intent-API responses (envelope ``{"response": ...}``) into the
Meraki-canonical shapes the ops layer consumes.

Concept mapping (documented in the README support matrix):

  * canonical **organizations** → Catalyst Center **sites**
    (``GET /dna/intent/api/v1/site``); ``org_id`` on a target = a site id.
  * canonical **networks** → **site health** rows
    (``GET /dna/intent/api/v1/site-health``) — Catalyst Center has one global
    site tree, so the canonical org scope is not used to filter.
  * canonical **device statuses** → ``GET /dna/intent/api/v1/device-health``.
  * canonical **network alerts** → ``GET /dna/intent/api/v1/issues`` (priority
    P1 → critical, P2 → warning, P3/P4 → info).
  * canonical **switch ports** → per-device interface stats
    (``GET /dna/intent/api/v1/interface/network-device/{id}``) — pass the
    Catalyst Center device **uuid** (``deviceId`` from the inventory) where
    Meraki takes a serial.
  * canonical **clients** → aggregate ``client-health`` categories for the
    list, and ``client-detail?macAddress=`` for a single client (the client id
    is a MAC address).

Auth is a short-lived (~1 h) session token: the stored secret is
``username:password``, exchanged via ``POST /dna/system/api/v1/auth/token``
(HTTP Basic) for an ``X-Auth-Token`` the connection layer attaches per request
and refreshes once on a 401. Everything else — licensing, admins, API usage,
VLANs, traffic, SSIDs, org-wide uplink telemetry, and **all writes** — raises a
teaching ``PlatformUnsupported`` (file an issue/PR to extend).
"""

from __future__ import annotations

from typing import Any

from fabric_aiops.platform import (
    AUTH_FLOW_SESSION,
    CATALYST,
    PathSpec,
    Platform,
    register,
)

_REACHABLE = {"UP", "REACHABLE", "SUCCESS"}
_PRIORITY_SEVERITY = {"P1": "critical", "P2": "warning", "P3": "info", "P4": "info"}


def _unwrap(payload: Any) -> Any:
    """Strip the Catalyst Center ``{"response": ...}`` envelope, recursively.

    The connection's page aggregator may hand back a *list of envelopes*; each
    is unwrapped and its rows folded into one flat list.
    """
    if isinstance(payload, list):
        out: list[Any] = []
        for item in payload:
            inner = _unwrap(item)
            if isinstance(inner, list):
                out.extend(inner)
            elif inner is not None:
                out.append(inner)
        return out
    if isinstance(payload, dict) and "response" in payload:
        return payload["response"]
    return payload


def _rows(payload: Any) -> list[dict]:
    data = _unwrap(payload)
    if isinstance(data, dict):
        data = [data]
    return [r for r in (data or []) if isinstance(r, dict)]


def _one(payload: Any) -> dict:
    rows = _rows(payload)
    return rows[0] if rows else {}


def _site_row(r: dict) -> dict:
    return {
        "id": r.get("id"),
        "name": r.get("name"),
        "url": r.get("siteNameHierarchy"),
        "apiEnabled": True,
    }


def _adapt_sites(payload: Any) -> list[dict]:
    return [_site_row(r) for r in _rows(payload)]


def _adapt_site(payload: Any) -> dict:
    return _site_row(_one(payload))


def _device_health_row(r: dict) -> dict:
    reachability = str(r.get("reachabilityHealth") or "").upper()
    return {
        "serial": r.get("uuid") or r.get("macAddress"),
        "name": r.get("name"),
        "mac": r.get("macAddress"),
        "lanIp": r.get("ipAddress"),
        "status": "online" if reachability in _REACHABLE else "offline",
        "productType": r.get("deviceFamily") or r.get("deviceType"),
        "networkId": r.get("location"),
        "model": r.get("model"),
        "overallHealth": r.get("overallHealth"),
        "issueCount": r.get("issueCount"),
    }


def _adapt_device_health(payload: Any) -> list[dict]:
    return [_device_health_row(r) for r in _rows(payload)]


def _site_health_row(r: dict) -> dict:
    site_type = r.get("siteType")
    return {
        "id": r.get("siteId"),
        "name": r.get("siteName"),
        "productTypes": [site_type] if site_type else [],
        "networkHealthAverage": r.get("networkHealthAverage"),
        "healthyNetworkDevicePercentage": r.get("healthyNetworkDevicePercentage"),
        "numberOfNetworkDevice": r.get("numberOfNetworkDevice"),
        "numberOfClients": r.get("numberOfClients"),
    }


def _adapt_site_health(payload: Any) -> list[dict]:
    return [_site_health_row(r) for r in _rows(payload)]


def _issue_row(r: dict) -> dict:
    priority = str(r.get("priority") or "").upper()
    return {
        "severity": _PRIORITY_SEVERITY.get(priority, "info"),
        "type": r.get("name"),
        "category": r.get("category"),
        "networkId": r.get("siteId"),
        "deviceSerial": r.get("deviceId"),
        "status": r.get("status"),
        "priority": priority or None,
    }


def _adapt_issues(payload: Any) -> list[dict]:
    return [_issue_row(r) for r in _rows(payload)]


def _network_device_row(r: dict) -> dict:
    reachable = str(r.get("reachabilityStatus") or "").lower() == "reachable"
    return {
        "serial": r.get("serialNumber") or r.get("id"),
        "deviceId": r.get("id"),
        "name": r.get("hostname"),
        "model": r.get("platformId") or r.get("type"),
        "mac": r.get("macAddress"),
        "lanIp": r.get("managementIpAddress"),
        "firmware": r.get("softwareVersion"),
        "productType": r.get("family"),
        "status": "online" if reachable else "offline",
        "networkId": r.get("siteId") or r.get("locationName"),
    }


def _adapt_network_devices(payload: Any) -> list[dict]:
    return [_network_device_row(r) for r in _rows(payload)]


def _adapt_network_device(payload: Any) -> dict:
    return _network_device_row(_one(payload))


def _interface_row(r: dict) -> dict:
    return {
        "portId": r.get("portName"),
        "name": r.get("description"),
        "enabled": str(r.get("adminStatus") or "").upper() == "UP",
        "status": r.get("status"),
        "vlan": r.get("vlanId"),
        "speed": r.get("speed"),
        "duplex": r.get("duplex"),
        "type": r.get("interfaceType"),
        "mac": r.get("macAddress"),
    }


def _adapt_interfaces(payload: Any) -> list[dict]:
    return [_interface_row(r) for r in _rows(payload)]


def _adapt_client_health(payload: Any) -> list[dict]:
    """client-health returns per-site score *categories*, not client rows —
    surfaced as aggregate entries (documented; per-client listing is a known
    Catalyst Center deferral)."""
    out: list[dict] = []
    for site in _rows(payload):
        for detail in site.get("scoreDetail") or []:
            if not isinstance(detail, dict):
                continue
            category = detail.get("scoreCategory") or {}
            name = category.get("value") or category.get("name") or "ALL"
            out.append({
                "id": str(name).lower(),
                "description": f"{name} client health (aggregate)",
                "clientCount": detail.get("clientCount"),
                "healthScore": detail.get("scoreValue"),
                "siteId": site.get("siteId"),
            })
    return out


def _adapt_client_detail(payload: Any) -> dict:
    data = _unwrap(payload)
    detail = data.get("detail") if isinstance(data, dict) else {}
    detail = detail if isinstance(detail, dict) else {}
    overall = None
    for score in detail.get("healthScore") or []:
        if isinstance(score, dict) and str(score.get("healthType")).upper() == "OVERALL":
            overall = score.get("score")
    connected = str(detail.get("connectionStatus") or "").upper() == "CONNECTED"
    return {
        "id": detail.get("hostMac"),
        "mac": detail.get("hostMac"),
        "description": detail.get("hostName"),
        "ip": detail.get("hostIpV4"),
        "vlan": detail.get("vlanId"),
        "ssid": detail.get("ssid"),
        "status": "Online" if connected else "Offline",
        "healthScore": overall,
    }


# Read subset that maps cleanly. Unlisted canonical keys — licensing, admins,
# API usage, VLANs, traffic, SSIDs, uplink loss/latency telemetry, client
# usage/connectivity, and every write — raise PlatformUnsupported.
CATALYST_PATHS: dict[str, PathSpec] = {
    "orgs.list": PathSpec("/dna/intent/api/v1/site", params_map={}),
    "orgs.get": PathSpec("/dna/intent/api/v1/site/{org_id}", params_map={}),
    "orgs.device_statuses": PathSpec("/dna/intent/api/v1/device-health", params_map={}),
    "networks.list": PathSpec("/dna/intent/api/v1/site-health", params_map={}),
    "networks.get": PathSpec("/dna/intent/api/v1/site/{network_id}", params_map={}),
    "networks.alerts": PathSpec(
        "/dna/intent/api/v1/issues",
        id_query={"network_id": "siteId"},
        params_map={},
    ),
    "devices.list": PathSpec("/dna/intent/api/v1/network-device", params_map={}),
    "devices.get": PathSpec("/dna/intent/api/v1/network-device/{serial}", params_map={}),
    "devices.switch_ports": PathSpec(
        "/dna/intent/api/v1/interface/network-device/{serial}", params_map={}
    ),
    "clients.list": PathSpec("/dna/intent/api/v1/client-health", params_map={}),
    "clients.get": PathSpec(
        "/dna/intent/api/v1/client-detail",
        id_query={"client_id": "macAddress"},
        params_map={},
    ),
}

CATALYST_ADAPTERS: dict[str, Any] = {
    "orgs.list": _adapt_sites,
    "orgs.get": _adapt_site,
    "orgs.device_statuses": _adapt_device_health,
    "networks.list": _adapt_site_health,
    "networks.get": _adapt_site,
    "networks.alerts": _adapt_issues,
    "devices.list": _adapt_network_devices,
    "devices.get": _adapt_network_device,
    "devices.switch_ports": _adapt_interfaces,
    "clients.list": _adapt_client_health,
    "clients.get": _adapt_client_detail,
}

register(
    Platform(
        name=CATALYST,
        default_base_url="",  # per-install: https://<catalyst-center-host>
        label="Cisco Catalyst Center API",
        org_noun="sites",
        org_id_hint="site id",
        secret_hint="Catalyst Center login (username:password)",  # nosec B106 — a UI label, not a secret
        secret_help=(
            "Enter the Catalyst Center credentials as a single "
            "'username:password' secret. fabric-aiops exchanges it (HTTP Basic) "
            "for a short-lived token via POST /dna/system/api/v1/auth/token and "
            "sends X-Auth-Token per request, refreshing once on a 401 (tokens "
            "expire after ~1 hour)."
        ),
        requires_base_url=True,
        auth_flow=AUTH_FLOW_SESSION,
        token_path="/dna/system/api/v1/auth/token",
        token_header="X-Auth-Token",
        paths=CATALYST_PATHS,
        adapters=CATALYST_ADAPTERS,
    )
)
