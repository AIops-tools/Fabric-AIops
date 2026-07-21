"""bind_network_to_template must not destroy the state its own undo needs.

Binding a Meraki network to a config template OVERWRITES that network's VLAN /
firewall configuration, and unbinding leaves the template-derived configuration
in place rather than restoring what was there before. Capturing only the
binding pointer therefore produced an undo that reported success while the
original VLANs were gone.

These tests pin the fix: the bind refuses when it would overwrite local VLANs
this tool cannot recreate; the refusal is reachable from every preview path;
an unreadable VLAN set is recorded as UNKNOWN (never as an empty list) and
yields no undo descriptor at all; and the undo that IS offered claims only the
binding state, never the configuration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

import fabric_aiops.governance.audit as audit_mod
import fabric_aiops.governance.policy as policy_mod
import fabric_aiops.governance.undo as undo_mod
from fabric_aiops.cli import app
from fabric_aiops.ops import remediation as ops
from fabric_aiops.platform import MERAKI, PathSpec, Platform, get_platform
from mcp_server.tools import remediation as gov

runner = CliRunner()

# A platform that maps the bind but NOT the VLAN read — the case where the tool
# cannot learn what the bind is about to overwrite. No registered platform is
# in that shape today (only meraki maps bind_template, and it maps vlans too),
# so it is constructed here rather than pulled from the registry.
_NO_VLAN_READ = Platform(
    name="novlan",
    default_base_url="https://example.invalid",
    label="Controller Without VLAN Read",
    paths={
        "networks.get": PathSpec("/networks/{network_id}"),
        "networks.bind_template": PathSpec("/networks/{network_id}/bind"),
    },
)


class _Conn:
    """Path-aware fake controller: GETs answer per endpoint, POSTs are recorded."""

    def __init__(self, *, network=None, vlans=None, vlans_error=None, platform=None):
        self.target = SimpleNamespace(
            platform_obj=platform or get_platform(MERAKI), org_id=""
        )
        self._network = {"id": "N1", "configTemplateId": None} if network is None else network
        self._vlans = vlans
        self._vlans_error = vlans_error
        self.calls: list[tuple[str, str]] = []

    def get(self, path, **kwargs):
        self.calls.append(("GET", path))
        if path.endswith("/appliance/vlans"):
            if self._vlans_error is not None:
                raise self._vlans_error
            return [] if self._vlans is None else self._vlans
        return self._network

    def post(self, path, **kwargs):
        self.calls.append(("POST", path))
        return {}

    def posted(self) -> list[str]:
        return [p for verb, p in self.calls if verb == "POST"]


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


_LOCAL_VLANS = [{"id": "10", "name": "data", "subnet": "10.0.10.0/24"}]


# ── (a) the refusal fires, and it fires BEFORE anything is written ───────────


@pytest.mark.unit
def test_bind_refuses_when_local_vlans_would_be_overwritten():
    conn = _Conn(vlans=_LOCAL_VLANS)
    with pytest.raises(ops.IrreversibleBind) as exc:
        ops.bind_network_to_template(conn, "N1", "T-new")
    message = str(exc.value)
    assert "1 local VLAN(s)" in message
    assert "Nothing was changed." in message
    assert "fabric-aiops network vlans N1" in message  # tells the caller what to do instead
    assert conn.posted() == []  # refused before the bind POST


@pytest.mark.unit
def test_bind_reads_the_vlan_set_before_it_posts_the_bind():
    """The capture is a precondition of the write, not a parallel best effort."""
    conn = _Conn(vlans=[])
    ops.bind_network_to_template(conn, "N1", "T-new")
    verbs = [verb for verb, _ in conn.calls]
    paths = [p for _, p in conn.calls]
    assert paths[:3] == ["/networks/N1", "/networks/N1/appliance/vlans", "/networks/N1/bind"]
    assert verbs.index("POST") == 2  # both reads precede the only write


@pytest.mark.unit
def test_bind_allows_rebinding_an_already_bound_network():
    """Already template-derived: rebinding to the prior template really restores it."""
    conn = _Conn(network={"id": "N1", "configTemplateId": "T-old"}, vlans=_LOCAL_VLANS)
    out = ops.bind_network_to_template(conn, "N1", "T-new")
    assert out["reversible"] is True
    assert out["priorState"]["configTemplateId"] == "T-old"
    assert out["priorState"]["vlanCapture"] == ops.VLAN_CAPTURE_TEMPLATE_DERIVED
    assert conn.posted() == ["/networks/N1/bind"]


# ── (b) the undo replays what it claims, and claims nothing more ─────────────


@pytest.mark.unit
def test_bind_undo_unbinds_only_when_the_empty_vlan_set_was_verified():
    conn = _Conn(vlans=[])
    result = ops.bind_network_to_template(conn, "N1", "T-new")
    assert result["priorState"]["vlanCapture"] == ops.VLAN_CAPTURE_CAPTURED
    assert result["priorState"]["vlans"] == []

    descriptor = gov._bind_undo({"network_id": "N1"}, result)
    assert descriptor["tool"] == "unbind_network_from_template"
    assert descriptor["params"] == {"network_id": "N1"}


@pytest.mark.unit
def test_bind_undo_note_does_not_claim_a_configuration_restoration():
    """The unbind branch restores the binding state only — it must say so."""
    conn = _Conn(vlans=[])
    descriptor = gov._bind_undo(
        {"network_id": "N1"}, ops.bind_network_to_template(conn, "N1", "T-new")
    )
    note = descriptor["note"]
    assert "binding state only" in note
    assert "firewall rules" in note and "not restored" in note


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kwargs", "expected_capture"),
    [
        ({"platform": _NO_VLAN_READ}, ops.VLAN_CAPTURE_UNSUPPORTED),
        ({"vlans_error": RuntimeError("VLANs are not enabled for this network")},
         ops.VLAN_CAPTURE_UNAVAILABLE),
    ],
)
def test_bind_offers_no_undo_when_the_prior_vlan_set_was_never_read(kwargs, expected_capture):
    conn = _Conn(**kwargs)
    result = ops.bind_network_to_template(conn, "N1", "T-new")
    assert result["priorState"]["vlanCapture"] == expected_capture
    assert result["reversible"] is False
    # No token at all beats a token that would report success restoring nothing.
    assert gov._bind_undo({"network_id": "N1"}, result) is None
    assert "no undo is recorded" in result["note"]


@pytest.mark.unit
def test_bind_undo_declines_when_prior_vlans_unverified():
    """The builder is a pure function of priorState — it must decline on its own."""
    for capture in (ops.VLAN_CAPTURE_UNSUPPORTED, ops.VLAN_CAPTURE_UNAVAILABLE):
        prior = {"configTemplateId": None, "vlans": None, "vlanCapture": capture}
        assert gov._bind_undo({"network_id": "N1"}, {"priorState": prior}) is None
    # A priorState with no capture field at all (an older record) also declines.
    assert gov._bind_undo({"network_id": "N1"}, {"priorState": {}}) is None


# ── (c) the unsupported / unreadable path is honest: unknown is not empty ────


@pytest.mark.unit
def test_unreadable_vlan_set_is_recorded_as_unknown_never_as_empty():
    """None means 'we could not look'; [] means 'we looked and there are none'."""
    unsupported = ops.bind_network_to_template(_Conn(platform=_NO_VLAN_READ), "N1", "T1")
    assert unsupported["priorState"]["vlans"] is None
    assert "does not map the 'networks.vlans' read" in (
        unsupported["priorState"]["vlanCaptureError"]
    )

    failed = ops.bind_network_to_template(_Conn(vlans_error=RuntimeError("boom")), "N1", "T1")
    assert failed["priorState"]["vlans"] is None
    assert "boom" in failed["priorState"]["vlanCaptureError"]

    # ...and the verified-empty case is the OTHER value, not the same one.
    verified = ops.bind_network_to_template(_Conn(vlans=[]), "N1", "T1")
    assert verified["priorState"]["vlans"] == []
    assert verified["priorState"]["vlanCaptureError"] is None


@pytest.mark.unit
def test_unmapped_bind_still_fails_fast_with_the_platform_error():
    """require_support runs first, so the guard never masks PlatformUnsupported."""
    from fabric_aiops.platform import CATALYST, PlatformUnsupported

    conn = _Conn(platform=get_platform(CATALYST))
    with pytest.raises(PlatformUnsupported):
        ops.guard_bind_network_to_template(conn, "N1")


# ── (d) no prior VLANs → fail open, bind proceeds, nothing crashes ───────────


@pytest.mark.unit
def test_bind_with_no_prior_vlans_proceeds_and_stays_reversible():
    conn = _Conn(vlans=[])
    result = ops.bind_network_to_template(conn, "N1", "T-new")
    assert conn.posted() == ["/networks/N1/bind"]
    assert result["action"] == "bind_network_to_template"
    assert result["reversible"] is True
    assert result["priorState"]["configTemplateId"] is None


# ── (e) the refusal is reachable from every preview path ────────────────────


@pytest.mark.unit
def test_mcp_dry_run_fires_the_refusal_instead_of_previewing_green(gov_home, monkeypatch):
    conn = _Conn(vlans=_LOCAL_VLANS)
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)
    out = gov.bind_network_to_template(network_id="N1", template_id="T1", dry_run=True)
    assert out.get("error"), out  # tool_errors turns the refusal into an error envelope
    assert "wouldBind" not in out
    assert conn.posted() == []


@pytest.mark.unit
def test_mcp_dry_run_surfaces_the_capture_when_the_bind_is_allowed(gov_home, monkeypatch):
    conn = _Conn(vlans=[])
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)
    out = gov.bind_network_to_template(network_id="N1", template_id="T1", dry_run=True)
    assert out["dryRun"] is True
    assert out["wouldBind"] == {"networkId": "N1", "templateId": "T1"}
    assert out["priorState"]["vlanCapture"] == ops.VLAN_CAPTURE_CAPTURED
    assert out["reversible"] is True
    assert conn.posted() == []  # a preview may read; it must never write


@pytest.mark.unit
def test_cli_dry_run_fires_the_refusal(gov_home, monkeypatch):
    conn = _Conn(vlans=_LOCAL_VLANS)
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)
    result = runner.invoke(app, ["remediate", "bind", "N1", "T1", "--dry-run"])
    assert result.exit_code == 1, result.output
    assert "Refusing to bind network 'N1'" in result.output
    assert "DRY-RUN" not in result.output
    assert conn.posted() == []


@pytest.mark.unit
def test_cli_confirmed_bind_fires_the_refusal(gov_home, monkeypatch):
    """Past both confirmations, the refusal still stops the write — and exits 1.

    The confirmed CLI path runs through the governed twin, whose ``tool_errors``
    wrapper renders any refusal as an error envelope rather than a traceback.
    The CLI now routes that envelope through ``governed()``, so the exit code
    agrees with the outcome; it used to be 0, which told a CI caller checking
    ``$?`` that a refused bind had succeeded.
    """
    conn = _Conn(vlans=_LOCAL_VLANS)
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)
    result = runner.invoke(app, ["remediate", "bind", "N1", "T1"], input="y\ny\n")
    assert result.exit_code == 1, result.output
    assert "Refusing to bind network 'N1'" in result.output
    assert conn.posted() == []


@pytest.mark.unit
def test_cli_dry_run_still_previews_an_allowed_bind(gov_home, monkeypatch):
    conn = _Conn(vlans=[])
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)
    result = runner.invoke(app, ["remediate", "bind", "N1", "T1", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output
    assert conn.posted() == []


# ── the recorded undo token matches what the builder promised ───────────────


@pytest.mark.unit
def test_no_undo_token_is_recorded_for_an_unverifiable_bind(gov_home, monkeypatch):
    """End to end through the harness: the store is offered nothing to record."""
    conn = _Conn(platform=_NO_VLAN_READ)
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)
    recorded: list = []

    store = MagicMock()
    store.record.side_effect = lambda **kw: recorded.append(kw) or "u1"
    monkeypatch.setattr(undo_mod, "get_undo_store", lambda: store)

    out = gov.bind_network_to_template(network_id="N1", template_id="T-new")
    assert out["reversible"] is False
    assert recorded == []
