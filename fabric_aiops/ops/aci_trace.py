"""ACI endpoint path reconstruction — the static path, read-only.

Given a MAC or an IP, rebuild what APIC knows about that endpoint: where it
attaches (pod / leaf / interface or vPC), which EPG / bridge domain / VRF it
lives in, which contracts that EPG provides and consumes, and which faults
name the EPG or the attachment interface. Nothing is written and no
troubleshooting session is created on the APIC.

What this is **not**, stated in every result: a reachability test. It reads
configuration and learned state. A complete chain says the fabric is
configured to carry the endpoint, not that a packet got through.

Rules the real samples (Fabric-AIops #1) forced:
  * the attachment verdict comes from ``fvRsCEpToPathEp`` (``state:
    formed``) only. The same endpoint's tag relations (``fvRsToEpIpTag`` /
    ``fvRsToEpMacTag``) were ``missing-target`` while its path was formed —
    a missing tag is a policy-tag lookup, not a failed attachment, so tags
    are listed apart and never produce a path finding;
  * ``tCl`` is reported as APIC sent it (``fabricAPathEp``), not assumed;
  * faults are matched by DN and presented as **candidates**. The real fault
    samples had no relationship to the chosen endpoint — being near an
    endpoint in the tree is not being its cause.
"""

from __future__ import annotations

from typing import Any

from fabric_aiops.ops import aci_mit as mit
from fabric_aiops.ops._util import clean
from fabric_aiops.ops.aci_common import aci_get, require_aci
from fabric_aiops.ops.aci_faults import SEVERITY_SORT, fault_row
from fabric_aiops.ops.aci_segment import (
    DEFAULT_CONTRACT_LIMIT,
    DEFAULT_ENTRY_LIMIT,
    Findings,
    build_segment,
    limits_for,
)

DEFAULT_ENDPOINT_LIMIT = 5
DEFAULT_FAULT_LIMIT = 20
#: Faults read per leaf when looking for ones that name the interface/EPG.
NODE_FAULT_SCAN = 500

_TAG_RELATIONS = ("fvRsToEpIpTag", "fvRsToEpMacTag")
_TAG_NOTE = (
    "Tag relations resolve endpoint policy tags. 'missing-target' here means no "
    "tag object matches this endpoint; it says nothing about the attachment path."
)
_TRACE_NOTE = (
    "Static reconstruction from APIC configuration and learned state. It is not "
    "a reachability test: a resolved chain means the fabric is configured to "
    "carry this endpoint, not that traffic flows."
)


# ─── lookup ─────────────────────────────────────────────────────────────────


def _by_mac(conn: Any, mac: str, page: int) -> tuple[list[dict], list[dict], int | None]:
    payload = aci_get(conn, mit.class_path("fvCEp"), {
        "query-target": "self",
        "query-target-filter": mit.eq_filter("fvCEp", "mac", mac),
        "rsp-subtree": "full",
        "page": "0",
        "page-size": str(page),
    })
    objs = mit.flatten(payload)
    roots = [o for o in objs if o["class"] == "fvCEp" and o["parentDn"] is None]
    return roots, objs, mit.total_count(payload)


def _by_ip(conn: Any, ip: str, page: int, f: Findings) -> tuple[list[dict], list[dict], int | None]:
    """IP → owning endpoint(s): ``fvIp`` rows name their parent ``fvCEp`` by DN.

    Built from the documented class query and the ``fvIp.addr`` attribute the
    real sample carried; the IP-keyed response itself has not been captured.
    """
    payload = aci_get(conn, mit.class_path("fvIp"), {
        "query-target-filter": mit.eq_filter("fvIp", "addr", ip),
        "page": "0",
        "page-size": str(page),
    })
    parents: list[str] = []
    for obj in mit.of_class(mit.flatten(payload), "fvIp"):
        parent = mit.parent_dn(obj["dn"]) if obj["dn"] else None
        if parent and parent not in parents:
            parents.append(parent)
    roots: list[dict] = []
    objs: list[dict] = []
    for dn in parents:
        got = f.step("endpoint", dn, lambda dn=dn: mit.flatten(aci_get(
            conn, mit.mo_path(dn), {"query-target": "self", "rsp-subtree": "full"})))
        if got is None:
            continue
        objs.extend(got)
        roots.extend(o for o in got if o["class"] == "fvCEp" and o["dn"] == dn)
    return roots, objs, mit.total_count(payload)


# ─── one endpoint ───────────────────────────────────────────────────────────


def _attachment(objs: list[dict], cep_dn: str) -> list[dict]:
    rows = []
    for obj in mit.children_of(objs, cep_dn, "fvRsCEpToPathEp"):
        rel = mit.relation(obj)
        rows.append({**rel, **mit.parse_path_dn(rel["tDn"] or ""),
                     "learnedBy": mit.opt(obj["attrs"], "lcC")})
    return rows


