"""Absent fields come back as null, not as an empty string.

An empty string reads as "this field exists and is empty"; a missing field is a
different fact. Collapsing the two hides information from any consumer, and a
smaller local model will confidently invent the difference. These tests pin the
contract end-to-end: helper, the platform adapters that fold a vendor payload
into the canonical shape, the ops read boundary, and the CLI rendering that has
to cope with a null.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from fabric_aiops.cli import app
from fabric_aiops.config import TargetConfig
from fabric_aiops.governance import opt_str
from fabric_aiops.ops import devices, networks, organizations

runner = CliRunner()


class _Conn:
    """Fake connection: get/get_pages look up canned responses by path."""

    def __init__(self, responses, org_id="O1"):
        self._responses = responses
        self.target = TargetConfig(name="t", org_id=org_id)

    def get(self, path, **_kw):
        return self._responses[path]

    def get_pages(self, path, params=None, **_kw):
        data = self._responses[path]
        return data if isinstance(data, list) else [data]


# ── the helper ───────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_opt_str_distinguishes_absent_from_empty():
    assert opt_str(None) is None, "absent must stay absent"
    assert opt_str("") == "", "a genuinely empty value is not the same as absent"
    assert opt_str("branch-1", 64) == "branch-1"


@pytest.mark.unit
def test_opt_str_still_sanitizes_and_truncates():
    assert opt_str("a\x00b") == "ab"  # control character stripped
    assert opt_str("abcdef", 3) == "abc"


@pytest.mark.unit
def test_opt_str_accepts_non_string_values():
    assert opt_str(42) == "42"


# ── the ops read boundary ────────────────────────────────────────────────────


@pytest.mark.unit
def test_ops_report_absent_fields_as_none():
    """A device row with no name/model reports null, not ''."""
    conn = _Conn({"/organizations/O1/devices": [{"serial": "Q1", "name": None}]})
    row = devices.inventory(conn)["devices"][0]
    assert row["serial"] == "Q1"
    assert row["name"] is None


@pytest.mark.unit
def test_ops_keep_empty_string_when_source_is_empty():
    """An explicitly empty upstream value is preserved as '' — not turned into null."""
    conn = _Conn({"/organizations/O1/devices": [{"serial": "Q1", "name": ""}]})
    assert devices.inventory(conn)["devices"][0]["name"] == ""


@pytest.mark.unit
def test_alert_rows_preserve_absent_text_fields():
    conn = _Conn({"/networks/N1/health/alerts": [{"type": "connectivity", "severity": None}]})
    row = networks.network_alerts(conn, "N1")["alerts"][0]
    assert row["type"] == "connectivity"
    assert row["severity"] is None


# ── the platform adapters ────────────────────────────────────────────────────


@pytest.mark.unit
def test_unifi_adapter_never_drops_the_key_itself():
    """Keys are always present; only their value may be null.

    Omitting a key entirely is worse than a null — the consumer cannot tell the
    field was even considered.
    """
    from fabric_aiops.platforms.unifi import _device_row

    row = _device_row({})
    for key in ("serial", "name", "mac", "lanIp", "model", "firmware", "productType"):
        assert key in row, f"{key} must be present even when the source omitted it"
        assert row[key] is None, f"{key} must be null, not '' , when absent"


@pytest.mark.unit
def test_catalyst_adapter_reports_absent_device_fields_as_null():
    from fabric_aiops.platforms.catalyst import _network_device_row

    row = _network_device_row({})
    assert row["name"] is None
    assert row["model"] is None
    assert row["lanIp"] is None


@pytest.mark.unit
def test_cvp_adapter_reports_absent_event_fields_as_null():
    from fabric_aiops.platforms.cvp import _event_row

    row = _event_row({})
    assert row["type"] is None
    assert row["message"] is None
    assert row["networkId"] is None


@pytest.mark.unit
def test_summarize_organizations_keeps_keys_with_null_values():
    rows = organizations.summarize_organizations([{}])
    assert rows[0] == {"id": None, "name": None, "url": None, "apiEnabled": None}


# ── the CLI ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_renders_rows_with_null_fields(monkeypatch):
    """The output must survive a null field rather than crashing on render."""
    import fabric_aiops.cli.device as device_cli
    from fabric_aiops.ops import devices as ops

    conn = _Conn({"/organizations/O1/devices": [{"serial": "Q1", "name": None}]})
    monkeypatch.setattr(device_cli, "get_connection", lambda target=None: (conn, None))
    monkeypatch.setattr(
        ops, "inventory", lambda *a, **k: {"total": 1, "devices": [{"serial": "Q1", "name": None}]}
    )

    result = runner.invoke(app, ["device", "inventory"])
    assert result.exit_code == 0, result.output
    assert "Q1" in result.output
    assert "null" in result.output, "a null must render as null, not as an empty string"
