# Live verification

`fabric-aiops` is exercised by a **mock-only** test suite (`uv run pytest`, no
real controller). None of its four platforms — Cisco Meraki Dashboard, Cisco
Catalyst Center, Arista CloudVision Portal, UniFi Network — has yet been
validated end-to-end against a live controller. Until one has, we do not claim
it works against a real API.

This document defines exactly what a live verification run must cover, and the
criteria for calling a platform live-verified. It is deliberately checklist-shaped
so the result is reproducible and auditable — not a subjective "seems fine".

**Verification is per platform.** A green run against a Meraki organization says
nothing about Catalyst Center, CVP, or UniFi: each platform is a separate
registry descriptor (path templates + response adapters), and the adapters are
exactly what a live run tests. Record which platform you verified.

## What the mock suite already guarantees

- Every module imports; the CLI builds; every MCP tool carries the
  `@governed_tool` harness marker (`tests/test_smoke.py`).
- The three flagship analyses are unit-tested against synthetic telemetry:
  `uplink_loss_and_latency_rca` (threshold ranking + cause/action mapping),
  `network_health_score` (composite scoring with every component shown), and
  `config_template_drift` (per-setting deviation against a bound template).
- The platform registry resolves canonical operations per platform, and an
  operation a platform does not map raises the teaching "not supported on
  `<platform>` yet" error rather than silently no-op'ing.
- Reversible writes (`update_device`, `update_network_vlan`,
  `claim_devices_into_network`, `remove_device_from_network`,
  `bind_network_to_template`, `unbind_network_from_template`) fetch a
  before-state and record the correct inverse undo descriptor against a mocked
  connection; `reboot_device` and `blink_device_leds` declare no undo.
- Agent-supplied ids (org / network / serial / VLAN) are percent-encoded before
  entering a URL path.
- Governance persistence: audited rows land in the SQLite audit DB. The harness
  authorizes nothing — there is no read-only, deny-rule, or approver gate to test.

What it does **not** guarantee: that the controller API paths, response field
names, pagination, rate-limit behaviour, and auth handshakes match a real
controller build. For Catalyst Center specifically, the
`POST /dna/system/api/v1/auth/token` exchange and the 401 auto-refresh are
untested against a real appliance; for UniFi, the API-key header and the
`/proxy/network` base-URL prefix on a UniFi OS console are untested.

## Prerequisites for a live run

A reachable controller for the platform under test, and a credential with
least privilege for that platform:

| Platform | Needs |
|----------|-------|
| `meraki` | An organization (a lab/test org preferred) and an API key. Read-only is enough for sections 1-3; the write checks need write scope. |
| `catalyst` | A Catalyst Center appliance and a `username:password` with read scope (reads only — no writes are mapped). |
| `cvp` | A CloudVision Portal instance and a service-account token with read scope (reads only). |
| `unifi` | A UniFi Network controller or UniFi OS console (Network Server 9.0+ for API keys) and an API key. Reads plus the device-restart write. |

You also need a **throwaway device and a throwaway network** you are willing to
rename, reclaim, remove, and rebind. Never verify against a production branch.

```bash
uv tool install fabric-aiops
fabric-aiops init            # encrypted secret store, TLS verify on by default
```

## Verification checklist

Tick every box for the platform under test. A box that cannot be ticked is a
verification gap — record it, do not silently pass. Boxes for operations a
platform does not map should be marked **n/a (unmapped)**, and the teaching
error confirmed instead (section 6).

### 1. Connectivity and auth (the fastest live gate)
- [ ] `fabric-aiops doctor` → all green (config, encrypted secret store, and a
      real reachability probe against the controller).
- [ ] For `catalyst`: the token exchange succeeds, and an expired token
      auto-refreshes once on 401 rather than failing the call.
- [ ] For `unifi` on a UniFi OS console: the `/proxy/network` base-URL prefix
      resolves — reads succeed without hand-editing paths.

### 2. Reads return real, well-shaped data
- [ ] `fabric-aiops overview` → network count and device status/product rollup
      match the controller dashboard.
