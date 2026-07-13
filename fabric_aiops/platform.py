"""Platform descriptors — the network-fabric controllers fabric-aiops speaks to.

fabric-aiops is multi-platform by construction. A registry maps a *platform
name* to a :class:`Platform` descriptor that captures everything the connection
layer needs to talk to that controller: its base URL, how to build the auth
header, how it paginates, and how a raw response is normalised (injection-safe).

v0.1 registers exactly one platform — the **Cisco Meraki Dashboard API**
(``https://api.meraki.com/api/v1``, hierarchy organizations → networks →
devices). Additional controllers (e.g. Catalyst Center, Arista CVP) can
``register`` their own descriptor later without touching the ops / CLI / MCP
layers — a registry keyed by ``platform`` name, so adding a controller is a new
descriptor, not a rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from fabric_aiops.governance import sanitize


def seg(value: object) -> str:
    """URL-encode one REST *path segment* (agent-supplied ids, serials, ...).

    Every value interpolated into a request path must pass through here so a
    hostile identifier (``../``, ``?``, ``#``, spaces) cannot rewrite the path
    or smuggle query parameters. ``safe=""`` also encodes ``/``. Query-string
    params passed via httpx ``params=`` are NOT routed through this — httpx
    encodes those itself.
    """
    return quote(str(value), safe="")

# ─── registered platform names ──────────────────────────────────────────────
MERAKI = "meraki"

DEFAULT_MERAKI_BASE_URL = "https://api.meraki.com/api/v1"

# Auth header styles the Meraki Dashboard API accepts.
AUTH_BEARER = "bearer"  # Authorization: Bearer <apiKey>
AUTH_MERAKI_KEY = "meraki-key"  # X-Cisco-Meraki-API-Key: <apiKey>
AUTH_STYLES = (AUTH_BEARER, AUTH_MERAKI_KEY)

# Bounds for the response normaliser (defensive against a hostile controller).
_MAX_STR = 512
_MAX_DEPTH = 8


def _sanitize_obj(obj: object, depth: int = 0) -> object:
    """Recursively fold controller-returned JSON into injection-safe values.

    Every string leaf passes through ``sanitize`` (bounded length); numbers,
    booleans and ``None`` pass through unchanged. Depth is capped so a
    pathological nesting cannot exhaust the stack.
    """
    if depth > _MAX_DEPTH:
        return None
    if isinstance(obj, dict):
        return {str(k): _sanitize_obj(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_obj(v, depth + 1) for v in obj]
    if isinstance(obj, str):
        return sanitize(obj, _MAX_STR)
    return obj


@dataclass(frozen=True)
class Platform:
    """A network-fabric controller's API shape: base URL + auth + normaliser."""

    name: str
    default_base_url: str
    label: str

    def auth_headers(self, api_key: str, style: str = AUTH_BEARER) -> dict[str, str]:
        """Build the request headers that authenticate to this platform.

        Meraki accepts the API key either as ``Authorization: Bearer <key>``
        (default) or the legacy ``X-Cisco-Meraki-API-Key: <key>`` header.
        """
        if style == AUTH_MERAKI_KEY:
            headers = {"X-Cisco-Meraki-API-Key": api_key}
        else:
            headers = {"Authorization": f"Bearer {api_key}"}
        headers["Accept"] = "application/json"
        headers["Content-Type"] = "application/json"
        return headers

    def normalise(self, payload: object) -> object:
        """Return an injection-safe copy of a raw response payload."""
        return _sanitize_obj(payload)


# ─── registry ───────────────────────────────────────────────────────────────
_REGISTRY: dict[str, Platform] = {}


def register(platform: Platform) -> None:
    """Register a platform descriptor under its name (idempotent overwrite)."""
    _REGISTRY[platform.name] = platform


def get_platform(name: str) -> Platform:
    """Return the descriptor for ``name`` or raise with the registered names."""
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        available = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(
            f"Unknown platform '{name}'. Registered platforms: {available}."
        ) from exc


def platform_names() -> tuple[str, ...]:
    """All registered platform names (sorted)."""
    return tuple(sorted(_REGISTRY))


register(
    Platform(
        name=MERAKI,
        default_base_url=DEFAULT_MERAKI_BASE_URL,
        label="Cisco Meraki Dashboard API",
    )
)


def parse_next_link(link_header: str | None) -> str | None:
    """Extract the ``rel=next`` URL from an RFC-5988 ``Link`` header, or None.

    Meraki paginates list endpoints via a ``Link`` header whose ``next`` member
    carries the absolute URL of the following page (``startingAfter`` already
    baked in). Returns None when there is no next page.
    """
    if not link_header:
        return None
    for part in link_header.split(","):
        segments = part.split(";")
        if len(segments) < 2:
            continue
        url = segments[0].strip().strip("<>")
        for attr in segments[1:]:
            rel = attr.strip().replace('"', "").replace(" ", "")
            if rel == "rel=next":
                return url or None
    return None


__all__ = [
    "MERAKI",
    "DEFAULT_MERAKI_BASE_URL",
    "AUTH_BEARER",
    "AUTH_MERAKI_KEY",
    "AUTH_STYLES",
    "Platform",
    "register",
    "get_platform",
    "platform_names",
    "parse_next_link",
    "seg",
]
