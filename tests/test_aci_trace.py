"""ACI endpoint path reconstruction against the real connected sample chain."""

from __future__ import annotations

import copy

from fabric_aiops.connection import FabricApiError
from fabric_aiops.ops import aci_segment as seg
from fabric_aiops.ops import aci_trace as tr
from tests.aci_fake import FakeConn, load, sample_apic

MAC = "02:00:00:00:00:01"
EPG_DN = "uni/tn-tenant-A/ap-app-A/epg-epg-A"


def _trace(apic=None, **kw):
    return tr.endpoint_trace(FakeConn(apic or sample_apic()), **({"mac": MAC} | kw))


def _one(out):
    assert out["returned"] == 1, out
    return out["endpoints"][0]


def _fault(dn: str, severity: str = "critical", stamp: str = "2026-09-20T10:00:00.000-03:00"):
    return {"faultInst": {"attributes": {
        "dn": dn, "code": "F0001", "severity": severity, "lc": "raised",
        "delegated": "no", "descr": "x", "lastTransition": stamp,
    }}}


def test_real_chain_resolves_end_to_end_including_cross_tenant_links():
    ep = _one(_trace())
    assert ep["attachmentFormed"] is True
    (path,) = ep["attachment"]
    got = (path["pod"], path["nodes"], path["endpoint"], path["kind"])
    assert got == (20, [301], "eth1/10", "interface")
    assert path["tCl"] == "fabricAPathEp"  # as APIC sent it, not an assumed class
    assert ep["epg"]["tenant"] == "tenant-A"
    assert ep["bd"]["dn"] == "uni/tn-tenant-A/BD-bd-A"
    assert ep["bd"]["subnets"][0]["ip"] == "192.0.2.1/22"
    assert ep["vrf"]["dn"] == "uni/tn-common/ctx-vrf-A" and ep["vrf"]["tenant"] == "common"
    assert ep["segmentResolved"] is True and ep["staticPathResolved"] is True
    assert ep["errors"] == []


def test_contract_chain_follows_tdns_into_common():
    ep = _one(_trace())
    assert [r["name"] for r in ep["epg"]["provides"]] == ["contract-A"]
    assert [r["name"] for r in ep["epg"]["consumes"]] == ["contract-A"]
    (contract,) = ep["contracts"]["contracts"]  # provided + consumed → read once
    assert contract["dn"] == "uni/tn-common/brc-contract-A" and contract["scope"] == "context"
    (subject,) = contract["subjects"]
    (flt,) = subject["filters"]
    got = (flt["tDn"], flt["action"], flt["direction"])
    assert got == ("uni/tn-common/flt-default", "permit", "both")
    (filt,) = ep["contracts"]["filters"]
    assert filt["tenant"] == "common" and filt["entries"][0]["etherType"] == "unspecified"


def test_missing_target_tags_are_listed_but_never_a_path_finding():
    ep = _one(_trace())
    states = {r["class"]: r["state"] for r in ep["tagRelations"]["relations"]}
    assert states == {"fvRsToEpMacTag": "missing-target", "fvRsToEpIpTag": "missing-target"}
    assert ep["findings"] == []  # formed path + resolved chain → nothing to report
    text = " ".join(str(f) for f in ep["findings"])
    assert "tag" not in text.lower()


def test_unformed_attachment_is_a_high_finding():
    apic = sample_apic()
    row = apic.class_rows["fvCEp"][0]["fvCEp"]
    row["children"][0]["fvRsCEpToPathEp"]["attributes"]["state"] = "missing-target"
    ep = _one(_trace(apic))
    assert ep["attachmentFormed"] is False and ep["staticPathResolved"] is False
    assert ep["findings"][0]["severity"] == "high"
    assert "missing-target" in ep["findings"][0]["signal"]