def _ips(objs: list[dict], cep_dn: str) -> list[dict]:
    out = []
    for obj in mit.children_of(objs, cep_dn, "fvIp"):
        nodes = [mit.opt(n["attrs"], "id")
                 for n in mit.children_of(objs, obj["dn"], "fvReportingNode")]
        out.append({
            "addr": mit.opt(obj["attrs"], "addr"),
            "fabricPathDn": mit.opt(obj["attrs"], "fabricPathDn"),
            "reportingNodes": nodes,
        })
    return out


def _tags(objs: list[dict], cep_dn: str) -> list[dict]:
    owners = [cep_dn] + [o["dn"] for o in mit.children_of(objs, cep_dn, "fvIp")]
    return [mit.relation(o) for dn in owners
            for o in mit.children_of(objs, dn, *_TAG_RELATIONS)]


def _path_findings(paths: list[dict], f: Findings) -> None:
    if not paths:
        f.add("medium", "No fvRsCEpToPathEp relation on the endpoint",
              "APIC records no local fabric attachment for this endpoint — it may be "
              "learned remotely or be ageing out — so the physical path cannot be "
              "reconstructed from this object.",
              "Look the endpoint up again later, or check the leaf that reports it.")
        return
    if not any(mit.relation_formed(p) for p in paths):
        states = ", ".join(sorted({str(p.get("state")) for p in paths}))
        f.add("high", f"Endpoint attachment relation state = {states}",
              "APIC holds an attachment relation for the endpoint but it is not formed.",
              "Check the interface / vPC named in 'attachment' and its policy on the leaf.")


def _cross_check(cep: dict, paths: list[dict], segment: dict, f: Findings) -> None:
    """Flag disagreement between the endpoint's own DNs and the resolved chain."""
    pairs = (
        ("bridge domain", cep["bdDn"], (segment.get("bd") or {}).get("dn")),
        ("VRF", cep["vrfDn"], (segment.get("vrf") or {}).get("dn")),
    )
    for what, own, resolved in pairs:
        if own and resolved and own != resolved:
            f.add("medium", f"endpoint {what} {own} ≠ EPG chain {what} {resolved}",
                  f"The endpoint's recorded {what} differs from the one its EPG resolves to.",
                  "The endpoint may have been learned before a BD/VRF change; re-check "
                  "after it is re-learned.")
    formed = {p["tDn"] for p in paths if mit.relation_formed(p)}
    if cep["fabricPathDn"] and formed and cep["fabricPathDn"] not in formed:
        f.add("medium", f"fabricPathDn {cep['fabricPathDn']} is not a formed attachment",
              "The endpoint's recorded path differs from its formed attachment relation.",
              "Treat the attachment relation as current; the endpoint may have moved.")


# ─── related faults ─────────────────────────────────────────────────────────


def _scan(conn: Any, dn: str, size: int, f: Findings) -> tuple[list[dict], bool] | None:
    payload = f.step("faults", dn, lambda: aci_get(conn, mit.mo_path(dn), {
        "query-target": "subtree",
        "target-subtree-class": "faultInst",
        "page": "0",
        "page-size": str(size + 1),
    }))
    if payload is None:
        return None
    rows = [fault_row(o) for o in mit.of_class(mit.flatten(payload), "faultInst")]
    return rows[:size], len(rows) > size


def _related_faults(conn: Any, epg_dn: str, paths: list[dict], limit: int, f: Findings) -> dict:
    matched: list[dict] = []
    scans: dict[str, bool | None] = {}
    unscanned = [p["tDn"] for p in paths if mit.relation_formed(p) and not p["nodes"]]
    got = _scan(conn, epg_dn, NODE_FAULT_SCAN, f)
    scans[epg_dn] = None if got is None else got[1]
    for row in (got or ([], False))[0]:
        matched.append({**row, "matchedBy": "fault is on the EPG or an object under it"})
    for p in (p for p in paths if mit.relation_formed(p)):
        iface = p["endpoint"] if p["kind"] == "interface" else None
        for node in p["nodes"]:
            node_dn = f"topology/pod-{p['pod']}/node-{node}"
            got = _scan(conn, node_dn, NODE_FAULT_SCAN, f)
            scans[node_dn] = None if got is None else got[1]
            for row in (got or ([], False))[0]:
                dn = row["dn"] or ""
                if iface and f"phys-[{iface}]" in dn:
                    matched.append({**row, "matchedBy": f"node {node} interface {iface}"})
                elif row.get("aboutDn") == epg_dn:
                    matched.append({**row, "matchedBy": f"node {node} deployed policy of this EPG"})
    # Newest first, then (stable) active, unknown, cleared; worst severity first.
    matched.sort(key=lambda r: r.get("lastTransition") or "", reverse=True)
    matched.sort(key=lambda r: (
        {True: 0, None: 1, False: 2}[r.get("active")],
        SEVERITY_SORT.get(r.get("severity") or "", 9),
    ))
    failed = [dn for dn, cut in scans.items() if cut is None]
    if scans and len(failed) == len(scans):
        # Nothing was read: say so, never an empty list that reads as "no faults".
        return {
            "faults": None, "returned": None, "limit": limit, "truncated": None,
            "scanTruncated": scans, "scansFailed": failed, "unscannedAttachments": unscanned,
            "note": "No fault scan succeeded — see 'errors'. Related faults are unknown.",
        }
    kept = matched[:limit]
    active = [r for r in kept if r.get("active") and r.get("severity") in ("critical", "major")]
    if active:
        f.add("medium", f"{len(active)} active critical/major fault(s) name this "
                        f"endpoint's EPG or interface",
              "Candidates matched by DN only; none is shown to cause a problem for "
              "this endpoint.",
              "Read each candidate's description and changeSet before acting.")
    return {
        "faults": kept,
        "returned": len(kept),
        "limit": limit,
        "truncated": len(matched) > limit,
        "scanTruncated": scans,
        "scansFailed": failed,
        "unscannedAttachments": unscanned,
        "complete": not failed and not unscanned and not any(scans.values()),
        "note": (
            "Matched by DN: the fault sits on this EPG, on the attachment "
            "interface, or on the leaf's deployed copy of this EPG's policy. A "
            "match is proximity, not cause. Interface faults are matched only for "
            "a single physical interface — for a vPC or port-channel, member-port "
            "faults are not matched. scanTruncated is true where a scan hit its "
            "page limit, null where it failed (listed in scansFailed); "
            "unscannedAttachments are attachment DNs whose leaves could not be "
            "parsed, so they were not scanned."
        ),
    }


