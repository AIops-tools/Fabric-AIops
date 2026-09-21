"""Connection management for network-fabric controllers.

Thin httpx wrapper with per-target session reuse and three auth flows, selected
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
  * **aci-login** (Cisco ACI APIC) — the ``username:password`` secret is
    POSTed as an ``aaaUser`` JSON body; APIC answers with a token and sets the
    session cookie the httpx client then carries. The session is extended at
    half its ``refreshTimeoutSeconds`` and, because APIC answers an expired
    session with **403**, a 401 *or* 403 triggers one fresh login and retry.

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
import time
from typing import Any

import httpx

from fabric_aiops.config import AppConfig, TargetConfig, load_config
from fabric_aiops.platform import (
    AUTH_FLOW_ACI,
    AUTH_FLOW_SESSION,
    AUTH_FLOW_STATIC,
    parse_next_link,
)

_log = logging.getLogger("fabric-aiops.connection")

_TIMEOUT = 30.0
_MAX_PAGES = 25
#: A 401/403 this soon after an APIC login cannot be an expired session.
_ACI_FRESH_SESSION_SEC = 30.0
#: After a refused APIC login, do not send the password again for this long —
#: a changed password must not become one login attempt per read (lockout).
_ACI_LOGIN_BACKOFF_SEC = 60.0

# Every live ConnectionManager registers here so the atexit hook can close any
# cached httpx clients when the interpreter shuts down. The reference is strong
# on purpose: the CLI builds a manager, returns only the connection and drops the
# manager (cli/_common.get_connection), so a weak registry would collect it and
# leave the hook nothing to close. Today that only skips a local socket close —
# but it is the same silence that hides a missing server-side teardown.
_MANAGERS: set[ConnectionManager] = set()


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
        # APIC session bookkeeping (aci-login flow only).
        self._aci_logged_in = False
        self._aci_login_at: float | None = None
        self._aci_login_failed_at: float | None = None
        self._aci_refresh_after: float | None = None
        platform = target.platform_obj
        if platform.auth_flow != AUTH_FLOW_STATIC:
            # The secret is exchanged lazily for a session — nothing secret
            # goes into the base headers at construction time.
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

    # ── aci-login flow (Cisco ACI APIC) ──────────────────────────────────────
    def _split_credentials(self) -> tuple[str, str]:
        platform = self._target.platform_obj
        secret = self._target.api_key
        user, sep, pwd = secret.partition(":")
        if not sep or not user or not pwd:
            raise FabricApiError(
                f"The {platform.label} secret must be 'username:password' (it is "
                f"sent to POST {platform.token_path} to open a session). Re-store "
                f"it with 'fabric-aiops secret set {self._target.name}'.",
                path=platform.token_path,
            )
        return user, pwd

    def _aci_login(self) -> None:
        """Open an APIC session (``aaaLogin``); the client keeps the cookie.

        The response body carries the token and ``refreshTimeoutSeconds``; the
        session cookie APIC sets rides on every later request through the httpx
        client's cookie jar, exactly as acitoolkit's ``requests.Session`` does.
        The password is never logged and never echoed in an error.
        """
        platform = self._target.platform_obj
        failed_at = self._aci_login_failed_at
        if failed_at is not None and time.monotonic() - failed_at < _ACI_LOGIN_BACKOFF_SEC:
            raise FabricApiError(
                f"Not retrying the APIC login: it was refused less than "
                f"{int(_ACI_LOGIN_BACKOFF_SEC)} s ago, and repeating it per read could "
                f"lock the account. Fix the secret ('fabric-aiops secret set "
                f"{self._target.name}') and retry after the pause.",
                path=platform.token_path,
            )
        user, pwd = self._split_credentials()
        body = {"aaaUser": {"attributes": {"name": user, "pwd": pwd}}}
        try:
            resp = self._client.request("POST", platform.token_path, json=body)
        except httpx.HTTPError as exc:
            raise FabricApiError(
                f"Could not reach {platform.label} at {self._target.api_base} to log "
                f"in (POST {platform.token_path}): {exc}. Check the base URL and "
                f"that the APIC is reachable.",
                path=platform.token_path,
            ) from exc
        if not (200 <= resp.status_code < 300):
            self._aci_login_failed_at = time.monotonic()
            raise FabricApiError(
                f"APIC login failed ({resp.status_code}) at {platform.token_path}: "
                f"{aci_error_text(self._parse(resp)) or 'no error text'}. Check the "
                f"'username:password' secret ('fabric-aiops secret set "
                f"{self._target.name}') and that the account may log in to this "
                f"login domain.",
                status_code=resp.status_code,
                path=platform.token_path,
            )
        attrs = _aaa_login_attrs(self._parse(resp))
        if not attrs.get("token"):
            self._aci_login_failed_at = time.monotonic()
            raise FabricApiError(
                f"{platform.label} login answered {resp.status_code} but carried no "
                f"aaaLogin token (POST {platform.token_path}) — is the base URL the "
                f"APIC itself, not a proxy or login page?",
                path=platform.token_path,
            )
        self._aci_logged_in = True
        self._aci_login_failed_at = None
        self._aci_login_at = time.monotonic()
        self._aci_refresh_after = _refresh_deadline(attrs)

    def _aci_refresh(self) -> None:
        """Extend the session via ``aaaRefresh``; fall back to a fresh login.

        A refresh that fails for any reason is not an error in itself — the
        session may already have lapsed — so it degrades to a new login, and
        only that login's failure reaches the caller.
        """
        platform = self._target.platform_obj
        try:
            resp = self._client.request("GET", platform.refresh_path)
            ok = 200 <= resp.status_code < 300
            attrs = _aaa_login_attrs(self._parse(resp)) if ok else {}
        except httpx.HTTPError:
            ok, attrs = False, {}
        if ok and attrs.get("token"):
            self._aci_refresh_after = _refresh_deadline(attrs)
            return
        self._aci_login()

    def _aci_request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Issue a request in an APIC session, re-logging in once on 401/403.

        APIC reports an expired session as **403** (acitoolkit re-logs in on
        exactly that status). A 403 is also what a read the account's role may
        not see returns. So: a 401/403 in a session opened within the last
        ``_ACI_FRESH_SESSION_SEC`` is a privilege problem at once (no second
        password login per denied object); an older session gets one fresh
        login and retry, and a 403 that survives it is a privilege problem too.
        """
        if not self._aci_logged_in:
            self._aci_login()
        elif self._aci_refresh_after is not None and time.monotonic() >= self._aci_refresh_after:
            self._aci_refresh()
        resp = self._client.request(method, path, **kwargs)
        if getattr(resp, "status_code", None) in (401, 403):
            if not self._aci_session_is_fresh():
                self._aci_login()
                resp = self._client.request(method, path, **kwargs)
            if getattr(resp, "status_code", None) in (401, 403):
                raise FabricApiError(
                    f"APIC refused {method} {path} ({resp.status_code}) in a freshly "
                    f"opened session, so this is not an expired session: the "
                    f"account's role/security domain cannot read this object. "
                    f"{aci_error_text(self._parse(resp))}".strip(),
                    status_code=resp.status_code,
                    path=path,
                )
        return resp

    def _aci_session_is_fresh(self) -> bool:
        return (
            self._aci_login_at is not None
            and time.monotonic() - self._aci_login_at < _ACI_FRESH_SESSION_SEC
        )

    # ── request core ─────────────────────────────────────────────────────────
    def _raw_request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Issue a request and return the raw response, translating transport errors."""
        platform = self._target.platform_obj
        try:
            if platform.auth_flow == AUTH_FLOW_SESSION:
                return self._session_request(method, path, **kwargs)
            if platform.auth_flow == AUTH_FLOW_ACI:
                return self._aci_request(method, path, **kwargs)
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


def aci_error_text(body: Any) -> str:
    """The ``imdata[0].error.attributes.text`` APIC puts in an error body, or ''."""
    try:
        return str(body["imdata"][0]["error"]["attributes"]["text"])[:300]
    except (KeyError, IndexError, TypeError):
        return ""


def _aaa_login_attrs(body: Any) -> dict:
    """``imdata[0].aaaLogin.attributes`` of a login/refresh response, or {}."""
    try:
        attrs = body["imdata"][0]["aaaLogin"]["attributes"]
    except (KeyError, IndexError, TypeError):
        return {}
    return attrs if isinstance(attrs, dict) else {}


def _refresh_deadline(attrs: dict) -> float | None:
    """When to extend the session: half its lifetime, as acitoolkit does.

    A missing or unparseable ``refreshTimeoutSeconds`` is *unknown*, not zero —
    the connection then stays reactive (re-login on 401/403) instead of
    refreshing before every request.
    """
    try:
        lifetime = float(attrs.get("refreshTimeoutSeconds"))
    except (TypeError, ValueError):
        return None
    if lifetime <= 0:
        return None
    return time.monotonic() + lifetime / 2


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
