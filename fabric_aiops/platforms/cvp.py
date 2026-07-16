"""Arista CloudVision Portal (CVP) platform descriptor.

Read-only coverage mapped onto the canonical op keys; adapters fold CVP
``/cvpservice`` REST responses into the Meraki-canonical shapes the ops layer
consumes.

Concept mapping (documented in the README support matrix):

  * canonical **organizations** AND **networks** → CVP **containers**
    (``GET /cvpservice/inventory/containers`` — CVP has one flat-ish container
    tree; both canonical scopes list it, and get-by-id resolves via
    ``getContainerInfoById.do``). ``org_id`` on a target = a container key.
  * canonical **devices** / **device statuses** →
    ``GET /cvpservice/inventory/devices``; rows carry the device's
    ``complianceCode`` / ``complianceIndication`` — the CVP config-drift
    signal (a non-zero complianceCode means the running config drifted from
    the designed configlets).
  * canonical **admins** → ``GET /cvpservice/user/getUsers.do``.
  * canonical **network alerts** → ``GET /cvpservice/event/getAllEvents.do``
    (CVP events are global; the canonical network scope is not used to
    filter).

Auth is a CloudVision **service-account token** (Settings → Access Control →
Service Accounts) sent as ``Authorization: Bearer`` — the same static flow as
the reference platform. Everything else — licensing, API usage, VLANs,
traffic, per-port/SSID/client reads, uplink telemetry, configlet *content*
retrieval, compliance re-check triggers, and **all writes** (CVP changes are
configlet + task workflows) — raises a teaching ``PlatformUnsupported``
(file an issue/PR to extend).
"""

from __future__ import annotations

from typing import Any

from fabric_aiops.platform import CVP, PathSpec, Platform, register

_SEVERITY = {
    "CRITICAL": "critical",
    "ERROR": "critical",
    "WARNING": "warning",
    "WARN": "warning",
    "INFO": "info",
}

# Keys under which CVP wraps list payloads (varies by endpoint/version).
_LIST_KEYS = ("data", "users", "containerList", "netElementList")


def _unwrap(payload: Any) -> Any:
    """Strip CVP's varying list envelopes; flatten aggregated page lists."""
    if isinstance(payload, list):
        out: list[Any] = []
        for item in payload:
            inner = _unwrap(item)
            if isinstance(inner, list):
                out.extend(inner)
            elif inner is not None:
                out.append(inner)
        return out
    if isinstance(payload, dict):
        for key in _LIST_KEYS:
            if isinstance(payload.get(key), list):
                return payload[key]
    return payload


def _rows(payload: Any) -> list[dict]:
    data = _unwrap(payload)
    if isinstance(data, dict):
        data = [data]
    return [r for r in (data or []) if isinstance(r, dict)]


def _get(r: dict, *names: str) -> Any:
    """First present key — CVP mixes 'Key'/'key' casing across versions."""
    for name in names:
        if r.get(name) is not None:
            return r.get(name)
    return None


def _container_org_row(r: dict) -> dict:
    return {
        "id": _get(r, "key", "Key"),
        "name": _get(r, "name", "Name"),
        "url": None,
        "apiEnabled": True,
    }


def _adapt_containers_as_orgs(payload: Any) -> list[dict]:
    return [_container_org_row(r) for r in _rows(payload)]


def _adapt_containers_as_networks(payload: Any) -> list[dict]:
    return [
        {
            "id": _get(r, "key", "Key"),
            "name": _get(r, "name", "Name"),
            "productTypes": ["container"],
        }
        for r in _rows(payload)
    ]


def _adapt_container_info(payload: Any) -> dict:
    data = _unwrap(payload)
    r = data if isinstance(data, dict) else {}
    return {
        "id": _get(r, "key", "Key"),
        "name": _get(r, "name", "Name"),
        "childContainerCount": _get(r, "childContainerCount"),
        "netElementCount": _get(r, "netElementCount"),
    }


