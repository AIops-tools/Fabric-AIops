"""``fabric-aiops remediate`` write flows: the dry-run preview branch of every
command (prints the API call, makes no call, writes no audit) and the confirmed
path (past double-confirm, through the governed twin, onto the audit log) for
every write except update-device (already covered in test_cli_writes)."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

import fabric_aiops.governance.audit as audit_mod
import fabric_aiops.governance.policy as policy_mod
import fabric_aiops.governance.undo as undo_mod
from fabric_aiops.cli import app
from fabric_aiops.config import TargetConfig

runner = CliRunner()


@pytest.fixture
def gov_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIC_AIOPS_HOME", str(tmp_path))
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()
    yield tmp_path
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()


def _audit_tools(db_path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute("SELECT tool FROM audit_log ORDER BY id")]
    finally:
        conn.close()


def _mock_conn() -> MagicMock:
    conn = MagicMock(name="conn")
    conn.target = TargetConfig(name="t")  # real meraki descriptor, org_id=""
    conn.get.return_value = {
        "serial": "Q2", "status": "online", "name": "n", "configTemplateId": None,
    }
    conn.post.return_value = {}
    conn.put.return_value = {}
    return conn


# ── dry-run branch of every remediate command ────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "args",
    [
        ["remediate", "reboot", "Q2", "--dry-run"],
        ["remediate", "update-vlan", "N1", "10", '{"name":"data"}', "--dry-run"],
        ["remediate", "claim", "N1", "Q2", "Q3", "--dry-run"],
        ["remediate", "remove", "N1", "Q2", "--dry-run"],
        ["remediate", "bind", "N1", "T1", "--dry-run"],
        ["remediate", "unbind", "N1", "--dry-run"],
    ],
)
def test_dry_run_prints_preview_and_writes_no_audit(gov_home, monkeypatch, args):
    conn = _mock_conn()
    import mcp_server.tools.remediation as govmod

    monkeypatch.setattr(govmod, "_get_connection", lambda target=None: conn)
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output
    conn.post.assert_not_called()
    conn.put.assert_not_called()
    assert not (gov_home / "audit.db").exists()


# ── confirmed writes go through the governed twin (audit row lands) ──────────


@pytest.mark.unit
def test_reboot_confirmed_audits_and_calls_controller(gov_home, monkeypatch):
    conn = _mock_conn()
    import mcp_server.tools.remediation as govmod

    monkeypatch.setattr(govmod, "_get_connection", lambda target=None: conn)
    result = runner.invoke(app, ["remediate", "reboot", "Q2"], input="y\ny\n")
    assert result.exit_code == 0, result.output
    conn.post.assert_called_once()
    assert _audit_tools(gov_home / "audit.db") == ["reboot_device"]


@pytest.mark.unit
def test_blink_needs_no_confirm_and_audits(gov_home, monkeypatch):
    conn = _mock_conn()
    import mcp_server.tools.remediation as govmod

    monkeypatch.setattr(govmod, "_get_connection", lambda target=None: conn)
    result = runner.invoke(app, ["remediate", "blink-leds", "Q2", "--duration", "15"])
    assert result.exit_code == 0, result.output
    conn.post.assert_called_once()
    assert _audit_tools(gov_home / "audit.db") == ["blink_device_leds"]


@pytest.mark.unit
def test_update_vlan_confirmed_captures_prior_and_audits(gov_home, monkeypatch):
    conn = _mock_conn()
    conn.get.return_value = {"id": "10", "name": "old-vlan"}
    import mcp_server.tools.remediation as govmod

    monkeypatch.setattr(govmod, "_get_connection", lambda target=None: conn)
    result = runner.invoke(
        app, ["remediate", "update-vlan", "N1", "10", '{"name":"data"}'], input="y\ny\n"
    )
    assert result.exit_code == 0, result.output
    conn.put.assert_called_once()
    assert _audit_tools(gov_home / "audit.db") == ["update_network_vlan"]


@pytest.mark.unit
def test_claim_confirmed_audits(gov_home, monkeypatch):
    conn = _mock_conn()
    import mcp_server.tools.remediation as govmod

    monkeypatch.setattr(govmod, "_get_connection", lambda target=None: conn)
    result = runner.invoke(app, ["remediate", "claim", "N1", "Q2", "Q3"], input="y\ny\n")
    assert result.exit_code == 0, result.output
    conn.post.assert_called_once()
    assert _audit_tools(gov_home / "audit.db") == ["claim_devices_into_network"]


@pytest.mark.unit
def test_remove_confirmed_audits(gov_home, monkeypatch):
    conn = _mock_conn()
    import mcp_server.tools.remediation as govmod

    monkeypatch.setattr(govmod, "_get_connection", lambda target=None: conn)
    result = runner.invoke(app, ["remediate", "remove", "N1", "Q2"], input="y\ny\n")
    assert result.exit_code == 0, result.output
    conn.post.assert_called_once()
    assert _audit_tools(gov_home / "audit.db") == ["remove_device_from_network"]


@pytest.mark.unit
def test_bind_confirmed_captures_prior_and_audits(gov_home, monkeypatch):
    conn = _mock_conn()
    conn.get.return_value = {"id": "N1", "configTemplateId": "T-old"}
    import mcp_server.tools.remediation as govmod

    monkeypatch.setattr(govmod, "_get_connection", lambda target=None: conn)
    result = runner.invoke(app, ["remediate", "bind", "N1", "T1", "--auto-bind"], input="y\ny\n")
    assert result.exit_code == 0, result.output
    conn.post.assert_called_once()
    assert _audit_tools(gov_home / "audit.db") == ["bind_network_to_template"]


@pytest.mark.unit
def test_unbind_confirmed_captures_prior_and_audits(gov_home, monkeypatch):
    conn = _mock_conn()
    conn.get.return_value = {"id": "N1", "configTemplateId": "T-old"}
    import mcp_server.tools.remediation as govmod

    monkeypatch.setattr(govmod, "_get_connection", lambda target=None: conn)
    result = runner.invoke(app, ["remediate", "unbind", "N1"], input="y\ny\n")
    assert result.exit_code == 0, result.output
    conn.post.assert_called_once()
    assert _audit_tools(gov_home / "audit.db") == ["unbind_network_from_template"]


@pytest.mark.unit
def test_confirmed_write_aborts_without_second_confirm(gov_home, monkeypatch):
    conn = _mock_conn()
    import mcp_server.tools.remediation as govmod

    monkeypatch.setattr(govmod, "_get_connection", lambda target=None: conn)
    result = runner.invoke(app, ["remediate", "reboot", "Q2"], input="y\nn\n")
    assert result.exit_code != 0
    conn.post.assert_not_called()
    assert not (gov_home / "audit.db").exists()
