"""Smoke + governance tests for fabric-aiops.

Proves: every module imports, the CLI Typer app builds and --help works, the MCP
server exposes the expected tools, EVERY MCP tool carries the harness marker
``_is_governed_tool``, the write tools have the right risk tiers, and the guarded
writes (undo capture of the fetched BEFORE-state, dry-run gating) behave. No real
Meraki organization is needed — the connection is a MagicMock.
"""

import asyncio
import importlib
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

EXPECTED_TOOLS = {
    # organizations + overview
    "overview", "org_list", "org_get", "org_licensing", "org_admins",
    "org_device_statuses", "org_api_requests",
    # networks
    "network_list", "network_get", "network_vlans", "network_alerts", "network_traffic",
    # devices
    "device_inventory", "device_status", "device_uplinks", "switch_ports", "wireless_ssids",
    # clients
    "client_list", "client_get", "client_usage", "client_connectivity",
    # flagship health analyses
    "uplink_loss_and_latency_rca", "network_health_score", "config_template_drift",
    # remediation (writes)
    "reboot_device", "blink_device_leds", "update_device", "update_network_vlan",
    "claim_devices_into_network", "remove_device_from_network",
    "bind_network_to_template", "unbind_network_from_template",
}

WRITE_TOOLS = {
    "reboot_device", "blink_device_leds", "update_device", "update_network_vlan",
    "claim_devices_into_network", "remove_device_from_network",
    "bind_network_to_template", "unbind_network_from_template",
}

HIGH_RISK = {
    "reboot_device", "claim_devices_into_network", "remove_device_from_network",
    "bind_network_to_template", "unbind_network_from_template",
}
# blink_device_leds is non-destructive but still a controller POST, so it is
# tiered as a write (non-low risk_level): risk_level "low" is what marks a tool
# as a *read*. There is no "low-risk write" tier.
MEDIUM_RISK = {"update_device", "update_network_vlan", "blink_device_leds"}


@pytest.mark.unit
def test_all_modules_import():
    for name in (
        "fabric_aiops",
        "fabric_aiops.config",
        "fabric_aiops.connection",
        "fabric_aiops.platform",
        "fabric_aiops.doctor",
        "fabric_aiops.secretstore",
        "fabric_aiops.ops.organizations",
        "fabric_aiops.ops.networks",
        "fabric_aiops.ops.devices",
        "fabric_aiops.ops.clients",
        "fabric_aiops.ops.health",
        "fabric_aiops.ops.remediation",
        "fabric_aiops.ops.overview",
        "fabric_aiops.cli",
        "fabric_aiops.cli._root",
        "fabric_aiops.cli._common",
        "fabric_aiops.cli.init",
        "fabric_aiops.cli.secret",
        "fabric_aiops.cli.org",
        "fabric_aiops.cli.network",
        "fabric_aiops.cli.device",
        "fabric_aiops.cli.client",
        "fabric_aiops.cli.health",
        "fabric_aiops.cli.remediate",
        "fabric_aiops.cli.overview",
        "fabric_aiops.cli.doctor",
        "mcp_server.server",
        "mcp_server._shared",
        "mcp_server.tools.organizations",
        "mcp_server.tools.networks",
        "mcp_server.tools.devices",
        "mcp_server.tools.clients",
        "mcp_server.tools.health",
        "mcp_server.tools.remediation",
    ):
        importlib.import_module(name)


@pytest.mark.unit
def test_version_matches_pyproject():
    """__version__ is single-sourced from package metadata; it must track
    pyproject.toml so a release bump can never ship a stale self-report."""
    import tomllib
    from pathlib import Path

    import fabric_aiops

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    expected = tomllib.loads(pyproject.read_text("utf-8"))["project"]["version"]
    assert fabric_aiops.__version__ == expected


@pytest.mark.unit
def test_cli_app_builds_and_help_works():
    from fabric_aiops.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for sub in ("org", "network", "device", "client", "health", "remediate",
                "secret", "init", "overview", "doctor", "mcp"):
        assert sub in result.output


