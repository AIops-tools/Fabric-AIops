"""``fabric-aiops remediate`` write flows: the dry-run preview branch of every
command (routed through the governed twin — reads and audits, never mutates)
and the confirmed path (past double-confirm, through the governed twin, onto
the audit log) for every write except update-device (already covered in
test_cli_writes)."""

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
from fabric_aiops.platform import CATALYST, UNIFI

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
    # configTemplateId is set: bind's guard then takes the already-bound branch
    # (config template-derived both sides) and needs no VLAN read from this
    # deliberately simple mock, which answers every GET with the same record.
    conn.get.return_value = {
        "serial": "Q2", "status": "online", "name": "n", "configTemplateId": "T-old",
    }
    conn.post.return_value = {}
    conn.put.return_value = {}
    return conn


# ── dry-run branch of every remediate command ────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("args", "tool"),
    [
        (["remediate", "reboot", "Q2", "--dry-run"], "reboot_device"),
        (
            ["remediate", "update-vlan", "N1", "10", '{"name":"data"}', "--dry-run"],
            "update_network_vlan",
        ),
        (["remediate", "claim", "N1", "Q2", "Q3", "--dry-run"], "claim_devices_into_network"),
        (["remediate", "remove", "N1", "Q2", "--dry-run"], "remove_device_from_network"),
        (["remediate", "bind", "N1", "T1", "--dry-run"], "bind_network_to_template"),
        (["remediate", "unbind", "N1", "--dry-run"], "unbind_network_from_template"),
    ],
)
def test_dry_run_reads_and_audits_but_never_writes(gov_home, monkeypatch, args, tool):
    """The invariant every remediate preview holds: a dry-run MAY read; it must never write.

    Each preview now runs the governed twin with ``dry_run=True``, so it reads
    whatever the guards need (bind reads the network's VLANs; all of them resolve
    the target's platform) and lands the same audit row a governed call always
    lands — the MCP preview always did, and the CLI silently skipping it was the
    outlier. The one thing it may never do is issue the mutating POST/PUT.
    """
    conn = _mock_conn()
    import mcp_server.tools.remediation as govmod

    monkeypatch.setattr(govmod, "_get_connection", lambda target=None: conn)
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output
    conn.post.assert_not_called()
    conn.put.assert_not_called()
    assert _audit_tools(gov_home / "audit.db") == [tool]


# ── a preview whose answer is "refused" must refuse ─────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "args",
    [
        ["remediate", "reboot", "Q2", "--dry-run"],
        ["remediate", "update-device", "Q2", '{"name":"ap1"}', "--dry-run"],
        ["remediate", "update-vlan", "N1", "10", '{"name":"data"}', "--dry-run"],
        ["remediate", "claim", "N1", "Q2", "--dry-run"],
        ["remediate", "remove", "N1", "Q2", "--dry-run"],
        ["remediate", "bind", "N1", "T1", "--dry-run"],
        ["remediate", "unbind", "N1", "--dry-run"],
    ],
)
def test_cli_dry_run_on_an_unsupported_platform_refuses_nonzero(gov_home, monkeypatch, args):
    """Catalyst Center maps none of these writes, so every preview is a refusal.

    The banner must not appear and the exit code must be non-zero. A green
    preview followed by "not supported on this platform" reads to a weak model
    as a transient failure worth retrying — and to a shell ``&&`` chain as a
    completed step.
    """
    conn = _mock_conn()
    conn.target = TargetConfig(name="t", platform=CATALYST)
    import mcp_server.tools.remediation as govmod

    monkeypatch.setattr(govmod, "_get_connection", lambda target=None: conn)
    result = runner.invoke(app, args)
    assert result.exit_code == 1, result.output
    assert "DRY-RUN" not in result.output
    assert "not supported on Cisco Catalyst Center API" in result.output
    conn.post.assert_not_called()
    conn.put.assert_not_called()


@pytest.mark.unit
def test_cli_dry_run_still_previews_on_a_platform_that_maps_the_write(gov_home, monkeypatch):
    """Exactness: UniFi maps device restart, so its preview is green, not refused.

    Guards the refusal above against over-reach — a preview that refuses
    everything off the reference platform would be just as wrong as one that
    refuses nothing.
    """
    conn = _mock_conn()
    conn.target = TargetConfig(name="t", platform=UNIFI)
    import mcp_server.tools.remediation as govmod

    monkeypatch.setattr(govmod, "_get_connection", lambda target=None: conn)
    result = runner.invoke(app, ["remediate", "reboot", "Q2", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output
    conn.post.assert_not_called()
    assert _audit_tools(gov_home / "audit.db") == ["reboot_device"]


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
