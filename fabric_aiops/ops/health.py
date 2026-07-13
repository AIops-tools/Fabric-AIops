"""Flagship signature analyses over Meraki fabric telemetry (pure analysis).

These are the differentiators — transparent heuristics, every flag reported with
its number so an operator can see *why* something was ranked, never a black-box
verdict:

  1. ``uplink_loss_and_latency_rca`` — rank the worst MX WAN uplinks by loss +
     latency and map each to a likely cause + action.
  2. ``network_health_score`` — a composite fleet health score per network from
     device online %, uplink health, and alert severity.
  3. ``config_template_drift`` — for networks bound to a config template, list
     the settings that have drifted from the template.

All three are pure functions (no I/O): pass them the telemetry (from the reads
in the other ops modules, or injected) and they return the analysis. The live
pulls that feed 1) live under ``pull_uplink_loss_latency``.
"""

from __future__ import annotations

from typing import Any

from fabric_aiops.ops._util import clean_list, require_org

MAX_ROWS = 100

# ── 1. uplink loss & latency RCA ────────────────────────────────────────────
# A WAN uplink with loss above this (%) or latency above this (ms) is "degraded".
DEFAULT_LOSS_PCT = 5.0
DEFAULT_LATENCY_MS = 150.0


def pull_uplink_loss_latency(conn: Any, org_id: str | None = None) -> list[dict]:
    """[READ] Raw MX WAN uplink loss+latency time series across the org."""
    oid = require_org(conn, org_id)
    return clean_list(conn.get_pages(f"/organizations/{oid}/devices/uplinksLossAndLatency"))


def _series_stats(record: dict) -> tuple[float, float, float, float]:
    """Return (avgLoss, maxLoss, avgLatency, maxLatency) over a record's series."""
    series = record.get("timeSeries") or record.get("series") or []
    losses = [
        float(p["lossPercent"])
        for p in series
        if isinstance(p, dict) and isinstance(p.get("lossPercent"), (int, float))
    ]
    latencies = [
        float(p["latencyMs"])
        for p in series
        if isinstance(p, dict) and isinstance(p.get("latencyMs"), (int, float))
    ]
    avg_loss = round(sum(losses) / len(losses), 2) if losses else 0.0
    avg_lat = round(sum(latencies) / len(latencies), 1) if latencies else 0.0
    max_loss = max(losses) if losses else 0.0
    max_lat = max(latencies) if latencies else 0.0
    return avg_loss, max_loss, avg_lat, max_lat


def _classify(avg_loss: float, avg_lat: float, loss_pct: float, latency_ms: float) -> dict:
    """Map a loss/latency profile to a likely cause + recommended action."""
    hi_loss = avg_loss >= loss_pct
    hi_lat = avg_lat >= latency_ms
    if hi_loss and hi_lat:
        return {
            "cause": "Upstream congestion or ISP degradation (loss and latency both high)",
            "action": "Open an ISP ticket; fail traffic over to the secondary WAN uplink.",
        }
    if hi_loss:
        return {
            "cause": "Packet loss on the last mile (loss high, latency normal)",
            "action": "Check WAN cabling/SFP and the modem; escalate line quality to the ISP.",
        }
    if hi_lat:
        return {
            "cause": "High latency on a distant/bufferbloated path (latency high, loss normal)",
            "action": "Enable per-uplink traffic shaping/QoS; verify the path/peering.",
        }
    return {"cause": "Healthy — within thresholds", "action": "No action needed."}


def uplink_loss_and_latency_rca(
    records: list[dict],
    loss_pct: float = DEFAULT_LOSS_PCT,
    latency_ms: float = DEFAULT_LATENCY_MS,
) -> dict:
    """[READ] Rank the worst MX WAN uplinks by loss + latency and map cause+action.

    Pure analysis over ``records`` (from ``pull_uplink_loss_latency`` or
    injected) — each {serial, networkId, uplink, ip, timeSeries:[{lossPercent,
    latencyMs}]}. Ranks worst-first by a composite of average loss and latency,
    flags each degraded uplink against the thresholds, and attaches a likely
    cause + recommended action. Every ranking carries its numbers.
    """
    ranked = []
    for r in records or []:
        avg_loss, max_loss, avg_lat, max_lat = _series_stats(r)
        degraded = avg_loss >= loss_pct or avg_lat >= latency_ms
        entry = {
            "serial": r.get("serial"),
            "networkId": r.get("networkId"),
            "uplink": r.get("uplink"),
            "ip": r.get("ip"),
            "avgLossPct": avg_loss,
            "maxLossPct": max_loss,
            "avgLatencyMs": avg_lat,
            "maxLatencyMs": max_lat,
            "degraded": degraded,
            # composite score for ranking: loss weighted heavier than latency
            "_score": avg_loss * 10 + avg_lat,
        }
        entry.update(_classify(avg_loss, avg_lat, loss_pct, latency_ms))
        ranked.append(entry)

    ranked.sort(key=lambda e: e["_score"], reverse=True)
    for e in ranked:
        e.pop("_score", None)
    degraded = [e for e in ranked if e["degraded"]]
    return {
        "uplinksEvaluated": len(ranked),
        "degradedCount": len(degraded),
        "thresholds": {"lossPct": loss_pct, "latencyMs": latency_ms},
        "worst": ranked[:MAX_ROWS],
        "note": (
            "Advisory read-only heuristic: uplinks ranked by avg loss (x10) + avg "
            "latency; 'degraded' means avg loss >= lossPct or avg latency >= latencyMs."
        ),
    }


