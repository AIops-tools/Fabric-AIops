"""Shared helpers for the Meraki ops modules.

Meraki Dashboard API list endpoints return a bare JSON array; a few wrap items
under a key. ``as_list`` normalises both. All controller-returned text reaches
the caller only after ``sanitize()`` (output hygiene: control/format characters
stripped, bounded length), applied via the platform normaliser at the read
boundary.
"""

from __future__ import annotations

from typing import Any

from fabric_aiops.governance import sanitize


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
