<!-- mcp-name: io.github.AIops-tools/fabric-aiops -->

# Fabric AIops

> **Disclaimer**: Community-maintained open-source project. **Not affiliated with, endorsed by, or sponsored by Cisco, Meraki, Arista, Ubiquiti, or any network-controller vendor.** "Cisco", "Meraki", "Catalyst", "DNA Center", "Arista", "CloudVision", "Ubiquiti", "UniFi", "ACI", "APIC" and all product/trademark names belong to their respective owners. MIT licensed.

Governed AI-ops for **network fabrics** managed through a controller — the
**Cisco Meraki Dashboard API** (the reference platform, full read + write),
**Cisco Catalyst Center** (formerly DNA Center; read subset), **Arista
CloudVision Portal (CVP)** (read subset), and **UniFi Network** (self-hosted
controller or UniFi OS console; read subset + device restart), plus read-only
**Cisco ACI (APIC)** endpoint path reconstruction and faults — with a
**built-in governance
harness**: unified audit log, token/runaway budget guard,
undo-token recording, and descriptive risk tiers. **Multi-platform by
construction**: a registry keyed by `platform` maps every *canonical operation*
onto each controller's REST API (path templates + response adapters), so adding
a controller is a registry entry, never new ops/CLI/MCP surface. An operation a
platform doesn't map returns a clear teaching error ("not supported on X yet —
open an issue"), never a silent no-op. No platform has yet been exercised
against a live controller by this tool; the ACI reads are the one part built
from **real** controller responses (anonymized APIC payloads a production user
supplied) — see [`docs/VERIFICATION.md`](docs/VERIFICATION.md).

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

- **CLI** (`fabric-aiops ...`): `init`, `overview`, `org`, `network`, `device`, `client`, `health`, `aci`, `remediate`, `secret`, `doctor`, `mcp`.
- **MCP server** (`fabric-aiops mcp` or `fabric-aiops-mcp`): **37 tools** (28 read, 9 write), every one wrapped with the bundled `@governed_tool` harness.
- **Encrypted credentials**: the controller secret (Meraki API key / Catalyst Center or APIC `username:password` / CVP service-account token / UniFi API key) lives in an encrypted store `~/.fabric-aiops/secrets.enc` (Fernet + scrypt) — **never plaintext on disk**. Unlock with a master password from `FABRIC_AIOPS_MASTER_PASSWORD` (MCP/CI) or an interactive prompt (CLI).
- **Reversibility**: mutating writes fetch the **real before-state first** and record a faithful inverse (`update_device`/`update_network_vlan` restore prior values; `claim`↔`remove`; `bind`↔`unbind`/rebind). Irreversible ops (`reboot_device`, `blink_device_leds`) record the prior state for audit but declare no undo.
- **Safety**: every state-changing CLI op supports `--dry-run` and requires double confirmation; every write MCP tool takes a `dry_run` preview.

## Capability matrix (37 MCP tools)

| Domain | Tools | Count | R/W |
|--------|-------|:-----:|:---:|
| **Overview** | `overview` | 1 | read |
| **Organizations** | `org_list`, `org_get`, `org_licensing`, `org_admins`, `org_device_statuses`, `org_api_requests` | 6 | read |
| **Networks** | `network_list`, `network_get`, `network_vlans`, `network_alerts`, `network_traffic` | 5 | read |
| **Devices** | `device_inventory`, `device_status`, `device_uplinks`, `switch_ports`, `wireless_ssids` | 5 | read |
| **Clients** | `client_list`, `client_get`, `client_usage`, `client_connectivity` | 4 | read |
| **Health (flagship)** | `uplink_loss_and_latency_rca`, `network_health_score`, `config_template_drift` | 3 | read |
| **Cisco ACI** (APIC only) | `aci_endpoint_trace`, `aci_epg_segment`, `aci_faults_list` | 3 | read |
| **Remediation** | `reboot_device`, `claim_devices_into_network`, `remove_device_from_network`, `bind_network_to_template`, `unbind_network_from_template` | 5 | write (high) |
| | `update_device`, `update_network_vlan` | 2 | write (medium) |
| | `blink_device_leds` | 1 | write (low) |
| **Undo** | `undo_list` | 1 | read |
| | `undo_apply` | 1 | write (medium) |

