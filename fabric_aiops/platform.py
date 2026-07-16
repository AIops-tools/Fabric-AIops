"""Platform descriptors — the network-fabric controllers fabric-aiops speaks to.

fabric-aiops is multi-platform by construction. A registry maps a *platform
name* to a :class:`Platform` descriptor that captures everything the connection
and ops layers need to talk to that controller: its base URL, how to build the
auth header (static key vs a short-lived session token), how each **canonical
operation** maps onto the controller's REST paths (:class:`PathSpec` templates,
every interpolated segment percent-encoded via :func:`seg`), and how a raw
response is normalised into the Meraki-canonical shape the ops layer consumes.

Registered platforms:

  * ``meraki`` — Cisco Meraki Dashboard API (the reference platform; full
    read + write coverage; hierarchy organizations → networks → devices).
  * ``catalyst`` — Cisco Catalyst Center (formerly DNA Center) REST API
    (read coverage; sites stand in for organizations/networks; writes raise a
    teaching :class:`PlatformUnsupported`).
  * ``cvp`` — Arista CloudVision Portal REST API (read coverage; containers
    stand in for organizations/networks; writes raise a teaching error).

Adding a controller is a new descriptor module under
``fabric_aiops/platforms/`` — registry entries plus request/response
adaptation, never new ops / CLI / MCP surface.
"""

from __future__ import annotations

import string
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
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
CATALYST = "catalyst"
CVP = "cvp"

DEFAULT_MERAKI_BASE_URL = "https://api.meraki.com/api/v1"

# Auth header styles for statically-keyed platforms.
AUTH_BEARER = "bearer"  # Authorization: Bearer <apiKey>
AUTH_MERAKI_KEY = "meraki-key"  # X-Cisco-Meraki-API-Key: <apiKey>
AUTH_STYLES = (AUTH_BEARER, AUTH_MERAKI_KEY)

# Auth flows: how the secret becomes request authentication.
AUTH_FLOW_STATIC = "static"  # the stored secret goes straight into a header
AUTH_FLOW_SESSION = "session-token"  # exchange the secret for a short-lived token

# Canonical operations the ops layer speaks. The reference platform (meraki)
# implements every key; other platforms implement the subset that maps cleanly
# and raise a teaching PlatformUnsupported for the rest.
CANONICAL_READS = (
    "orgs.list",
    "orgs.get",
    "orgs.licensing",
    "orgs.admins",
    "orgs.device_statuses",
    "orgs.api_requests",
    "networks.list",
    "networks.get",
    "networks.vlans",
    "networks.vlan_get",
    "networks.alerts",
    "networks.traffic",
    "devices.list",
    "devices.get",
    "devices.uplinks",
    "devices.switch_ports",
    "devices.wireless_ssids",
    "clients.list",
    "clients.get",
    "clients.usage",
    "clients.connectivity",
    "health.uplink_loss_latency",
)
CANONICAL_WRITES = (
    "devices.reboot",
    "devices.blink_leds",
    "devices.update",
    "networks.vlan_update",
    "networks.claim_devices",
    "networks.remove_device",
    "networks.bind_template",
    "networks.unbind_template",
)
CANONICAL_OPS = CANONICAL_READS + CANONICAL_WRITES

# Bounds for the response normaliser (defensive against a hostile controller).
_MAX_STR = 512
_MAX_DEPTH = 8

_ISSUES_URL = "https://github.com/AIops-tools/Fabric-AIops/issues"


class PlatformUnsupported(ValueError):  # noqa: N818 — family style (PolicyDenied, BudgetExceeded)
    """A canonical operation has no mapping on this platform (teaching error)."""

    def __init__(self, key: str, platform: Platform) -> None:
        supporting = ", ".join(platforms_supporting(key)) or "(none)"
        super().__init__(
            f"'{key}' is not supported on {platform.label} (platform "
            f"'{platform.name}') yet — it currently works on: {supporting}. "
            f"Nothing was changed. If you need this on {platform.label}, open an "
            f"issue or PR at {_ISSUES_URL} — contributions welcome."
        )
        self.key = key
        self.platform = platform.name


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


_FORMATTER = string.Formatter()


def _placeholders(template: str) -> set[str]:
    """Names of the ``{placeholder}`` fields in a path template."""
    return {name for _, name, _, _ in _FORMATTER.parse(template) if name}


@dataclass(frozen=True)
class PathSpec:
    """How one canonical operation maps onto a platform's REST API.

    ``template`` is the request path with ``{placeholder}`` segments; every
    interpolated value is percent-encoded via :func:`seg` centrally in
    :meth:`Platform.request_for`. Canonical ids that the platform carries as
    *query* parameters instead of path segments are declared in ``id_query``
    (canonical name → native query-param name). ``params_map`` translates
    canonical query params to native names — ``None`` means pass params through
    unchanged (the reference platform), ``{}`` means drop them (they have no
    native equivalent). ``default_params`` are always sent (e.g. paging bounds
    a native endpoint requires).
    """

    template: str
    id_query: Mapping[str, str] | None = None
    params_map: Mapping[str, str] | None = None
    default_params: Mapping[str, str] | None = None


