"""Cisco ACI (APIC REST API) platform descriptor.

ACI registers **no canonical-op mapping**. Its object model has two axes —
the logical tree (tenant → VRF → bridge domain → EPG, with contracts between
EPGs) and the physical one (fabric → pod → node → interface) — and neither
is an organization → network → device hierarchy. Mapping ACI onto the Meraki
signatures would make every ``network_id`` argument mean something different
per call, so the canonical tools raise a teaching ``PlatformUnsupported`` on
an ``aci`` target and the ACI-native reads in :mod:`fabric_aiops.ops.aci`
take ACI references (tenant/app/EPG names, DNs, MAC/IP) instead.

Auth (from Cisco's own SDK, ``acitoolkit/acisession.py``): ``POST
/api/aaaLogin.json`` with ``{"aaaUser": {"attributes": {"name", "pwd"}}}``
returns ``imdata[0].aaaLogin.attributes.{token, refreshTimeoutSeconds}`` and
sets the session cookie the client then carries; ``GET /api/aaaRefresh.json``
extends it. An expired session is answered with **403**, not 401 — the
connection layer handles that (see ``FabricConnection._aci_request``).
"""

from __future__ import annotations

from fabric_aiops.platform import ACI, AUTH_FLOW_ACI, Platform, register

register(
    Platform(
        name=ACI,
        default_base_url="",  # per-install: https://<apic-host>
        label="Cisco ACI APIC API",
        org_noun="tenants",
        org_id_hint="tenant name (optional; ACI reads take tenant/dn explicitly)",
        secret_hint="APIC login (username:password)",  # nosec B106 — a UI label, not a secret
        secret_help=(
            "Enter the APIC credentials as a single 'username:password' secret. "
            "fabric-aiops logs in via POST /api/aaaLogin.json, carries the "
            "session cookie APIC returns, extends it via /api/aaaRefresh.json and "
            "logs in again once when APIC answers 401/403. Use an account whose "
            "role is read-only: every ACI read here is a GET."
        ),
        requires_base_url=True,
        auth_flow=AUTH_FLOW_ACI,
        token_path="/api/aaaLogin.json",
        refresh_path="/api/aaaRefresh.json",
    )
)
