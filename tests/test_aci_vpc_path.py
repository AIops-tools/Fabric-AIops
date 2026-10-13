"""The endpoint's recorded path vs its formed attachment (Fabric-AIops #1).

The real vPC sample (``09-endpoint-vpc.json``, APIC 5.3(2f)) shows APIC
recording a vPC-learned endpoint's ``fabricPathDn`` as **one member leaf**
(``paths-301/pathep-[vpc_storage_a]``) while the formed ``fvRsCEpToPathEp``
names the **pair** (``protpaths-301-302/pathep-[vpc_storage_a]``). That is one
attachment written two ways, not a disagreement — and certainly not evidence
that the endpoint moved.
"""

from __future__ import annotations

import copy

import pytest

from fabric_aiops.ops import aci_trace as tr
from tests.aci_fake import FakeApic, FakeConn, load

VPC_MAC = "02:00:00:00:00:01"
PAIR = "topology/pod-2/protpaths-301-302/pathep-[vpc_storage_a]"


def _vpc_apic(recorded: str | None = None, formed: str | None = None) -> FakeApic:
    row = copy.deepcopy(load("09-endpoint-vpc")["imdata"][0])
    attrs = row["fvCEp"]["attributes"]
    if recorded is not None:
        attrs["fabricPathDn"] = recorded
    if formed is not None:
        for child in row["fvCEp"]["children"]:
            if "fvRsCEpToPathEp" in child:
                rel = child["fvRsCEpToPathEp"]["attributes"]
                rel["tDn"] = formed
                rel["rn"] = f"rscEpToPathEp-[{formed}]"
    apic = FakeApic()
    apic.add_class_rows("fvCEp", [row])
    return apic


def _endpoint(apic: FakeApic) -> dict:
    out = tr.endpoint_trace(FakeConn(apic), mac=VPC_MAC)
    assert out["returned"] == 1, out
    return out["endpoints"][0]


def _path_findings(ep: dict) -> list[dict]:
    return [f for f in ep["findings"] if "fabricPathDn" in f["signal"]]


def test_real_vpc_sample_member_leaf_path_is_not_a_finding():
    ep = _endpoint(_vpc_apic())
    (path,) = ep["attachment"]
    assert path["kind"] == "vpc" and path["nodes"] == [301, 302]
    assert ep["attachmentFormed"] is True
    assert ep["endpoint"]["fabricPathDn"] == "topology/pod-2/paths-301/pathep-[vpc_storage_a]"
    assert _path_findings(ep) == []
    assert ep["endpoint"]["recordedPathMatch"] == "vpc-member"


def test_the_other_member_leaf_is_equally_consistent():
    ep = _endpoint(_vpc_apic(recorded="topology/pod-2/paths-302/pathep-[vpc_storage_a]"))
    assert _path_findings(ep) == []
    assert ep["endpoint"]["recordedPathMatch"] == "vpc-member"


def test_identical_paths_are_an_exact_match():
    ep = _endpoint(_vpc_apic(recorded=PAIR))
    assert _path_findings(ep) == []
    assert ep["endpoint"]["recordedPathMatch"] == "exact"


@pytest.mark.parametrize("recorded", [
    "topology/pod-2/paths-303/pathep-[vpc_storage_a]",   # a leaf outside the pair
    "topology/pod-2/paths-301/pathep-[vpc_other]",       # same leaf, other policy group
    "topology/pod-3/paths-301/pathep-[vpc_storage_a]",   # same names, other pod
    "topology/pod-2/paths-301/pathep-[eth1/7]",          # a physical port on a member
    "topology/pod-2/protpaths-301-303/pathep-[vpc_storage_a]",  # another pair
])
def test_a_genuinely_different_path_is_still_reported(recorded):
    ep = _endpoint(_vpc_apic(recorded=recorded))
    (finding,) = _path_findings(ep)
    assert finding["severity"] == "medium"
    assert ep["endpoint"]["recordedPathMatch"] == "differs"


def test_a_difference_is_not_presented_as_movement():
    ep = _endpoint(_vpc_apic(recorded="topology/pod-2/paths-303/pathep-[vpc_storage_a]"))
    (finding,) = _path_findings(ep)
    text = f"{finding['cause']} {finding['action']}"
    assert "may have moved" not in text
    assert "does not by itself show" in finding["cause"]


