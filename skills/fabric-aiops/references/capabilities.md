# fabric-aiops capabilities

> Preview / mock-only. 32 MCP tools (24 read, 8 write). Cisco Meraki Dashboard
> API paths are modelled from the public API shape and need live verification.
> Community-maintained; not affiliated with Cisco/Meraki.

Meraki hierarchy: **organizations → networks → devices**. Device models carry a
product-type prefix: **MX** appliance, **MS** switch, **MR** wireless AP, **MV**
camera, **MG** cellular gateway.

## Read tools (24)

### Overview + organizations
| Tool | Meraki API (preview) | Returns |
|------|----------------------|---------|
| `overview` | `/organizations/{id}/networks` + `/devices/statuses` | organizationId, networks, devicesTotal, devicesByStatus, devicesByProductType |
| `org_list` | `GET /organizations` | id, name, url, apiEnabled |
| `org_get` | `GET /organizations/{id}` | one org detail |
| `org_licensing` | `GET /organizations/{id}/licenses/overview` | status, expiration, per-device-type counts |
| `org_admins` | `GET /organizations/{id}/admins` | name, email, access level |
| `org_device_statuses` | `GET /organizations/{id}/devices/statuses` | total, byStatus, byProductType, devices[] |
| `org_api_requests` | `GET /organizations/{id}/apiRequests/overview` | totalRequests, rateLimited429, responseCodeCounts |

### Networks
| Tool | Meraki API (preview) | Returns |
|------|----------------------|---------|
| `network_list` | `GET /organizations/{id}/networks` | id, name, productTypes, tags |
| `network_get` | `GET /networks/{id}` | one network detail |
| `network_vlans` | `GET /networks/{id}/appliance/vlans` | id, subnet, applianceIp |
| `network_alerts` | `GET /networks/{id}/health/alerts` | total, bySeverity, alerts[] |
| `network_traffic` | `GET /networks/{id}/traffic` | applicationCount, topApplications[] (by bytes) |

### Devices
| Tool | Meraki API (preview) | Returns |
|------|----------------------|---------|
| `device_inventory` | `GET /organizations/{id}/devices` | total, byModelFamily, matched, devices[] (filterable by model) |
| `device_status` | `GET /organizations/{id}/devices/statuses` | one device's status row |
| `device_uplinks` | `GET /organizations/{id}/uplinks/statuses` | appliance/gateway WAN uplink statuses |
| `switch_ports` | `GET /devices/{serial}/switch/ports` | MS port configuration |
| `wireless_ssids` | `GET /networks/{id}/wireless/ssids` | MR SSIDs (number, name, enabled) |

### Clients
| Tool | Meraki API (preview) | Returns |
|------|----------------------|---------|
| `client_list` | `GET /networks/{id}/clients` | clients seen in the window |
| `client_get` | `GET /networks/{id}/clients/{clientId}` | description, MAC, IP, VLAN, manufacturer |
| `client_usage` | `GET .../clients/{clientId}/usageHistory` | samples, totalSentKb, totalReceivedKb, totalKb |
| `client_connectivity` | `GET .../clients/{clientId}/connectionStats` | assoc, auth, dhcp, dns, success |

### Health (flagship)
| Tool | Source | Returns |
|------|--------|---------|
| `uplink_loss_and_latency_rca` | `GET /organizations/{id}/devices/uplinksLossAndLatency` or injected `records` | uplinksEvaluated, degradedCount, thresholds, worst[]{serial, uplink, avgLossPct, avgLatencyMs, degraded, cause, action}, note |
| `network_health_score` | injected only | networksEvaluated, fleetScore, summary, weights, worst[]{networkId, score, band, onlinePct, uplinkHealthPct, alertPenalty}, note |
| `config_template_drift` | injected only | templateId, boundNetworks, driftedCount, compliantCount, settingsChecked, driftedNetworks[]{networkId, deviations[]}, note |

`uplink_loss_and_latency_rca` accepts `records=` for offline analysis or pulls
live from a `target`/`org_id`. `network_health_score` and `config_template_drift`
are injected-only (they score data you already hold, e.g. from
`org_device_statuses` / `device_uplinks`).

## Write tools (8) — all support `dry_run`; CLI adds double-confirm

| Tool | Risk | Meraki API (preview) | Undo / safety |
|------|------|----------------------|---------------|
| `reboot_device` | **high** | `POST /devices/{serial}/reboot` | captures prior status; no safe inverse, no undo |
| `claim_devices_into_network` | **high** | `POST /networks/{id}/devices/claim` | inverse = remove the claimed serials |
| `remove_device_from_network` | **high** | `POST /networks/{id}/devices/remove` | inverse = claim it back into the network |
| `bind_network_to_template` | **high** | `POST /networks/{id}/bind` | captures the prior binding; inverse = rebind prior / unbind |
| `unbind_network_from_template` | **high** | `POST /networks/{id}/unbind` | captures the prior template; inverse = rebind to it |
| `update_device` | medium | `PUT /devices/{serial}` | fetches + captures the changed keys' prior values; inverse = restore them |
| `update_network_vlan` | medium | `PUT /networks/{id}/appliance/vlans/{vlanId}` | fetches + captures prior values; inverse = restore them |
| `blink_device_leds` | low | `POST /devices/{serial}/blinkLeds` | locator aid; no config change, no undo |

## Out of scope (by design)

- Full org/network **provisioning** workflows (create org, create network)
- Firmware upgrade orchestration
- SSID/switch-port config CRUD beyond the writes above
- OT / industrial equipment (use the `industrial-aiops` line) and device-level
  CLI/SSH network automation

Want one of these, or another controller platform (Catalyst Center, Arista CVP)?
Open an issue or PR — feedback and contributions welcome.
