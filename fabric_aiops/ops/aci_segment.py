"""ACI segment reads: EPG → bridge domain → VRF, plus the EPG's contracts.

Every link is followed through the relation APIC itself resolved — the
``tDn`` of ``fvRsBd`` / ``fvRsCtx`` / ``fvRsProv`` / ``fvRsCons`` /
``vzRsSubjFiltAtt`` — never rebuilt from a name. That is what makes
cross-tenant references work: a real EPG in ``tenant-A`` consumes a contract
in ``common`` whose subject points at ``common``'s ``default`` filter, and
only the ``tDn`` says so. A relation that is not ``formed`` is reported with
its state, never silently followed or dropped.

Query shapes (all read-only GETs):
  * EPG / BD: ``/api/mo/<dn>.json?rsp-subtree=children&rsp-subtree-class=…``
    (acitoolkit uses this exact form on an EPG) — a real EPG had 911 children,
    506 of them endpoints, so the class list is not optional.
  * VRF: ``/api/mo/<dn>.json?query-target=self``.
  * Contract: ``?query-target=subtree&target-subtree-class=…`` (flat rows).
  * Filter entries: ``?query-target=subtree&target-subtree-class=vzEntry``
    (the query from the real sample; it returns entries, not the vzFilter).

A step that fails is recorded in ``errors`` and its section is ``None`` — a
read that did not happen is never shown as an empty configuration.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fabric_aiops.connection import FabricApiError
from fabric_aiops.ops import aci_mit as mit
from fabric_aiops.ops._util import clean
from fabric_aiops.ops.aci_common import aci_get, require_aci

DEFAULT_CONTRACT_LIMIT = 10
DEFAULT_FILTER_LIMIT = 20
DEFAULT_ENTRY_LIMIT = 50

_EPG_RELATIONS = ("fvRsBd", "fvRsProv", "fvRsCons", "fvRsConsIf", "fvRsProtBy")
_CONTRACT_CLASSES = (
    "vzBrCP", "vzSubj", "vzRsSubjFiltAtt", "vzInTerm", "vzOutTerm", "vzRsFiltAtt",
)
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2, "info": 3}
_CHAIN_STEPS = ("epg", "bd", "vrf")
_NOT_RETURNED = (
    "APIC returned an empty list for the {what}: it does not exist, or this "
    "account's security domain cannot see it — APIC answers both the same way."
)

NOT_CHECKED = (
    "vzAny contracts on the VRF (they apply to every EPG in it)",
    "taboo contracts (fvRsProtBy relations are listed, not expanded)",
    "imported contract interfaces (fvRsConsIf relations are listed, not expanded)",
    "preferred-group peers (only this EPG's own prefGrMemb flag is read)",
    "service graphs and L3Out external EPGs",
)


class Findings:
    """Collects ranked findings and failed steps for one reconstruction."""

    def __init__(self) -> None:
        self.findings: list[dict] = []
        self.errors: list[dict] = []

    def add(self, severity: str, signal: str, cause: str, action: str) -> None:
        self.findings.append(
            {"severity": severity, "signal": signal, "cause": cause, "action": action}
        )

    def step(self, name: str, dn: str | None, fn: Callable[[], Any]) -> Any:
        """Run one read; on failure record it and return None (not empty)."""
        try:
            return fn()
        except (FabricApiError, ValueError) as exc:
            self.errors.append({"step": name, "dn": dn, "error": str(exc)[:400]})
            return None

    def ranked(self) -> list[dict]:
        ordered = sorted(
            self.findings, key=lambda f: _SEVERITY_RANK.get(f["severity"], 9)
        )
        return [{**f, "rank": i} for i, f in enumerate(ordered, start=1)]


def _read_mo(conn: Any, dn: str, params: dict[str, str]) -> list[dict]:
    return mit.flatten(aci_get(conn, mit.mo_path(dn), params))


def _named_relation(obj: dict, name_attr: str) -> dict:
    rel = mit.relation(obj)
    rel["name"] = mit.opt(obj["attrs"], name_attr)
    return rel


# ─── EPG ────────────────────────────────────────────────────────────────────


def _read_epg(conn: Any, epg_dn: str, f: Findings) -> dict | None:
    objs = f.step("epg", epg_dn, lambda: _read_mo(conn, epg_dn, {
        "query-target": "self",
        "rsp-subtree": "children",
        "rsp-subtree-class": ",".join(_EPG_RELATIONS),
    }))
    if objs is None:
        return None
    roots = [o for o in mit.of_class(objs, "fvAEPg") if o["dn"] == epg_dn]
    if not roots:
        other = next((o["class"] for o in objs if o["dn"] == epg_dn), None)
        if other:
            f.add("info", f"parent object is {other}, not an application EPG",
                  "This endpoint belongs to a kind of EPG the segment trace does not "
                  "follow yet (e.g. an L2Out external EPG).",
                  "Inspect the object in APIC; open an issue if you need it traced.")
            return {"dn": epg_dn, "exists": True, "class": other, "supported": False}
        f.add("high", f"APIC returned no EPG at {epg_dn}",
              _NOT_RETURNED.format(what="EPG"),
              "Check the tenant / application profile / EPG names and the account's "
              "security domains.")
        return {"dn": epg_dn, "exists": False}
    a = roots[0]["attrs"]
    rels = mit.children_of(objs, epg_dn, *_EPG_RELATIONS)
    epg = {
        "dn": epg_dn,
        "exists": True,
        "name": mit.opt(a, "name"),
        "tenant": mit.tenant_of(epg_dn),
        "app": mit.rn_value(epg_dn, "ap-"),
        "configState": mit.opt(a, "configSt"),
        "configIssues": mit.opt(a, "configIssues"),
        "shutdown": mit.opt(a, "shutdown"),
        "intraEpgIsolation": mit.opt(a, "pcEnfPref"),
        "preferredGroupMember": mit.opt(a, "prefGrMemb"),
        "pcTag": mit.opt(a, "pcTag"),
        "bdRelation": next(
            (_named_relation(o, "tnFvBDName") for o in rels if o["class"] == "fvRsBd"), None
        ),
        "provides": [_named_relation(o, "tnVzBrCPName") for o in rels if o["class"] == "fvRsProv"],
        "consumes": [_named_relation(o, "tnVzBrCPName") for o in rels if o["class"] == "fvRsCons"],
        "consumesImported": [
            _named_relation(o, "tnVzCPIfName") for o in rels if o["class"] == "fvRsConsIf"
        ],
        "tabooRelations": [
            _named_relation(o, "tnVzTabooName") for o in rels if o["class"] == "fvRsProtBy"
        ],
    }
    if epg["shutdown"] == "yes":
        f.add("high", "EPG shutdown = yes", "The EPG is administratively shut down.",
              "Confirm the shutdown is intended; no endpoint in it forwards traffic.")
    if epg["configState"] not in (None, "applied"):
        f.add("medium", f"EPG configSt = {epg['configState']}",
              "APIC has not finished applying this EPG's configuration.",
              "Recheck after the policy converges; inspect faults on the EPG if it persists.")
    if epg["configIssues"]:
        f.add("medium", f"EPG configIssues = {epg['configIssues']}",
              "APIC flagged a configuration problem on the EPG.",
              "Resolve the named issue on the EPG in APIC.")
    return epg


# ─── BD / VRF ───────────────────────────────────────────────────────────────


def _unresolved(f: Findings, what: str, rel: dict | None, owner: str) -> None:
    if rel is None:
        f.add("high", f"{owner} has no {what} relation",
              f"APIC returned no {what} relation for {owner}.",
              f"Associate a {what} with it in APIC.")
    else:
        f.add("high",
              f"{owner} → {what} relation state = {rel.get('state')} "
              f"(target {rel.get('name')!r})",
              f"The {what} the configuration names did not resolve, so the "
              f"segment chain stops here.",
              f"Check that the {what} named {rel.get('name')!r} exists in the tenant "
              f"or in 'common'.")


def _read_bd(conn: Any, bd_dn: str, f: Findings) -> dict | None:
    objs = f.step("bd", bd_dn, lambda: _read_mo(conn, bd_dn, {
        "query-target": "self",
        "rsp-subtree": "children",
        "rsp-subtree-class": "fvRsCtx,fvSubnet",
    }))
    if objs is None:
        return None
    roots = [o for o in mit.of_class(objs, "fvBD") if o["dn"] == bd_dn]
    if not roots:
        f.add("high", f"APIC returned no bridge domain at {bd_dn}",
              _NOT_RETURNED.format(what="bridge domain the EPG relation points at"),
              "Check the BD in the tenant (or in 'common') and the account's security domains.")
        return {"dn": bd_dn, "exists": False}
    a = roots[0]["attrs"]
    ctx = mit.children_of(objs, bd_dn, "fvRsCtx")
    bd = {
        "dn": bd_dn,
        "exists": True,
        "name": mit.opt(a, "name"),
        "tenant": mit.tenant_of(bd_dn),
        "unicastRoute": mit.opt(a, "unicastRoute"),
        "arpFlood": mit.opt(a, "arpFlood"),
        "unknownUnicast": mit.opt(a, "unkMacUcastAct"),
        "ipLearning": mit.opt(a, "ipLearning"),
        "limitIpLearnToSubnets": mit.opt(a, "limitIpLearnToSubnets"),
        "subnets": [
            {
                "ip": mit.opt(o["attrs"], "ip"),
                "scope": mit.opt(o["attrs"], "scope"),
                "preferred": mit.opt(o["attrs"], "preferred"),
                "virtual": mit.opt(o["attrs"], "virtual"),
            }
            for o in mit.children_of(objs, bd_dn, "fvSubnet")
        ],
        "vrfRelation": _named_relation(ctx[0], "tnFvCtxName") if ctx else None,
    }
    if bd["unicastRoute"] == "no":
        f.add("info", "BD unicastRoute = no",
              "The BD is layer-2 only: the fabric does not route for it.",
              "Expected for L2 segments; if the endpoint needs routing, its "
              "gateway is outside ACI.")
    return bd


def _read_vrf(conn: Any, vrf_dn: str, f: Findings) -> dict | None:
    objs = f.step("vrf", vrf_dn, lambda: _read_mo(conn, vrf_dn, {"query-target": "self"}))
    if objs is None:
        return None
    roots = [o for o in mit.of_class(objs, "fvCtx") if o["dn"] == vrf_dn]
    if not roots:
        f.add("high", f"APIC returned no VRF at {vrf_dn}",
              _NOT_RETURNED.format(what="VRF the BD relation points at"),
              "Check the VRF in the tenant (or in 'common') and the account's security domains.")
        return {"dn": vrf_dn, "exists": False}
    a = roots[0]["attrs"]
    vrf = {
        "dn": vrf_dn,
        "exists": True,
        "name": mit.opt(a, "name"),
        "tenant": mit.tenant_of(vrf_dn),
        "policyEnforcement": mit.opt(a, "pcEnfPref"),
        "enforcementDirection": mit.opt(a, "pcEnfDir"),
        "bdEnforced": mit.opt(a, "bdEnforcedEnable"),
    }
    if vrf["policyEnforcement"] == "unenforced":
        f.add("info", "VRF pcEnfPref = unenforced",
              "Contracts are not enforced in this VRF: EPGs in it talk freely.",
              "Contract findings below do not restrict traffic in this VRF.")
    return vrf


# ─── contracts ──────────────────────────────────────────────────────────────


def _filter_relation(obj: dict, direction: str) -> dict:
    rel = _named_relation(obj, "tnVzFilterName")
    a = obj["attrs"]
    rel.update({
        "direction": direction,
        "action": mit.opt(a, "action"),
        "directives": mit.opt(a, "directives"),
        "priorityOverride": mit.opt(a, "priorityOverride"),
    })
    return rel


def _read_contract(conn: Any, dn: str, f: Findings) -> dict | None:
    objs = f.step("contract", dn, lambda: _read_mo(conn, dn, {
        "query-target": "subtree",
        "target-subtree-class": ",".join(_CONTRACT_CLASSES),
    }))
    if objs is None:
        return None
    roots = [o for o in mit.of_class(objs, "vzBrCP") if o["dn"] == dn]
    if not roots:
        f.add("medium", f"APIC returned no contract at {dn}",
              _NOT_RETURNED.format(what="contract the EPG relation points at"),
              "Its rules are unknown — check the contract and the account's security domains.")
    a = roots[0]["attrs"] if roots else {}
    subjects = []
    for subj in mit.of_class(objs, "vzSubj"):
        filters = [_filter_relation(o, "both")
                   for o in mit.children_of(objs, subj["dn"], "vzRsSubjFiltAtt")]
        for term_cls, direction in (("vzInTerm", "consumer-to-provider"),
                                    ("vzOutTerm", "provider-to-consumer")):
            for term in mit.children_of(objs, subj["dn"], term_cls):
                filters += [_filter_relation(o, direction)
                            for o in mit.children_of(objs, term["dn"], "vzRsFiltAtt")]
        subjects.append({
            "name": mit.opt(subj["attrs"], "name"),
            "dn": subj["dn"],
            "reverseFilterPorts": mit.opt(subj["attrs"], "revFltPorts"),
            "filters": filters,
        })
    return {
        "dn": dn,
        "exists": bool(roots),
        "name": mit.opt(a, "name"),
        "tenant": mit.tenant_of(dn),
        "scope": mit.opt(a, "scope"),
        "subjects": subjects,
    }


def _entry_row(obj: dict) -> dict:
    a = obj["attrs"]
    return {
        "name": mit.opt(a, "name"),
        "etherType": mit.opt(a, "etherT"),
        "protocol": mit.opt(a, "prot"),
        "srcFromPort": mit.opt(a, "sFromPort"),
        "srcToPort": mit.opt(a, "sToPort"),
        "dstFromPort": mit.opt(a, "dFromPort"),
        "dstToPort": mit.opt(a, "dToPort"),
        "stateful": mit.opt(a, "stateful"),
        "applyToFragments": mit.opt(a, "applyToFrag"),
        "tcpRules": mit.opt(a, "tcpRules"),
    }


def _read_filter(conn: Any, dn: str, entry_limit: int, f: Findings) -> dict:
    # vzFilter is asked for alongside its entries: a subtree query returns the
    # queried object itself (the real contract-subtree response carried its own
    # vzBrCP), so a missing vzFilter row separates "filter not returned" from
    # "filter with no entries" — otherwise both read as a filter with no rules.
    objs = f.step("filter", dn, lambda: _read_mo(conn, dn, {
        "query-target": "subtree",
        "target-subtree-class": "vzFilter,vzEntry",
    }))
    if objs is None:
        return {"dn": dn, "entries": None}
    if not any(o["dn"] == dn for o in mit.of_class(objs, "vzFilter")):
        f.add("medium", f"APIC returned no filter at {dn}",
              _NOT_RETURNED.format(what="filter a contract subject points at"),
              "Its rules are unknown — check the filter and the account's security domains.")
        return {"dn": dn, "exists": False, "entries": None}
    entries = [_entry_row(o) for o in mit.of_class(objs, "vzEntry")]
    return {
        "dn": dn,
        "exists": True,
        "name": mit.rn_value(dn, "flt-"),
        "tenant": mit.tenant_of(dn),
        "entries": entries[:entry_limit],
        "returned": min(len(entries), entry_limit),
        "limit": entry_limit,
        "truncated": len(entries) > entry_limit,
    }


def _distinct(dns: list[str | None]) -> list[str]:
    seen: list[str] = []
    for dn in dns:
        if dn and dn not in seen:
            seen.append(dn)
    return seen


def _read_contracts(conn: Any, epg: dict, limits: dict, f: Findings) -> dict:
    relations = epg["provides"] + epg["consumes"]
    for rel in relations:
        if not mit.relation_formed(rel):
            f.add("medium", f"EPG {rel['class']} → contract {rel.get('name')!r} "
                            f"state = {rel.get('state')}",
                  "The EPG names a contract APIC could not resolve, so it applies no rules.",
                  "Check that the contract exists in the tenant or in 'common'.")
    dns = _distinct([r["tDn"] for r in relations if mit.relation_formed(r)])
    kept = dns[: limits["contracts"]]
    # A contract whose read failed stays in the list as a placeholder, so the
    # list, 'returned' and the step error all agree — it is never just absent.
    contracts = [_read_contract(conn, dn, f) or {"dn": dn, "read": False} for dn in kept]
    filter_rels = [flt for c in contracts for s in c.get("subjects", []) for flt in s["filters"]]
    for rel in filter_rels:
        if not mit.relation_formed(rel):
            f.add("medium", f"contract filter {rel.get('name')!r} state = {rel.get('state')}",
                  "A contract subject names a filter APIC could not resolve.",
                  "Check that the filter exists in the contract's tenant or in 'common'.")
    filter_dns = _distinct([r["tDn"] for r in filter_rels if mit.relation_formed(r)])
    filters = [_read_filter(conn, dn, limits["entries"], f)
               for dn in filter_dns[: limits["filters"]]]
    unread = any(
        c.get("read") is False or c.get("exists") is False for c in contracts
    ) or any(
        flt.get("entries") is None for flt in filters
    )
    return {
        "contracts": contracts,
        "returned": len(kept),
        "limit": limits["contracts"],
        "truncated": len(dns) > limits["contracts"],
        "filters": filters,
        "filtersTruncated": len(filter_dns) > limits["filters"],
        "complete": False if unread else True,
    }


# ─── assembly ───────────────────────────────────────────────────────────────


def build_segment(conn: Any, epg_dn: str, limits: dict, f: Findings) -> dict:
    """Follow EPG → BD → VRF and the EPG's contracts; findings go into ``f``."""
    epg = _read_epg(conn, epg_dn, f)
    bd = vrf = contracts = None
    if epg and epg.get("exists") and epg.get("supported", True):
        bd_rel = epg["bdRelation"]
        if bd_rel and mit.relation_formed(bd_rel):
            bd = _read_bd(conn, bd_rel["tDn"], f)
        else:
            _unresolved(f, "bridge domain", bd_rel, "EPG")
        if bd and bd.get("exists"):
            vrf_rel = bd["vrfRelation"]
            if vrf_rel and mit.relation_formed(vrf_rel):
                vrf = _read_vrf(conn, vrf_rel["tDn"], f)
            else:
                _unresolved(f, "VRF", vrf_rel, "bridge domain")
        contracts = _read_contracts(conn, epg, limits, f)
        _contract_posture(epg, vrf, f)
    # Resolution is about the EPG → BD → VRF chain only: a failed contract or
    # filter read is reported by contracts.complete, not by un-deciding the chain.
    chain_unread = any(e["step"] in _CHAIN_STEPS for e in f.errors)
    unsupported = bool(epg and epg.get("supported") is False)
    complete = (
        None if chain_unread or unsupported else
        bool(epg and epg.get("exists") and bd and bd.get("exists") and vrf and vrf.get("exists"))
    )
    return {
        "epg": epg,
        "bd": bd,
        "vrf": vrf,
        "contracts": contracts,
        "segmentResolved": complete,
        "notChecked": list(NOT_CHECKED),
    }


