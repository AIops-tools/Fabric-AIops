"""UniFi Network controller platform descriptor (Ubiquiti).

Read coverage plus ONE guarded write (device restart) mapped onto the canonical
op keys; adapters fold classic UniFi Network REST responses (envelope
``{"meta": {"rc": "ok"}, "data": [...]}``) into the Meraki-canonical shapes the
ops layer consumes.

Concept mapping (documented in the README support matrix):

  * canonical **organizations** AND **networks** → UniFi **sites**
    (``GET /api/self/sites``) — like CVP's containers, both canonical scopes
    list the same collection. The canonical id is the site's short ``name``
    (e.g. ``default``), because that is the segment UniFi paths carry
    (``/api/s/{site}/...``); the Mongo ``_id`` is surfaced as ``internalId``.
  * canonical **device inventory / statuses** → ``GET /api/s/{site}/stat/device``
    (``state`` 1 → online, anything else → offline; ``version`` → firmware,
    ``type`` uap/usw/ugw/udm → wireless/switch/appliance, plus uptime).
  * canonical **switch ports** → the device detail's ``port_table``
    (``GET /api/s/{site}/stat/device/{mac}``) — pass the device **MAC** where
    Meraki takes a serial (the MAC is UniFi's stable device handle).
  * canonical **clients** → ``GET /api/s/{site}/stat/sta`` (currently
    connected clients) and ``/stat/user/{mac}`` for one known client.
  * canonical **network alerts** → ``GET /api/s/{site}/stat/alarm``
    (unarchived ``*_Lost_Contact`` → critical, other unarchived → warning,
    archived → info).
  * canonical **network get** → ``GET /api/s/{site}/stat/health`` — the site's
    per-subsystem (wlan/lan/wan/www/vpn) health rollup.
  * canonical **device reboot** (the only mapped write) →
    ``POST /api/s/{site}/cmd/devmgr`` with body
    ``{"cmd": "restart-device", "mac": <device-mac>}`` (built via the spec's
    ``static_body``/``body_ids`` — the command envelope pattern).

Device-scoped canonical ops (device get / switch ports / reboot) carry no site
in their canonical signature; the target's default ``org_id`` (the site name)
fills the ``{org_id}`` path segment — set it in config.yaml or the init wizard.

**Auth** is a UniFi **API key** sent as ``X-API-KEY`` on every request —
stateless, so it needs no session handling (available on UniFi OS consoles and
self-hosted Network Server 9.0+; created under Settings → Control Plane →
Integrations, or the admin's profile on older UIs). The legacy cookie login
(``POST /api/login`` + CSRF token) is deliberately NOT implemented — it needs a
cookie jar and CSRF header plumbing that does not fit the header-based
connection layer (a known deferral; use an API key).

**Both controller layouts** are supported through the target's ``base_url``:

  * classic self-hosted controller — ``https://<host>:8443`` (paths start at
    ``/api/...``);
  * UniFi OS console (UDM / UDM-Pro / Cloud Key Gen2) —
    ``https://<console>/proxy/network`` — the ``/proxy/network`` prefix is part
    of the base URL and every path template is issued relative to it.

Everything else — licensing, admins, API usage, VLAN reads/writes, traffic mix,
SSIDs, client usage/connectivity history, uplink loss/latency telemetry, and
every other write — raises a teaching ``PlatformUnsupported`` (file an issue or
PR to extend).
"""

from __future__ import annotations

from typing import Any

from fabric_aiops.platform import UNIFI, PathSpec, Platform, register

# UniFi device ``type`` → canonical product type.
_PRODUCT_TYPES = {
    "uap": "wireless",
    "usw": "switch",
    "ugw": "appliance",
    "udm": "appliance",
    "uxg": "appliance",
}