`network_health_score` and `config_template_drift` are injected-only (they score
data you already hold); `uplink_loss_and_latency_rca` accepts injected `records`
for offline analysis or pulls live from a configured target. Device models carry
a product-type prefix: **MX** appliance, **MS** switch, **MR** wireless AP, **MV**
camera, **MG** cellular gateway.

## Platform support matrix

One tool, five controller platforms. For the four in the table below the
ops/CLI/MCP surface is identical everywhere; each platform maps the canonical
operations it supports and raises a teaching error for the rest ("not supported
on `<platform>` yet — open an issue or PR"). **ACI maps none of these** — see
[Cisco ACI](#cisco-aci-apic) below.

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

### Cisco ACI (APIC)

ACI has two axes — tenant → VRF → bridge domain → EPG, and pod → node →
interface — and neither is an organization → network → device tree. Rather than
make every `network_id` mean something different per call, an `aci` target
answers the canonical tools with a teaching error and gets three **read-only**
ACI-native tools that take ACI references (MAC/IP, tenant/app/EPG names, DNs):

| Tool (CLI) | What it answers |
|---|---|
| `aci_endpoint_trace` (`aci trace --mac/--ip`) | Where is this endpoint and what carries it: attachment (pod / leaf / interface or vPC) → EPG → bridge domain → VRF → the EPG's contracts → filter entries, plus faults whose DN names the EPG or the attachment interface |
| `aci_epg_segment` (`aci segment TENANT APP EPG`) | The same EPG → BD → VRF → contracts chain from an EPG name |
| `aci_faults_list` (`aci faults`) | Fabric faults, worst severity first |

How to read them:

- **A static reconstruction, not a reachability test.** `staticPathResolved:
  true` means the fabric is configured to carry the endpoint, not that traffic
  flows. Nothing is written to the APIC and no troubleshooting session is created.
- **Links follow the relation APIC resolved** (`tDn`), so cross-tenant links into
  `common` are followed; a relation that is not `formed` is a finding with its state.
- **Related faults are candidates** matched by DN (the EPG, the attachment
  interface, the leaf's deployed copy of the EPG's policy) — proximity, not cause.
- **Fault rows are records, not incidents**: a `delegated` record mirrors a fault
  on another object. A cleared fault keeps the description it had when raised.
- **Not evaluated** (listed in every result as `notChecked`): vzAny contracts,
  taboo contracts, imported contract interfaces, preferred-group peers, service
  graphs / L3Out.

Leaf/spine inventory and interface state (`fabricNode`, `topSystem`, `l1PhysIf`)
are not in this release — they will be built once real responses for those
classes are available, as the ones above were.

### Per-platform auth

| Platform | `platform:` | Secret stored (encrypted) | Auth on the wire | Base URL |
|----------|-------------|---------------------------|------------------|----------|
| Cisco Meraki Dashboard | `meraki` | API key (Dashboard → Organization → Settings → API access) | `Authorization: Bearer` (or `auth_style: meraki-key` → `X-Cisco-Meraki-API-Key`) | default `https://api.meraki.com/api/v1` |
| Cisco Catalyst Center | `catalyst` | `username:password` (one string) | exchanged via `POST /dna/system/api/v1/auth/token` (HTTP Basic) for a ~1 h `X-Auth-Token`, auto-refreshed once on a 401 | required, e.g. `https://<catalyst-center-host>` |
| Arista CloudVision Portal | `cvp` | service-account token (Settings → Access Control → Service Accounts) | `Authorization: Bearer` | required, e.g. `https://<cvp-host>` |
| Cisco ACI (APIC) | `aci` | `username:password` (one string; a read-only role is enough — every ACI call is a GET) | `POST /api/aaaLogin.json` opens a session carried as a cookie; extended via `/api/aaaRefresh.json` at half its lifetime; one fresh login on a 401/403 (APIC reports an expired session as 403) | required, e.g. `https://<apic-host>` |
| UniFi Network | `unifi` | API key (UniFi OS: Settings → Control Plane → Integrations; self-hosted Network Server 9.0+) | `X-API-KEY` (stateless; legacy cookie login is a known deferral) | required — classic controller `https://<host>:8443`, or UniFi OS console `https://<console>/proxy/network` (keep the prefix) |

`fabric-aiops init` walks through the platform choice and stores the right kind
of secret; `fabric-aiops doctor` probes each target with the canonical
top-of-hierarchy read (organizations / sites / containers / UniFi sites; on an
APIC, the tenant count), exercising the full auth flow.

## What this tool does, and does not, decide

It delivers network-fabric operations — reads and writes — accurately and
efficiently, and records every one of them. It does **not** decide whether a
write is allowed to happen. That is the agent's judgement, or the permission of
the account you connect it with: give it a Meraki API key whose admin has
read-only organization access (or the read-only equivalent on your controller)
and the writes fail at the controller — the place that actually owns the
permission.

So there is no read-only switch, no policy file, no approval gate to configure.
The one thing the tool guarantees is that nothing is silent: **every call, over
MCP and over the CLI alike, lands an audit row** in `~/.fabric-aiops/audit.db`,
and mutating writes still capture their before-state and record an inverse where
one exists.

> Each tool declares a `risk_level`, kept in agreement with its `[READ]`/`[WRITE]`
> documentation tag by a test, and carried into the audit row as a descriptive
> tier — so a reviewer can see at a glance that a row was a high-risk delete. It
> is a label, not a gate.

## Quick start

### As a Claude Code plugin

One install gives an agent both the skill and the MCP server:

```
/plugin marketplace add AIops-tools/marketplace
/plugin install fabric-aiops@aiops-tools
```

The MCP server is fetched with [uv](https://docs.astral.sh/uv/) and pinned to the
package version this plugin declares, so an audit row can be traced back to the
code that wrote it. Credentials are still configured with `fabric-aiops init` — see below.

### As an OpenClaw plugin

The same bundle is published on [ClawHub](https://clawhub.ai/plugins), where one
install delivers the skill and its MCP server together:

```bash
openclaw plugins install clawhub:@zw008/fabric-aiops
openclaw skills info fabric-aiops          # expect: Visible to model: yes
```

Restart the OpenClaw gateway afterwards so it loads the plugin. The MCP server is
fetched with [uv](https://docs.astral.sh/uv/), pinned to this exact release, so
`uvx` has to be on `PATH` — without it the skill still installs but reports
`Visible to model: no`. Credentials are configured exactly as below.

### As a CLI or standalone MCP server

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

> **Where that password then lives**: an exported variable is readable by
> every process this shell starts and is recorded by shell history. On a
> shared or long-lived host, prefer the interactive prompt, or inject it from
> a secret manager for the life of the one command that needs it.

## Governance

Every operation — MCP **and** CLI — passes through the bundled `@governed_tool`
harness. It records; it does not authorize (see above).

- **Audit** — every call (params, result, status, duration, risk tier, and any
  operator-supplied approver/rationale) is logged to `~/.fabric-aiops/audit.db`
  (relocatable via `FABRIC_AIOPS_HOME`). The CLI writes the same row the MCP path
  does — there is no unaudited entry point.
- **Runaway guard** — a safety backstop, not an authorization gate: the same
  call hammered in a tight loop trips a circuit breaker so a stuck agent can't
  burn unbounded calls/time. Disable with `FABRIC_RUNAWAY_MAX=0`; optional hard
  ceilings via `FABRIC_MAX_TOOL_CALLS` / `FABRIC_MAX_TOOL_SECONDS`.
- **Undo recording** — reversible writes record an inverse descriptor built from
  the fetched before-state.
- **Risk tier** — a descriptive label on the audit row derived from
  `risk_level`; it gates nothing.

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

No platform has yet been exercised by this tool against a live Meraki
organization, Catalyst Center appliance, CloudVision Portal instance, UniFi
controller or APIC. The first four platforms' API paths are modelled from the
public API shapes and tested against mocks. The ACI reads are tested against
**real, anonymized APIC responses** from a production fabric (endpoint, EPG,
bridge domain, VRF, contract, filter entry, faults) and their query mechanics
against Cisco's own SDK — but a live run of this tool against an APIC is still
outstanding. [`docs/VERIFICATION.md`](docs/VERIFICATION.md) defines the checklist
a live run must cover; `fabric-aiops doctor` is the fastest live check.
