"""claim_devices_into_network → remove_device_from_network undo REPLAY — the
descriptor passes a ``serials`` list, so the target must accept it (found
broken in the line-wide undo-replayability sweep: signature was singular)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mcp_server.tools import remediation as gov


@pytest.mark.unit
def test_claim_undo_descriptor_replays_through_remove():
    result = {"priorState": {"claimedSerials": ["Q2XX-1", "Q2XX-2"]}}
    d = gov._claim_undo({"network_id": "N_1"}, result)
    assert d["tool"] == "remove_device_from_network"

    with patch.object(gov, "_get_connection", return_value=MagicMock()), patch.object(
        gov.ops, "remove_device_from_network",
        side_effect=lambda c, n, s: {"action": "remove_device_from_network", "serial": s},
    ) as mock_remove:
        replay = gov.remove_device_from_network(**d["params"])
    assert [c.args[2] for c in mock_remove.call_args_list] == ["Q2XX-1", "Q2XX-2"]
    assert replay["priorState"]["serials"] == ["Q2XX-1", "Q2XX-2"]


@pytest.mark.unit
def test_remove_single_serial_keeps_original_shape():
    with patch.object(gov, "_get_connection", return_value=MagicMock()), patch.object(
        gov.ops, "remove_device_from_network",
        return_value={"action": "remove_device_from_network", "serial": "Q2XX-1"},
    ):
        result = gov.remove_device_from_network(network_id="N_1", serial="Q2XX-1")
    assert result["serial"] == "Q2XX-1"


@pytest.mark.unit
def test_remove_requires_exactly_one_serial_form():
    r = gov.remove_device_from_network(network_id="N_1", serial="a", serials=["b"])
    assert "not both" in r["error"]
    r = gov.remove_device_from_network(network_id="N_1")
    assert "requires serial or serials" in r["error"]


@pytest.mark.unit
def test_multi_remove_undo_claims_all_back():
    result = {"priorState": {"networkId": "N_1", "serials": ["Q2XX-1", "Q2XX-2"]}}
    d = gov._remove_undo({}, result)
    assert d["tool"] == "claim_devices_into_network"
    assert d["params"]["serials"] == ["Q2XX-1", "Q2XX-2"]