@pytest.mark.parametrize("recorded", [
    "topology/pod-2/paths-301/extpaths-111/pathep-[eth1/1]",
    "topology/pod-2/paths-302/extpaths-111/pathep-[eth1/1]",
    "topology/pod-2/paths-301/extprotpaths-111-112/pathep-[eth1/1]",
    "topology/pod-2/paths-301/pathep-[eth1/1]",
])
def test_fex_paths_are_not_excused_without_a_real_sample(recorded):
    # No FEX-behind-vPC response has been seen; which pairing APIC records is
    # unknown, so these stay 'differs' rather than being guessed into a match.
    pair = "topology/pod-2/protpaths-301-302/extprotpaths-111-112/pathep-[eth1/1]"
    ep = _endpoint(_vpc_apic(recorded=recorded, formed=pair))
    assert ep["endpoint"]["recordedPathMatch"] == "differs", recorded


def test_a_single_node_protpaths_dn_is_not_a_member_path():
    ep = _endpoint(_vpc_apic(recorded="topology/pod-2/protpaths-301/pathep-[vpc_storage_a]"))
    assert ep["endpoint"]["recordedPathMatch"] == "differs"


def test_a_recorded_two_node_paths_dn_is_not_a_member_path():
    ep = _endpoint(_vpc_apic(recorded="topology/pod-2/paths-301-302/pathep-[vpc_storage_a]"))
    assert ep["endpoint"]["recordedPathMatch"] == "differs"


def test_a_recorded_fex_path_is_not_a_member_of_a_fexless_pair():
    recorded = "topology/pod-2/paths-301/extpaths-111/pathep-[vpc_storage_a]"
    ep = _endpoint(_vpc_apic(recorded=recorded))
    assert ep["endpoint"]["recordedPathMatch"] == "differs"


def test_a_formed_relation_that_is_not_a_vpc_excuses_nothing():
    # paths-<a>-<b> parses (two nodes) but is not a vPC; only protpaths is.
    ep = _endpoint(_vpc_apic(formed="topology/pod-2/paths-301-302/pathep-[vpc_storage_a]"))
    assert ep["endpoint"]["recordedPathMatch"] == "differs"


def test_an_unparseable_recorded_path_differs_and_is_never_excused():
    ep = _endpoint(_vpc_apic(recorded="topology/pod-2/odd-shape-301"))
    assert ep["endpoint"]["recordedPathMatch"] == "differs"


def test_an_unparseable_formed_path_cannot_excuse_a_member_leaf():
    ep = _endpoint(_vpc_apic(formed="topology/pod-2/odd-shape-301-302"))
    assert ep["endpoint"]["recordedPathMatch"] == "differs"


def test_any_formed_vpc_relation_among_several_excuses_its_member_path():
    apic = _vpc_apic()
    row = apic.class_rows["fvCEp"][0]["fvCEp"]
    extra = copy.deepcopy(next(c for c in row["children"] if "fvRsCEpToPathEp" in c))
    other = "topology/pod-2/paths-305/pathep-[eth1/9]"
    extra["fvRsCEpToPathEp"]["attributes"].update(tDn=other, rn=f"rscEpToPathEp-[{other}]")
    row["children"].append(extra)
    ep = _endpoint(apic)
    assert len(ep["attachment"]) == 2
    assert ep["endpoint"]["recordedPathMatch"] == "vpc-member"


def test_only_a_vpc_attachment_excuses_a_member_leaf_path():
    # A single-leaf formed attachment (here behind a FEX pair) is never read as
    # a vPC whose member leaf APIC recorded.
    formed = "topology/pod-2/paths-301/extprotpaths-111-112/pathep-[eth1/1]"
    recorded = "topology/pod-2/paths-301/extpaths-111/pathep-[eth1/1]"
    ep = _endpoint(_vpc_apic(recorded=recorded, formed=formed))
    assert ep["endpoint"]["recordedPathMatch"] == "differs"


def test_unformed_relation_is_not_used_to_excuse_a_path():
    apic = _vpc_apic()
    row = apic.class_rows["fvCEp"][0]["fvCEp"]
    for child in row["children"]:
        if "fvRsCEpToPathEp" in child:
            child["fvRsCEpToPathEp"]["attributes"]["state"] = "missing-target"
    ep = _endpoint(apic)
    assert ep["endpoint"]["recordedPathMatch"] is None
    assert any(f["severity"] == "high" for f in ep["findings"])


def test_missing_recorded_path_is_unknown_not_a_match():
    apic = _vpc_apic()
    del apic.class_rows["fvCEp"][0]["fvCEp"]["attributes"]["fabricPathDn"]
    ep = _endpoint(apic)
    assert ep["endpoint"]["recordedPathMatch"] is None
    assert _path_findings(ep) == []


def test_tag_relations_in_the_vpc_sample_stay_out_of_path_findings():
    ep = _endpoint(_vpc_apic())
    states = {r["state"] for r in ep["tagRelations"]["relations"]}
    assert states == {"missing-target"}
    assert all("tag" not in f["signal"].lower() for f in ep["findings"])
