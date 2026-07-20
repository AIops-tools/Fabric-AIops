"""MCP remediation tools: dry-run previews (no controller call) and the undo
descriptor builders (pure functions, both the has-prior and no-prior branches).

The undo builders are what the harness records to make a write reversible; each
must produce a faithful inverse when a prior state exists and decline (None)
when there is nothing to reverse."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import fabric_aiops.governance.audit as audit_mod
import fabric_aiops.governance.policy as policy_mod
import fabric_aiops.governance.undo as undo_mod
from mcp_server.tools import remediation as gov


@pytest.fixture
def gov_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIC_AIOPS_HOME", str(tmp_path))
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: MagicMock(name="conn"))
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()
    yield tmp_path
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()


# ── dry-run previews: return the preview, never call the controller ──────────


@pytest.mark.unit
def test_reboot_dry_run_previews_without_writing(gov_home):
    out = gov.reboot_device(serial="Q2", dry_run=True)
    assert out["dryRun"] is True and out["wouldReboot"] == {"serial": "Q2"}


@pytest.mark.unit
def test_update_device_dry_run_previews(gov_home):
    out = gov.update_device(serial="Q2", attrs={"name": "ap1"}, dry_run=True)
    assert out["dryRun"] is True
    assert out["wouldUpdate"] == {"serial": "Q2", "attrs": {"name": "ap1"}}


@pytest.mark.unit
def test_update_vlan_dry_run_previews(gov_home):
    out = gov.update_network_vlan(
        network_id="N1", vlan_id="10", attrs={"name": "data"}, dry_run=True
    )
    assert out["dryRun"] is True and out["wouldUpdate"]["vlanId"] == "10"


@pytest.mark.unit
def test_claim_dry_run_previews(gov_home):
    out = gov.claim_devices_into_network(network_id="N1", serials=["Q2"], dry_run=True)
    assert out["dryRun"] is True and out["wouldClaim"]["serials"] == ["Q2"]


@pytest.mark.unit
def test_remove_dry_run_previews_and_normalises_batch(gov_home):
    out = gov.remove_device_from_network(network_id="N1", serial="Q2", dry_run=True)
    assert out["dryRun"] is True and out["wouldRemove"]["serials"] == ["Q2"]


@pytest.mark.unit
def test_bind_dry_run_previews(gov_home):
    out = gov.bind_network_to_template(network_id="N1", template_id="T1", dry_run=True)
    assert out["dryRun"] is True and out["wouldBind"]["templateId"] == "T1"


@pytest.mark.unit
def test_unbind_dry_run_previews(gov_home):
    out = gov.unbind_network_from_template(network_id="N1", dry_run=True)
    assert out["dryRun"] is True and out["wouldUnbind"] == {"networkId": "N1"}


# ── blink: low-risk, runs through the ops layer with no undo ──────────────────


@pytest.mark.unit
def test_blink_runs_through_ops(gov_home, monkeypatch):
    monkeypatch.setattr(
        gov.ops, "blink_device_leds",
        lambda conn, serial, duration: {"action": "blink_device_leds", "serial": serial},
    )
    out = gov.blink_device_leds(serial="Q2", duration=30)
    assert out["action"] == "blink_device_leds" and out["serial"] == "Q2"


# ── undo descriptors: update_device / update_vlan ────────────────────────────


@pytest.mark.unit
def test_update_device_undo_restores_prior_and_declines_without_prior():
    d = gov._update_device_undo({"serial": "Q2"}, {"priorState": {"name": "old"}})
    assert d["tool"] == "update_device"
    assert d["params"] == {"serial": "Q2", "attrs": {"name": "old"}}
    assert gov._update_device_undo({"serial": "Q2"}, {"priorState": {}}) is None
    assert gov._update_device_undo({"serial": "Q2"}, "not-a-dict") is None


@pytest.mark.unit
def test_update_vlan_undo_restores_prior_and_declines_without_prior():
    d = gov._update_vlan_undo(
        {"network_id": "N1", "vlan_id": "10"}, {"priorState": {"name": "old"}}
    )
    assert d["tool"] == "update_network_vlan"
    assert d["params"]["attrs"] == {"name": "old"}
    assert gov._update_vlan_undo({}, {"priorState": {}}) is None
    assert gov._update_vlan_undo({}, None) is None


# ── undo descriptors: claim / remove ─────────────────────────────────────────


@pytest.mark.unit
def test_claim_undo_declines_when_no_serials_or_not_dict():
    assert gov._claim_undo({"network_id": "N1"}, {"priorState": {"claimedSerials": []}}) is None
    assert gov._claim_undo({"network_id": "N1"}, "nope") is None


@pytest.mark.unit
def test_remove_undo_handles_single_and_declines_when_empty():
    d = gov._remove_undo({}, {"priorState": {"networkId": "N1", "serial": "Q2"}})
    assert d["tool"] == "claim_devices_into_network" and d["params"]["serials"] == ["Q2"]
    assert gov._remove_undo({}, {"priorState": {}}) is None
    assert gov._remove_undo({}, 123) is None


# ── undo descriptors: bind / unbind (both branches) ──────────────────────────


@pytest.mark.unit
def test_bind_undo_rebinds_prior_or_unbinds_when_none():
    rebind = gov._bind_undo({"network_id": "N1"}, {"priorState": {"configTemplateId": "T0"}})
    assert rebind["tool"] == "bind_network_to_template"
    assert rebind["params"]["template_id"] == "T0"

    # No prior template, and the VLAN set was READ and found empty → the
    # inverse of bind is to unbind. (Without that verification it declines —
    # see test_bind_undo_declines_when_prior_vlans_unverified.)
    unbind = gov._bind_undo(
        {"network_id": "N1"},
        {"priorState": {"configTemplateId": None, "vlans": [], "vlanCapture": "captured"}},
    )
    assert unbind["tool"] == "unbind_network_from_template"
    assert unbind["params"] == {"network_id": "N1"}

    assert gov._bind_undo({}, "not-a-dict") is None


@pytest.mark.unit
def test_unbind_undo_rebinds_prior_or_declines_when_none():
    rebind = gov._unbind_undo({"network_id": "N1"}, {"priorState": {"configTemplateId": "T0"}})
    assert rebind["tool"] == "bind_network_to_template"
    assert rebind["params"]["template_id"] == "T0"

    assert gov._unbind_undo({"network_id": "N1"}, {"priorState": {}}) is None
    assert gov._unbind_undo({}, "not-a-dict") is None