@pytest.mark.unit
def test_cli_leaf_help_triggers_lazy_imports():
    """Recurse into leaf commands so any broken lazy import surfaces."""
    from fabric_aiops.cli import app

    runner = CliRunner()
    for cmd in (
        ["org", "--help"], ["network", "--help"], ["device", "--help"],
        ["client", "--help"], ["health", "--help"], ["remediate", "--help"],
        ["secret", "--help"], ["doctor", "--help"], ["overview", "--help"], ["init", "--help"],
        ["org", "list", "--help"], ["org", "get", "--help"],
        ["org", "device-statuses", "--help"], ["org", "api-usage", "--help"],
        ["network", "list", "--help"], ["network", "vlans", "--help"],
        ["network", "alerts", "--help"], ["network", "traffic", "--help"],
        ["device", "inventory", "--help"], ["device", "status", "--help"],
        ["device", "uplinks", "--help"], ["device", "switch-ports", "--help"],
        ["device", "ssids", "--help"],
        ["client", "list", "--help"], ["client", "usage", "--help"],
        ["health", "uplink-rca", "--help"], ["health", "score", "--help"],
        ["remediate", "reboot", "--help"], ["remediate", "blink-leds", "--help"],
        ["remediate", "update-device", "--help"], ["remediate", "update-vlan", "--help"],
        ["remediate", "claim", "--help"], ["remediate", "remove", "--help"],
        ["remediate", "bind", "--help"], ["remediate", "unbind", "--help"],
        ["secret", "list", "--help"], ["secret", "set", "--help"],
    ):
        result = runner.invoke(app, cmd)
        assert result.exit_code == 0, f"{cmd} failed: {result.output}"


@pytest.mark.unit
def test_mcp_list_tools_exposes_expected_tools():
    from mcp_server.server import mcp

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names, f"missing: {EXPECTED_TOOLS - names}"


@pytest.mark.unit
def test_every_mcp_tool_is_governed_by_harness():
    """Every registered tool callable must carry the @governed_tool marker."""
    from mcp_server import _shared

    tool_objs = _shared.mcp._tool_manager._tools
    assert EXPECTED_TOOLS <= set(tool_objs), "tool registry incomplete"
    assert len(tool_objs) == 34, (
        "tool count changed — update README/SKILL/server.json too"
    )
    for name, tool in tool_objs.items():
        fn = getattr(tool, "fn", None)
        assert fn is not None, f"{name} has no fn"
        assert getattr(fn, "_is_governed_tool", False), (
            f"{name} is not wrapped with @governed_tool (harness marker missing)"
        )


@pytest.mark.unit
def test_write_tools_have_correct_risk_tiers():
    from mcp_server.tools import remediation as rem

    for name in HIGH_RISK:
        assert getattr(rem, name)._risk_level == "high", name
    for name in MEDIUM_RISK:
        assert getattr(rem, name)._risk_level == "medium", name


@pytest.mark.unit
def test_update_device_records_undo_token_via_harness(monkeypatch):
    """update_device through the harness records an inverse (restore prior attrs)."""
    import fabric_aiops.governance.undo as undo_mod
    from mcp_server.tools import remediation as rem

    conn = MagicMock(name="conn")
    conn.get.return_value = {"serial": "Q2", "name": "old-name", "tags": ["a"]}
    conn.put.return_value = {}
    monkeypatch.setattr(rem, "_get_connection", lambda target=None: conn)

    recorded = {}

    class _Store:
        def record(self, *, skill, tool, undo_descriptor, orig_params, effect_verified=True):
            recorded["descriptor"] = undo_descriptor
            return "undo-42"

    monkeypatch.setattr(undo_mod, "get_undo_store", lambda: _Store())

    result = rem.update_device(serial="Q2", attrs={"name": "new-name"})
    assert "error" not in result
    assert recorded["descriptor"]["tool"] == "update_device"
    # the undo restores the fetched BEFORE value, not a guess
    assert recorded["descriptor"]["params"]["attrs"]["name"] == "old-name"
    assert result.get("_undo_id") == "undo-42"