def test_lower_case_mac_matches_the_upper_case_apic_value():
    apic = sample_apic()
    row = apic.class_rows["fvCEp"][0]["fvCEp"]["attributes"]
    row["mac"] = "0A:BB:00:00:00:01"
    assert _one(_trace(apic, mac="0a:bb:00:00:00:01"))["endpoint"]["mac"] == "0A:BB:00:00:00:01"


def test_unknown_mac_says_not_learned_not_down():
    out = _trace(mac="02:00:00:00:00:99")
    assert out["endpoints"] == [] and out["truncated"] is False
    assert "does not mean the host is" in out["notFound"]


def test_ip_lookup_finds_the_owning_endpoint():
    ep = _one(_trace(mac=None, ip="192.0.2.200"))
    assert ep["endpoint"]["mac"] == MAC
    assert ep["ips"][0]["reportingNodes"] == ["301"]


def test_exactly_one_of_mac_or_ip():
    for kw in ({"mac": None}, {"ip": "192.0.2.1"}):
        try:
            _trace(**kw)
        except ValueError as exc:
            assert "exactly one" in str(exc)
        else:
            raise AssertionError("expected ValueError")


def test_a_failed_read_is_null_not_empty_configuration():
    apic = sample_apic()
    apic.fail["/api/mo/uni/tn-tenant-A/BD-bd-A.json"] = FabricApiError("boom", status_code=500)
    ep = _one(_trace(apic))
    assert ep["bd"] is None and ep["vrf"] is None
    assert ep["errors"][0]["step"] == "bd"
    assert ep["segmentResolved"] is None and ep["staticPathResolved"] is None


def test_unresolved_bd_relation_stops_the_chain_with_a_high_finding():
    apic = sample_apic()
    epg = apic.mos[EPG_DN]["fvAEPg"]
    bd_rel = next(c for c in epg["children"] if "fvRsBd" in c)["fvRsBd"]["attributes"]
    bd_rel.update(state="missing-target", tDn="")
    ep = _one(_trace(apic))
    assert ep["bd"] is None and ep["segmentResolved"] is False
    assert any(f["severity"] == "high" and "bridge domain" in f["signal"] for f in ep["findings"])


def test_missing_epg_is_reported_as_absent():
    apic = sample_apic()
    del apic.mos[EPG_DN]
    ep = _one(_trace(apic))
    assert ep["epg"] == {"dn": EPG_DN, "exists": False}
    assert ep["segmentResolved"] is False


def test_unenforced_vrf_and_no_contracts_are_explained():
    apic = sample_apic()
    apic.mos["uni/tn-common/ctx-vrf-A"]["fvCtx"]["attributes"]["pcEnfPref"] = "unenforced"
    ep = _one(_trace(apic))
    assert any("unenforced" in f["signal"] for f in ep["findings"])
    apic = sample_apic()
    epg = apic.mos[EPG_DN]["fvAEPg"]
    epg["children"] = [c for c in epg["children"] if "fvRsBd" in c]
    ep = _one(_trace(apic))
    assert any(f["severity"] == "low" and "no contract" in f["signal"] for f in ep["findings"])


def test_cross_check_flags_an_endpoint_bd_that_differs_from_the_chain():
    apic = sample_apic()
    apic.class_rows["fvCEp"][0]["fvCEp"]["attributes"]["bdDn"] = "uni/tn-tenant-A/BD-old"
    ep = _one(_trace(apic))
    assert any("BD-old" in f["signal"] for f in ep["findings"])


def test_related_faults_are_dn_matched_candidates_active_first():
    apic = sample_apic()
    node = "topology/pod-20/node-301"
    apic.add_subtree_rows(node, [
        _fault(f"{node}/sys/phys-[eth1/10]/fault-F1", "major"),
        _fault(f"{node}/sys/phys-[eth1/11]/fault-F2"),          # other interface
        _fault(f"{node}/local/svc-policyelem-id-0/uni/epp/fv-[{EPG_DN}]/fault-F3",
               "cleared"),
        _fault(f"{node}/sys/ctx-[vxlan-1]/fault-F4"),            # unrelated
    ])
    cands = _one(_trace(apic))["relatedFaultCandidates"]
    assert [f["matchedBy"] for f in cands["faults"]] == [
        "node 301 interface eth1/10", "node 301 deployed policy of this EPG",
    ]
    assert cands["faults"][0]["active"] is True and cands["faults"][1]["active"] is False
    assert cands["scanTruncated"] == {EPG_DN: False, node: False}
    assert "not cause" in cands["note"]


