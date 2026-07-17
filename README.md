<!-- mcp-name: io.github.AIops-tools/fabric-aiops -->

# Fabric AIops (preview)

> **Disclaimer**: Community-maintained open-source project. **Not affiliated with, endorsed by, or sponsored by Cisco, Meraki, Arista, Ubiquiti, or any network-controller vendor.** "Cisco", "Meraki", "Catalyst", "DNA Center", "Arista", "CloudVision", "Ubiquiti", "UniFi" and all product/trademark names belong to their respective owners. MIT licensed.

Governed AI-ops for **network fabrics** managed through a controller — the
**Cisco Meraki Dashboard API** (the reference platform, full read + write),
**Cisco Catalyst Center** (formerly DNA Center; read subset), **Arista
CloudVision Portal (CVP)** (read subset), and **UniFi Network** (self-hosted
controller or UniFi OS console; read subset + device restart) — with a
**built-in governance
harness**: unified audit log, policy engine, token/runaway budget guard,
undo-token recording, and graduated-autonomy risk tiers. **Multi-platform by
construction**: a registry keyed by `platform` maps every *canonical operation*
onto each controller's REST API (path templates + response adapters), so adding
a controller is a registry entry, never new ops/CLI/MCP surface. An operation a
platform doesn't map returns a clear teaching error ("not supported on X yet —
open an issue"), never a silent no-op. **Preview — mock-validated only, not
verified against a live controller of any platform.**

## What it does

Three flagship signature analyses, plus the guarded reads and writes around them:

- **Uplink loss & latency RCA** — pull MX WAN uplink loss + latency across an
  org, rank the worst uplinks by a composite of average loss and latency, and
  map each degraded uplink to a likely cause + recommended action. Every ranking
  carries its numbers, not a black-box verdict.
- **Network health score** — a composite 0-100 score per network from device
  online %, uplink health %, and an alert-severity penalty (weighted 0.5/0.3/0.2),
  with every component returned so the number is explainable.
- **Config template drift** — for networks bound to a config template, list the
  settings that have drifted from the template (expected vs actual).

## What works

- **CLI** (`fabric-aiops ...`): `init`, `overview`, `org`, `network`, `device`, `client`, `health`, `remediate`, `secret`, `doctor`, `mcp`.
- **MCP server** (`fabric-aiops mcp` or `fabric-aiops-mcp`): **32 tools** (24 read, 8 write), every one wrapped with the bundled `@governed_tool` harness.
- **Encrypted credentials**: the controller secret (Meraki API key / Catalyst Center `username:password` / CVP service-account token / UniFi API key) lives in an encrypted store `~/.fabric-aiops/secrets.enc` (Fernet + scrypt) — **never plaintext on disk**. Unlock with a master password from `FABRIC_AIOPS_MASTER_PASSWORD` (MCP/CI) or an interactive prompt (CLI).
- **Reversibility**: mutating writes fetch the **real before-state first** and record a faithful inverse (`update_device`/`update_network_vlan` restore prior values; `claim`↔`remove`; `bind`↔`unbind`/rebind). Irreversible ops (`reboot_device`, `blink_device_leds`) record the prior state for audit but declare no undo.
- **Safety**: every state-changing CLI op supports `--dry-run` and requires double confirmation; every write MCP tool takes a `dry_run` preview.

## Capability matrix (32 MCP tools)

| Domain | Tools | Count | R/W |
|--------|-------|:-----:|:---:|
| **Overview** | `overview` | 1 | read |
| **Organizations** | `org_list`, `org_get`, `org_licensing`, `org_admins`, `org_device_statuses`, `org_api_requests` | 6 | read |
| **Networks** | `network_list`, `network_get`, `network_vlans`, `network_alerts`, `network_traffic` | 5 | read |
| **Devices** | `device_inventory`, `device_status`, `device_uplinks`, `switch_ports`, `wireless_ssids` | 5 | read |
| **Clients** | `client_list`, `client_get`, `client_usage`, `client_connectivity` | 4 | read |
| **Health (flagship)** | `uplink_loss_and_latency_rca`, `network_health_score`, `config_template_drift` | 3 | read |
| **Remediation** | `reboot_device`, `claim_devices_into_network`, `remove_device_from_network`, `bind_network_to_template`, `unbind_network_from_template` | 5 | write (high) |
| | `update_device`, `update_network_vlan` | 2 | write (medium) |
| | `blink_device_leds` | 1 | write (low) |

