"""ACI fault reads against the real anonymized faultInst samples."""

from __future__ import annotations

import pytest

from fabric_aiops.ops import aci_faults as af
from fabric_aiops.ops import aci_mit as mit
from tests.aci_fake import FakeApic, FakeConn, load, sample_apic


def _row(name: str, i: int) -> dict:
    return af.fault_row(mit.flatten(load(name))[i])


def test_cleared_fault_is_inactive_even_though_its_text_says_frozen():
    f3696 = _row("08-faults-cleared", 0)
    assert f3696["code"] == "F3696"
    assert f3696["descr"] == "1 EPs are in freeze state."  # text kept verbatim
    assert f3696["active"] is False
    assert f3696["lifecycle"] == "retaining"
    assert "not the current state" in f3696["note"]


def test_active_fault_has_no_historical_note():
    row = _row("07-faults-active-excerpt", 0)
    assert row["active"] is True and "note" not in row
    assert row["scope"] == "vmm" and row["occurrences"] == 1


def test_delegated_pair_is_two_records_one_marked_delegated():
    rows = [_row("07-faults-active-excerpt", i) for i in (0, 1)]
    assert [r["delegated"] for r in rows] == [False, True]


def test_list_faults_counts_delegated_records_and_orders_worst_first():
    conn = FakeConn(sample_apic())
    out = af.list_faults(conn, include_cleared=True, limit=10)
    assert [f["severity"] for f in out["faults"]] == ["critical", "critical", "cleared",
                                                      "cleared", "cleared"]
    assert [f["rank"] for f in out["faults"]] == [1, 2, 3, 4, 5]
    assert out["delegatedRecords"] == 3  # 1 active + 2 cleared rows are delegated
    assert out["truncated"] is False
    assert out["bySeverity"]["serverReported"] == {
        "critical": 2, "major": 0, "minor": 0, "warning": 0, "info": 0, "cleared": 3,
    }


def test_active_only_by_default():
    out = af.list_faults(FakeConn(sample_apic()))
    assert "cleared" not in out["severities"]
    assert all(f["active"] for f in out["faults"])


def test_truncation_is_measured_not_inferred_from_length():
    conn = FakeConn(sample_apic())
    exact = af.list_faults(conn, severity="critical", limit=2)
    assert exact["returned"] == 2 and exact["truncated"] is False
    cut = af.list_faults(conn, include_cleared=True, limit=2)
    assert cut["returned"] == 2 and cut["truncated"] is True


def test_every_level_is_still_counted_after_the_limit_fills():
    apic = sample_apic()
    conn = FakeConn(apic)
    out = af.list_faults(conn, include_cleared=True, limit=1)
    assert out["bySeverity"]["serverReported"]["cleared"] == 3
    sizes = [p["page-size"] for path, p in apic.calls if path.endswith("faultInst.json")]
    assert sizes == ["2", "1", "1", "1", "1", "1"]


def test_newest_first_within_a_severity():
    out = af.list_faults(FakeConn(sample_apic()), severity="cleared")
    stamps = [f["lastTransition"] for f in out["faults"]]
    assert stamps == sorted(stamps, reverse=True)


def test_bad_severity_rejected_before_any_request():
    apic = sample_apic()
    with pytest.raises(ValueError):
        af.list_faults(FakeConn(apic), severity="urgent")
    assert apic.calls == []


def test_non_aci_target_rejected_before_any_request():
    apic = FakeApic()
    with pytest.raises(ValueError, match="Cisco ACI read"):
        af.list_faults(FakeConn(apic, platform="meraki"))
    assert apic.calls == []