def test_active_major_candidate_raises_only_a_medium_candidate_finding():
    apic = sample_apic()
    node = "topology/pod-20/node-301"
    apic.add_subtree_rows(node, [_fault(f"{node}/sys/phys-[eth1/10]/fault-F1", "major")])
    ep = _one(_trace(apic))
    (finding,) = ep["findings"]
    assert finding["severity"] == "medium" and "Candidates" in finding["cause"]


def test_node_fault_scan_truncation_is_measured():
    apic = sample_apic()
    node = "topology/pod-20/node-301"
    apic.add_subtree_rows(node, [_fault(f"{node}/sys/x-{i}/fault-F9") for i in range(3)])
    old = tr.NODE_FAULT_SCAN
    tr.NODE_FAULT_SCAN = 2
    try:
        cands = _one(_trace(apic))["relatedFaultCandidates"]
    finally:
        tr.NODE_FAULT_SCAN = old
    assert cands["scanTruncated"][node] is True


def test_segment_for_builds_the_dn_from_validated_names():
    conn = FakeConn(sample_apic())
    out = seg.segment_for(conn, "tenant-A", "app-A", "epg-A")
    assert out["segmentResolved"] is True and out["epg"]["dn"] == EPG_DN
    try:
        seg.segment_for(conn, "tenant-A", "../x", "epg-A")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_epg_read_asks_only_for_the_relation_children():
    apic = sample_apic()
    _trace(apic)
    epg_calls = [p for path, p in apic.calls if path == f"/api/mo/{EPG_DN}.json"
                 and p.get("query-target") == "self"]
    assert epg_calls[0]["rsp-subtree-class"] == "fvRsBd,fvRsProv,fvRsCons,fvRsConsIf,fvRsProtBy"


def test_fixture_is_not_mutated_by_a_trace():
    before = copy.deepcopy(load("01-endpoint-excerpt"))
    _trace()
    assert load("01-endpoint-excerpt") == before


def test_deepest_trace_field_survives_output_sanitisation():
    # The read-boundary sanitiser replaces anything past its depth bound with
    # None. At the old bound (8) this exact field — a contract filter row —
    # came back as null, i.e. a permit rule rendered as no rule.
    ep = _one(_trace())
    flt = ep["contracts"]["contracts"][0]["subjects"][0]["filters"][0]
    assert flt is not None and flt["name"] == "default"


# ── review findings (2026-09-21) ────────────────────────────────────────────


class _HtmlOkConn(FakeConn):
    """A proxy/login page answering 200 — the connection folds it into {}."""

    def get(self, path, params=None):
        return {}


def test_a_2xx_that_is_not_an_apic_response_is_an_error_not_a_clean_fabric():
    from fabric_aiops.connection import FabricApiError as Err
    from fabric_aiops.ops import aci_faults as af

    conn = _HtmlOkConn(sample_apic())
    for call in (lambda: af.list_faults(conn), lambda: tr.endpoint_trace(conn, mac=MAC),
                 lambda: seg.segment_for(conn, "tenant-A", "app-A", "epg-A")):
        try:
            out = call()
        except Err as exc:
            assert "imdata" in str(exc)
        else:
            # segment_for records the failed step rather than raising
            assert out["segmentResolved"] is None and out["errors"], out