def _contract_posture(epg: dict, vrf: dict | None, f: Findings) -> None:
    if epg["provides"] or epg["consumes"] or epg["consumesImported"]:
        return
    if vrf and vrf.get("policyEnforcement") == "unenforced":
        return
    f.add("low", "EPG provides and consumes no contract",
          "In an enforced VRF, traffic between this EPG and other EPGs is denied "
          "unless vzAny or a preferred group allows it — neither is checked here.",
          "If the endpoint cannot reach peers in other EPGs, check the VRF's vzAny "
          "contracts and preferred-group membership.")


def segment_for(
    conn: Any,
    tenant: str,
    app: str,
    epg: str,
    contract_limit: int = DEFAULT_CONTRACT_LIMIT,
    entry_limit: int = DEFAULT_ENTRY_LIMIT,
) -> dict:
    """[READ] EPG → BD → VRF and the EPG's contracts, from ACI names."""
    require_aci(conn)
    epg_dn = (
        f"uni/tn-{mit.validate_name('tenant', tenant)}"
        f"/ap-{mit.validate_name('application profile', app)}"
        f"/epg-{mit.validate_name('EPG', epg)}"
    )
    f = Findings()
    segment = build_segment(conn, epg_dn, limits_for(contract_limit, entry_limit), f)
    return clean({**segment, "findings": f.ranked(), "errors": f.errors})


def limits_for(contract_limit: int, entry_limit: int) -> dict:
    return {
        "contracts": max(1, min(int(contract_limit), 50)),
        "filters": DEFAULT_FILTER_LIMIT,
        "entries": max(1, min(int(entry_limit), 500)),
    }
