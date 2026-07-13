---
name: fabric-aiops
description: >
  Use this skill whenever the user needs to operate a Cisco Meraki network fabric through the Dashboard API — a one-shot fabric health overview; organization reads (list/get, licensing, admins, org-wide device statuses, API usage); network reads (list/get, VLANs, health alerts, traffic); device reads (inventory by model MX/MS/MR/MV/MG, status, uplinks, switch ports, wireless SSIDs); client reads (list, detail, usage, connectivity); three flagship analyses — uplink loss & latency RCA (rank worst MX WAN uplinks + cause/action), network health score (composite per-network), and config template drift (settings drifted from a bound template); and eight guarded writes (reboot, blink LEDs, update device, update VLAN, claim/remove devices, bind/unbind a config template).
  Always use this skill for "Meraki org overview", "which uplinks are worst", "uplink loss and latency", "WAN degradation RCA", "network health score", "config template drift", "list Meraki networks/devices/clients", "reboot a Meraki device", "blink device LEDs", "claim a device into a network", "bind a network to a template" when the context is a Cisco Meraki Dashboard fabric.
  Do NOT use when the target is OT / industrial equipment (Modbus, OPC-UA, PLCs — use industrial-aiops), a hypervisor, a storage appliance, a backup product, a container/cluster orchestrator, or device-level CLI/SSH network automation (negative routing hints only).
  Preview — common Cisco Meraki fabric operations with a built-in governance harness (audit, policy, token budget, undo, risk-tiers). Mock-validated only, not verified against a live Meraki organization.
installer:
  kind: uv
  package: fabric-aiops
argument-hint: "[org/network/device id or describe your fabric task]"
allowed-tools:
  - Bash
metadata: {"openclaw":{"requires":{"env":["FABRIC_AIOPS_CONFIG"],"bins":["fabric-aiops"],"config":["~/.fabric-aiops/config.yaml","~/.fabric-aiops/secrets.enc"]},"optional":{"env":["FABRIC_AIOPS_MASTER_PASSWORD"]},"primaryEnv":"FABRIC_AIOPS_CONFIG","homepage":"https://github.com/AIops-tools/Fabric-AIops","emoji":"🛰️","os":["macos","linux"]}}
compatibility: >
  Standalone, self-governed Cisco Meraki network-fabric operations (preview). The governance harness (audit, policy, token/runaway budget, undo, risk-tiers) is bundled in the package — no external skill-family dependency. Multi-platform by construction (a platform registry); only Meraki is registered in v0.1.
  All write operations are audited to a local SQLite DB under ~/.fabric-aiops/ (relocatable via FABRIC_AIOPS_HOME).
  Credentials: the Meraki API key is stored ENCRYPTED in ~/.fabric-aiops/secrets.enc (Fernet/AES-128 + scrypt-derived key) — never plaintext on disk. Run 'fabric-aiops init' to onboard, or 'fabric-aiops secret set <target>' to add one. The store is unlocked by a master password from FABRIC_AIOPS_MASTER_PASSWORD (non-interactive/MCP/CI) or an interactive prompt (CLI on a TTY). A legacy plaintext env var FABRIC_<TARGET_NAME_UPPER>_APIKEY is still honoured as a fallback with a deprecation warning (migrate with 'fabric-aiops secret migrate'). The API key is sent in the platform auth header (Authorization: Bearer, or X-Cisco-Meraki-API-Key) at request time and held only in memory; keys are never logged or echoed.
  State-changing operations require double confirmation at the CLI layer and support --dry-run. All write tools pass through the @governed_tool decorator (pre-check + budget guard + audit + risk-tier gate) and take a dry_run preview. Mutating/reversible writes fetch the real before-state first and record a faithful inverse undo descriptor; irreversible ops (reboot, blink) record only the before-state.
  Webhooks: none — no outbound network calls beyond the configured controller REST API base URL.
  SSL: verify_ssl defaults to true; disable only for a self-signed on-prem controller proxy.
  Transitive dependencies: httpx (HTTP client) and the MCP SDK. No post-install scripts or background services.
  PREVIEW: mock-validated only; the Dashboard API paths are modelled from the public API shape and need live verification. Community-maintained; not affiliated with or endorsed by Cisco/Meraki — trademarks belong to their owners.
---

# Fabric AIops (preview)