`network_health_score` and `config_template_drift` are injected-only (they score
data you already hold); `uplink_loss_and_latency_rca` accepts injected `records`
for offline analysis or pulls live from a configured target. Device models carry
a product-type prefix: **MX** appliance, **MS** switch, **MR** wireless AP, **MV**
camera, **MG** cellular gateway.

## Platform support matrix

One tool, four controller platforms. The ops/CLI/MCP surface is identical
everywhere; each platform maps the canonical operations it supports and raises
a teaching error for the rest ("not supported on `<platform>` yet — open an
issue or PR").

| Canonical operation | meraki | catalyst | cvp | unifi |
|---------------------|:------:|:--------:|:---:|:-----:|
| `overview` (org/site/container rollup) | ✅ | ✅ | ✅ | ✅ |
| `org_list` / `org_get` | ✅ | ✅ sites | ✅ containers | ✅ sites (list; get ❌) |
| `org_licensing`, `org_api_requests` | ✅ | ❌ | ❌ | ❌ |
| `org_admins` | ✅ | ❌ | ✅ users | ❌ |
| `org_device_statuses` | ✅ | ✅ device-health | ✅ inventory + streaming status | ✅ stat/device (state, uptime, firmware) |
| `network_list` / `network_get` | ✅ | ✅ site-health / site | ✅ containers | ✅ sites / stat/health (subsystem rollup) |
| `network_vlans`, `network_traffic` | ✅ | ❌ | ❌ | ❌ |
| `network_alerts` | ✅ | ✅ issues (P1→critical, P2→warning) | ✅ events | ✅ alarms (*_Lost_Contact→critical) |
| `device_inventory`, `device_status` | ✅ | ✅ network-device | ✅ inventory (+ complianceCode drift signal) | ✅ stat/device (id = device **MAC**) |
| `device_uplinks` | ✅ | ❌ | ❌ | ❌ |
| `switch_ports` | ✅ | ✅ interface stats (pass the device **uuid**) | ❌ | ✅ device `port_table` (pass the device **MAC**) |
| `wireless_ssids` | ✅ | ❌ | ❌ | ❌ |
| `client_list` / `client_get` | ✅ | ✅ client-health (aggregate) / client-detail (by MAC) | ❌ | ✅ stat/sta (connected) / stat/user (by MAC) |
| `client_usage`, `client_connectivity` | ✅ | ❌ | ❌ | ❌ |
| `uplink_loss_and_latency_rca` (live pull) | ✅ | ❌ (injected `records` still work) | ❌ (injected `records` still work) | ❌ (injected `records` still work) |
| `network_health_score`, `config_template_drift` (injected-only) | ✅ | ✅ | ✅ | ✅ |
| `reboot_device` | ✅ | ❌ teaching error | ❌ teaching error | ✅ `cmd/devmgr` restart-device |
| **The other 7 writes** (blink/update/claim/remove/bind/unbind/VLAN) | ✅ | ❌ teaching error | ❌ teaching error | ❌ teaching error |

Concept mapping: canonical *organizations/networks* are Catalyst Center
**sites**, CVP **containers**, and UniFi **sites** (the canonical id is the
site's short name — the `/api/s/{site}/` path segment); all have one global
tree, so the org scope does not filter their lists. On unifi, device-scoped
calls (`device get` / `switch_ports` / `reboot`) fill the site from the
target's default `org_id` — set it in config.yaml (or the init wizard). Writes
are **Meraki-only except UniFi device restart** — Catalyst Center and CVP
change models (task/configlet workflows) don't map cleanly onto these
canonical writes, so each write fails fast with a teaching error *before* any
controller call (never a silent no-op). CVP config-drift surfaces through
`device_inventory` (`complianceCode`/`complianceIndication` per device) and
`network_alerts` (events); configlet-content retrieval and deep pagination on
catalyst/cvp/unifi are known deferrals.

### Per-platform auth

| Platform | `platform:` | Secret stored (encrypted) | Auth on the wire | Base URL |
|----------|-------------|---------------------------|------------------|----------|
| Cisco Meraki Dashboard | `meraki` | API key (Dashboard → Organization → Settings → API access) | `Authorization: Bearer` (or `auth_style: meraki-key` → `X-Cisco-Meraki-API-Key`) | default `https://api.meraki.com/api/v1` |
| Cisco Catalyst Center | `catalyst` | `username:password` (one string) | exchanged via `POST /dna/system/api/v1/auth/token` (HTTP Basic) for a ~1 h `X-Auth-Token`, auto-refreshed once on a 401 | required, e.g. `https://<catalyst-center-host>` |
| Arista CloudVision Portal | `cvp` | service-account token (Settings → Access Control → Service Accounts) | `Authorization: Bearer` | required, e.g. `https://<cvp-host>` |
| UniFi Network | `unifi` | API key (UniFi OS: Settings → Control Plane → Integrations; self-hosted Network Server 9.0+) | `X-API-KEY` (stateless; legacy cookie login is a known deferral) | required — classic controller `https://<host>:8443`, or UniFi OS console `https://<console>/proxy/network` (keep the prefix) |

`fabric-aiops init` walks through the platform choice and stores the right kind
of secret; `fabric-aiops doctor` probes each target with the canonical
top-of-hierarchy read (organizations / sites / containers / UniFi sites),
exercising the full auth flow.

## Quick start

```bash
uv tool install fabric-aiops              # or: pipx install fabric-aiops
fabric-aiops init                         # wizard: choose platform (meraki/catalyst/cvp/unifi) + store the secret (encrypted)
fabric-aiops doctor                       # verify config, secrets, connectivity (per-platform auth probe)
fabric-aiops overview                     # one-shot fabric fleet health
fabric-aiops health uplink-rca            # rank worst MX WAN uplinks + cause/action (meraki)
fabric-aiops device inventory --model MS  # switches in the org
```

Run as an MCP server (stdio):

```bash
export FABRIC_AIOPS_MASTER_PASSWORD=...   # unlock secrets non-interactively
fabric-aiops-mcp
```

## Governance

Every MCP tool passes through the bundled `@governed_tool` harness:

- **Audit** — every call (params, result, status, duration, risk tier, approver,
  rationale) is logged to `~/.fabric-aiops/audit.db` (relocatable via
  `FABRIC_AIOPS_HOME`).
- **Budget / runaway guard** — token and call budgets trip a circuit breaker.
- **Risk tiers** — graduated autonomy; high-risk ops can require a named approver
  (`FABRIC_AUDIT_APPROVED_BY` / `FABRIC_AUDIT_RATIONALE` — the env-var names
  the bundled harness reads).
- **Undo recording** — reversible writes record an inverse descriptor built from
  the fetched before-state.

## Scope

This is the **network-fabric / controller** member of the AIops-tools family
(governed AI-ops with audit + budget + undo + risk tiers). Do **NOT** use it for
OT / industrial edge (Modbus, OPC-UA, PROFINET) — see the separate
`industrial-aiops` line — nor for device-level CLI/SSH network automation.

## Missing a capability?

Coverage is intentionally a curated subset of each controller's API. Missing a
call or a device family on **Meraki**? A ❌ in the support matrix you need on
**Catalyst Center**, **CloudVision Portal**, or **UniFi Network** (writes
included — e.g. the UniFi cookie-login fallback for pre-9.0 controllers)? Want
another controller platform entirely? **Open an issue or PR** — contributions
welcome (a platform is a single descriptor module: path templates + response
adapters).

## Status

**Preview — mock-validated only, not verified against a live Meraki
organization, Catalyst Center appliance, CloudVision Portal instance, or UniFi
controller.** All four platforms' API paths are modelled from the public API
shapes and need live verification. `fabric-aiops doctor` is the fastest live
check.