# ── 2. network health score ─────────────────────────────────────────────────
# Composite weights (must sum to 1.0): device availability, uplink health, alerts.
_W_ONLINE = 0.5
_W_UPLINK = 0.3
_W_ALERTS = 0.2
_ALERT_SEVERITY_WEIGHT = {"critical": 25, "warning": 10, "info": 2}
_BAND_HEALTHY = 80
_BAND_DEGRADED = 50


def _band(score: float) -> str:
    if score >= _BAND_HEALTHY:
        return "healthy"
    if score >= _BAND_DEGRADED:
        return "degraded"
    return "critical"


def _online_component(devices: list[dict]) -> tuple[float, int, int]:
    total = len(devices)
    online = sum(1 for d in devices if str(d.get("status")).lower() == "online")
    pct = (online / total * 100) if total else 100.0
    return pct, online, total


def _uplink_component(uplinks: list[dict]) -> float:
    total = len(uplinks)
    if not total:
        return 100.0
    healthy = sum(1 for u in uplinks if str(u.get("status")).lower() in {"active", "ready"})
    return healthy / total * 100


def _alert_penalty(alerts: list[dict]) -> int:
    penalty = 0
    for a in alerts:
        penalty += _ALERT_SEVERITY_WEIGHT.get(str(a.get("severity")).lower(), 0)
    return min(100, penalty)


def network_health_score(
    device_statuses: list[dict],
    uplinks: list[dict] | None = None,
    alerts: list[dict] | None = None,
) -> dict:
    """[READ] Composite fleet health score per network, worst-first.

    Pure analysis. Groups the inputs by ``networkId`` and computes each
    network's 0-100 score as a weighted blend of device online %% (weight 0.5),
    uplink health %% (0.3), and an alert-severity penalty (0.2). Every component
    is returned alongside the score so the number is explainable. ``uplinks`` and
    ``alerts`` are optional — a network with neither is scored on availability
    alone.

    Args:
        device_statuses: rows {serial, networkId, status, productType}.
        uplinks: rows {networkId, status} (active/ready = healthy).
        alerts: rows {networkId, severity} (critical/warning/info).
    """
    dev_by_net: dict[str, list[dict]] = {}
    for d in device_statuses or []:
        dev_by_net.setdefault(str(d.get("networkId") or "unknown"), []).append(d)
    up_by_net: dict[str, list[dict]] = {}
    for u in uplinks or []:
        up_by_net.setdefault(str(u.get("networkId") or "unknown"), []).append(u)
    al_by_net: dict[str, list[dict]] = {}
    for a in alerts or []:
        al_by_net.setdefault(str(a.get("networkId") or "unknown"), []).append(a)

    scored = []
    for net_id, devices in dev_by_net.items():
        online_pct, online, total = _online_component(devices)
        uplink_pct = _uplink_component(up_by_net.get(net_id, []))
        alert_pen = _alert_penalty(al_by_net.get(net_id, []))
        alert_component = max(0, 100 - alert_pen)
        score = round(
            _W_ONLINE * online_pct + _W_UPLINK * uplink_pct + _W_ALERTS * alert_component, 1
        )
        scored.append({
            "networkId": net_id,
            "score": score,
            "band": _band(score),
            "devicesOnline": online,
            "devicesTotal": total,
            "onlinePct": round(online_pct, 1),
            "uplinkHealthPct": round(uplink_pct, 1),
            "alertPenalty": alert_pen,
        })

    scored.sort(key=lambda e: e["score"])
    summary = {"healthy": 0, "degraded": 0, "critical": 0}
    for e in scored:
        summary[e["band"]] += 1
    fleet = round(sum(e["score"] for e in scored) / len(scored), 1) if scored else 0.0
    return {
        "networksEvaluated": len(scored),
        "fleetScore": fleet,
        "summary": summary,
        "weights": {"online": _W_ONLINE, "uplink": _W_UPLINK, "alerts": _W_ALERTS},
        "worst": scored[:MAX_ROWS],
        "note": (
            "Advisory read-only heuristic: score = 0.5*online% + 0.3*uplinkHealth% "
            "+ 0.2*(100 - alertPenalty); bands healthy>=80, degraded 50-79, critical<50."
        ),
    }


# ── 3. config template drift ────────────────────────────────────────────────


def config_template_drift(template: dict, networks: list[dict]) -> dict:
    """[READ] For networks bound to a config template, list drifted settings.

    Pure analysis. ``template`` is {id, name, settings:{key: value}}; each
    network is {networkId, name, boundTemplateId, settings:{key: value}}. Only
    networks whose ``boundTemplateId`` matches ``template['id']`` are compared;
    for each, every settings key whose value differs from the template's is
    reported as expected-vs-actual. Networks that match exactly are compliant.
    """
    tmpl_id = (template or {}).get("id")
    tmpl_settings = (template or {}).get("settings") or {}

    bound: list[dict] = []
    drifted: list[dict] = []
    for net in networks or []:
        if str(net.get("boundTemplateId")) != str(tmpl_id):
            continue
        bound.append(net)
        net_settings = net.get("settings") or {}
        deviations = []
        for key, expected in tmpl_settings.items():
            actual = net_settings.get(key)
            if actual != expected:
                deviations.append({"setting": key, "expected": expected, "actual": actual})
        if deviations:
            drifted.append({
                "networkId": net.get("networkId"),
                "name": net.get("name"),
                "deviations": deviations,
            })

    return {
        "templateId": tmpl_id,
        "templateName": (template or {}).get("name"),
        "boundNetworks": len(bound),
        "driftedCount": len(drifted),
        "compliantCount": len(bound) - len(drifted),
        "settingsChecked": list(tmpl_settings.keys()),
        "driftedNetworks": drifted[:MAX_ROWS],
        "note": (
            "Advisory read-only check: compares each bound network's settings to "
            "the template by exact value; only networks bound to templateId are checked."
        ),
    }