> **Disclaimer**: Community-maintained open-source project, **not affiliated with, endorsed by, or sponsored by Cisco, Meraki, or any network-controller vendor.** Product and trademark names belong to their owners. Source at [github.com/AIops-tools/Fabric-AIops](https://github.com/AIops-tools/Fabric-AIops) under the MIT license.

Governed Cisco Meraki network-fabric operations — **32 MCP tools**, every one wrapped with the bundled `@governed_tool` harness: a local unified audit log under `~/.fabric-aiops/`, policy engine, token/runaway budget guard, undo-token recording, and graduated-autonomy risk tiers. The Meraki API key is stored **encrypted** (`~/.fabric-aiops/secrets.enc`, Fernet + scrypt) — never plaintext on disk.

> **Standalone**: the governance harness is bundled in the package (`fabric_aiops.governance`) — fabric-aiops has no external skill-family dependency. **Preview / mock-only**: not yet validated against a live Meraki organization.

## What This Skill Does

| Domain | Tools | Count | Read or Write |
|--------|-------|:-----:|:-------------:|
| **Overview** | fabric fleet overview | 1 | 1 read |
| **Organizations** | list/get, licensing, admins, device statuses, API usage | 6 | 6 read |
| **Networks** | list/get, VLANs, health alerts, traffic | 5 | 5 read |
| **Devices** | inventory (by model), status, uplinks, switch ports, SSIDs | 5 | 5 read |
| **Clients** | list, detail, usage, connectivity | 4 | 4 read |
| **Health (flagship)** | uplink loss/latency RCA, network health score, config template drift | 3 | 3 read |
| **Remediation** | reboot, claim, remove, bind, unbind | 5 | 5 write (high) |
| | update device, update VLAN | 2 | 2 write (medium) |
| | blink LEDs | 1 | 1 write (low) |

`network_health_score` and `config_template_drift` are injected-only (they score data you already hold); `uplink_loss_and_latency_rca` accepts injected `records` for offline analysis, or pulls live from a configured target. Meraki device models carry a product-type prefix: **MX** appliance, **MS** switch, **MR** wireless AP, **MV** camera, **MG** cellular gateway.

## Quick Install

```bash
uv tool install fabric-aiops
fabric-aiops init       # interactive wizard: connection + encrypted Meraki API key
fabric-aiops doctor
```

## When to Use This Skill

- Triage an organization (`overview`): network count + device status/product rollup
- Find the worst WAN uplinks (`health uplink-rca` / `uplink_loss_and_latency_rca`): ranked by loss + latency with a likely cause and action
- Score fleet health per network (`health score` / `network_health_score`): a composite 0-100, worst first, every component shown
- List/inspect organizations, networks, devices (by model), and clients
- Reboot/blink a device, update device or VLAN attributes (reversible), claim/remove devices, or bind/unbind a config template — all with dry-run + double-confirm

**Do NOT use when** the target is OT/industrial equipment (use industrial-aiops), a hypervisor, a storage appliance, a backup product, a container cluster, or device-level CLI/SSH network automation.

## Related Skills — Skill Routing

| If the user wants… | Use |
|--------------------|-----|
| Cisco Meraki fabric: uplinks, health, config templates, device lifecycle | **fabric-aiops** (this skill) |
| OT / industrial edge (Modbus, OPC-UA, PLC, PROFINET) | the **industrial-aiops** line |
| Hypervisor VM lifecycle (power, snapshot, migrate) | a hypervisor ops skill |
| Container/cluster lifecycle | a cluster ops skill |

## Common Workflows

### Diagnose degraded WAN uplinks

1. `fabric-aiops health uplink-rca` → worst MX WAN uplinks ranked by avg loss + latency, each with a likely cause and action
2. Cross-check the network's device availability: `fabric-aiops org device-statuses` and `fabric-aiops device uplinks`
3. If a branch is failing over, confirm with `fabric-aiops network alerts <networkId>`

### Bring a drifted network back to its template (reversible)

1. Pass the template + bound networks to `config_template_drift` → find the drifted settings
2. Correct the network, or (for the binding itself) `fabric-aiops remediate bind <networkId> <templateId> --dry-run` → preview the call
3. Re-run without `--dry-run` (double-confirm) — captures the prior binding and records an inverse (rebind/unbind) undo descriptor

### Offline analysis (no live org)

Pass data straight to the analysis tools — `uplink_loss_and_latency_rca(records=[...])`, `network_health_score(device_statuses=[...])`, or `config_template_drift(template=..., networks=[...])` — to analyse an exported dataset without connecting to a Meraki org.

## Governance & Safety

- Every tool is audited to `~/.fabric-aiops/audit.db` (relocatable via `FABRIC_AIOPS_HOME`).
- High-risk ops can require a named approver: set `FABRIC_AUDIT_APPROVED_BY` and `FABRIC_AUDIT_RATIONALE` (the env-var names the bundled harness reads).
- Writes support `--dry-run` / `dry_run=True` and double confirmation at the CLI.
- Mutating/reversible writes fetch the real before-state and record an inverse descriptor; irreversible ops (reboot, blink) record only the before-state.

## References

- `references/capabilities.md` — full tool + field reference
- `references/cli-reference.md` — CLI command reference
- `references/setup-guide.md` — onboarding, credentials, and connectivity
