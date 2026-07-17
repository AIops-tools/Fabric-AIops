"""CLI read-command bodies (org / network / device / client / health / overview).

Each read command follows the same shape: resolve a connection, call one ops
function, and print its JSON. These tests drive every command through the real
Typer app, stubbing ``get_connection`` (so nothing touches a live controller)
and the single ops call (already exercised directly in the ops/platform tests),
and assert the command body runs, prints, and exits 0 — the CLI wiring itself.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from fabric_aiops.cli import app

runner = CliRunner()

_SENTINEL_CONN = object()


def _patch_conn(monkeypatch, cli_mod) -> None:
    """Make the module's get_connection return a (conn, cfg) tuple, no I/O."""
    monkeypatch.setattr(cli_mod, "get_connection", lambda target=None: (_SENTINEL_CONN, None))


# ── org ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("args", "fn", "ret"),
    [
        (["org", "list"], "list_organizations", [{"id": "O1", "name": "Acme"}]),
        (["org", "get", "-o", "O1"], "get_organization", {"id": "O1"}),
        (["org", "licensing", "-o", "O1"], "licensing_overview", {"status": "OK"}),
        (["org", "admins", "-o", "O1"], "list_admins", [{"email": "a@x"}]),
        (["org", "device-statuses"], "device_statuses", {"total": 3}),
        (["org", "api-usage"], "api_request_usage", {"totalRequests": 10}),
    ],
)
def test_org_commands_run_and_print(monkeypatch, args, fn, ret):
    import fabric_aiops.cli.org as org_cli
    from fabric_aiops.ops import organizations as ops

    _patch_conn(monkeypatch, org_cli)
    monkeypatch.setattr(ops, fn, lambda *a, **k: ret)
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output


# ── network ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("args", "fn", "ret"),
    [
        (["network", "list"], "list_networks", [{"id": "N1"}]),
        (["network", "get", "N1"], "get_network", {"id": "N1"}),
        (["network", "vlans", "N1"], "list_vlans", [{"id": "10"}]),
        (["network", "alerts", "N1"], "network_alerts", {"total": 2}),
        (["network", "traffic", "N1"], "traffic_summary", {"topApplications": []}),
    ],
)
def test_network_commands_run_and_print(monkeypatch, args, fn, ret):
    import fabric_aiops.cli.network as net_cli
    from fabric_aiops.ops import networks as ops

    _patch_conn(monkeypatch, net_cli)
    monkeypatch.setattr(ops, fn, lambda *a, **k: ret)
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output


# ── device ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("args", "fn", "ret"),
    [
        (["device", "inventory", "--model", "MS"], "inventory", {"total": 1}),
        (["device", "status", "Q2"], "device_status", {"status": "online"}),
        (["device", "uplinks"], "uplink_status", [{"serial": "Q2"}]),
        (["device", "switch-ports", "Q2"], "switch_ports", [{"portId": "1"}]),
        (["device", "ssids", "N1"], "wireless_ssids", [{"name": "corp"}]),
    ],
)
def test_device_commands_run_and_print(monkeypatch, args, fn, ret):
    import fabric_aiops.cli.device as dev_cli
    from fabric_aiops.ops import devices as ops

    _patch_conn(monkeypatch, dev_cli)
    monkeypatch.setattr(ops, fn, lambda *a, **k: ret)
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output


# ── client ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("args", "fn", "ret"),
    [
        (["client", "list", "N1"], "list_clients", [{"id": "C1"}]),
        (["client", "get", "N1", "C1"], "get_client", {"id": "C1"}),
        (["client", "usage", "N1", "C1"], "client_usage", {"totalKb": 40}),
        (["client", "connectivity", "N1", "C1"], "client_connectivity", {"success": 100}),
    ],
)
def test_client_commands_run_and_print(monkeypatch, args, fn, ret):
    import fabric_aiops.cli.client as cli_mod
    from fabric_aiops.ops import clients as ops

    _patch_conn(monkeypatch, cli_mod)
    monkeypatch.setattr(ops, fn, lambda *a, **k: ret)
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output


# ── health ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_health_uplink_rca_command_runs(monkeypatch):
    import fabric_aiops.cli.health as health_cli
    from fabric_aiops.ops import health as ops

    _patch_conn(monkeypatch, health_cli)
    monkeypatch.setattr(ops, "pull_uplink_loss_latency", lambda conn, org_id: [{"serial": "Q"}])
    monkeypatch.setattr(
        ops, "uplink_loss_and_latency_rca", lambda records, **k: {"degradedCount": 1}
    )
    result = runner.invoke(app, ["health", "uplink-rca", "--loss-pct", "5", "--latency-ms", "150"])
    assert result.exit_code == 0, result.output
    assert "degradedCount" in result.output


@pytest.mark.unit
def test_health_score_command_runs(monkeypatch):
    import fabric_aiops.cli.health as health_cli
    from fabric_aiops.ops import devices as dev
    from fabric_aiops.ops import health as ops
    from fabric_aiops.ops import organizations as org

    _patch_conn(monkeypatch, health_cli)
    monkeypatch.setattr(org, "device_statuses", lambda conn, org_id: {"devices": [{"x": 1}]})
    monkeypatch.setattr(dev, "uplink_status", lambda conn, org_id: [{"status": "active"}])
    monkeypatch.setattr(ops, "network_health_score", lambda rows, **k: {"fleetScore": 100.0})
    result = runner.invoke(app, ["health", "score"])
    assert result.exit_code == 0, result.output
    assert "fleetScore" in result.output


# ── overview ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_overview_command_runs(monkeypatch):
    import fabric_aiops.cli.overview as ov_cli
    from fabric_aiops.ops import overview as ops

    _patch_conn(monkeypatch, ov_cli)
    monkeypatch.setattr(ops, "fleet_overview", lambda conn, org_id=None: {"networks": 2})
    result = runner.invoke(app, ["overview"])
    assert result.exit_code == 0, result.output
    assert "networks" in result.output


# ── cli_errors translation: an ops failure becomes one red line + exit 1 ─────


@pytest.mark.unit
def test_cli_error_translates_fabric_api_error_to_one_line(monkeypatch):
    import fabric_aiops.cli.org as org_cli
    from fabric_aiops.connection import FabricApiError
    from fabric_aiops.ops import organizations as ops

    _patch_conn(monkeypatch, org_cli)

    def _boom(*a, **k):
        raise FabricApiError("controller unreachable", status_code=503, path="/organizations")

    monkeypatch.setattr(ops, "list_organizations", _boom)
    result = runner.invoke(app, ["org", "list"])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "controller unreachable" in result.output


@pytest.mark.unit
def test_cli_error_key_error_is_labelled_as_missing_key(monkeypatch):
    import fabric_aiops.cli.device as dev_cli
    from fabric_aiops.ops import devices as ops

    _patch_conn(monkeypatch, dev_cli)

    def _missing(*a, **k):
        raise KeyError("NOPE")

    monkeypatch.setattr(ops, "device_status", _missing)
    result = runner.invoke(app, ["device", "status", "Q2"])
    assert result.exit_code == 1
    assert "Missing required key" in result.output