- [ ] `fabric-aiops org list` / `fabric-aiops org get` → the real organizations
      (or the platform's stand-in: Catalyst sites, CVP containers, UniFi sites).
- [ ] `fabric-aiops network list` and `network get <networkId>` → the real
      networks with populated ids and names.
- [ ] `fabric-aiops device inventory` → the real devices; `--model` filtering
      returns the expected model family.
- [ ] `fabric-aiops device status <serial>` and `fabric-aiops device uplinks` →
      availability and per-uplink state match the dashboard.
- [ ] `fabric-aiops network vlans <networkId>`, `network alerts <networkId>`,
      `network traffic <networkId>` → real values; no crash on missing fields.
- [ ] `fabric-aiops client list` / `client get` / `client usage` /
      `client connectivity` → real clients with plausible usage figures.
- [ ] `fabric-aiops org licensing`, `org admins`, `org device-statuses`,
      `org api-usage` → match the dashboard.
- [ ] Pagination: an org large enough to page returns the **full** set, not just
      the first page.

### 3. The flagship analyses hold up against real telemetry
- [ ] `fabric-aiops health uplink-rca` → the ranked uplinks match what the
      dashboard shows as degraded; the cited loss/latency numbers agree with the
      controller's own graphs; the cause/action mapping is defensible.
- [ ] `fabric-aiops health uplink-rca --loss-pct 2 --latency-ms 100` → tightening
      thresholds pulls in more uplinks, in the expected order.
- [ ] `fabric-aiops health score` → the worst-scoring network is one operators
      independently agree is worst; every deduction is traceable to a real
      device/uplink state.
- [ ] `config_template_drift` against a real template and its bound networks →
      the flagged settings are genuinely drifted (spot-check two in the
      dashboard), and an in-compliance network is reported clean.

### 4. A reversible write + its undo (governance closes the loop)
- [ ] `fabric-aiops remediate update-device <serial> '{"name":"<new>"}' --dry-run`
      → prints the exact API call, changes nothing.
- [ ] Same without `--dry-run` → the dashboard shows the new name; the result
      carries an `_undo_id`; a row lands in `~/.fabric-aiops/audit.db`.
- [ ] `fabric-aiops undo list` then `undo apply <id>` → the **prior** name is
      restored (proves the before-state was fetched, not guessed).
- [ ] `fabric-aiops remediate update-vlan <networkId> <vlanId> '{...}'` then
      `undo apply` → the prior VLAN attributes come back.
- [ ] `fabric-aiops remediate claim <networkId> <serial>` then `undo apply` →
      the device is removed from the network again; and the reverse pair
      (`remediate remove` → `undo apply`) re-claims it.
- [ ] `fabric-aiops remediate bind <networkId> <templateId>` then `undo apply` →
      the network returns to its **captured** prior binding (unbound, or bound
      to the prior template — not a guess).

### 5. Irreversible writes are honest about it
- [ ] `fabric-aiops remediate reboot <serial> --dry-run` → previews only;
      without `--dry-run` the device actually reboots, the audit row records the
      before-state, and **no** undo descriptor is declared.
- [ ] `fabric-aiops remediate blink-leds <serial> --duration 20` → the LEDs
      actually blink (low risk, no confirmation), and no undo is declared.

### 6. Unmapped operations teach instead of failing silently
- [ ] On `catalyst` / `cvp`, a write (e.g. `remediate update-device`) returns the
      "not supported on `<platform>` yet" error and performs **no** API call.
- [ ] On `unifi`, `remediate reboot` works (the one mapped write) while the other
      writes return the teaching error.

### 7. Audit is unbypassable — both entry points
- [ ] Run a `high`-risk write (e.g. `remediate reboot`, `remediate bind`) over MCP
      and the same op over the CLI; confirm **both** land a row in `audit.db`, and
      that `FABRIC_AUDIT_APPROVED_BY` / `FABRIC_AUDIT_RATIONALE`, when set, appear on
      the row (recorded, never required — the skill authorizes nothing).
- [ ] A tight poll loop trips the runaway budget guard rather than burning the
      controller's API rate limit.
- [ ] Relocation works: with `FABRIC_AIOPS_HOME` set, `audit.db`, the undo store,
      and `secrets.enc` all land under that directory.

### 8. Cleanup
- [ ] The test device is back in its original network with its original name and
      binding; the test VLAN's attributes are restored; every step above appears
      in the audit DB.

## Criteria to consider a platform live-verified

1. Every applicable checklist box is ticked against a real controller, and the
   **platform and version are recorded** (e.g. "verified against Meraki
   Dashboard API v1, <date>"). Boxes marked n/a must cite the unmapped
   operation.
2. Any path, field-shape, pagination, or auth mismatch found during the run is
   fixed and covered by a regression test.
3. The run is written up with the date and package version, matching how the
   product line records its other live-verified tools.

Verifying one platform does **not** clear the others — each is claimed
separately.

## Notes for maintainers

- `fabric-aiops doctor` is the single fastest live entry point; start there.
- The three analyses accept **injected records**, so a partial verification is
  still valuable: export real uplink, device-status, and template data from a
  controller you cannot get write access to, feed it to
  `uplink_loss_and_latency_rca` / `network_health_score` /
  `config_template_drift`, and tick section 3 while leaving 4-6 open.
- Rate limits are the most likely live surprise. Run section 2 against a large
  org deliberately, and watch whether the budget guard or the controller's
  429 handling trips first.
- Record the result in the product line's verification ledger once green so the
  "verification debt" list stays accurate.
