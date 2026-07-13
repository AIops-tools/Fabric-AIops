"""Tests for the flagship health analyses (pure fns + governed MCP tools)."""

import pytest

from fabric_aiops.ops import health as ops

# ── uplink_loss_and_latency_rca ─────────────────────────────────────────────


def _uplink(serial, loss, latency, uplink="wan1"):
    return {
        "serial": serial,
        "networkId": "N1",
        "uplink": uplink,
        "ip": "1.2.3.4",
        "timeSeries": [{"lossPercent": loss, "latencyMs": latency}],
    }


@pytest.mark.unit
def test_uplink_rca_ranks_worst_first_and_classifies():
    records = [
        _uplink("Q-OK", 0.0, 20.0),
        _uplink("Q-LOSSY", 12.0, 30.0),
        _uplink("Q-SLOW", 0.5, 400.0),
        _uplink("Q-BOTH", 20.0, 300.0),
    ]
    out = ops.uplink_loss_and_latency_rca(records)
    assert out["uplinksEvaluated"] == 4
    # composite = loss*10 + latency → Q-BOTH (500) worst, then Q-SLOW (405)
    assert out["worst"][0]["serial"] == "Q-BOTH"
    assert "congestion" in out["worst"][0]["cause"].lower()
    assert out["worst"][-1]["serial"] == "Q-OK"
    assert out["worst"][-1]["degraded"] is False
    # loss-only vs latency-only classified distinctly
    by_serial = {e["serial"]: e for e in out["worst"]}
    assert "last mile" in by_serial["Q-LOSSY"]["cause"].lower()
    assert "latency" in by_serial["Q-SLOW"]["cause"].lower()


@pytest.mark.unit
def test_uplink_rca_degraded_count_respects_thresholds():
    records = [_uplink("Q1", 6.0, 10.0), _uplink("Q2", 1.0, 10.0)]
    out = ops.uplink_loss_and_latency_rca(records, loss_pct=5.0, latency_ms=150.0)
    assert out["degradedCount"] == 1


@pytest.mark.unit
def test_uplink_rca_empty():
    out = ops.uplink_loss_and_latency_rca([])
    assert out["uplinksEvaluated"] == 0 and out["worst"] == []


# ── network_health_score ────────────────────────────────────────────────────


@pytest.mark.unit
def test_network_health_score_online_component():
    devices = [
        {"networkId": "N1", "status": "online"},
        {"networkId": "N1", "status": "online"},
        {"networkId": "N2", "status": "offline"},
        {"networkId": "N2", "status": "online"},
    ]
    out = ops.network_health_score(devices)
    assert out["networksEvaluated"] == 2
    by_net = {e["networkId"]: e for e in out["worst"]}
    # N1 all online (no uplinks/alerts → those components 100) → 100
    assert by_net["N1"]["score"] == 100.0
    # N2 50% online → 0.5*50 + 0.3*100 + 0.2*100 = 75
    assert by_net["N2"]["score"] == 75.0
    assert out["worst"][0]["networkId"] == "N2"  # worst first


@pytest.mark.unit
def test_network_health_score_uplink_and_alert_penalty():
    devices = [{"networkId": "N1", "status": "online"}]
    uplinks = [{"networkId": "N1", "status": "active"}, {"networkId": "N1", "status": "failed"}]
    alerts = [{"networkId": "N1", "severity": "critical"}]
    out = ops.network_health_score(devices, uplinks=uplinks, alerts=alerts)
    net = out["worst"][0]
    # online 100*0.5 + uplink 50*0.3 + (100-25)*0.2 = 50 + 15 + 15 = 80
    assert net["score"] == 80.0
    assert net["uplinkHealthPct"] == 50.0
    assert net["alertPenalty"] == 25


@pytest.mark.unit
def test_network_health_score_empty():
    out = ops.network_health_score([])
    assert out["networksEvaluated"] == 0 and out["fleetScore"] == 0.0


# ── config_template_drift ───────────────────────────────────────────────────


@pytest.mark.unit
def test_config_template_drift_flags_deviations():
    template = {"id": "T1", "name": "branch", "settings": {"timezone": "UTC", "vlan": 10}}
    networks = [
        {"networkId": "N1", "name": "hq", "boundTemplateId": "T1",
         "settings": {"timezone": "UTC", "vlan": 10}},
        {"networkId": "N2", "name": "drifted", "boundTemplateId": "T1",
         "settings": {"timezone": "PST", "vlan": 10}},
        {"networkId": "N3", "name": "unbound", "boundTemplateId": "OTHER",
         "settings": {"timezone": "EST"}},
    ]
    out = ops.config_template_drift(template, networks)
    assert out["boundNetworks"] == 2  # N3 excluded (different template)
    assert out["driftedCount"] == 1
    assert out["compliantCount"] == 1
    drift = out["driftedNetworks"][0]
    assert drift["networkId"] == "N2"
    assert drift["deviations"][0]["setting"] == "timezone"
    assert drift["deviations"][0]["expected"] == "UTC"
    assert drift["deviations"][0]["actual"] == "PST"


# ── governed MCP tool wrappers ──────────────────────────────────────────────


@pytest.mark.unit
def test_health_tools_are_governed_low_and_run():
    from mcp_server.tools import health as tools

    for fn in (
        tools.uplink_loss_and_latency_rca,
        tools.network_health_score,
        tools.config_template_drift,
    ):
        assert getattr(fn, "_is_governed_tool", False) is True
        assert fn._risk_level == "low"

    out = tools.uplink_loss_and_latency_rca(records=[_uplink("Q1", 20.0, 300.0)])
    assert "error" not in out and out["degradedCount"] == 1

    score = tools.network_health_score(device_statuses=[{"networkId": "N1", "status": "online"}])
    assert score["worst"][0]["score"] == 100.0
