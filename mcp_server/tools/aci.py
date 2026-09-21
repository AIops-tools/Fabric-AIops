"""Cisco ACI (APIC) read-only MCP tools — ACI-native references, not Meraki ids."""

from typing import Optional

from fabric_aiops.governance import governed_tool
from fabric_aiops.ops import aci_faults, aci_segment, aci_trace
from mcp_server._shared import _get_connection, mcp, tool_errors


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def aci_endpoint_trace(
    mac: Optional[str] = None,
    ip: Optional[str] = None,
    limit: int = aci_trace.DEFAULT_ENDPOINT_LIMIT,
    fault_limit: int = aci_trace.DEFAULT_FAULT_LIMIT,
    target: Optional[str] = None,
) -> dict:
    """[READ] Reconstruct an ACI endpoint's static fabric path from its MAC or IP.

    One call follows: endpoint → attachment (pod / leaf / interface or vPC) →
    EPG → bridge domain → VRF → contracts the EPG provides/consumes → filter
    entries, plus faults whose DN names the EPG or the attachment interface.
    Every link is the relation APIC resolved (its tDn), so cross-tenant links
    into 'common' are followed correctly.

    How to read the result (do not over-state it):
      * It is NOT a reachability test. 'staticPathResolved: true' means the
        fabric is configured to carry the endpoint, not that traffic flows.
      * 'relatedFaultCandidates' are matched by DN only — proximity, not cause.
        Never report one as the reason for a problem. 'faults: null' means no
        fault scan succeeded (related faults unknown, not none); 'complete:
        false' means some scan failed, hit its page limit, or could not run.
      * 'tagRelations' in state 'missing-target' are policy-tag lookups and say
        nothing about the attachment path; do not report them as a path failure.
      * A section that is null with an entry in 'errors' was NOT read — never
        describe it as empty or absent. 'segmentResolved'/'staticPathResolved'
        are null in that case, not false.
      * A cleared fault keeps the description it had when raised; judge it by
        severity/lifecycle, not by its text.
      * 'contracts.complete: false' means a contract or filter read failed (its
        entry says 'read: false' or has 'entries: null'); the EPG → BD → VRF
        verdict is unaffected.
      * An 'epg' with 'supported: false' is a parent object this read does not
        trace (e.g. an L2Out external EPG) — the chain is undetermined, not broken.
      * 'notFound' means the fabric has not learned the address now, or it is in
        a tenant this account cannot see — not that the host is down.
      * 'notChecked' lists what this read does not evaluate (vzAny, taboo
        contracts, imported contracts, preferred-group peers, service graphs).

    Args:
        mac: Endpoint MAC (any case; e.g. 00:50:56:AB:CD:EF). Pass mac OR ip.
        ip: Endpoint IPv4/IPv6 address. Pass mac OR ip.
        limit: Max matching endpoints to trace (default 5, max 20); the result
            carries returned/limit/truncated.
        fault_limit: Max related-fault candidates per endpoint (default 20).
        target: Target name from config (must be 'platform: aci').

    Returns dict: {query, endpoints:[{endpoint, ips, attachment, attachmentFormed,
        tagRelations, epg, bd, vrf, contracts, segmentResolved, notChecked,
        relatedFaultCandidates, staticPathResolved, findings:[{rank, severity,
        signal, cause, action}], errors}], returned, limit, truncated,
        serverReportedMatches, lookupErrors, note, notFound?}.
    """
    return aci_trace.endpoint_trace(
        _get_connection(target), mac=mac, ip=ip, limit=limit, fault_limit=fault_limit
    )


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def aci_epg_segment(
    tenant: str,
    app: str,
    epg: str,
    target: Optional[str] = None,
) -> dict:
    """[READ] An ACI EPG's segment: bridge domain, VRF and contracts, by name.

    Follows EPG → BD → VRF through the relations APIC resolved (cross-tenant
    links into 'common' included) and expands the contracts the EPG provides
    and consumes down to filter entries. Use it when you know the EPG but not
    an endpoint in it.

    A relation that is not 'formed' is reported as a finding with its state; a
    read that failed is null with an entry in 'errors' (not empty — say it was
    not read). An unenforced VRF means contracts do not restrict traffic in it.
    'notChecked' lists what is not evaluated (vzAny, taboo, imported
    contracts, preferred-group peers, service graphs).

    Args:
        tenant: Tenant name (e.g. 'tenant-A').
        app: Application profile name.
        epg: EPG name.
        target: Target name from config (must be 'platform: aci').

    Returns dict: {epg, bd, vrf, contracts:{contracts, returned, limit,
        truncated, filters, filtersTruncated}, segmentResolved, notChecked,
        findings:[{rank, severity, signal, cause, action}], errors}.
    """
    return aci_segment.segment_for(_get_connection(target), tenant, app, epg)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def aci_faults_list(
    severity: Optional[str] = None,
    include_cleared: bool = False,
    limit: int = aci_faults.DEFAULT_FAULT_LIMIT,
    target: Optional[str] = None,
) -> dict:
    """[READ] ACI fabric faults, worst severity first.

    Rows are fault RECORDS, not incidents: a record with 'delegated: true'
    mirrors a fault raised on another object, so one condition can appear
    twice — do not count them as separate problems. 'active' comes from
    severity only; a cleared fault ('active: false') keeps its original
    description, which is not current state. 'bySeverity.serverReported' is
    APIC's own totalCount per level. Within one severity, rows are sorted
    newest first among the rows APIC returned; when a level is truncated, rows
    beyond the page may be newer — raise the limit before claiming "latest".

    Args:
        severity: One of critical/major/minor/warning/info/cleared; omit for
            every active level.
        include_cleared: Also read cleared faults when no severity is given.
        limit: Max rows (default 50, max 500). The result carries
            returned/limit/truncated; re-run with a higher limit when
            'truncated' is true rather than treating the list as complete.
        target: Target name from config (must be 'platform: aci').

    Returns dict: {severities, faults:[{rank, code, severity, active, lifecycle,
        delegated, dn, scope, pod, node, aboutDn, cause, descr, changeSet,
        lastTransition, ...}], returned, limit, truncated,
        bySeverity:{serverReported}, delegatedRecords, note}.
    """
    return aci_faults.list_faults(
        _get_connection(target), severity=severity, include_cleared=include_cleared,
        limit=limit,
    )
