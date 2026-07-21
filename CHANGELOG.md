# Changelog

## v0.7.0 — 2026-07-21

### Changed (BREAKING)
- **Removed the authorization layer** — read-only mode, the approver gate, and rules.yaml deny are gone. The skill no longer decides read vs write; that is the agent's judgement or the connecting account's permissions. `<PREFIX>_READ_ONLY` now has no effect (a startup warning is logged); `<PREFIX>_AUDIT_APPROVED_BY`/`_RATIONALE` are optional audit annotations.
- The retained guarantee is **unbypassable audit over MCP and CLI alike** — no unaudited entry point. Harness = audit + runaway safety guard + undo + sanitize; `risk_level` is a descriptive audit label, not a gate.

See RELEASE_NOTES.md for tool-specific changes.


## v0.6.0 — 2026-07-20

### Fixed
- **`bind_network_to_template` refuses a bind that would destroy config it cannot restore.** Binding overwrites the network's VLAN configuration, and the undo only ever restored the binding *pointer* — so it reported success while the original VLANs were gone.
- Harness: a write whose response is lost is audited `status=unknown`, not `error` — it may have taken effect. Undo tokens gain `effectVerified` (undo.db migrated in place).
- Harness: a dry-run no longer records an undo token, and no longer requires a named approver. Guards now run on the preview path.
- Truncated strings end in an ellipsis instead of being cut silently; error messages are capped at 800 chars, not 300.

See RELEASE_NOTES.md for the full detail.

## v0.4.0 — 2026-07-17

### Added
- **New:** UniFi controller platform (4th: Meraki/Catalyst/CVP/UniFi).
- **Undo executor**: `undo list` / `undo apply <id>` (CLI + MCP) — apply a recorded replayable inverse; the dispatched inverse is re-gated by its own risk tier; single-use, dry-run, double-confirm, both wrapper + inverse audited.

## v0.3.0 — 2026-07-16

### Fixed
- **`secrets.enc` now follows `FABRIC_AIOPS_HOME`** (secretstore hardcoded the real
  home directory; config/audit/undo already relocated — found in live verification).
- **Audit fidelity**: failures sanitized into `{"error": ...}` results by the MCP error
  layer are now audited as `status=error` (they previously read as `ok`, hiding failed
  attempts from exception reports), and no undo is recorded for a call that failed.
- **New controller platforms: Cisco Catalyst Center and Arista CloudVision Portal (CVP)** alongside Meraki — one tool, platform registry; unsupported ops fail fast with a teaching error (see the support matrix in README).
- Catalyst token auth with automatic refresh-on-401.
- Undo replay fix: `remove_device_from_network` now accepts a `serials` list, so `claim_devices_into_network`'s undo descriptor is replayable.

### Tests
- `doctor` and the `init` wizard are now fully covered (previously ~10–20%); plus a
  regression test for the sanitized-failure audit status.

## (merged into v0.3.0)

### Added

- **Two new controller platforms** alongside Meraki, per the platform-registry
  architecture (registry entries + request/response adaptation — zero new
  ops/CLI/MCP surface):
  - **`catalyst` — Cisco Catalyst Center** (formerly DNA Center), read subset:
    sites stand in for canonical organizations/networks (`/dna/intent/api/v1/site`,
    `site-health`), device inventory + health (`network-device`, `device-health`),
    issues → network alerts (P1→critical, P2→warning, P3/P4→info), per-device
    interface stats → switch ports (by device uuid), and aggregate
    `client-health` / per-MAC `client-detail` for clients. Auth is a short-lived
    (~1 h) session token: the stored secret is `username:password`, exchanged via
    `POST /dna/system/api/v1/auth/token` (HTTP Basic) for an `X-Auth-Token`
    that is auto-refreshed once on a 401 and the request retried.
  - **`cvp` — Arista CloudVision Portal**, read subset: containers stand in for
    canonical organizations/networks (`/cvpservice/inventory/containers`,
    `getContainerInfoById.do`), device inventory with the
    `complianceCode`/`complianceIndication` config-drift signal
    (`/cvpservice/inventory/devices`), events → network alerts
    (`getAllEvents.do`), users → org admins. Auth is a service-account token
    (`Authorization: Bearer`).
- **Canonical-op layer**: `Platform` descriptors now declare per-key
  `PathSpec` path templates (all interpolated segments percent-encoded through
  the central `seg()` helper) plus response adapters; the ops layer resolves
  canonical keys (`orgs.list`, `networks.alerts`, `devices.update`, ...)
  through the target's platform. Ops a platform does not map raise a teaching
  `PlatformUnsupported` ("not supported on X yet — open an issue or PR"),
  never a silent no-op; **writes are Meraki-only today** and fail fast before
  any controller call on catalyst/cvp.
- `init` wizard gains a platform choice with per-platform base-URL, default
  scope-id, and secret prompts; `doctor` probes each target with the canonical
  top-of-hierarchy read (organizations / sites / containers) and reports the
  platform's own vocabulary.
- Tests: 70 new (catalyst session-token flow incl. refresh-on-401, path
  building with hostile ids, response normalisation for every mapped canonical
  key on both new platforms, unsupported-op/write teaching errors, cross-platform
  doctor probe). 134 total.

### Notes / known deferrals

- Catalyst Center and CVP list endpoints are fetched as one bounded page (no
  Link-header pagination on those platforms); deep pagination is deferred.
- CVP configlet-content retrieval and compliance re-check triggers, and
  Catalyst Center per-client listing, are deferred (would need new surface).
- Both new platforms are **preview / mock-validated only** — modelled from the
  public API shapes, not verified against a live controller.

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
