# fabric-aiops setup & security guide

> Preview / mock-only — not yet validated against a live controller of any
> platform (Meraki organization, Catalyst Center appliance, or CloudVision
> Portal instance). Community-maintained; not affiliated with or endorsed by
> Cisco/Meraki/Arista.

## 1. Install

```bash
uv tool install fabric-aiops
```

## 2. Create the platform credential

**Cisco Meraki Dashboard (`platform: meraki`)** — in the Dashboard:
**Organization → Settings → API access → Generate API key**. Copy the key.
fabric-aiops sends it as `Authorization: Bearer <key>` (or, with
`auth_style: meraki-key`, `X-Cisco-Meraki-API-Key`) against the Dashboard API
base `https://api.meraki.com/api/v1`.

**Cisco Catalyst Center (`platform: catalyst`)** — use a Catalyst Center
account (a read-only role suffices for the current read subset) and store the
secret as a single `username:password` string. fabric-aiops exchanges it via
`POST /dna/system/api/v1/auth/token` (HTTP Basic) for a short-lived (~1 h)
`X-Auth-Token`, attached per request and auto-refreshed once on a 401. A
`base_url` is required (`https://<catalyst-center-host>`).

**Arista CloudVision Portal (`platform: cvp`)** — create a service-account
token in CloudVision: **Settings → Access Control → Service Accounts**.
fabric-aiops sends it as `Authorization: Bearer <token>`. A `base_url` is
required (`https://<cvp-host>`).

## 3. Onboard

```bash
fabric-aiops init
```

The wizard asks for the platform (`meraki` / `catalyst` / `cvp`), collects
(non-secret) connection details into `~/.fabric-aiops/config.yaml`, and stores
the secret **encrypted** into `~/.fabric-aiops/secrets.enc`. Example config:

```yaml
targets:
  - name: org1
    platform: meraki
    org_id: "123456"            # default organization id (optional)
    verify_ssl: true
    # base_url: https://api.meraki.eu/api/v1   # override only for a region/proxy
    # auth_style: meraki-key                    # use X-Cisco-Meraki-API-Key instead of Bearer
  - name: campus
    platform: catalyst
    base_url: https://catalyst.example.com     # required (per-install)
    org_id: "site-uuid"          # default site id (optional)
    # verify_ssl: false          # only for self-signed lab controllers
  - name: dc-fabric
    platform: cvp
    base_url: https://cvp.example.com          # required (per-install)
    org_id: "root"               # default container key (optional)
```

## 4. Non-interactive use (MCP server / CI / cron)

Export the master password so the encrypted store can be unlocked without a
prompt:

```bash
export FABRIC_AIOPS_MASTER_PASSWORD='your-master-password'
```

## Credential security

- The controller secret (Meraki API key / Catalyst Center `username:password`
  / CVP service-account token) is **never** written to disk in plaintext. It lives only in
  `~/.fabric-aiops/secrets.enc`, encrypted with Fernet (AES-128-CBC + HMAC),
  the key derived from your master password via scrypt. Only a per-store random
  salt and the ciphertext are on disk (chmod 600); the master password itself is
  never stored.
- A legacy plaintext env var `FABRIC_<TARGET_NAME_UPPER>_APIKEY` is still honoured
  as a fallback with a deprecation warning — migrate with `fabric-aiops secret
  migrate` (it imports then renames the old `.env`).
- The key is held only in memory during a session and is never logged or echoed;
  exception text and tracebacks are scrubbed of secret-shaped strings before
  being written to the audit log.

## Governance harness state

State lives under `~/.fabric-aiops/` (relocate with `FABRIC_AIOPS_HOME`):

- `audit.db` — every tool call (SQLite), with risk tier, approver, rationale
- `rules.yaml` — policy: deny rules, maintenance windows, approval tiers
- `undo.db` — inverse descriptors for reversible writes (e.g. `update_device`,
  `bind_network_to_template`)
- budget / runaway guard — caps cumulative tool calls and wall-time; trips on
  tight poll/retry loops (also your first line of defense against Dashboard API
  rate limits)

## Verify

```bash
fabric-aiops doctor
```

`doctor` checks the config file, the encrypted store and its permissions, that a
secret (and, for on-prem platforms, a `base_url`) is present per target, and
(unless `--skip-auth`) connectivity via the canonical top-of-hierarchy read —
Meraki organizations, Catalyst Center sites, or CVP containers — which also
exercises the platform's full auth flow (including the Catalyst Center
session-token exchange).