def _unwrap(payload: Any) -> Any:
    """Strip the UniFi ``{"meta": ..., "data": [...]}`` envelope, recursively.

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
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _rows(payload: Any) -> list[dict]:
    data = _unwrap(payload)
    if isinstance(data, dict):
        data = [data]
    return [r for r in (data or []) if isinstance(r, dict)]


def _one(payload: Any) -> dict:
    rows = _rows(payload)
    return rows[0] if rows else {}


# ── sites (canonical organizations AND networks) ────────────────────────────


def _site_org_row(r: dict) -> dict:
    return {
        "id": r.get("name"),  # the short code UniFi paths carry (/api/s/{name})
        "name": r.get("desc") or r.get("name"),
        "url": None,
        "apiEnabled": True,
        "internalId": r.get("_id"),
    }


def _adapt_sites_as_orgs(payload: Any) -> list[dict]:
    return [_site_org_row(r) for r in _rows(payload)]


def _adapt_sites_as_networks(payload: Any) -> list[dict]:
    return [
        {
            "id": r.get("name"),
            "name": r.get("desc") or r.get("name"),
            "productTypes": ["site"],
            "internalId": r.get("_id"),
        }
        for r in _rows(payload)
    ]


# ── site health (canonical network get) ─────────────────────────────────────


def _adapt_health(payload: Any) -> dict:
    """Fold ``stat/health`` subsystem rows into one canonical-ish record."""
    subsystems = []
    statuses: set[str] = set()
    for r in _rows(payload):
        status = str(r.get("status") or "unknown").lower()
        statuses.add(status)
        subsystems.append({
            "subsystem": r.get("subsystem"),
            "status": status,
            "deviceCount": r.get("num_adopted"),
            "userCount": r.get("num_user"),
        })
    if "error" in statuses:
        overall = "error"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "ok" if "ok" in statuses else "unknown"
    return {"productTypes": ["site"], "overallStatus": overall, "health": subsystems}


# ── devices (stat/device) ───────────────────────────────────────────────────


def _device_row(r: dict) -> dict:
    try:
        state = int(r.get("state") or 0)
    except (TypeError, ValueError):
        state = -1
    kind = str(r.get("type") or "").lower()
    return {
        # The MAC is UniFi's stable device handle (paths + devmgr commands).
        "serial": r.get("mac") or r.get("serial"),
        "name": r.get("name") or r.get("mac"),
        "mac": r.get("mac"),
        "lanIp": r.get("ip"),
        "model": r.get("model"),
        "firmware": r.get("version"),
        "productType": _PRODUCT_TYPES.get(kind, kind or None),
        "status": "online" if state == 1 else "offline",
        "networkId": r.get("site_id"),
        "uptimeSeconds": r.get("uptime"),
        "adopted": r.get("adopted"),
    }


def _adapt_devices(payload: Any) -> list[dict]:
    return [_device_row(r) for r in _rows(payload)]


def _adapt_device(payload: Any) -> dict:
    return _device_row(_one(payload))


def _port_row(r: dict) -> dict:
    return {
        "portId": str(r.get("port_idx")) if r.get("port_idx") is not None else None,
        "name": r.get("name"),
        "enabled": bool(r.get("enable")),
        "status": "connected" if r.get("up") else "disconnected",
        "speed": r.get("speed"),
        "duplex": "full" if r.get("full_duplex") else "half",
        "poeEnabled": r.get("poe_enable"),
    }


def _adapt_switch_ports(payload: Any) -> list[dict]:
    """Canonical switch ports come from the device detail's ``port_table``."""
    device = _one(payload)
    table = device.get("port_table") or []
    return [_port_row(r) for r in table if isinstance(r, dict)]


# ── clients (stat/sta, stat/user) ───────────────────────────────────────────


def _client_row(r: dict) -> dict:
    return {
        "id": r.get("mac"),
        "mac": r.get("mac"),
        "description": r.get("name") or r.get("hostname"),
        "ip": r.get("ip"),
        "vlan": r.get("vlan"),
        "ssid": r.get("essid"),
        # stat/sta lists currently-connected clients; stat/user rows carry no
        # live association — "Online" mirrors the Meraki status vocabulary.
        "status": "Online",
        "wired": r.get("is_wired"),
        "uplinkMac": r.get("ap_mac") or r.get("sw_mac"),
        "usage": {"sent": r.get("tx_bytes"), "recv": r.get("rx_bytes")},
    }


def _adapt_clients(payload: Any) -> list[dict]:
    return [_client_row(r) for r in _rows(payload)]


def _adapt_client(payload: Any) -> dict:
    return _client_row(_one(payload))


