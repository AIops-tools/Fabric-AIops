"""Cisco Catalyst Center platform: session-token auth, path building with
central encoding, response adaptation onto every mapped canonical key, and the
teaching errors for unmapped ops (all mocked httpx — no live controller)."""

from __future__ import annotations

import base64

import pytest

from fabric_aiops.config import TargetConfig
from fabric_aiops.connection import FabricApiError, FabricConnection
from fabric_aiops.ops import clients, devices, networks, organizations
from fabric_aiops.ops import remediation as rem_ops
from fabric_aiops.platform import (
    AUTH_FLOW_SESSION,
    CATALYST,
    PlatformUnsupported,
    get_platform,
    platform_names,
)

TOKEN_PATH = "/dna/system/api/v1/auth/token"  # nosec B105 — an API path, not a secret


def _target(**kw) -> TargetConfig:
    kw.setdefault("name", "cc1")
    kw.setdefault("platform", CATALYST)
    kw.setdefault("base_url", "https://catalyst.example.com")
    return TargetConfig(**kw)


class _Resp:
    def __init__(self, status: int, payload=None, text: str = ""):
        self.status_code = status
        self._payload = payload
        self.content = b"x" if payload is not None else b""
        self.text = text
        self.headers: dict = {}

    def json(self):
        return self._payload


class _CatalystClient:
    """Mock httpx client: Basic → token exchange, then X-Auth-Token reads."""

    def __init__(self, responses=None, token_ttl_calls: int | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, str, dict]] = []
        self.token_fetches = 0
        self.token_ttl_calls = token_ttl_calls  # data calls before the token "expires"
        self._data_calls_on_token = 0

    def request(self, method, path, headers=None, **kw):
        headers = headers or {}
        self.calls.append((method, path, headers))
        if path == TOKEN_PATH:
            assert method == "POST"
            assert headers.get("Authorization", "").startswith("Basic ")
            self.token_fetches += 1
            self._data_calls_on_token = 0
            return _Resp(200, {"Token": f"tok-{self.token_fetches}"})
        expected = f"tok-{self.token_fetches}"
        if headers.get("X-Auth-Token") != expected:
            return _Resp(401, text="invalid token")
        if self.token_ttl_calls is not None:
            self._data_calls_on_token += 1
            if self._data_calls_on_token > self.token_ttl_calls:
                return _Resp(401, text="token expired")
        key = path.split("?")[0]
        if key not in self.responses:
            return _Resp(404, text="no such route")
        return _Resp(200, self.responses[key])

    def close(self):
        pass


def _conn(monkeypatch, responses=None, **client_kw) -> tuple[FabricConnection, _CatalystClient]:
    monkeypatch.setenv("FABRIC_CC1_APIKEY", "admin:s3cret")
    client = _CatalystClient(responses, **client_kw)
    return FabricConnection(_target(), client=client), client


# ── registry ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_catalyst_registered_with_session_auth_metadata():
    assert CATALYST in platform_names()
    p = get_platform(CATALYST)
    assert p.label == "Cisco Catalyst Center API"
    assert p.auth_flow == AUTH_FLOW_SESSION
    assert p.token_path == TOKEN_PATH
    assert p.token_header == "X-Auth-Token"
    assert p.requires_base_url and p.requires_secret
    assert p.org_noun == "sites"


@pytest.mark.unit
def test_catalyst_base_headers_carry_no_secret():
    headers = get_platform(CATALYST).auth_headers("admin:s3cret")
    assert "Authorization" not in headers
    assert "X-Auth-Token" not in headers
    assert headers["Accept"] == "application/json"


@pytest.mark.unit
def test_catalyst_target_requires_base_url():
    t = TargetConfig(name="cc-nourl", platform=CATALYST)
    with pytest.raises(ValueError, match="base_url"):
        _ = t.api_base


# ── session-token auth flow ─────────────────────────────────────────────────


@pytest.mark.unit
def test_token_fetched_once_and_reused(monkeypatch):
    conn, client = _conn(monkeypatch, {"/dna/intent/api/v1/site": {"response": []}})
    conn.get("/dna/intent/api/v1/site")
    conn.get("/dna/intent/api/v1/site")
    assert client.token_fetches == 1
    method, path, headers = client.calls[0]
    assert (method, path) == ("POST", TOKEN_PATH)
    expected = base64.b64encode(b"admin:s3cret").decode()
    assert headers["Authorization"] == f"Basic {expected}"


