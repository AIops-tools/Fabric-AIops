<!-- mcp-name: io.github.AIops-tools/fabric-aiops -->

# Fabric AIops (preview)

> **Disclaimer**: Community-maintained open-source project. **Not affiliated with, endorsed by, or sponsored by Cisco, Meraki, or any network-controller vendor.** "Cisco" and "Meraki" and all product/trademark names belong to their respective owners. MIT licensed.

Governed AI-ops for **network fabrics** managed through a cloud controller —
starting with the **Cisco Meraki Dashboard API** (organizations → networks →
devices) — with a **built-in governance harness**: unified audit log, policy
engine, token/runaway budget guard, undo-token recording, and graduated-autonomy
risk tiers. **Multi-platform by construction**: a registry keyed by `platform`
means Catalyst Center or Arista CVP can be added later as additional platforms
without touching the ops/CLI/MCP layers — **Meraki is the one platform shipped in
v0.1**. **Preview — mock-validated only, not verified against a live Meraki org.**

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
- **Encrypted credentials**: the Meraki API key lives in an encrypted store `~/.fabric-aiops/secrets.enc` (Fernet + scrypt) — **never plaintext on disk**. Unlock with a master password from `FABRIC_AIOPS_MASTER_PASSWORD` (MCP/CI) or an interactive prompt (CLI).
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

## Quick start

```bash
uv tool install fabric-aiops              # or: pipx install fabric-aiops
fabric-aiops init                         # wizard: add a target + store the Meraki API key (encrypted)
fabric-aiops doctor                       # verify config, secrets, connectivity
fabric-aiops overview                     # one-shot fabric fleet health
fabric-aiops health uplink-rca            # rank worst MX WAN uplinks + cause/action
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
  (`ENDPOINT_AUDIT_APPROVED_BY` / `ENDPOINT_AUDIT_RATIONALE` — the env-var names
  the bundled harness reads).
- **Undo recording** — reversible writes record an inverse descriptor built from
  the fetched before-state.

## Scope

This is the **network-fabric / controller** member of the AIops-tools family
(governed AI-ops with audit + budget + undo + risk tiers). Do **NOT** use it for
OT / industrial edge (Modbus, OPC-UA, PROFINET) — see the separate
`industrial-aiops` line — nor for device-level CLI/SSH network automation.

## Missing a capability?

Coverage is intentionally a curated subset of the Meraki Dashboard API. Missing a
call, a device family, or want another controller platform (Catalyst Center,
Arista CVP)? **Open an issue or PR** — contributions welcome.

## Status

**Preview — mock-validated only, not verified against a live Meraki org.** The
Dashboard API paths are modelled from the public API shape and need live
verification. `fabric-aiops doctor` is the fastest live check.
