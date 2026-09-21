"""Pure helpers for the ACI management information tree (MIT) — no I/O.

Everything here was written against **real, anonymized APIC responses**
(Fabric-AIops issue #1) and against Cisco's own SDK (``acitoolkit``) for the
query mechanics. Two response shapes exist and both must parse:

  * **nested** — ``rsp-subtree=children|full`` returns each object with a
    ``children`` list whose members carry an ``rn`` but usually no ``dn``;
    the child's DN is ``<parent dn>/<rn>``.
  * **flat** — ``query-target=subtree`` (optionally with
    ``target-subtree-class=``) returns every object as its own ``imdata`` row
    with a full ``dn``.

``totalCount`` is what APIC *reported*; it is never used as a claim that the
returned rows are complete (a real contract-subtree response reported 31 and
carried more objects).

Every agent-supplied value that reaches a URL or a filter expression is
validated here first: names against ACI's own name alphabet, MACs and IPs by
parsing, DNs against a strict character set with no ``..`` segment. DNs are
then sent unencoded, as the SDK sends them — brackets and colons are part of
ACI DNs (``pathep-[eth1/10]``, ``cep-00:50:56:…``).
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any

# ─── validation ─────────────────────────────────────────────────────────────

#: ACI object names: letters, digits, ``_ . : -``, at most 64 characters.
_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
#: Characters a DN may contain here (names plus the ``/ [ ]`` DN syntax).
_DN_RE = re.compile(r"^[A-Za-z0-9_.:/\[\]-]{1,512}$")
_DN_ROOTS = ("uni/", "topology/", "comp/")
_CLASS_RE = re.compile(r"^[a-z][A-Za-z0-9]{1,63}$")
_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}$")

SEVERITY_ORDER = ("critical", "major", "minor", "warning", "info", "cleared")


def validate_name(kind: str, value: str) -> str:
    """Return ``value`` if it is a legal ACI object name, else raise ValueError."""
    text = str(value or "").strip()
    if not _NAME_RE.match(text):
        raise ValueError(
            f"Invalid ACI {kind} name {value!r}: ACI names are 1-64 characters of "
            f"letters, digits and '_ . : -'."
        )
    return text


def validate_dn(value: str) -> str:
    """Return a DN that is safe to put in a ``/api/mo/<dn>.json`` path.

    A DN must start at a known MIT root, use only DN characters, contain no
    empty or ``..`` segment, and balance its brackets — so it can neither
    escape the ``/api/mo/`` prefix nor smuggle query syntax.
    """
    dn = str(value or "").strip()
    bad = (
        not _DN_RE.match(dn)
        or not dn.startswith(_DN_ROOTS)
        or dn.count("[") != dn.count("]")
        # Plain "/" split on purpose: a URL normaliser resolves ".." even
        # inside ACI's brackets (pathep-[../../x]), so no URL segment may be
        # "." or ".." wherever it sits.
        or any(part in ("", "..", ".") for part in dn.split("/"))
    )
    if bad:
        raise ValueError(
            f"Invalid ACI DN {value!r}: expected a distinguished name under "
            f"uni/, topology/ or comp/ (e.g. uni/tn-<tenant>/ap-<app>/epg-<epg>)."
        )
    return dn


def validate_class(value: str) -> str:
    """Return an APIC class name (``fvCEp``, ``faultInst``) or raise ValueError."""
    text = str(value or "").strip()
    if not _CLASS_RE.match(text):
        raise ValueError(f"Invalid APIC class name {value!r}.")
    return text


def normalize_mac(value: str) -> str:
    """Canonical APIC MAC form: upper-case, colon-separated.

    APIC stores endpoint MACs upper-case, and ``eq()`` compares exactly, so a
    lower-case MAC would silently match nothing and read as "no such endpoint".
    """
    text = str(value or "").strip()
    if not _MAC_RE.match(text):
        raise ValueError(f"Invalid MAC address {value!r} (expected e.g. 00:50:56:AB:CD:EF).")
    return text.replace("-", ":").upper()


def normalize_ip(value: str) -> str:
    """A parsed, canonical IPv4/IPv6 address string, or ValueError."""
    try:
        return str(ipaddress.ip_address(str(value or "").strip()))
    except ValueError as exc:
        raise ValueError(f"Invalid IP address {value!r}.") from exc


def validate_severity(value: str) -> str:
    text = str(value or "").strip().lower()
    if text not in SEVERITY_ORDER:
        raise ValueError(
            f"Invalid fault severity {value!r}; one of: {', '.join(SEVERITY_ORDER)}."
        )
    return text


# ─── query construction ─────────────────────────────────────────────────────


def class_path(cls: str) -> str:
    """``/api/node/class/<cls>.json`` for a validated class name."""
    return f"/api/node/class/{validate_class(cls)}.json"


def mo_path(dn: str) -> str:
    """``/api/mo/<dn>.json`` for a validated DN (sent unencoded, as the SDK does)."""
    return f"/api/mo/{validate_dn(dn)}.json"


def eq_filter(cls: str, attr: str, value: str) -> str:
    """``eq(<cls>.<attr>,"<value>")`` — callers pass only validated values."""
    if '"' in value or "\\" in value:
        raise ValueError(f"Refusing a filter value containing quotes: {value!r}.")
    return f'eq({validate_class(cls)}.{attr},"{value}")'


# ─── DN helpers ─────────────────────────────────────────────────────────────


def split_dn(dn: str) -> list[str]:
    """Split a DN into its RNs, respecting brackets (``pathep-[eth1/10]``)."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in dn:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        if ch == "/" and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def parent_dn(dn: str) -> str:
    """The DN one level up (``.../epg-e/cep-X`` → ``.../epg-e``)."""
    parts = split_dn(dn)
    return "/".join(parts[:-1])


