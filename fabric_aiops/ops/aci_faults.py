"""ACI fault reads (``faultInst``) — read-only, built from real APIC responses.

Three things the real samples (Fabric-AIops #1) showed, each enforced here:

  * **A cleared fault keeps its old description.** F3696 still read
    "1 EPs are in freeze state." with ``severity: cleared``,
    ``lc: retaining`` and a changeSet reporting zero dampened endpoints. So
    ``active`` comes from ``severity``, never from the text, and a cleared
    row says outright that its description is historical.
  * **One condition can appear as two records.** ``delegated: yes`` marks a
    copy raised on another object (a VMM host mirroring its NIC's fault).
    Records are counted as records; delegated ones are counted separately
    and never presented as independent incidents.
  * **``totalCount`` is what APIC reported, not a completeness proof.** It is
    surfaced per severity as ``serverReported``; truncation of the returned
    list is measured by fetching one row more than the limit.

Worst-first ordering is exact rather than inferred: each severity is queried
on its own (``eq(faultInst.severity,"<s>")`` — the filter the real sample
used), in severity order, until the limit is filled.
"""

from __future__ import annotations

from typing import Any

from fabric_aiops.ops import aci_mit as mit
from fabric_aiops.ops._util import clean
from fabric_aiops.ops.aci_common import aci_get, require_aci

DEFAULT_FAULT_LIMIT = 50
MAX_FAULT_LIMIT = 500
ACTIVE_SEVERITIES = ("critical", "major", "minor", "warning", "info")
#: Sort position of each severity, worst first.
SEVERITY_SORT = {s: i for i, s in enumerate(mit.SEVERITY_ORDER)}

_CLEARED_NOTE = (
    "Cleared fault: 'descr' describes the condition when it was raised, not the "
    "current state. Use severity/lc/changeSet for what is true now."
)


def fault_row(obj: dict) -> dict:
    """One ``faultInst`` as a stable row; ``active`` derives from severity only."""
    a = obj["attrs"]
    severity = mit.opt(a, "severity")
    scope = mit.fault_scope(obj["dn"] or "")
    row: dict[str, Any] = {
        "code": mit.opt(a, "code"),
        "severity": severity,
        "active": None if severity is None else severity != "cleared",
        "lifecycle": mit.opt(a, "lc"),
        "delegated": _yes_no(a.get("delegated")),
        "acknowledged": _yes_no(a.get("ack")),
        "dn": obj["dn"],
        "scope": scope["scope"],
        "pod": scope["pod"],
        "node": scope["node"],
        "aboutDn": scope["aboutDn"],
        "cause": mit.opt(a, "cause"),
        "rule": mit.opt(a, "rule"),
        "subject": mit.opt(a, "subject"),
        "type": mit.opt(a, "type"),
        "domain": mit.opt(a, "domain"),
        "descr": mit.opt(a, "descr"),
        "changeSet": mit.opt(a, "changeSet"),
        "created": mit.opt(a, "created"),
        "lastTransition": mit.opt(a, "lastTransition"),
        "occurrences": _as_int(a.get("occur")),
        "origSeverity": mit.opt(a, "origSeverity"),
        "highestSeverity": mit.opt(a, "highestSeverity"),
    }
    if severity == "cleared":
        row["note"] = _CLEARED_NOTE
    return row


def _yes_no(value: Any) -> bool | None:
    if value is None:
        return None
    text = str(value).lower()
    return True if text == "yes" else False if text == "no" else None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


def _newest_first(rows: list[dict]) -> list[dict]:
    # ISO-8601 with a fixed offset per APIC sorts correctly as text; a row
    # without a timestamp sorts last rather than being dropped.
    return sorted(rows, key=lambda r: r.get("lastTransition") or "", reverse=True)


def _fetch_severity(conn: Any, severity: str, page_size: int) -> tuple[list[dict], int | None]:
    payload = aci_get(
        conn,
        mit.class_path("faultInst"),
        {
            "query-target-filter": mit.eq_filter("faultInst", "severity", severity),
            "page": "0",
            "page-size": str(max(1, page_size)),
        },
    )
    rows = [fault_row(o) for o in mit.of_class(mit.flatten(payload), "faultInst")]
    return _newest_first(rows), mit.total_count(payload)


def list_faults(
    conn: Any,
    severity: str | None = None,
    include_cleared: bool = False,
    limit: int = DEFAULT_FAULT_LIMIT,
) -> dict:
    """[READ] Fabric-wide faults, worst severity first (newest first per page).

    ``severity`` narrows to one level; otherwise every active level is read
    (and ``cleared`` too when ``include_cleared``). Every level is queried even
    after the limit fills — with a one-row page — so ``bySeverity`` always
    carries APIC's reported count for each level asked about.
    """
    require_aci(conn)
    capped = max(1, min(int(limit), MAX_FAULT_LIMIT))
    if severity is not None:
        levels: tuple[str, ...] = (mit.validate_severity(severity),)
    else:
        levels = ACTIVE_SEVERITIES + (("cleared",) if include_cleared else ())

    collected: list[dict] = []
    by_severity: dict[str, int | None] = {}
    for level in levels:
        want = capped + 1 - len(collected)
        rows, reported = _fetch_severity(conn, level, want if want > 0 else 1)
        by_severity[level] = reported
        if want > 0:
            collected.extend(rows[:want])

    kept = collected[:capped]
    for i, row in enumerate(kept, start=1):
        row["rank"] = i
    delegated = sum(1 for r in kept if r.get("delegated"))
    return clean({
        "severities": list(levels),
        "faults": kept,
        "returned": len(kept),
        "limit": capped,
        "truncated": len(collected) > capped,
        "bySeverity": {"serverReported": by_severity},
        "delegatedRecords": delegated,
        "note": (
            "Rows are fault records, not incidents: a delegated record (delegated: "
            "true) mirrors a fault raised on another object, so the same condition "
            "can appear twice. Within one severity, order is newest first among the "
            "rows APIC returned. bySeverity is APIC's own totalCount per level."
        ),
    })