def test_all_fault_scans_failing_is_unknown_not_no_faults():
    apic = sample_apic()
    apic.fail[f"/api/mo/{EPG_DN}.json"] = FabricApiError("boom", status_code=500)
    apic.fail["/api/mo/topology/pod-20/node-301.json"] = FabricApiError("boom", status_code=500)
    # the EPG MO read also fails, so give the segment its EPG through a fresh path
    ep = _one(_trace(apic))
    cands = ep["relatedFaultCandidates"]
    assert cands["faults"] is None and cands["returned"] is None
    assert set(cands["scansFailed"]) == {EPG_DN, "topology/pod-20/node-301"}


def test_one_failed_scan_marks_candidates_incomplete():
    apic = sample_apic()
    apic.fail["/api/mo/topology/pod-20/node-301.json"] = FabricApiError("boom", status_code=500)
    cands = _one(_trace(apic))["relatedFaultCandidates"]
    assert cands["faults"] == [] and cands["complete"] is False
    assert cands["scansFailed"] == ["topology/pod-20/node-301"]


def test_failed_contract_read_is_a_placeholder_and_does_not_undecide_the_chain():
    apic = sample_apic()
    apic.fail["/api/mo/uni/tn-common/brc-contract-A.json"] = FabricApiError("x", status_code=500)
    ep = _one(_trace(apic))
    contracts = ep["contracts"]
    assert contracts["contracts"] == [{"dn": "uni/tn-common/brc-contract-A", "read": False}]
    assert contracts["returned"] == 1 and contracts["complete"] is False
    assert ep["segmentResolved"] is True  # EPG → BD → VRF did resolve
    assert ep["errors"][0]["step"] == "contract"


def test_non_epg_parent_is_reported_as_unsupported_not_missing():
    apic = sample_apic()
    apic.mos[EPG_DN] = {"l2extInstP": {"attributes": {"dn": EPG_DN, "name": "ext"}}}
    ep = _one(_trace(apic))
    assert ep["epg"] == {"dn": EPG_DN, "exists": True, "class": "l2extInstP", "supported": False}
    assert ep["segmentResolved"] is None and ep["staticPathResolved"] is None
    assert not any("returned no EPG" in f["signal"] for f in ep["findings"])


def test_empty_epg_answer_says_missing_or_not_visible():
    apic = sample_apic()
    del apic.mos[EPG_DN]
    ep = _one(_trace(apic))
    assert "cannot see it" in ep["findings"][0]["cause"]


def test_unparsed_attachment_is_listed_as_unscanned():
    apic = sample_apic()
    rel = apic.class_rows["fvCEp"][0]["fvCEp"]["children"][0]["fvRsCEpToPathEp"]["attributes"]
    rel["tDn"] = "topology/pod-20/node-301/sys/tunnel-[tunnel5]"
    cands = _one(_trace(apic))["relatedFaultCandidates"]
    assert cands["unscannedAttachments"] == ["topology/pod-20/node-301/sys/tunnel-[tunnel5]"]
    assert cands["complete"] is False


def test_unknown_activity_sorts_between_active_and_cleared():
    apic = sample_apic()
    node = "topology/pod-20/node-301"
    unknown = _fault(f"{node}/sys/phys-[eth1/10]/fault-F7")
    del unknown["faultInst"]["attributes"]["severity"]
    apic.add_subtree_rows(node, [
        _fault(f"{node}/sys/phys-[eth1/10]/fault-F5", "cleared"),
        unknown,
        _fault(f"{node}/sys/phys-[eth1/10]/fault-F6", "warning"),
    ])
    faults = _one(_trace(apic))["relatedFaultCandidates"]["faults"]
    assert [f["active"] for f in faults] == [True, None, False]


def test_ip_lookup_with_a_failed_endpoint_read_cannot_claim_not_truncated():
    apic = sample_apic()
    cep_dn = f"{EPG_DN}/cep-{MAC}"
    apic.fail[f"/api/mo/{cep_dn}.json"] = FabricApiError("boom", status_code=500)
    out = _trace(apic, mac=None, ip="192.0.2.200")
    assert out["endpoints"] == [] and out["truncated"] is None
    assert out["lookupErrors"] and "notFound" not in out