@dataclass(frozen=True)
class Platform:
    """A network-fabric controller's API shape: base URL + auth + op mapping."""

    name: str
    default_base_url: str
    label: str
    # Vocabulary + onboarding hints (init wizard, doctor, teaching errors).
    org_noun: str = "organizations"
    org_id_hint: str = "organization id"
    secret_hint: str = "API key"
    secret_help: str = ""
    requires_secret: bool = True
    requires_base_url: bool = False
    default_port: int = 443
    # Auth: static header (bearer / vendor key) or a session-token exchange.
    auth_flow: str = AUTH_FLOW_STATIC
    token_path: str = ""
    token_header: str = ""
    # Canonical-op mapping: path templates + per-key response adapters.
    paths: Mapping[str, PathSpec] = field(default_factory=dict)
    adapters: Mapping[str, Callable[[object], object]] = field(default_factory=dict)

    def auth_headers(self, api_key: str, style: str = AUTH_BEARER) -> dict[str, str]:
        """Build the request headers that authenticate to this platform.

        Static-flow platforms carry the stored secret directly: Meraki accepts
        ``Authorization: Bearer <key>`` (default) or the legacy
        ``X-Cisco-Meraki-API-Key`` header; CVP uses a Bearer service-account
        token. Session-token platforms (Catalyst Center) return only the base
        headers here — the short-lived token is fetched and attached per
        request by the connection layer.
        """
        headers: dict[str, str] = {}
        if self.auth_flow == AUTH_FLOW_STATIC:
            if style == AUTH_MERAKI_KEY:
                headers["X-Cisco-Meraki-API-Key"] = api_key
            else:
                headers["Authorization"] = f"Bearer {api_key}"
        headers["Accept"] = "application/json"
        headers["Content-Type"] = "application/json"
        return headers

    def supports(self, key: str) -> bool:
        """True when this platform maps the canonical operation ``key``."""
        return key in self.paths

    def require(self, *keys: str) -> None:
        """Fail fast (teaching error) when any canonical op is unmapped here."""
        for key in keys:
            if key not in self.paths:
                raise PlatformUnsupported(key, self)

    def request_for(
        self,
        key: str,
        ids: Mapping[str, object] | None = None,
        params: Mapping[str, object] | None = None,
    ) -> tuple[str, dict[str, object]]:
        """Resolve a canonical op to ``(path, query_params)`` for this platform.

        Path placeholders are filled from ``ids`` with every value
        percent-encoded via :func:`seg`; leftover ids become native query
        params when declared in the spec's ``id_query`` (and are dropped
        otherwise — a canonical scope the platform does not use). ``params``
        pass through unchanged when ``params_map`` is None, else are
        translated/dropped per the map.
        """
        spec = self.paths.get(key)
        if spec is None:
            raise PlatformUnsupported(key, self)
        given = {k: v for k, v in (ids or {}).items() if v is not None}
        holes = _placeholders(spec.template)
        missing = holes - given.keys()
        if missing:
            raise ValueError(
                f"Operation '{key}' on {self.label} requires: {', '.join(sorted(missing))}."
            )
        path = spec.template.format(
            **{k: seg(v) for k, v in given.items() if k in holes}
        )
        query: dict[str, object] = dict(spec.default_params or {})
        id_query = spec.id_query or {}
        for k, v in given.items():
            if k in holes:
                continue
            native = id_query.get(k)
            if native:
                query[native] = str(v)
        if params:
            if spec.params_map is None:
                query.update({k: v for k, v in params.items() if v is not None})
            else:
                for k, v in params.items():
                    native = spec.params_map.get(k)
                    if native and v is not None:
                        query[native] = v
        return path, query

    def adapt(self, key: str, payload: object) -> object:
        """Fold a raw response for ``key`` into the canonical (Meraki) shape."""
        adapter = self.adapters.get(key)
        return adapter(payload) if adapter else payload

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


def platforms_supporting(key: str) -> tuple[str, ...]:
    """Names of the registered platforms that map canonical op ``key``."""
    return tuple(sorted(name for name, p in _REGISTRY.items() if key in p.paths))


def parse_next_link(link_header: str | None) -> str | None:
    """Extract the ``rel=next`` URL from an RFC-5988 ``Link`` header, or None.

    Meraki paginates list endpoints via a ``Link`` header whose ``next`` member
    carries the absolute URL of the following page (``startingAfter`` already
    baked in). Returns None when there is no next page. Catalyst Center and CVP
    do not use Link headers — their list endpoints are fetched as one bounded
    page (deep pagination on those platforms is a known deferral).
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


# Import the platform descriptor modules for their registration side-effect.
# This sits at the bottom so every name they import from here already exists.
from fabric_aiops import platforms as _platforms  # noqa: E402,F401  (registers descriptors)

__all__ = [
    "MERAKI",
    "CATALYST",
    "CVP",
    "DEFAULT_MERAKI_BASE_URL",
    "AUTH_BEARER",
    "AUTH_MERAKI_KEY",
    "AUTH_STYLES",
    "AUTH_FLOW_STATIC",
    "AUTH_FLOW_SESSION",
    "CANONICAL_READS",
    "CANONICAL_WRITES",
    "CANONICAL_OPS",
    "PathSpec",
    "Platform",
    "PlatformUnsupported",
    "register",
    "get_platform",
    "platform_names",
    "platforms_supporting",
    "parse_next_link",
    "seg",
]