def tenant_of(dn: str) -> str | None:
    """Tenant name from a ``uni/tn-<t>/…`` DN, else None."""
    parts = split_dn(dn or "")
    if len(parts) > 1 and parts[0] == "uni" and parts[1].startswith("tn-"):
        return parts[1][3:]
    return None


def rn_value(dn: str, prefix: str) -> str | None:
    """Value of the first RN starting with ``prefix`` (``ap-`` → app name)."""
    for part in split_dn(dn or ""):
        if part.startswith(prefix):
            return part[len(prefix):]
    return None


_PATH_RE = re.compile(
    r"^topology/pod-(?P<pod>\d+)/(?P<kind>paths|protpaths)-(?P<nodes>\d+(?:-\d+)?)"
    r"(?:/(?:extpaths|extprotpaths)-(?P<fex>\d+(?:-\d+)?))?/pathep-\[(?P<ep>.+)\]$"
)
_IFACE_RE = re.compile(r"^eth\d+(/\d+){1,2}$")


def parse_path_dn(tdn: str) -> dict:
    """Decompose a fabric path-endpoint DN into pod / node(s) / interface.

    ``paths-<n>`` is one leaf; ``protpaths-<a>-<b>`` is a vPC pair; an
    ``extpaths-<f>`` / ``extprotpaths-<f>-<g>`` segment names the FEX(es) the
    port sits on. An ``eth…`` endpoint is a physical interface; any other name
    on a single leaf is an interface policy group (a port-channel) — reported
    as such, not guessed further. A DN this does not recognise keeps
    ``kind: None``, rather than being forced into a shape.
    """
    m = _PATH_RE.match(tdn or "")
    if not m:
        return {"pod": None, "nodes": [], "fex": [], "endpoint": None, "kind": None}
    nodes = [int(n) for n in m.group("nodes").split("-")]
    endpoint = m.group("ep")
    if m.group("kind") == "protpaths":
        kind = "vpc"
    elif _IFACE_RE.match(endpoint):
        kind = "interface"
    else:
        kind = "port-channel"
    return {
        "pod": int(m.group("pod")),
        "nodes": nodes,
        "fex": [int(n) for n in m.group("fex").split("-")] if m.group("fex") else [],
        "endpoint": endpoint,
        "kind": kind,
    }


_NODE_RE = re.compile(r"^topology/pod-(\d+)/node-(\d+)(/|$)")


