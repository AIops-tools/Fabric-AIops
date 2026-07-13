# Changelog

All notable changes to fabric-aiops are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); this project uses semantic
versioning (currently 0.x preview — the API may change).

## [0.1.0] — 2026-07-13

Initial preview release: governed AI-ops for network fabrics managed through the
**Cisco Meraki Dashboard API** (organizations → networks → devices) with a
bundled governance harness. **Mock-validated only — not verified against a live
Meraki organization.** Community-maintained; not affiliated with Cisco/Meraki.

### Added

- **Multi-platform connection layer** — a `platform` registry (`fabric_aiops.platform`)
  keyed by name so future controllers (Catalyst Center, Arista CVP) can register a
  descriptor without touching the ops/CLI/MCP layers. Only **meraki** is
  registered in v0.1. Handles `Authorization: Bearer` / `X-Cisco-Meraki-API-Key`
  auth, `Link`-header pagination, and injection-safe response normalisation.
- **32 governed MCP tools**, every one wrapped with `@governed_tool`:
  - **Overview** — `overview` (org networks + device status/product rollup).
  - **Organizations** — `org_list`, `org_get`, `org_licensing`, `org_admins`,
    `org_device_statuses`, `org_api_requests`.
  - **Networks** — `network_list`, `network_get`, `network_vlans`,
    `network_alerts`, `network_traffic`.
  - **Devices** — `device_inventory` (by model MX/MS/MR/MV/MG), `device_status`,
    `device_uplinks`, `switch_ports`, `wireless_ssids`.
  - **Clients** — `client_list`, `client_get`, `client_usage`,
    `client_connectivity`.
  - **Health (flagship)** — `uplink_loss_and_latency_rca`, `network_health_score`,
    `config_template_drift`.
  - **Remediation (writes)** — `reboot_device` (high), `blink_device_leds` (low),
    `update_device` (medium), `update_network_vlan` (medium),
    `claim_devices_into_network` (high), `remove_device_from_network` (high),
    `bind_network_to_template` (high), `unbind_network_from_template` (high).
- **Guarded writes** — every write supports a `dry_run` preview and (at the CLI)
  double confirmation. Mutating/reversible writes fetch the **real before-state**
  and record a faithful inverse undo descriptor.
- **Bundled governance harness** (`fabric_aiops.governance`) — audit log, policy
  engine, token/runaway budget guard, undo-token recording, graduated risk tiers,
  prompt-injection `sanitize`. State under `~/.fabric-aiops/` (relocatable via
  `FABRIC_AIOPS_HOME`).
- **Encrypted secret store** — Meraki API keys in `~/.fabric-aiops/secrets.enc`
  (Fernet + scrypt); legacy `FABRIC_<TARGET>_APIKEY` env fallback + `secret
  migrate`.
- **CLI** — `init` wizard, `overview`, `org`, `network`, `device`, `client`,
  `health`, `remediate`, `secret`, `doctor`, `mcp`.

### Known limitations

- Preview / mock-only: Dashboard API paths need live verification.
- Coverage is a curated subset of the Dashboard API; open an issue/PR for gaps.

[0.1.0]: https://github.com/AIops-tools/Fabric-AIops/releases/tag/v0.1.0
