"""A truncated read announces itself.

A bare capped list cannot say "there is more" — the consumer has to infer it
from the length happening to equal the cap, and a smaller local model faced with
a cut-off result tends to summarise the partial answer as the whole story. So
every capped read carries ``returned`` / ``limit`` / ``truncated``, and
truncation is *measured* against the full result rather than guessed.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from fabric_aiops.cli import app
from fabric_aiops.config import TargetConfig
from fabric_aiops.ops import devices, health, networks, organizations
from fabric_aiops.ops._util import bounded

runner = CliRunner()


class _Conn:
    def __init__(self, responses, org_id="O1"):
        self._responses = responses
        self.target = TargetConfig(name="t", org_id=org_id)

    def get(self, path, **_kw):
        return self._responses[path]

    def get_pages(self, path, params=None, **_kw):
        data = self._responses[path]
        return data if isinstance(data, list) else [data]


def _devices(n):
    return [{"serial": f"Q{i}", "status": "online", "model": "MS120"} for i in range(n)]


# ── the helper ───────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_bounded_reports_a_cut():
    out = bounded([1, 2, 3, 4, 5], 3, "rows")
    assert out == {"rows": [1, 2, 3], "returned": 3, "limit": 3, "truncated": True}


@pytest.mark.unit
def test_bounded_is_not_truncated_at_exactly_the_limit():
    """The classic off-by-one: len == limit is NOT truncation."""
    out = bounded([1, 2, 3], 3, "rows")
    assert out["truncated"] is False
    assert out["returned"] == 3


@pytest.mark.unit
def test_bounded_handles_an_empty_result():
    assert bounded([], 10, "rows") == {"rows": [], "returned": 0, "limit": 10, "truncated": False}


# ── the ops ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_device_inventory_announces_truncation():
    conn = _Conn({"/organizations/O1/devices": _devices(5)})
    out = devices.inventory(conn, limit=2)
    assert out["total"] == 5, "the full count is still reported"
    assert out["returned"] == 2 and out["limit"] == 2
    assert out["truncated"] is True
    assert len(out["devices"]) == 2


@pytest.mark.unit
def test_device_inventory_not_truncated_within_limit():
    conn = _Conn({"/organizations/O1/devices": _devices(2)})
    out = devices.inventory(conn, limit=10)
    assert out["truncated"] is False and out["returned"] == 2


@pytest.mark.unit
def test_org_device_statuses_announces_truncation():
    conn = _Conn({"/organizations/O1/devices/statuses": _devices(4)})
    out = organizations.device_statuses(conn, limit=1)
    assert out["total"] == 4
    assert out["truncated"] is True and out["returned"] == 1


@pytest.mark.unit
def test_network_alerts_announces_truncation():
    rows = [{"type": "conn", "severity": "critical"} for _ in range(3)]
    conn = _Conn({"/networks/N1/health/alerts": rows})
    out = networks.network_alerts(conn, "N1", limit=2)
    assert out["total"] == 3
    assert out["truncated"] is True and len(out["alerts"]) == 2


@pytest.mark.unit
def test_traffic_summary_announces_truncation():
    rows = [{"application": f"app{i}", "sent": i, "recv": i} for i in range(6)]
    conn = _Conn({"/networks/N1/traffic": rows})
    out = networks.traffic_summary(conn, "N1", limit=3)
    assert out["applicationCount"] == 6
    assert out["truncated"] is True and len(out["topApplications"]) == 3


@pytest.mark.unit
def test_uplink_rca_announces_truncation():
    records = [
        {"serial": f"Q{i}", "timeSeries": [{"lossPercent": float(i), "latencyMs": 10.0}]}
        for i in range(5)
    ]
    out = health.uplink_loss_and_latency_rca(records, limit=2)
    assert out["uplinksEvaluated"] == 5
    assert out["truncated"] is True and len(out["worst"]) == 2


@pytest.mark.unit
def test_network_health_score_announces_truncation():
    rows = [{"networkId": f"N{i}", "status": "online"} for i in range(4)]
    out = health.network_health_score(rows, limit=2)
    assert out["networksEvaluated"] == 4
    assert out["truncated"] is True and len(out["worst"]) == 2


@pytest.mark.unit
def test_config_template_drift_announces_truncation():
    template = {"id": "T1", "settings": {"timezone": "UTC"}}
    nets = [
        {"networkId": f"N{i}", "boundTemplateId": "T1", "settings": {"timezone": "PST"}}
        for i in range(4)
    ]
    out = health.config_template_drift(template, nets, limit=1)
    assert out["driftedCount"] == 4, "the full drifted count is still reported"
    assert out["truncated"] is True and len(out["driftedNetworks"]) == 1


# ── undo_list: truncation measured with a limit + 1 fetch ────────────────────


@pytest.mark.unit
def test_undo_list_measures_truncation_with_an_extra_row(monkeypatch):
    from mcp_server.tools import undo as gov

    asked = {}

    class _Store:
        def list(self, status, limit):
            asked["limit"] = limit
            return [
                {"undo_id": f"u{i}", "ts": 1, "tool": "t", "undo_tool": "i", "note": None}
                for i in range(limit)
            ]

    monkeypatch.setattr(gov, "get_undo_store", lambda: _Store())
    out = gov.undo_list(limit=3)

    assert asked["limit"] == 4, "one extra row is fetched so truncation is measured"
    assert out["returned"] == 3 and out["limit"] == 3
    assert out["truncated"] is True
    assert len(out["undos"]) == 3
    assert out["undos"][0]["note"] is None, "an absent note stays null"


@pytest.mark.unit
def test_undo_list_is_not_truncated_when_fewer_rows_exist(monkeypatch):
    from mcp_server.tools import undo as gov

    class _Store:
        def list(self, status, limit):
            return [{"undo_id": "u1", "ts": 1, "tool": "t", "undo_tool": "i", "note": "n"}]

    monkeypatch.setattr(gov, "get_undo_store", lambda: _Store())
    out = gov.undo_list(limit=50)
    assert out["truncated"] is False and out["returned"] == 1


# ── the CLI says so out loud ─────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_prints_a_truncation_notice(monkeypatch):
    import fabric_aiops.cli.device as device_cli
    from fabric_aiops.ops import devices as ops

    monkeypatch.setattr(device_cli, "get_connection", lambda target=None: (object(), None))
    monkeypatch.setattr(
        ops,
        "inventory",
        lambda *a, **k: {"total": 9, "devices": [], "returned": 0, "limit": 2, "truncated": True},
    )

    result = runner.invoke(app, ["device", "inventory", "--limit", "2"])
    assert result.exit_code == 0, result.output
    assert "truncated" in result.output
    assert "--limit" in result.output


@pytest.mark.unit
def test_cli_stays_quiet_when_nothing_was_truncated(monkeypatch):
    import fabric_aiops.cli.device as device_cli
    from fabric_aiops.ops import devices as ops

    monkeypatch.setattr(device_cli, "get_connection", lambda target=None: (object(), None))
    monkeypatch.setattr(
        ops,
        "inventory",
        lambda *a, **k: {"total": 1, "devices": [], "returned": 1, "limit": 500,
                         "truncated": False},
    )

    result = runner.invoke(app, ["device", "inventory"])
    assert result.exit_code == 0, result.output
    assert "re-run with a higher" not in result.output


@pytest.mark.unit
def test_cli_limit_is_forwarded_to_the_ops_layer(monkeypatch):
    import fabric_aiops.cli.network as network_cli
    from fabric_aiops.ops import networks as ops

    seen = {}

    def _fake(conn, network_id, **kwargs):
        seen.update(kwargs)
        return {"total": 0, "alerts": [], "returned": 0, "limit": 7, "truncated": False}

    monkeypatch.setattr(network_cli, "get_connection", lambda target=None: (object(), None))
    monkeypatch.setattr(ops, "network_alerts", _fake)

    result = runner.invoke(app, ["network", "alerts", "N1", "--limit", "7"])
    assert result.exit_code == 0, result.output
    assert seen == {"limit": 7}


@pytest.mark.unit
def test_cli_without_limit_leaves_the_ops_default(monkeypatch):
    """Omitting --limit must not pass limit=None down and blow up the cap."""
    import fabric_aiops.cli.network as network_cli
    from fabric_aiops.ops import networks as ops

    seen = {}

    def _fake(conn, network_id, **kwargs):
        seen.update(kwargs)
        return {"total": 0, "alerts": [], "returned": 0, "limit": 200, "truncated": False}

    monkeypatch.setattr(network_cli, "get_connection", lambda target=None: (object(), None))
    monkeypatch.setattr(ops, "network_alerts", _fake)

    result = runner.invoke(app, ["network", "alerts", "N1"])
    assert result.exit_code == 0, result.output
    assert seen == {}, "no limit kwarg means the ops default applies"
