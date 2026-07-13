"""Client-scoped reads over the Meraki Dashboard API (read-only).

A *client* is an end device seen on a Meraki network (by MAC/identifier). These
reads answer "who is on this network, and for one client — its detail, usage,
and connection quality". All controller text is sanitized at the read boundary.
"""

from __future__ import annotations

from typing import Any

from fabric_aiops.ops._util import clean, clean_list


def list_clients(conn: Any, network_id: str, timespan: int = 86400) -> list[dict]:
    """[READ] Clients seen on a network within ``timespan`` seconds."""
    span = max(7200, min(int(timespan), 2592000))
    return clean_list(conn.get_pages(f"/networks/{network_id}/clients", params={"timespan": span}))


def get_client(conn: Any, network_id: str, client_id: str) -> dict:
    """[READ] One client's detail (description, MAC, IP, VLAN, manufacturer)."""
    return clean(conn.get(f"/networks/{network_id}/clients/{client_id}"))


def client_usage(conn: Any, network_id: str, client_id: str) -> dict:
    """[READ] A client's usage history, rolled up to total sent/received KB.

    Pulls ``/networks/{id}/clients/{clientId}/usageHistory`` and sums the series
    so an agent gets a single total plus the number of samples, not a raw series.
    """
    rows = clean_list(conn.get(f"/networks/{network_id}/clients/{client_id}/usageHistory"))
    sent = sum(r.get("sent") or 0 for r in rows if isinstance(r.get("sent"), (int, float)))
    recv = sum(r.get("received") or 0 for r in rows if isinstance(r.get("received"), (int, float)))
    return {
        "networkId": network_id,
        "clientId": client_id,
        "samples": len(rows),
        "totalSentKb": sent,
        "totalReceivedKb": recv,
        "totalKb": sent + recv,
    }


def client_connectivity(conn: Any, network_id: str, client_id: str) -> dict:
    """[READ] A client's connection-quality stats (assoc/auth/dhcp/dns/success).

    Pulls ``/networks/{id}/clients/{clientId}/connectionStats`` — the failure
    counts across the connection lifecycle — so an agent can tell *where* a
    client's connectivity is breaking (association vs auth vs DHCP vs DNS).
    """
    stats = clean(conn.get(f"/networks/{network_id}/clients/{client_id}/connectionStats"))
    stats = stats if isinstance(stats, dict) else {}
    return {
        "networkId": network_id,
        "clientId": client_id,
        "assoc": stats.get("assoc"),
        "auth": stats.get("auth"),
        "dhcp": stats.get("dhcp"),
        "dns": stats.get("dns"),
        "success": stats.get("success"),
    }
