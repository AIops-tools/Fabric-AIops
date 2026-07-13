# Fabric AIops v0.1.0 — preview

Governed AI-ops for **network fabrics** managed through the **Cisco Meraki
Dashboard API** (organizations → networks → devices) for AI agents, with a
built-in governance harness (audit, policy, token/runaway budget, undo-token
recording, graduated risk tiers) and an encrypted credential store. Standalone —
no external skill-family dependency. Multi-platform by construction; Meraki is
the one platform shipped in v0.1.

> **Preview / mock-only.** All behaviour is validated against mocked Dashboard
> API responses; it has not been run against a live Meraki organization. The
> fastest live check is `fabric-aiops doctor`.
>
> Community-maintained; **not affiliated with or endorsed by Cisco/Meraki.**
> Trademarks belong to their owners.

## Highlights

- **32 MCP tools** (24 read, 8 write), every one wrapped with `@governed_tool`.
  - Read: fleet `overview`; organizations (6); networks (5); devices (5);
    clients (4); and three flagship analyses.
  - Write: `reboot_device`/`claim_devices_into_network`/`remove_device_from_network`/
    `bind_network_to_template`/`unbind_network_from_template` (high),
    `update_device`/`update_network_vlan` (medium, capture before-state),
    `blink_device_leds` (low).
- **Three signature analyses** — `uplink_loss_and_latency_rca` (rank worst MX WAN
  uplinks + cause/action), `network_health_score` (composite per-network health),
  and `config_template_drift` (settings drifted from a bound template).
- **Encrypted API key store** (`~/.fabric-aiops/secrets.enc`, Fernet + scrypt) —
  never plaintext on disk; legacy `FABRIC_<TARGET>_APIKEY` env fallback.
- **CLI** with an `init` onboarding wizard, `secret` management, and `doctor`.
- **Multi-platform connection layer** — a `platform` registry (`Authorization:
  Bearer` or `X-Cisco-Meraki-API-Key` auth, `Link`-header pagination) with
  teaching error translation (`FabricApiError`); only Meraki registered in v0.1.

## Install

```bash
uv tool install fabric-aiops
fabric-aiops init
fabric-aiops doctor
```

## Caveats

- The Dashboard API paths are modelled from the public API shape and need live
  verification against a real organization.
- Out of scope by design: full org/network provisioning workflows, firmware
  upgrade orchestration, and any bulk destructive operation.
- Missing a capability or want another controller platform (Catalyst Center,
  Arista CVP)? Open an issue or PR.
