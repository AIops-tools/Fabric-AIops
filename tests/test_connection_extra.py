"""Connection layer edge cases: teaching-message mapping per status class, the
verb wrappers, JSON parsing of odd bodies, transport-error translation, and the
ConnectionManager's connect/cache/disconnect bookkeeping — all with a mocked
httpx client (never a live controller)."""

from __future__ import annotations

import httpx
import pytest

from fabric_aiops.config import AppConfig, TargetConfig
from fabric_aiops.connection import (
    ConnectionManager,
    FabricApiError,
    FabricConnection,
    _teaching_message,
)


class _Resp:
    def __init__(self, status=200, payload=None, content=b"{}", text="body", headers=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.content = content
        self.text = text
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, ValueError):
            raise self._payload
        return self._payload


class _RecordingClient:
    """Records (method, path, kwargs); returns a caller-provided response fn."""

    def __init__(self, respond):
        self._respond = respond
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, path, **kw):
        self.calls.append((method, path, kw))
        return self._respond(method, path, kw)

    def close(self):
        pass


def _conn(respond) -> FabricConnection:
    """A Meraki connection over an injected client (no auth header built)."""
    conn = FabricConnection.__new__(FabricConnection)
    conn._target = TargetConfig(name="org1")
    conn._session_token = None
    conn._client = _RecordingClient(respond)
    return conn


# ── teaching-message mapping covers every status class ───────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "needle"),
    [
        (401, "Authentication/authorization failed"),
        (403, "Authentication/authorization failed"),
        (404, "not found"),
        (429, "Rate limited"),
        (400, "Validation error"),
        (422, "Validation error"),
        (500, "server error"),
        (503, "server error"),
        (418, "API error"),  # fall-through branch
    ],
)
def test_teaching_message_per_status_class(status, needle):
    msg = _teaching_message(status, "/x", "detail body", "Cisco Meraki Dashboard API")
    assert needle.lower() in msg.lower()
    assert "detail body" in msg


@pytest.mark.unit
def test_request_raises_translated_error_with_status_and_path():
    conn = _conn(lambda m, p, kw: _Resp(429, text="slow down", content=b"x"))
    with pytest.raises(FabricApiError) as ei:
        conn.get("/organizations")
    assert ei.value.status_code == 429
    assert ei.value.path == "/organizations"
    assert "Rate limited" in str(ei.value)


# ── verb wrappers all reach request() with the right method ──────────────────


@pytest.mark.unit
def test_post_put_delete_get_dispatch_the_right_verb():
    conn = _conn(lambda m, p, kw: _Resp(200, {"ok": m}, content=b"{}"))
    assert conn.get("/x")["ok"] == "GET"
    assert conn.post("/x")["ok"] == "POST"
    assert conn.put("/x")["ok"] == "PUT"
    assert conn.delete("/x")["ok"] == "DELETE"


# ── _parse: empty body → {}, non-JSON body → {} ──────────────────────────────


@pytest.mark.unit
def test_parse_empty_body_returns_empty_dict():
    conn = _conn(lambda m, p, kw: _Resp(200, content=b""))
    assert conn.get("/x") == {}


@pytest.mark.unit
def test_parse_non_json_body_returns_empty_dict():
    conn = _conn(lambda m, p, kw: _Resp(200, payload=ValueError("not json"), content=b"<html>"))
    assert conn.get("/x") == {}


# ── transport error translation (httpx.HTTPError → FabricApiError) ────────────


@pytest.mark.unit
def test_transport_error_is_translated_to_teaching_error():
    def _explode(method, path, kw):
        raise httpx.ConnectError("name resolution failed")

    conn = _conn(_explode)
    with pytest.raises(FabricApiError, match="Could not reach"):
        conn.get("/organizations")


# ── get_pages: a dict page (non-list) is appended, then stops without a Link ─


@pytest.mark.unit
def test_get_pages_appends_dict_page_and_stops_without_link():
    conn = _conn(lambda m, p, kw: _Resp(200, {"id": "solo"}, content=b"{}"))
    rows = conn.get_pages("/organizations/1/devices")
    assert rows == [{"id": "solo"}]
    # exactly one page fetched (no Link header → loop breaks immediately)
    assert len(conn._client.calls) == 1


@pytest.mark.unit
def test_get_pages_raises_for_status_on_a_page():
    conn = _conn(lambda m, p, kw: _Resp(500, text="boom", content=b"x"))
    with pytest.raises(FabricApiError):
        conn.get_pages("/organizations/1/devices")


# ── target property ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_target_property_exposes_the_configured_target():
    conn = _conn(lambda m, p, kw: _Resp(200))
    assert conn.target.name == "org1"


# ── ConnectionManager: connect default/caching, disconnect, listings ─────────


@pytest.mark.unit
def test_manager_connect_caches_and_uses_default_target(monkeypatch):
    monkeypatch.setenv("FABRIC_ORG1_APIKEY", "k")
    monkeypatch.setenv("FABRIC_ORG2_APIKEY", "k")
    cfg = AppConfig(targets=[TargetConfig(name="org1"), TargetConfig(name="org2")])
    mgr = ConnectionManager(cfg)

    built: list[str] = []
    real_client = httpx.Client
    monkeypatch.setattr(
        "fabric_aiops.connection.httpx.Client",
        lambda **kw: built.append("c") or real_client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})), **kw
        ),
    )

    default_conn = mgr.connect()  # no name → default target (first)
    assert default_conn.target.name == "org1"
    again = mgr.connect("org1")  # cached: no second client built
    assert again is default_conn
    assert built == ["c"]
    assert set(mgr.list_targets()) == {"org1", "org2"}
    assert mgr.list_connected() == ["org1"]

    mgr.disconnect("org1")
    assert mgr.list_connected() == []
    mgr.disconnect("org1")  # idempotent: popping a missing name is a no-op


@pytest.mark.unit
def test_manager_from_config_uses_loader(monkeypatch):
    sentinel = AppConfig(targets=[TargetConfig(name="org1")])
    monkeypatch.setattr("fabric_aiops.connection.load_config", lambda: sentinel)
    mgr = ConnectionManager.from_config()
    assert mgr.list_targets() == ["org1"]
