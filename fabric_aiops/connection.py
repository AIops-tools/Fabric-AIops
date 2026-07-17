"""Connection management for network-fabric controllers.

Thin httpx wrapper with per-target session reuse and two auth flows, selected
by the target's :class:`~fabric_aiops.platform.Platform` descriptor:

  * **static** (Meraki, CVP, UniFi) — a long-lived secret (Meraki API key,
    CloudVision service-account token, or UniFi API key) is carried on every
    request in the header the platform builds — ``Authorization: Bearer
    <secret>`` by default, Meraki's legacy ``X-Cisco-Meraki-API-Key`` header
    (``auth_style: meraki-key``), or a vendor header the platform declares
    (UniFi's ``X-API-KEY``).
  * **session-token** (Catalyst Center) — the stored ``username:password``
    secret is exchanged (HTTP Basic) at the platform's ``token_path`` for a
    short-lived token (~1 h) attached per request as ``X-Auth-Token``; on a
    401 the token is refreshed once and the request retried.

``api_base`` comes from the target (Meraki's includes the version path,
``https://api.meraki.com/api/v1``; Catalyst Center / CVP / UniFi targets point
at the controller host and the platform's path templates carry the full API
paths — a UniFi OS console's base URL additionally carries the
``/proxy/network`` prefix, which httpx joins ahead of every template path).
Meraki list endpoints paginate via a ``Link`` header + ``perPage``/
``startingAfter``; :meth:`FabricConnection.get_pages` follows ``rel=next`` and
aggregates. Catalyst Center / CVP / UniFi list endpoints return one bounded
page.

All non-2xx responses are translated centrally into ``FabricApiError`` with a
teaching message — HTTP errors are translated at the connection layer rather
than leaking raw tracebacks.

The httpx client is injectable for tests: pass ``client=`` to
``FabricConnection`` to substitute a mock that implements ``request`` / ``close``.
"""

from __future__ import annotations

import atexit
import base64
import logging
import weakref
from typing import Any

import httpx

from fabric_aiops.config import AppConfig, TargetConfig, load_config
from fabric_aiops.platform import AUTH_FLOW_SESSION, parse_next_link

_log = logging.getLogger("fabric-aiops.connection")

_TIMEOUT = 30.0
_MAX_PAGES = 25

# Every live ConnectionManager registers here (weakly) so the atexit hook can
# close any cached httpx clients when the interpreter shuts down.
_MANAGERS: weakref.WeakSet = weakref.WeakSet()


def _close_all_managers() -> None:
    """atexit hook: close every cached httpx client. Idempotent and error-safe —
    close failures are logged, never raised (raising at interpreter exit only
    obscures the real shutdown path)."""
    for mgr in list(_MANAGERS):
        try:
            mgr.disconnect_all()
        except Exception:  # noqa: BLE001 — never raise at interpreter exit
            _log.debug("Error closing cached connections at exit", exc_info=True)


atexit.register(_close_all_managers)


class FabricApiError(Exception):
    """A controller REST API call failed; carries a teaching message + status."""

    def __init__(self, message: str, *, status_code: int | None = None, path: str = "") -> None:
        self.status_code = status_code
        self.path = path
        super().__init__(message)


def _teaching_message(status: int, path: str, body: str, label: str) -> str:
    """Map a non-2xx status to an actionable, teaching error message."""
    snippet = body[:200].strip()
    if status in (401, 403):
        return (
            f"Authentication/authorization failed ({status}) on {label} {path}. "
            f"Check the stored credential ('fabric-aiops secret set <target>') and "
            f"that it has access/privileges on this controller. {snippet}"
        )
    if status == 404:
        return (
            f"Resource not found (404) on {label} {path}. The id may be stale — "
            f"list the parent collection first to get a current id. {snippet}"
        )
    if status == 429:
        return (
            f"Rate limited (429) on {label} {path}. The controller API rate-limits "
            f"requests; back off and retry after the Retry-After delay. {snippet}"
        )
    if status in (400, 422):
        return (
            f"Validation error ({status}) on {label} {path}. The controller rejected "
            f"the request body — check required fields and value formats. {snippet}"
        )
    if status in (500, 502, 503, 504):
        return (
            f"{label} server error ({status}) on {path}. The controller may be busy; "
            f"retry shortly. {snippet}"
        )
    return f"{label} API error ({status}) on {path}. {snippet}"


