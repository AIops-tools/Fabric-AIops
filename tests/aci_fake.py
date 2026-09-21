"""A fake APIC that serves the real anonymized samples with APIC semantics.

It is deliberately *not* a lookup table of canned URLs. It holds MIT objects
and answers the two query forms the ACI reads use, applying the server
behaviour the code depends on — so a test cannot pass merely because the fake
is more obliging than an APIC:

  * ``eq(<cls>.<attr>,"<v>")`` filters are evaluated (exact, case-sensitive);
  * ``page-size`` caps the top-level rows while ``totalCount`` stays the full
    match count;
  * ``rsp-subtree-class`` prunes returned children, ``target-subtree-class``
    returns only those classes as flat rows;
  * a missing MO answers 200 with an empty ``imdata`` (as APIC does);
  * any other URL raises ``KeyError`` — a strict fake, so an unexpected
    request fails the test instead of reading as "nothing there".
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fabric_aiops.connection import FabricApiError
from fabric_aiops.platform import ACI, get_platform

FIXTURES = Path(__file__).parent / "fixtures" / "aci"
_EQ = re.compile(r'^eq\((\w+)\.(\w+),"([^"]*)"\)$')


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _rows(payload: dict) -> list[dict]:
    return copy.deepcopy(payload["imdata"])


def _cls_attrs(row: dict) -> tuple[str, dict]:
    cls = next(iter(row))
    return cls, row[cls].get("attributes", {})


def _walk_flat(row: dict, parent: str | None, out: list[dict]) -> None:
    """Flatten a nested row into flat subtree rows (each with a full dn)."""
    cls, attrs = _cls_attrs(row)
    dn = attrs.get("dn") or (f"{parent}/{attrs['rn']}" if parent and attrs.get("rn") else None)
    flat_attrs = {**attrs, "dn": dn}
    out.append({cls: {"attributes": flat_attrs}})
    for child in row[cls].get("children", []) or []:
        _walk_flat(child, dn, out)


class FakeApic:
    """Holds MIT objects: class-query rows and MO trees keyed by DN."""

    def __init__(self) -> None:
        self.class_rows: dict[str, list[dict]] = {}
        self.mos: dict[str, dict] = {}  # dn -> nested row
        self.subtree_extra: dict[str, list[dict]] = {}  # dn -> extra flat rows
        self.fail: dict[str, Exception] = {}  # dn/path -> exception to raise
        self.calls: list[tuple[str, dict]] = []

    # ── population ──
    def add_class_rows(self, cls: str, rows: list[dict]) -> None:
        self.class_rows.setdefault(cls, []).extend(copy.deepcopy(rows))

    def add_mo(self, row: dict) -> None:
        _, attrs = _cls_attrs(row)
        self.mos[attrs["dn"]] = copy.deepcopy(row)

    def add_subtree_rows(self, dn: str, rows: list[dict]) -> None:
        self.subtree_extra.setdefault(dn, []).extend(copy.deepcopy(rows))

    # ── serving ──
    def get(self, path: str, params: dict | None = None) -> dict:
        params = dict(params or {})
        self.calls.append((path, params))
        if path in self.fail:
            raise self.fail[path]
        if path.startswith("/api/node/class/"):
            return self._class(path[len("/api/node/class/"):-len(".json")], params)
        if path.startswith("/api/mo/"):
            return self._mo(path[len("/api/mo/"):-len(".json")], params)
        raise KeyError(f"fake APIC has no route for {path}")

    def _class(self, cls: str, params: dict) -> dict:
        if cls not in self.class_rows:
            raise KeyError(f"fake APIC has no class {cls}")
        rows = _rows({"imdata": self.class_rows[cls]})
        flt = params.get("query-target-filter")
        if flt:
            m = _EQ.match(flt)
            assert m and m.group(1) == cls, f"unsupported filter {flt}"
            rows = [r for r in rows if _cls_attrs(r)[1].get(m.group(2)) == m.group(3)]
        if params.get("rsp-subtree") not in ("full", "children"):
            for r in rows:
                r[next(iter(r))].pop("children", None)
        return self._page(rows, params)

    def _mo(self, dn: str, params: dict) -> dict:
        if dn not in self.mos and dn not in self.subtree_extra:
            return {"totalCount": "0", "imdata": []}
        target = params.get("query-target", "self")
        if target == "subtree":
            flat: list[dict] = []
            if dn in self.mos:
                _walk_flat(self.mos[dn], None, flat)
            flat += copy.deepcopy(self.subtree_extra.get(dn, []))
            wanted = params.get("target-subtree-class")
            if wanted:
                keep = set(wanted.split(","))
                flat = [r for r in flat if next(iter(r)) in keep]
            return self._page(flat, params)
        if dn not in self.mos:
            return {"totalCount": "0", "imdata": []}
        row = copy.deepcopy(self.mos[dn])
        cls = next(iter(row))
        subtree = params.get("rsp-subtree")
        if subtree not in ("children", "full"):
            row[cls].pop("children", None)
        elif params.get("rsp-subtree-class"):
            keep = set(params["rsp-subtree-class"].split(","))
            row[cls]["children"] = [
                c for c in row[cls].get("children", []) if next(iter(c)) in keep
            ]
        return {"totalCount": "1", "imdata": [row]}

    @staticmethod
    def _page(rows: list[dict], params: dict) -> dict:
        total = len(rows)
        if "page-size" in params:
            size, page = int(params["page-size"]), int(params.get("page", "0"))
            rows = rows[page * size:(page + 1) * size]
        return {"totalCount": str(total), "imdata": rows}


class FakeConn:
    """Duck-typed connection: ``target.platform_obj`` + ``get``."""

    def __init__(self, apic: FakeApic, platform: str = ACI) -> None:
        self.apic = apic
        self.target = SimpleNamespace(platform_obj=get_platform(platform), org_id="")

    def get(self, path: str, params: dict | None = None) -> Any:
        return self.apic.get(path, params)


def sample_apic() -> FakeApic:
    """A fake APIC populated with the connected chain from the real samples."""
    apic = FakeApic()
    apic.add_class_rows("fvCEp", load("01-endpoint-excerpt")["imdata"])
    for name in ("02-epg-excerpt", "03-bd-excerpt", "04-vrf"):
        apic.add_mo(load(name)["imdata"][0])
    contract = load("05-contract-excerpt")["imdata"]
    apic.add_subtree_rows("uni/tn-common/brc-contract-A", contract)
    # The sample captured the entry only; this vzFilter row is SYNTHETIC (just
    # its dn/name), standing in for the queried object a subtree answer carries
    # — as the real contract-subtree sample carried its own vzBrCP.
    apic.add_subtree_rows("uni/tn-common/flt-default", [
        {"vzFilter": {"attributes": {"dn": "uni/tn-common/flt-default", "name": "default"}}},
        *load("06-filter-entry")["imdata"],
    ])
    faults = load("07-faults-active-excerpt")["imdata"] + load("08-faults-cleared")["imdata"]
    apic.add_class_rows("faultInst", faults)
    # IP lookup: fvIp rows as a class query returns them (flat, with dn).
    cep = load("01-endpoint-excerpt")["imdata"][0]
    apic.add_mo(cep)
    cep_dn = cep["fvCEp"]["attributes"]["dn"]
    ip_attrs = dict(cep["fvCEp"]["children"][1]["fvIp"]["attributes"])
    ip_attrs["dn"] = f"{cep_dn}/{ip_attrs['rn']}"
    apic.add_class_rows("fvIp", [{"fvIp": {"attributes": ip_attrs}}])
    # Leaf 301 exists (its fault subtree is empty unless a test adds rows).
    apic.add_subtree_rows("topology/pod-20/node-301", [])
    return apic


__all__ = ["FakeApic", "FakeConn", "FabricApiError", "load", "sample_apic"]
