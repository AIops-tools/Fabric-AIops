# fabric-aiops setup & security guide

> Preview / mock-only — not yet validated against a live Meraki organization.
> Community-maintained; not affiliated with or endorsed by Cisco/Meraki.

## 1. Install

```bash
uv tool install fabric-aiops
```

## 2. Create a Meraki API key

In the Cisco Meraki Dashboard: **Organization → Settings → API access →
Generate API key**. Copy the key. fabric-aiops sends it as `Authorization:
Bearer <key>` (or, with `auth_style: meraki-key`, `X-Cisco-Meraki-API-Key`)
against the Dashboard API base `https://api.meraki.com/api/v1`.

## 3. Onboard

```bash
fabric-aiops init
```

The wizard collects (non-secret) connection details into
`~/.fabric-aiops/config.yaml` and stores the API key **encrypted** into
`~/.fabric-aiops/secrets.enc`. Example config:

```yaml
targets:
  - name: org1
    platform: meraki
    org_id: "123456"            # default organization id (optional)
    verify_ssl: true
    # base_url: https://api.meraki.eu/api/v1   # override only for a region/proxy
    # auth_style: meraki-key                    # use X-Cisco-Meraki-API-Key instead of Bearer
```

## 4. Non-interactive use (MCP server / CI / cron)

Export the master password so the encrypted store can be unlocked without a
prompt:

```bash
export FABRIC_AIOPS_MASTER_PASSWORD='your-master-password'
```

## Credential security

- The API key is **never** written to disk in plaintext. It lives only in
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

`doctor` checks the config file, the encrypted store and its permissions, that an
API key is present per target, and (unless `--skip-auth`) connectivity by listing
`GET /organizations`.