def _inventory_row(r: dict) -> dict:
    streaming = str(r.get("streamingStatus") or "").lower() == "active"
    return {
        "serial": r.get("serialNumber") or r.get("systemMacAddress"),
        "name": r.get("hostname") or r.get("fqdn"),
        "model": r.get("modelName"),
        "mac": r.get("systemMacAddress"),
        "lanIp": r.get("ipAddress"),
        "firmware": r.get("version") or r.get("internalVersion"),
        "productType": "switch",
        "status": "online" if streaming else "offline",
        "networkId": r.get("parentContainerKey"),
        "complianceCode": r.get("complianceCode"),
        "complianceIndication": r.get("complianceIndication"),
    }


def _adapt_inventory(payload: Any) -> list[dict]:
    return [_inventory_row(r) for r in _rows(payload)]


def _event_row(r: dict) -> dict:
    severity = str(r.get("severity") or "").upper()
    return {
        "severity": _SEVERITY.get(severity, "info"),
        "type": r.get("title") or r.get("eventType") or r.get("name"),
        "category": r.get("className") or r.get("category"),
        "networkId": r.get("objectId") or r.get("containerId"),
        "occurredAt": r.get("timestamp") or r.get("date"),
        "message": r.get("description") or r.get("message"),
    }


def _adapt_events(payload: Any) -> list[dict]:
    return [_event_row(r) for r in _rows(payload)]


def _user_row(r: dict) -> dict:
    first = str(r.get("firstName") or "").strip()
    last = str(r.get("lastName") or "").strip()
    full = f"{first} {last}".strip()
    return {
        "id": r.get("userId"),
        "name": full or r.get("userId"),
        "email": r.get("email"),
        "accountStatus": r.get("userStatus") or r.get("currentStatus"),
    }


def _adapt_users(payload: Any) -> list[dict]:
    return [_user_row(r) for r in _rows(payload)]


# Read subset that maps cleanly. Unlisted canonical keys — licensing, API
# usage, VLANs, traffic, switch ports, SSIDs, client reads, uplink telemetry,
# and every write — raise PlatformUnsupported.
CVP_PATHS: dict[str, PathSpec] = {
    "orgs.list": PathSpec("/cvpservice/inventory/containers", params_map={}),
    "orgs.get": PathSpec(
        "/cvpservice/provisioning/getContainerInfoById.do",
        id_query={"org_id": "containerId"},
        params_map={},
    ),
    "orgs.admins": PathSpec(
        "/cvpservice/user/getUsers.do",
        params_map={},
        default_params={"queryparam": "", "startIndex": "0", "endIndex": "200"},
    ),
    "orgs.device_statuses": PathSpec("/cvpservice/inventory/devices", params_map={}),
    "networks.list": PathSpec("/cvpservice/inventory/containers", params_map={}),
    "networks.get": PathSpec(
        "/cvpservice/provisioning/getContainerInfoById.do",
        id_query={"network_id": "containerId"},
        params_map={},
    ),
    "networks.alerts": PathSpec(
        "/cvpservice/event/getAllEvents.do",
        params_map={},
        default_params={"startIndex": "0", "endIndex": "200"},
    ),
    "devices.list": PathSpec("/cvpservice/inventory/devices", params_map={}),
}

CVP_ADAPTERS: dict[str, Any] = {
    "orgs.list": _adapt_containers_as_orgs,
    "orgs.get": _adapt_container_info,
    "orgs.admins": _adapt_users,
    "orgs.device_statuses": _adapt_inventory,
    "networks.list": _adapt_containers_as_networks,
    "networks.get": _adapt_container_info,
    "networks.alerts": _adapt_events,
    "devices.list": _adapt_inventory,
}

register(
    Platform(
        name=CVP,
        default_base_url="",  # per-install: https://<cvp-host>
        label="Arista CloudVision Portal API",
        org_noun="containers",
        org_id_hint="container key",
        secret_hint="CloudVision service-account token",  # nosec B106 — a UI label, not a secret
        secret_help=(
            "Create a service-account token in CloudVision: Settings → Access "
            "Control → Service Accounts. fabric-aiops sends it as "
            "'Authorization: Bearer <token>' on every request."
        ),
        requires_base_url=True,
        paths=CVP_PATHS,
        adapters=CVP_ADAPTERS,
    )
)
