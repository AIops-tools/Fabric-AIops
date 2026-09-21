"""ACI MIT helpers — validation, DN parsing, and both response shapes."""

from __future__ import annotations

import pytest

from fabric_aiops.ops import aci_mit as mit
from tests.aci_fake import load

CEP_DN = "uni/tn-tenant-A/ap-app-A/epg-epg-A/cep-02:00:00:00:00:01"
PATH_DN = "topology/pod-20/paths-301/pathep-[eth1/10]"


@pytest.mark.parametrize("dn", [
    CEP_DN,
    PATH_DN,
    "topology/pod-20/protpaths-101-102/pathep-[vpc-pg-A]",
    "uni/tn-common/brc-contract-A/subj-permit/rssubjFiltAtt-default",
    "comp/prov-VMM-A/ctrlr-[vds-A]-vmmctrlr-A/hv-host-10001",
])
def test_validate_dn_accepts_real_dns(dn):
    assert mit.validate_dn(dn) == dn


@pytest.mark.parametrize("dn", [
    "uni/../api/aaaLogin",
    "topology/pod-1/paths-1/pathep-[../../../api/x]",
    "uni/tn-a?query-target=subtree",
    "uni/tn-a#frag",
    "uni/tn-a/ap b",
    "api/node/class/fvCEp",
    "uni//tn-a",
    "uni/tn-a/epg-[unbalanced",
    "uni/tn-a%2F..",
    "",
])
def test_validate_dn_rejects_escapes_and_query_syntax(dn):
    with pytest.raises(ValueError):
        mit.validate_dn(dn)


def test_mo_path_sends_the_dn_unencoded_like_the_sdk():
    assert mit.mo_path(PATH_DN) == f"/api/mo/{PATH_DN}.json"


def test_mac_is_upper_cased_because_apic_compares_exactly():
    assert mit.normalize_mac("aa-bb-cc-dd-ee-0f") == "AA:BB:CC:DD:EE:0F"
    with pytest.raises(ValueError):
        mit.normalize_mac("aa:bb:cc")


def test_ip_and_name_validation():
    assert mit.normalize_ip(" 192.0.2.200 ") == "192.0.2.200"
    assert mit.normalize_ip("2001:DB8::1") == "2001:db8::1"
    with pytest.raises(ValueError):
        mit.normalize_ip("192.0.2.300")
    assert mit.validate_name("tenant", "tenant-A") == "tenant-A"
    for bad in ("a/b", 'x"', "", "a" * 65, "a b"):
        with pytest.raises(ValueError):
            mit.validate_name("tenant", bad)


def test_eq_filter_refuses_quotes():
    assert mit.eq_filter("fvCEp", "mac", "02:00:00:00:00:01") == 'eq(fvCEp.mac,"02:00:00:00:00:01")'
    with pytest.raises(ValueError):
        mit.eq_filter("fvCEp", "mac", 'x"),or(')


def test_split_dn_respects_brackets():
    assert mit.split_dn(PATH_DN) == ["topology", "pod-20", "paths-301", "pathep-[eth1/10]"]
    assert mit.parent_dn(CEP_DN) == "uni/tn-tenant-A/ap-app-A/epg-epg-A"
    assert mit.tenant_of(CEP_DN) == "tenant-A"
    assert mit.rn_value(CEP_DN, "ap-") == "app-A"


@pytest.mark.parametrize("tdn,expected", [
    (PATH_DN, {"pod": 20, "nodes": [301], "fex": [], "endpoint": "eth1/10", "kind": "interface"}),
    ("topology/pod-1/protpaths-101-102/pathep-[vpc-pg-A]",
     {"pod": 1, "nodes": [101, 102], "fex": [], "endpoint": "vpc-pg-A", "kind": "vpc"}),
    ("topology/pod-1/paths-101/pathep-[pc-pg-A]",
     {"pod": 1, "nodes": [101], "fex": [], "endpoint": "pc-pg-A", "kind": "port-channel"}),
    ("topology/pod-1/paths-101/extpaths-110/pathep-[eth1/3]",
     {"pod": 1, "nodes": [101], "fex": [110], "endpoint": "eth1/3", "kind": "interface"}),
    ("topology/pod-1/protpaths-101-102/extprotpaths-111-112/pathep-[eth1/1]",
     {"pod": 1, "nodes": [101, 102], "fex": [111, 112], "endpoint": "eth1/1", "kind": "vpc"}),
    ("topology/pod-1/node-101/sys/tunnel-[tunnel5]",
     {"pod": None, "nodes": [], "fex": [], "endpoint": None, "kind": None}),
])
def test_parse_path_dn(tdn, expected):
    assert mit.parse_path_dn(tdn) == expected


def test_flatten_nested_sample_builds_child_dns_from_rn():
    objs = mit.flatten(load("01-endpoint-excerpt"))
    path = mit.of_class(objs, "fvRsCEpToPathEp")[0]
    assert path["dn"] == f"{CEP_DN}/rscEpToPathEp-[{PATH_DN}]"
    assert path["parentDn"] == CEP_DN
    node = mit.of_class(objs, "fvReportingNode")[0]
    assert node["dn"] == f"{CEP_DN}/ip-[192.0.2.200]/node-301"


def test_flatten_flat_contract_rows_and_children_of_both_shapes():
    objs = mit.flatten(load("05-contract-excerpt"))
    assert [o["class"] for o in objs] == ["vzBrCP", "vzSubj", "vzRsSubjFiltAtt"]
    subj_dn = "uni/tn-common/brc-contract-A/subj-permit"
    rels = mit.children_of(objs, subj_dn, "vzRsSubjFiltAtt")
    assert len(rels) == 1 and rels[0]["parentDn"] is None  # flat row, matched by dn


def test_total_count_is_parsed_but_never_the_row_count():
    payload = load("05-contract-excerpt")
    assert mit.total_count(payload) == 31
    assert len(payload["imdata"]) == 3
    assert mit.total_count({}) is None
    assert mit.total_count({"totalCount": "x"}) is None


def test_fault_scope_of_the_three_real_dn_shapes():
    active = load("07-faults-active-excerpt")["imdata"][0]["faultInst"]["attributes"]["dn"]
    assert mit.fault_scope(active)["scope"] == "vmm"
    cleared = [r["faultInst"]["attributes"]["dn"] for r in load("08-faults-cleared")["imdata"]]
    assert mit.fault_scope(cleared[0]) == {"scope": "node", "pod": 10, "node": 101, "aboutDn": None}
    deployed = mit.fault_scope(cleared[1])
    assert deployed["node"] == 302
    assert deployed["aboutDn"] == "uni/tn-tenant-B/ap-app-B/epg-epg-B"
    assert mit.fault_scope("uni/tn-a/fault-F1")["scope"] == "logical"


def test_opt_keeps_empty_string_distinct_from_absent():
    assert mit.opt({"descr": ""}, "descr") == ""
    assert mit.opt({}, "descr") is None