@pytest.mark.unit
def test_token_refreshed_once_on_401_and_request_retried(monkeypatch):
    """A mid-session 401 (token ~1 h expiry) triggers ONE refresh + retry."""
    conn, client = _conn(
        monkeypatch,
        {"/dna/intent/api/v1/network-device": {"response": []}},
        token_ttl_calls=1,
    )
    conn.get("/dna/intent/api/v1/network-device")  # call 1 on tok-1: fine
    conn.get("/dna/intent/api/v1/network-device")  # call 2: 401 → refresh → retry
    assert client.token_fetches == 2


@pytest.mark.unit
def test_malformed_secret_raises_teaching_error(monkeypatch):
    monkeypatch.setenv("FABRIC_CC1_APIKEY", "no-colon-here")
    conn = FabricConnection(_target(), client=_CatalystClient())
    with pytest.raises(FabricApiError, match="username:password"):
        conn.get("/dna/intent/api/v1/site")


@pytest.mark.unit
def test_token_response_without_token_field_raises(monkeypatch):
    class _BadTokenClient(_CatalystClient):
        def request(self, method, path, headers=None, **kw):
            if path == TOKEN_PATH:
                return _Resp(200, {"unexpected": "shape"})
            return _Resp(500, text="never reached")

    monkeypatch.setenv("FABRIC_CC1_APIKEY", "admin:s3cret")
    conn = FabricConnection(_target(), client=_BadTokenClient())
    with pytest.raises(FabricApiError, match="Token"):
        conn.get("/dna/intent/api/v1/site")


@pytest.mark.unit
def test_bad_credentials_on_token_endpoint_teach(monkeypatch):
    class _DenyClient(_CatalystClient):
        def request(self, method, path, headers=None, **kw):
            return _Resp(401, text="Authentication failed")

    monkeypatch.setenv("FABRIC_CC1_APIKEY", "admin:wrong")
    conn = FabricConnection(_target(), client=_DenyClient())
    with pytest.raises(FabricApiError, match="secret set"):
        conn.get("/dna/intent/api/v1/site")


# ── path building (central percent-encoding) ────────────────────────────────


@pytest.mark.unit
def test_request_for_encodes_hostile_ids_and_maps_query():
    p = get_platform(CATALYST)
    path, query = p.request_for("orgs.get", {"org_id": "../admin"})
    assert path == "/dna/intent/api/v1/site/..%2Fadmin"
    path, query = p.request_for("networks.alerts", {"network_id": "site 1"})
    assert path == "/dna/intent/api/v1/issues"
    assert query == {"siteId": "site 1"}  # httpx encodes query params itself
    path, query = p.request_for("clients.get", {"network_id": "S1", "client_id": "aa:bb"})
    assert path == "/dna/intent/api/v1/client-detail"
    assert query == {"macAddress": "aa:bb"}  # network scope dropped (global tree)


@pytest.mark.unit
def test_meraki_canonical_query_params_are_dropped_not_forwarded():
    """Meraki-specific params (timespan, serials[]) have no Catalyst native
    equivalent — they must be dropped, not sent verbatim."""
    p = get_platform(CATALYST)
    _, query = p.request_for("clients.list", {"network_id": "S1"}, {"timespan": 86400})
    assert query == {}
    _, query = p.request_for("orgs.device_statuses", {"org_id": "S1"}, {"serials[]": "X"})
    assert query == {}


# ── response adaptation per canonical key (through the real ops layer) ──────


class _OpsConn:
    """Ops-layer double: a catalyst target + canned enveloped payloads by path."""

    def __init__(self, responses, org_id="site-1"):
        self._responses = responses
        self.target = _target(org_id=org_id)

    def get(self, path, **_kw):
        return self._responses[path]

    def get_pages(self, path, params=None, **_kw):
        data = self._responses[path]
        return data if isinstance(data, list) else [data]