def _endpoint(conn: Any, root: dict, objs: list[dict], limits: dict, fault_limit: int) -> dict:
    f = Findings()
    a, dn = root["attrs"], root["dn"]
    cep = {
        "dn": dn,
        "mac": mit.opt(a, "mac"),
        "encap": mit.opt(a, "encap"),
        "learnedBy": mit.opt(a, "lcC"),
        "bdDn": mit.opt(a, "bdDn"),
        "vrfDn": mit.opt(a, "vrfDn"),
        "fabricPathDn": mit.opt(a, "fabricPathDn"),
        "modified": mit.opt(a, "modTs"),
        "tenant": mit.tenant_of(dn),
        "app": mit.rn_value(dn, "ap-"),
        "epg": mit.rn_value(dn, "epg-"),
    }
    paths = _attachment(objs, dn)
    _path_findings(paths, f)
    epg_dn = mit.parent_dn(dn)
    segment = build_segment(conn, epg_dn, limits, f)
    _cross_check(cep, paths, segment, f)
    faults = _related_faults(conn, epg_dn, paths, fault_limit, f)
    attached = any(mit.relation_formed(p) for p in paths)
    resolved = segment["segmentResolved"]
    return {
        "endpoint": cep,
        "ips": _ips(objs, dn),
        "attachment": paths,
        "attachmentFormed": attached,
        "tagRelations": {"relations": _tags(objs, dn), "note": _TAG_NOTE},
        **segment,
        "relatedFaultCandidates": faults,
        "staticPathResolved": None if resolved is None else (attached and resolved),
        "findings": f.ranked(),
        "errors": f.errors,
    }


# ─── entry point ────────────────────────────────────────────────────────────


def endpoint_trace(
    conn: Any,
    mac: str | None = None,
    ip: str | None = None,
    limit: int = DEFAULT_ENDPOINT_LIMIT,
    fault_limit: int = DEFAULT_FAULT_LIMIT,
    contract_limit: int = DEFAULT_CONTRACT_LIMIT,
    entry_limit: int = DEFAULT_ENTRY_LIMIT,
) -> dict:
    """[READ] Reconstruct the static fabric path of an endpoint found by MAC or IP."""
    require_aci(conn)
    if bool(mac) == bool(ip):
        raise ValueError("Pass exactly one of mac or ip.")
    capped = max(1, min(int(limit), 20))
    lookup = Findings()
    if mac:
        key = {"mac": mit.normalize_mac(mac)}
        roots, objs, reported = _by_mac(conn, key["mac"], capped + 1)
    else:
        key = {"ip": mit.normalize_ip(ip or "")}
        roots, objs, reported = _by_ip(conn, key["ip"], capped + 1, lookup)
    limits = limits_for(contract_limit, entry_limit)
    fault_cap = max(1, min(int(fault_limit), 200))
    endpoints = [_endpoint(conn, r, objs, limits, fault_cap) for r in roots[:capped]]
    truncated: bool | None = len(roots) > capped
    if not truncated and lookup.errors:
        truncated = None  # an endpoint that failed to load may have been a match
    result: dict[str, Any] = {
        "query": key,
        "endpoints": endpoints,
        "returned": len(endpoints),
        "limit": capped,
        "truncated": truncated,
        "serverReportedMatches": reported,
        "lookupErrors": lookup.errors,
        "note": _TRACE_NOTE,
    }
    if not roots and not lookup.errors:
        result["notFound"] = (
            "APIC returned no endpoint with this address — the fabric has not "
            "learned it now, or it sits in a tenant this account cannot see. An "
            "endpoint that has been silent ages out of the fabric tables, so this "
            "does not mean the host is down."
        )
    return clean(result)
