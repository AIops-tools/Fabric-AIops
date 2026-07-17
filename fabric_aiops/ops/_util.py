"""Shared helpers for the ops modules.

The ops layer speaks **canonical operations** (``orgs.list``,
``networks.alerts``, ``devices.update``, ...). The ``op_get`` / ``op_get_pages``
/ ``op_post`` / ``op_put`` helpers resolve each canonical key through the
target's :class:`~fabric_aiops.platform.Platform` descriptor — path template
(centrally percent-encoded), native query params, and the response adapter that
folds the platform's payload into the canonical (Meraki) shape. A platform that
does not map a key raises a teaching ``PlatformUnsupported``.

Meraki Dashboard API list endpoints return a bare JSON array; a few wrap items
under a key. ``as_list`` normalises both. All controller-returned text reaches
the caller only after ``sanitize()`` (output hygiene: control/format characters
stripped, bounded length), applied at the read boundary.
"""

from __future__ import annotations

from typing import Any

from fabric_aiops.governance import sanitize
from fabric_aiops.platform import MERAKI, Platform, get_platform


def platform_of(conn: Any) -> Platform:
    """The connection target's platform descriptor (reference platform when
    the connection carries no real target — e.g. a bare test double)."""
    platform = getattr(getattr(conn, "target", None), "platform_obj", None)
    return platform if isinstance(platform, Platform) else get_platform(MERAKI)


def require_support(conn: Any, *keys: str) -> None:
    """Fail fast — BEFORE any controller call — when the target's platform
    does not map one of the canonical ops (teaching ``PlatformUnsupported``)."""
    platform_of(conn).require(*keys)


def _request_kwargs(query: dict | None, json_body: Any = None) -> dict:
    kwargs: dict = {}
    if query:
        kwargs["params"] = query
    if json_body is not None:
        kwargs["json"] = json_body
    return kwargs


def _scoped_ids(conn: Any, ids: dict) -> dict:
    """Return ``ids`` with the target's default ``org_id`` added when absent.

    Some platforms scope *device-level* paths under the canonical org segment
    (UniFi's ``/api/s/{site}/...``) while the canonical signature carries only
    the device id. The target's default ``org_id`` fills that hole; platforms
    whose templates don't use it simply drop the extra id (``request_for``
    discards leftover ids). Returns a new dict — never mutates the input.
    """
    if ids.get("org_id"):
        return ids
    default = getattr(getattr(conn, "target", None), "org_id", "") or ""
    return {**ids, "org_id": default} if default else ids


def op_get(conn: Any, key: str, *, params: dict | None = None, **ids: Any) -> Any:
    """GET one canonical resource; returns the adapted (canonical-shape) payload."""
    platform = platform_of(conn)
    path, query = platform.request_for(key, _scoped_ids(conn, ids), params)
    return platform.adapt(key, conn.get(path, **_request_kwargs(query)))


def op_get_pages(conn: Any, key: str, *, params: dict | None = None, **ids: Any) -> list:
    """GET a canonical list resource (paginated where the platform paginates)."""
    platform = platform_of(conn)
    path, query = platform.request_for(key, _scoped_ids(conn, ids), params)
    adapted = platform.adapt(key, conn.get_pages(path, params=query or None))
    return adapted if isinstance(adapted, list) else [adapted]


def op_post(conn: Any, key: str, *, json_body: Any = None, **ids: Any) -> Any:
    """POST a canonical operation (write paths; guarded by the callers)."""
    platform = platform_of(conn)
    scoped = _scoped_ids(conn, ids)
    path, query = platform.request_for(key, scoped)
    body = platform.body_for(key, scoped, json_body)
    return platform.adapt(key, conn.post(path, **_request_kwargs(query, body)))


def op_put(conn: Any, key: str, *, json_body: Any = None, **ids: Any) -> Any:
    """PUT a canonical operation (write paths; guarded by the callers)."""
    platform = platform_of(conn)
    scoped = _scoped_ids(conn, ids)
    path, query = platform.request_for(key, scoped)
    body = platform.body_for(key, scoped, json_body)
    return platform.adapt(key, conn.put(path, **_request_kwargs(query, body)))


def as_list(data: Any, list_key: str | None = None) -> list[dict]:
    """Normalise a list payload to a list of dicts.

    A bare JSON array passes through; a dict is unwrapped via ``list_key`` when
    given, else returned as a single-item list when it looks like one record.
    """
    if isinstance(data, dict):
        items = data.get(list_key, []) if list_key else [data]
    else:
        items = data
    return [i for i in (items or []) if isinstance(i, dict)]


def clean(payload: Any) -> Any:
    """Return an injection-safe copy of a raw controller payload."""
    return _sanitize_obj(payload)


def clean_list(data: Any, list_key: str | None = None) -> list[dict]:
    """as_list + recursive sanitize — the standard read-path normalisation."""
    return [_sanitize_obj(row) for row in as_list(data, list_key)]


def s(value: Any, limit: int = 128) -> str:
    """Sanitize an arbitrary value to a bounded, injection-safe string."""
    return sanitize(str(value if value is not None else ""), limit)


def require_org(conn: Any, org_id: str | None) -> str:
    """Resolve the organization id: explicit arg, else the target default.

    Raises a teaching ``ValueError`` when neither is available, so an org-scoped
    call fails fast with actionable guidance instead of a confusing 404.
    """
    if org_id:
        return str(org_id)
    default = getattr(getattr(conn, "target", None), "org_id", "") or ""
    if default:
        return str(default)
    raise ValueError(
        "No organization id. Pass org_id, or set 'org_id' on the target in "
        "config.yaml (list organizations with 'fabric-aiops org list')."
    )


_MAX_STR = 512
_MAX_DEPTH = 8


def _sanitize_obj(obj: Any, depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        return None
    if isinstance(obj, dict):
        return {str(k): _sanitize_obj(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_obj(v, depth + 1) for v in obj]
    if isinstance(obj, str):
        return sanitize(obj, _MAX_STR)
    return obj
