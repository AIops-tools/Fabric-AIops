# Changelog

## v0.2.0 — 2026-07-13

Security-hardening release from a line-wide code review.

### Changed (behavior)
- **Secure by default**: with no `rules.yaml`, high/critical operations now require a
  named approver (`FABRIC_AUDIT_APPROVED_BY`). A fresh install no longer allows
  destructive writes unattended; `init` seeds a starter `rules.yaml` you can edit,
  and an operator-authored rules file is honoured as-is.
- `__version__` is now single-sourced from package metadata (the previous release
  self-reported a stale version string).
- Sanitize docs no longer overstate scope: it strips control/format characters and
  truncates; semantic prompt-injection resistance must come from the consuming agent.

### Fixed
- All controller API paths percent-encode agent-supplied segments via a central helper (34 sites).
- `init` gains an explicit TLS-verification prompt (default ON).
- Governance docstrings no longer reference a sibling tool.
- Cached HTTP clients are closed at process exit.

### Tests
- Governance persistence is now tested against REAL `audit.db`/`undo.db` files
  (write → audit row + inverse undo row with captured prior state).
- The CLI confirmed-write path (dry-run / double-confirm / governed execution) is
  covered end-to-end.
- `pytest-cov` added to the dev dependencies.

## v0.1.2

- Fix: `FABRIC_AIOPS_HOME` now also relocates `config.yaml` (was hardcoded to `~/.fabric-aiops`).
- Fix: **CLI writes are now audited + undo-recorded** via the governance path — previously only the MCP tools recorded audit/undo; CLI `manage`/`remediate`/etc. writes now go through the same `@governed_tool` layer (they keep their dry-run + double-confirm). CLI write output is now the governed JSON result. No API/tool changes.


## v0.1.1

- Fix: governance env-var prefix `ENDPOINT_*` → `FABRIC_*` (operator budget/policy/audit overrides like `FABRIC_MAX_TOOL_CALLS`, `FABRIC_POLICY_DISABLED`, `FABRIC_AUDIT_APPROVED_BY` now take effect; the v0.1.0 harness read the wrong namespace). No API or tool changes.


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