@pytest.mark.unit
def test_update_device_captures_before_state():
    """ops.update_device fetches the device first and records prior values."""
    from fabric_aiops.ops import remediation as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {"serial": "Q2", "name": "old-name", "notes": "keep"}
    conn.put.return_value = {}
    result = ops.update_device(conn, "Q2", {"name": "new-name"})
    assert result["action"] == "update_device"
    assert result["priorState"] == {"name": "old-name"}
    conn.put.assert_called_once_with("/devices/Q2", json={"name": "new-name"})


@pytest.mark.unit
def test_bind_undo_rebinds_to_prior_template(monkeypatch):
    """bind captures the prior template so undo rebinds to it."""
    import fabric_aiops.governance.undo as undo_mod
    from mcp_server.tools import remediation as rem

    conn = MagicMock(name="conn")
    conn.get.return_value = {"id": "N1", "configTemplateId": "T-old"}
    conn.post.return_value = {}
    monkeypatch.setattr(rem, "_get_connection", lambda target=None: conn)

    recorded = {}

    class _Store:
        def record(self, *, skill, tool, undo_descriptor, orig_params, effect_verified=True):
            recorded["d"] = undo_descriptor
            return "u1"

    monkeypatch.setattr(undo_mod, "get_undo_store", lambda: _Store())
    rem.bind_network_to_template(network_id="N1", template_id="T-new")
    assert recorded["d"]["tool"] == "bind_network_to_template"
    assert recorded["d"]["params"]["template_id"] == "T-old"


@pytest.mark.unit
def test_dry_run_gates_destructive_cli(monkeypatch):
    """remediate reboot --dry-run previews without issuing the reboot.

    It goes through the governed twin, so it needs a connection — resolving the
    target's platform is how the preview learns whether the write is even
    available there. What it must never do is POST.
    """
    from fabric_aiops.cli import app
    from mcp_server.tools import remediation as rem

    conn = MagicMock(name="conn")
    monkeypatch.setattr(rem, "_get_connection", lambda target=None: conn)

    runner = CliRunner()
    result = runner.invoke(app, ["remediate", "reboot", "Q2XX-XXXX-XXXX", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output
    conn.post.assert_not_called()
    conn.put.assert_not_called()


@pytest.mark.unit
def test_mcp_write_dry_run_does_not_execute():
    """A write tool's dry_run returns a preview without calling the API."""
    from unittest.mock import patch

    from mcp_server.tools import remediation as rem

    conn = MagicMock(name="conn")
    with patch.object(rem, "_get_connection", lambda target=None: conn):
        out = rem.reboot_device(serial="Q2", dry_run=True)
    assert out.get("dryRun") is True
    conn.post.assert_not_called()


@pytest.mark.unit
def test_risk_level_agrees_with_read_write_docstring_tag():
    """The two write-markers must never drift apart.

    A tool's ``risk_level`` decides its audit tier and whether it gets dry-run /
    undo handling; its ``[READ]``/``[WRITE]`` docstring tag is what the docs and
    capability tables are built from. If a ``[WRITE]`` were left ``risk_level=low``
    it would be audited as a read and skip the write machinery — this test caught
    16 such mislabels line-wide once, so it is kept even though read-only mode
    (its original motivation) is gone.
    """
    from mcp_server import server

    untagged, mismatched = [], []
    for name, tool in server.mcp._tool_manager._tools.items():
        doc = (tool.fn.__doc__ or "").lstrip()
        if doc.startswith("[READ]"):
            tagged_as_read = True
        elif doc.startswith("[WRITE]"):
            tagged_as_read = False
        else:
            untagged.append(name)
            continue
        if tagged_as_read != (getattr(tool.fn, "_risk_level", "low") == "low"):
            mismatched.append(name)

    assert not untagged, f"tools missing a [READ]/[WRITE] docstring tag: {untagged}"
    assert not mismatched, f"risk_level disagrees with the docstring tag: {mismatched}"