@pytest.mark.unit
def test_orgs_list_maps_sites_to_canonical_orgs():
    conn = _OpsConn({
        "/dna/intent/api/v1/site": {
            "response": [
                {"id": "s1", "name": "HQ", "siteNameHierarchy": "Global/HQ"},
                {"id": "s2", "name": "Branch", "siteNameHierarchy": "Global/Branch"},
            ]
        }
    })
    rows = organizations.list_organizations(conn)
    assert [r["id"] for r in rows] == ["s1", "s2"]
    assert rows[0]["name"] == "HQ" and rows[0]["url"] == "Global/HQ"


@pytest.mark.unit
def test_orgs_get_unwraps_single_site():
    conn = _OpsConn({
        "/dna/intent/api/v1/site/site-1": {
            "response": [{"id": "site-1", "name": "HQ", "siteNameHierarchy": "Global/HQ"}]
        }
    })
    out = organizations.get_organization(conn)
    assert out["id"] == "site-1" and out["name"] == "HQ"


@pytest.mark.unit
def test_device_statuses_adapts_device_health_and_rolls_up():
    conn = _OpsConn({
        "/dna/intent/api/v1/device-health": {
            "response": [
                {
                    "uuid": "u1",
                    "name": "sw1",
                    "reachabilityHealth": "UP",
                    "deviceFamily": "Switches and Hubs",
                    "location": "Global/HQ",
                    "overallHealth": 9,
                },
                {
                    "uuid": "u2",
                    "name": "ap1",
                    "reachabilityHealth": "UNREACHABLE",
                    "deviceFamily": "Unified AP",
                    "location": "Global/Branch",
                    "overallHealth": 1,
                },
            ]
        }
    })
    out = organizations.device_statuses(conn)
    assert out["total"] == 2
    assert out["byStatus"] == {"online": 1, "offline": 1}
    assert out["byProductType"]["Switches and Hubs"] == 1
    assert out["devices"][0]["serial"] == "u1"


@pytest.mark.unit
def test_networks_list_adapts_site_health():
    conn = _OpsConn({
        "/dna/intent/api/v1/site-health": {
            "response": [
                {
                    "siteId": "s1",
                    "siteName": "HQ",
                    "siteType": "building",
                    "numberOfNetworkDevice": 12,
                }
            ]
        }
    })
    rows = networks.list_networks(conn)
    assert rows[0]["id"] == "s1" and rows[0]["name"] == "HQ"
    assert rows[0]["productTypes"] == ["building"]


@pytest.mark.unit
def test_network_alerts_maps_issue_priority_to_severity():
    conn = _OpsConn({
        "/dna/intent/api/v1/issues": {
            "response": [
                {"name": "AP down", "priority": "P1", "siteId": "s1"},
                {"name": "High util", "priority": "P2", "siteId": "s1"},
                {"name": "Noise", "priority": "P4", "siteId": "s1"},
            ]
        }
    })
    out = networks.network_alerts(conn, "s1")
    assert out["total"] == 3
    assert out["bySeverity"] == {"critical": 1, "warning": 1, "info": 1}
    assert out["alerts"][0]["type"] == "AP down"


@pytest.mark.unit
def test_device_inventory_adapts_network_device_rows():
    conn = _OpsConn({
        "/dna/intent/api/v1/network-device": {
            "response": [
                {
                    "id": "u1",
                    "serialNumber": "FCW1",
                    "hostname": "core-1",
                    "platformId": "C9300-48P",
                    "managementIpAddress": "10.0.0.2",
                    "softwareVersion": "17.9.4",
                    "family": "Switches and Hubs",
                    "reachabilityStatus": "Reachable",
                }
            ]
        }
    })
    out = devices.inventory(conn)
    assert out["total"] == 1
    row = out["devices"][0]
    assert row["serial"] == "FCW1" and row["deviceId"] == "u1"
    assert row["model"] == "C9300-48P" and row["status"] == "online"
    # Non-Meraki model strings bucket under 'other' (documented).
    assert out["byModelFamily"] == {"other": 1}


@pytest.mark.unit
def test_switch_ports_maps_interface_stats():
    conn = _OpsConn({
        "/dna/intent/api/v1/interface/network-device/u1": {
            "response": [
                {
                    "portName": "GigabitEthernet1/0/1",
                    "adminStatus": "UP",
                    "status": "connected",
                    "vlanId": "10",
                    "speed": "1000000",
                    "duplex": "FullDuplex",
                    "interfaceType": "Physical",
                }
            ]
        }
    })
    rows = devices.switch_ports(conn, "u1")
    assert rows[0]["portId"] == "GigabitEthernet1/0/1"
    assert rows[0]["enabled"] is True and rows[0]["vlan"] == "10"


