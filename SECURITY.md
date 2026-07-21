# Security Policy

## Disclaimer

Community-maintained open-source project. **Not affiliated with, endorsed by, or
sponsored by Cisco, Meraki, or any network-controller vendor.** "Cisco",
"Meraki", and all product/trademark names belong to their respective owners.
Source is publicly auditable under the MIT license.

## Reporting Vulnerabilities

Report privately via a GitHub Security Advisory on
[github.com/AIops-tools/Fabric-AIops](https://github.com/AIops-tools/Fabric-AIops/security/advisories)
or email zhouwei008@gmail.com. Please do not open public issues for security
reports.

## Security Design

### Credential Management
- Per-target Meraki API keys live **encrypted** in `~/.fabric-aiops/secrets.enc`
  (Fernet/AES-128 + scrypt-derived key; chmod 600), never in `config.yaml` and
  never in source. The master password is never stored — only a per-store random
  salt and the ciphertext are on disk.
- A legacy plaintext env var `FABRIC_<TARGET_NAME_UPPER>_APIKEY` is still honoured
  as a fallback with a deprecation warning (migrate with `fabric-aiops secret
  migrate`).
- The API key is sent in the platform auth header (`Authorization: Bearer` by
  default, or `X-Cisco-Meraki-API-Key`) at request time and held only in memory.
  Keys are never logged or echoed; the config file holds only platform, base URL,
  default org id, and TLS settings.

### Governed Operations
Every MCP tool runs through the bundled `@governed_tool` harness
(`fabric_aiops.governance`):
- **Audit** — every call logged to a local SQLite DB under `~/.fabric-aiops/`
  (relocatable via `FABRIC_AIOPS_HOME`), agent-attributed, secret-redacted.
- **Token/runaway budget** — hard ceilings (`FABRIC_MAX_TOOL_CALLS` /
  `FABRIC_MAX_TOOL_SECONDS` — the env-var names the bundled harness reads) plus
  an on-by-default guard that trips a tight poll/retry loop, preventing unbounded
  Dashboard API consumption (and the associated rate-limit exposure).
- **Risk-tier labelling** — each tool's declared `risk_level` is carried into the
  audit row as a descriptive tier. It labels the row; it does not gate the call.
  Whether a write is permitted is the agent's or the account's decision, not the
  skill's. `FABRIC_AUDIT_APPROVED_BY` / `FABRIC_AUDIT_RATIONALE` are optional audit
  annotations, never required.
- **Undo-token recording** — mutating/reversible writes fetch the **real
  before-state first** and record a faithful inverse descriptor (e.g.
  `update_device` restores the prior attributes; `bind`↔`unbind`; `claim`↔`remove`).

### State-Changing Operations
Every write supports `--dry-run` (CLI) / `dry_run=True` (MCP) and requires double
confirmation at the CLI layer. Destructive or fleet-affecting ops (`reboot_device`,
`claim_devices_into_network`, `remove_device_from_network`,
`bind_network_to_template`, `unbind_network_from_template`) are `risk_level=high`;
mutating reversible ops (`update_device`, `update_network_vlan`) are `medium`;
`blink_device_leds` (a locator aid, no config change) is `low`. Irreversible ops
capture the before-state for the audit record but record no undo token.

### SSL/TLS Verification
`verify_ssl` defaults to true; disable only for a self-signed on-prem controller
proxy.

### Prompt-Injection Protection
All controller-returned text (network names, device names, client descriptions,
tags, settings) is passed through a `sanitize()` truncate + control-character
strip before reaching the agent.

### Network Scope
No webhooks, no telemetry, no outbound calls beyond the configured controller REST
API base URL. No post-install scripts or background services.

## Static Analysis

```bash
uvx bandit -r fabric_aiops/ mcp_server/
uv run ruff check .
```

## Supported Versions

The latest released version receives security fixes. This is a preview (0.x);
pin a version in production.