def fault_scope(dn: str) -> dict:
    """Where a fault lives: a fabric node, a VMM controller tree, or a logical object.

    Faults are children of the object they are about, so the DN prefix says
    what kind of thing raised it. A node-scoped fault can still be *about* a
    logical object — deployed-policy faults embed the EPG DN in brackets
    (``…/node-302/local/svc-policyelem-id-0/uni/epp/fv-[uni/tn-…/epg-…]/…``);
    ``aboutDn`` carries that when present.
    """
    text = dn or ""
    m = _NODE_RE.match(text)
    about = None
    bracket = re.search(r"fv-\[(uni/[^\]]+)\]", text)
    if bracket:
        about = bracket.group(1)
    if m:
        return {"scope": "node", "pod": int(m.group(1)), "node": int(m.group(2)), "aboutDn": about}
    if text.startswith("comp/"):
        return {"scope": "vmm", "pod": None, "node": None, "aboutDn": about}
    if text.startswith("uni/"):
        return {"scope": "logical", "pod": None, "node": None, "aboutDn": about}
    return {"scope": "other", "pod": None, "node": None, "aboutDn": about}


# ─── response parsing ───────────────────────────────────────────────────────


def total_count(payload: Any) -> int | None:
    """APIC's reported ``totalCount`` as an int, or None when absent/unparseable."""
    raw = payload.get("totalCount") if isinstance(payload, dict) else None
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _entries(items: Any) -> list[tuple[str, dict, list]]:
    """``[{cls: {attributes, children}}]`` → ``[(cls, attrs, children)]``."""
    out: list[tuple[str, dict, list]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        for cls, body in item.items():
            if not isinstance(body, dict):
                continue
            attrs = body.get("attributes")
            children = body.get("children")
            out.append((
                str(cls),
                attrs if isinstance(attrs, dict) else {},
                children if isinstance(children, list) else [],
            ))
    return out


def flatten(payload: Any) -> list[dict]:
    """Every object in a response, nested or flat, as ``{class, dn, attrs, parentDn}``.

    A child without its own ``dn`` gets ``<parent dn>/<rn>``; a child with
    neither stays in the list with ``dn: None`` rather than being dropped or
    given an invented DN.
    """
    imdata = payload.get("imdata") if isinstance(payload, dict) else None
    out: list[dict] = []

    def walk(items: Any, parent: str | None) -> None:
        for cls, attrs, children in _entries(items):
            dn = attrs.get("dn") or None
            rn = attrs.get("rn")
            if dn is None and parent and rn:
                dn = f"{parent}/{rn}"
            out.append({"class": cls, "dn": dn, "attrs": attrs, "parentDn": parent})
            if children:
                walk(children, dn)

    walk(imdata, None)
    return out


def of_class(objs: list[dict], *classes: str) -> list[dict]:
    """The objects of the given classes, in response order."""
    wanted = set(classes)
    return [o for o in objs if o["class"] in wanted]


def children_of(objs: list[dict], dn: str | None, *classes: str) -> list[dict]:
    """Objects of ``classes`` whose parent DN is ``dn`` (nested or flat shape)."""
    wanted = set(classes)
    return [
        o for o in objs
        if o["class"] in wanted and dn is not None
        and (o["parentDn"] == dn or (o["dn"] and parent_dn(o["dn"]) == dn))
    ]


def opt(attrs: dict, key: str) -> str | None:
    """An attribute value, or None when APIC did not send it.

    ACI sends empty strings for set-but-empty fields (``descr: ""``) — those
    stay ``""``; only an absent key becomes None (missing is not empty).
    """
    value = attrs.get(key)
    return None if value is None else str(value)


def relation(obj: dict) -> dict:
    """The fields every ``fvRs*``/``vzRs*`` relation carries, uniformly."""
    a = obj["attrs"]
    return {
        "class": obj["class"],
        "tDn": opt(a, "tDn"),
        "tCl": opt(a, "tCl"),
        "state": opt(a, "state"),
        "stateQual": opt(a, "stateQual"),
    }


def relation_formed(rel: dict) -> bool:
    return rel.get("state") == "formed" and bool(rel.get("tDn"))