@pytest.mark.unit
def test_clients_list_surfaces_aggregate_client_health():
    conn = _OpsConn({
        "/dna/intent/api/v1/client-health": {
            "response": [
                {
                    "siteId": "global",
                    "scoreDetail": [
                        {"scoreCategory": {"value": "ALL"}, "scoreValue": 87, "clientCount": 250},
                        {"scoreCategory": {"value": "WIRED"}, "scoreValue": 95, "clientCount": 90},
                    ],
                }
            ]
        }
    })
    rows = clients.list_clients(conn, "global")
    assert [r["id"] for r in rows] == ["all", "wired"]
    assert rows[0]["healthScore"] == 87 and rows[0]["clientCount"] == 250


@pytest.mark.unit
def test_client_get_maps_client_detail():
    conn = _OpsConn({
        "/dna/intent/api/v1/client-detail": {
            "detail": {
                "hostMac": "aa:bb:cc:dd:ee:ff",
                "hostName": "laptop-1",
                "hostIpV4": "10.0.1.5",
                "vlanId": "20",
                "ssid": "corp",
                "connectionStatus": "CONNECTED",
                "healthScore": [{"healthType": "OVERALL", "score": 10}],
            }
        }
    })
    out = clients.get_client(conn, "global", "aa:bb:cc:dd:ee:ff")
    assert out["mac"] == "aa:bb:cc:dd:ee:ff" and out["description"] == "laptop-1"
    assert out["status"] == "Online" and out["healthScore"] == 10


# ── unsupported ops + writes: teaching errors, never silent no-ops ──────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "call",
    [
        lambda c: organizations.licensing_overview(c),
        lambda c: organizations.list_admins(c),
        lambda c: organizations.api_request_usage(c),
        lambda c: networks.list_vlans(c, "s1"),
        lambda c: networks.traffic_summary(c, "s1"),
        lambda c: devices.uplink_status(c),
        lambda c: devices.wireless_ssids(c, "s1"),
        lambda c: clients.client_usage(c, "s1", "aa:bb"),
        lambda c: clients.client_connectivity(c, "s1", "aa:bb"),
    ],
)
def test_unmapped_reads_raise_teaching_error(call):
    conn = _OpsConn({}, org_id="site-1")
    with pytest.raises(PlatformUnsupported, match="not supported on Cisco Catalyst Center"):
        call(conn)


@pytest.mark.unit
@pytest.mark.parametrize(
    "call",
    [
        lambda c: rem_ops.reboot_device(c, "u1"),
        lambda c: rem_ops.blink_device_leds(c, "u1"),
        lambda c: rem_ops.update_device(c, "u1", {"name": "x"}),
        lambda c: rem_ops.update_network_vlan(c, "s1", "10", {"name": "x"}),
        lambda c: rem_ops.claim_devices_into_network(c, "s1", ["u1"]),
        lambda c: rem_ops.remove_device_from_network(c, "s1", "u1"),
        lambda c: rem_ops.bind_network_to_template(c, "s1", "t1"),
        lambda c: rem_ops.unbind_network_from_template(c, "s1"),
    ],
)
def test_all_writes_fail_fast_before_any_controller_call(call):
    """Writes are Meraki-only: on catalyst they must raise the teaching error
    BEFORE issuing any request (never a silent no-op, never a partial write)."""

    class _NoCallConn(_OpsConn):
        def __init__(self):
            super().__init__({})
            self.called = False

        def get(self, path, **kw):
            self.called = True
            raise AssertionError("controller must not be called")

        post = put = get

        def get_pages(self, path, **kw):  # pragma: no cover - defensive
            self.called = True
            raise AssertionError("controller must not be called")

    conn = _NoCallConn()
    with pytest.raises(PlatformUnsupported, match="open an\n?.*issue|issue or PR"):
        call(conn)
    assert conn.called is False


@pytest.mark.unit
def test_teaching_error_names_platforms_that_do_support_the_op():
    with pytest.raises(PlatformUnsupported, match="meraki"):
        get_platform(CATALYST).require("devices.reboot")
