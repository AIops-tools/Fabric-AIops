"""Cisco Meraki Dashboard API — the reference platform descriptor.

Meraki defines the *canonical* shapes: every canonical op key maps 1:1 onto a
Dashboard API path and responses pass through unadapted (identity). The
hierarchy is organizations → networks → devices; list endpoints paginate via
the RFC-5988 ``Link`` header, followed by the connection layer. Auth is a
long-lived API key (Dashboard → Organization → Settings → API access) sent as
``Authorization: Bearer`` or, with ``auth_style: meraki-key``, the legacy
``X-Cisco-Meraki-API-Key`` header.
"""

from __future__ import annotations

from fabric_aiops.platform import (
    DEFAULT_MERAKI_BASE_URL,
    MERAKI,
    PathSpec,
    Platform,
    register,
)

# Full canonical coverage: reads AND writes. ``params_map`` is left at None
# throughout — Meraki query params ARE the canonical params (passthrough).
MERAKI_PATHS: dict[str, PathSpec] = {
    # organizations
    "orgs.list": PathSpec("/organizations"),
    "orgs.get": PathSpec("/organizations/{org_id}"),
    "orgs.licensing": PathSpec("/organizations/{org_id}/licenses/overview"),
    "orgs.admins": PathSpec("/organizations/{org_id}/admins"),
    "orgs.device_statuses": PathSpec("/organizations/{org_id}/devices/statuses"),
    "orgs.api_requests": PathSpec("/organizations/{org_id}/apiRequests/overview"),
    # networks
    "networks.list": PathSpec("/organizations/{org_id}/networks"),
    "networks.get": PathSpec("/networks/{network_id}"),
    "networks.vlans": PathSpec("/networks/{network_id}/appliance/vlans"),
    "networks.vlan_get": PathSpec("/networks/{network_id}/appliance/vlans/{vlan_id}"),
    "networks.alerts": PathSpec("/networks/{network_id}/health/alerts"),
    "networks.traffic": PathSpec("/networks/{network_id}/traffic"),
    # devices
    "devices.list": PathSpec("/organizations/{org_id}/devices"),
    "devices.get": PathSpec("/devices/{serial}"),
    "devices.uplinks": PathSpec("/organizations/{org_id}/uplinks/statuses"),
    "devices.switch_ports": PathSpec("/devices/{serial}/switch/ports"),
    "devices.wireless_ssids": PathSpec("/networks/{network_id}/wireless/ssids"),
    # clients
    "clients.list": PathSpec("/networks/{network_id}/clients"),
    "clients.get": PathSpec("/networks/{network_id}/clients/{client_id}"),
    "clients.usage": PathSpec("/networks/{network_id}/clients/{client_id}/usageHistory"),
    "clients.connectivity": PathSpec(
        "/networks/{network_id}/clients/{client_id}/connectionStats"
    ),
    # health telemetry
    "health.uplink_loss_latency": PathSpec(
        "/organizations/{org_id}/devices/uplinksLossAndLatency"
    ),
    # writes (guarded at the ops/MCP/CLI layers)
    "devices.reboot": PathSpec("/devices/{serial}/reboot"),
    "devices.blink_leds": PathSpec("/devices/{serial}/blinkLeds"),
    "devices.update": PathSpec("/devices/{serial}"),
    "networks.vlan_update": PathSpec("/networks/{network_id}/appliance/vlans/{vlan_id}"),
    "networks.claim_devices": PathSpec("/networks/{network_id}/devices/claim"),
    "networks.remove_device": PathSpec("/networks/{network_id}/devices/remove"),
    "networks.bind_template": PathSpec("/networks/{network_id}/bind"),
    "networks.unbind_template": PathSpec("/networks/{network_id}/unbind"),
}

register(
    Platform(
        name=MERAKI,
        default_base_url=DEFAULT_MERAKI_BASE_URL,
        label="Cisco Meraki Dashboard API",
        org_noun="organizations",
        org_id_hint="organization id",
        secret_hint="Meraki API key",  # nosec B106 — a UI label, not a secret
        secret_help=(
            "Create an API key in the Meraki Dashboard: Organization → Settings "
            "→ API access → Generate. Sent as 'Authorization: Bearer' (or, with "
            "auth_style: meraki-key, the X-Cisco-Meraki-API-Key header)."
        ),
        paths=MERAKI_PATHS,
        # No adapters: Meraki responses ARE the canonical shape.
    )
)