class FabricConnection:
    """A single authenticated session against one network-fabric controller target."""

    def __init__(self, target: TargetConfig, client: Any | None = None) -> None:
        self._target = target
        self._session_token: str | None = None
        platform = target.platform_obj
        if platform.auth_flow == AUTH_FLOW_SESSION:
            # The secret is exchanged lazily for a short-lived token — nothing
            # secret goes into the base headers at construction time.
            headers = platform.auth_headers("", target.auth_style)
        else:
            headers = platform.auth_headers(target.api_key, target.auth_style)
        self._client = client or httpx.Client(
            base_url=target.api_base,
            verify=target.verify_ssl,
            timeout=_TIMEOUT,
            headers=headers,
        )

    @property
    def target(self) -> TargetConfig:
        return self._target

    # ── session-token flow (Catalyst Center) ────────────────────────────────
    def _fetch_session_token(self) -> str:
        """Exchange the stored ``username:password`` secret for a session token."""
        platform = self._target.platform_obj
        secret = self._target.api_key
        if ":" not in secret:
            raise FabricApiError(
                f"The {platform.label} secret must be 'username:password' (it is "
                f"exchanged for a short-lived token via POST {platform.token_path}). "
                f"Re-store it with 'fabric-aiops secret set {self._target.name}'.",
                path=platform.token_path,
            )
        basic = base64.b64encode(secret.encode("utf-8")).decode("ascii")
        try:
            resp = self._client.request(
                "POST",
                platform.token_path,
                headers={"Authorization": f"Basic {basic}"},
            )
        except httpx.HTTPError as exc:
            raise FabricApiError(
                f"Could not reach {platform.label} at {self._target.api_base} for a "
                f"session token (POST {platform.token_path}): {exc}. Check the base "
                f"URL and that the controller REST API is reachable.",
                path=platform.token_path,
            ) from exc
        if not (200 <= resp.status_code < 300):
            raise FabricApiError(
                f"Session-token request failed ({resp.status_code}) on {platform.label} "
                f"{platform.token_path}. Check the 'username:password' secret "
                f"('fabric-aiops secret set {self._target.name}') and the account's "
                f"role. {resp.text[:200].strip()}",
                status_code=resp.status_code,
                path=platform.token_path,
            )
        body = self._parse(resp)
        token = body.get("Token") or body.get("token") if isinstance(body, dict) else None
        if not token:
            raise FabricApiError(
                f"{platform.label} token response carried no 'Token' field "
                f"(POST {platform.token_path}) — is the base URL pointing at the "
                f"controller, not a proxy login page?",
                path=platform.token_path,
            )
        return str(token)

    def _ensure_session_token(self) -> str:
        if self._session_token is None:
            self._session_token = self._fetch_session_token()
        return self._session_token

    def _session_request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Issue a token-authenticated request, refreshing once on a 401.

        Session tokens expire (~1 h on Catalyst Center); a 401 mid-session is
        treated as expiry — the token is dropped, re-fetched once, and the
        request retried. A second 401 surfaces via the normal error path.
        """
        platform = self._target.platform_obj
        headers = dict(kwargs.pop("headers", None) or {})
        headers[platform.token_header] = self._ensure_session_token()
        resp = self._client.request(method, path, headers=headers, **kwargs)
        if getattr(resp, "status_code", None) == 401:
            self._session_token = None
            headers[platform.token_header] = self._ensure_session_token()
            resp = self._client.request(method, path, headers=headers, **kwargs)
        return resp

    # ── request core ─────────────────────────────────────────────────────────
    def _raw_request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Issue a request and return the raw response, translating transport errors."""
        platform = self._target.platform_obj
        try:
            if platform.auth_flow == AUTH_FLOW_SESSION:
                return self._session_request(method, path, **kwargs)
            return self._client.request(method, path, **kwargs)
        except FabricApiError:
            raise
        except httpx.HTTPError as exc:
            raise FabricApiError(
                f"Could not reach {platform.label} at "
                f"{self._target.api_base} ({method} {path}): {exc}. Check the base "
                f"URL and that the controller REST API is reachable.",
                path=path,
            ) from exc

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Issue a request and return parsed JSON, translating errors centrally."""
        resp = self._raw_request(method, path, **kwargs)
        self._raise_for_status(resp, path)
        return self._parse(resp)

    def _raise_for_status(self, resp: Any, path: str) -> None:
        if not (200 <= resp.status_code < 300):
            raise FabricApiError(
                _teaching_message(
                    resp.status_code, path, resp.text, self._target.platform_obj.label
                ),
                status_code=resp.status_code,
                path=path,
            )

    @staticmethod
    def _parse(resp: Any) -> Any:
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)

    def get_pages(self, path: str, params: dict | None = None, max_pages: int = _MAX_PAGES) -> list:
        """GET a paginated list resource, following the ``Link`` rel=next header.

        Aggregates every page's array (Meraki list endpoints return bare JSON
        arrays) up to ``max_pages`` so a runaway pagination loop is bounded. The
        first request uses ``path``+``params``; subsequent requests follow the
        absolute ``next`` URL the controller returns. Platforms without Link
        pagination (Catalyst Center, CVP) return their single — possibly
        enveloped — page as a one-item list; the platform adapter unwraps it.
        """
        results: list = []
        next_target: str = path
        next_params = dict(params or {})
        for _ in range(max(1, max_pages)):
            resp = self._raw_request("GET", next_target, params=next_params or None)
            self._raise_for_status(resp, next_target)
            page = self._parse(resp)
            if isinstance(page, list):
                results.extend(page)
            elif isinstance(page, dict):
                results.append(page)
            headers = getattr(resp, "headers", {}) or {}
            nxt = parse_next_link(headers.get("Link") or headers.get("link"))
            if not nxt:
                break
            next_target = nxt
            next_params = {}  # the next URL already carries startingAfter
        return results

    def close(self) -> None:
        self._client.close()


class ConnectionManager:
    """Manages connections to multiple controller targets with session reuse."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._connections: dict[str, FabricConnection] = {}
        _MANAGERS.add(self)

    @classmethod
    def from_config(cls, config: AppConfig | None = None) -> ConnectionManager:
        cfg = config or load_config()
        return cls(cfg)

    def connect(self, target_name: str | None = None) -> FabricConnection:
        """Connect to a target by name, or the default target."""
        target = (
            self._config.get_target(target_name)
            if target_name
            else self._config.default_target
        )
        cached = self._connections.get(target.name)
        if cached is not None:
            return cached
        conn = FabricConnection(target)
        self._connections[target.name] = conn
        return conn

    def disconnect(self, target_name: str) -> None:
        conn = self._connections.pop(target_name, None)
        if conn is not None:
            conn.close()

    def disconnect_all(self) -> None:
        for name in list(self._connections):
            self.disconnect(name)

    def list_targets(self) -> list[str]:
        return [t.name for t in self._config.targets]

    def list_connected(self) -> list[str]:
        return list(self._connections.keys())
