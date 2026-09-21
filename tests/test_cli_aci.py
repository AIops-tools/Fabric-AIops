"""``fabric-aiops aci`` commands end to end over the fake APIC (real samples)."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import fabric_aiops.cli.aci as aci_cli
from fabric_aiops.cli import app
from tests.aci_fake import FakeConn, sample_apic

runner = CliRunner()


@pytest.fixture
def apic(monkeypatch, tmp_path):
    monkeypatch.setenv("FABRIC_AIOPS_HOME", str(tmp_path))
    fake = sample_apic()
    monkeypatch.setattr(aci_cli, "get_connection", lambda target=None: (FakeConn(fake), None))
    return fake


def _json(output: str) -> dict:
    return json.loads(output[output.index("{"): output.rindex("}") + 1])


def test_trace_by_mac_prints_the_resolved_chain(apic):
    res = runner.invoke(app, ["aci", "trace", "--mac", "02:00:00:00:00:01"])
    assert res.exit_code == 0, res.output
    ep = _json(res.output)["endpoints"][0]
    assert ep["staticPathResolved"] is True
    assert ep["vrf"]["dn"] == "uni/tn-common/ctx-vrf-A"


def test_trace_with_both_mac_and_ip_is_a_one_line_error(apic):
    res = runner.invoke(app, ["aci", "trace", "--mac", "02:00:00:00:00:01", "--ip", "192.0.2.200"])
    assert res.exit_code == 1 and "exactly one of mac or ip" in res.output
    assert apic.calls == []


def test_segment_by_names(apic):
    res = runner.invoke(app, ["aci", "segment", "tenant-A", "app-A", "epg-A"])
    assert res.exit_code == 0, res.output
    assert _json(res.output)["segmentResolved"] is True


def test_faults_announce_truncation(apic):
    res = runner.invoke(app, ["aci", "faults", "--include-cleared", "--limit", "2"])
    assert res.exit_code == 0, res.output
    assert "truncated at 2" in res.output


def test_bad_severity_exits_1(apic):
    res = runner.invoke(app, ["aci", "faults", "--severity", "urgent"])
    assert res.exit_code == 1 and "Invalid fault severity" in res.output