# ── alarms (stat/alarm → canonical network alerts) ──────────────────────────


def _alarm_row(r: dict) -> dict:
    key = str(r.get("key") or "")
    if r.get("archived"):
        severity = "info"
    elif "lost_contact" in key.lower():
        severity = "critical"
    else:
        severity = "warning"
    return {
        "severity": severity,
        "type": key or None,
        "category": r.get("subsystem") or r.get("catname"),
        "networkId": r.get("site_id"),
        "deviceSerial": r.get("ap") or r.get("sw") or r.get("gw") or r.get("mac"),
        "occurredAt": r.get("datetime") or r.get("time"),
        "message": r.get("msg"),
    }


def _adapt_alarms(payload: Any) -> list[dict]:
    return [_alarm_row(r) for r in _rows(payload)]


# Read subset + the one clean write. Unlisted canonical keys — orgs.get,
# licensing, admins, API usage, VLANs, traffic, SSIDs, uplink loss/latency
# telemetry, client usage/connectivity history, and every other write — raise
# PlatformUnsupported. Meraki-canonical query params (timespan, serials[]) have
# no UniFi native equivalent: params_map={} drops them, never forwards them.
UNIFI_PATHS: dict[str, PathSpec] = {
    "orgs.list": PathSpec("/api/self/sites", params_map={}),
    "orgs.device_statuses": PathSpec("/api/s/{org_id}/stat/device", params_map={}),
    "networks.list": PathSpec("/api/self/sites", params_map={}),
    "networks.get": PathSpec("/api/s/{network_id}/stat/health", params_map={}),
    "networks.alerts": PathSpec("/api/s/{network_id}/stat/alarm", params_map={}),
    "devices.list": PathSpec("/api/s/{org_id}/stat/device", params_map={}),
    "devices.get": PathSpec("/api/s/{org_id}/stat/device/{serial}", params_map={}),
    "devices.switch_ports": PathSpec(
        "/api/s/{org_id}/stat/device/{serial}", params_map={}
    ),
    "clients.list": PathSpec("/api/s/{network_id}/stat/sta", params_map={}),
    "clients.get": PathSpec("/api/s/{network_id}/stat/user/{client_id}", params_map={}),
    # The one clean write: a devmgr command envelope (guarded upstream by the
    # same dry-run + double-confirm + governance gates as every write).
    "devices.reboot": PathSpec(
        "/api/s/{org_id}/cmd/devmgr",
        params_map={},
        static_body={"cmd": "restart-device"},
        body_ids={"serial": "mac"},
    ),
}

UNIFI_ADAPTERS: dict[str, Any] = {
    "orgs.list": _adapt_sites_as_orgs,
    "orgs.device_statuses": _adapt_devices,
    "networks.list": _adapt_sites_as_networks,
    "networks.get": _adapt_health,
    "networks.alerts": _adapt_alarms,
    "devices.list": _adapt_devices,
    "devices.get": _adapt_device,
    "devices.switch_ports": _adapt_switch_ports,
    "clients.list": _adapt_clients,
    "clients.get": _adapt_client,
}

register(
    Platform(
        name=UNIFI,
        default_base_url="",  # per-install: classic controller or UniFi OS console
        label="UniFi Network API",
        org_noun="sites",
        org_id_hint="site name (short code, e.g. 'default')",
        secret_hint="UniFi API key",  # nosec B106 — a UI label, not a secret
        secret_help=(
            "Create an API key in the UniFi console (UniFi OS: Settings → "
            "Control Plane → Integrations; self-hosted Network Server 9.0+: "
            "the admin's API-key page). fabric-aiops sends it as 'X-API-KEY' "
            "on every request. Legacy cookie login (POST /api/login) is not "
            "supported — use an API key."
        ),
        requires_base_url=True,
        base_url_help=(
            "Classic self-hosted controller: https://<host>:8443. UniFi OS "
            "console (UDM/UDM-Pro/Cloud Key Gen2): "
            "https://<console>/proxy/network — keep the /proxy/network suffix; "
            "every API path is issued relative to it."
        ),
        api_key_header="X-API-KEY",
        paths=UNIFI_PATHS,
        adapters=UNIFI_ADAPTERS,
    )
)
