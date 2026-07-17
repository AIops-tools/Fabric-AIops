"""UniFi Network platform: X-API-KEY auth, path building with central encoding
(including the UniFi OS ``/proxy/network`` base-URL variant), response
adaptation onto every mapped canonical key, the devmgr command-envelope write,
and the teaching errors for unmapped ops (all mocked httpx — no live
controller)."""

from __future__ import annotations

import httpx
import pytest

from fabric_aiops.config import TargetConfig
from fabric_aiops.connection import FabricConnection
from fabric_aiops.ops import clients, devices, networks, organizations
from fabric_aiops.ops import remediation as rem_ops
from fabric_aiops.platform import (
    AUTH_FLOW_STATIC,
    UNIFI,
    PlatformUnsupported,
    get_platform,
    platform_names,
)

API_KEY = "unifi-api-key-123"  # nosec B105 — a test fixture, not a real secret


def _target(**kw) -> TargetConfig:
    kw.setdefault("name", "u1")
    kw.setdefault("platform", UNIFI)
    kw.setdefault("base_url", "https://unifi.example.com:8443")
    return TargetConfig(**kw)


def _envelope(rows) -> dict:
    return {"meta": {"rc": "ok"}, "data": rows}


# ── registry ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_unifi_registered_with_api_key_auth_metadata():
    assert UNIFI in platform_names()
    p = get_platform(UNIFI)
    assert p.label == "UniFi Network API"
    assert p.auth_flow == AUTH_FLOW_STATIC
    assert p.api_key_header == "X-API-KEY"
    assert p.requires_base_url and p.requires_secret
    assert p.org_noun == "sites"
    assert "/proxy/network" in p.base_url_help


@pytest.mark.unit
def test_unifi_auth_headers_carry_the_vendor_key_header():
    headers = get_platform(UNIFI).auth_headers(API_KEY)
    assert headers["X-API-KEY"] == API_KEY
    assert "Authorization" not in headers
    assert headers["Accept"] == "application/json"


@pytest.mark.unit
def test_unifi_target_requires_base_url():
    t = TargetConfig(name="u-nourl", platform=UNIFI)
    with pytest.raises(ValueError, match="base_url"):
        _ = t.api_base


# ── auth + base-URL variants on the wire (real httpx + MockTransport) ───────


def _wire_conn(monkeypatch, base_url: str, captured: list) -> FabricConnection:
    """A real FabricConnection whose real httpx.Client hits a mock transport,
    so the wire URL (base-URL merge) and headers are the genuine article."""
    monkeypatch.setenv("FABRIC_U1_APIKEY", API_KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_envelope([{"name": "default", "desc": "HQ"}]))

    real_client = httpx.Client
    monkeypatch.setattr(
        "fabric_aiops.connection.httpx.Client",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    return FabricConnection(_target(base_url=base_url))


@pytest.mark.unit
def test_classic_controller_base_url_and_api_key_on_the_wire(monkeypatch):
    captured: list[httpx.Request] = []
    conn = _wire_conn(monkeypatch, "https://unifi.example.com:8443", captured)
    rows = conn.get("/api/self/sites")
    assert rows["data"][0]["name"] == "default"
    req = captured[0]
    assert str(req.url) == "https://unifi.example.com:8443/api/self/sites"
    assert req.headers["X-API-KEY"] == API_KEY
    assert "Authorization" not in req.headers
    conn.close()


@pytest.mark.unit
def test_unifi_os_console_proxy_network_prefix_variant(monkeypatch):
    """A UniFi OS console target carries /proxy/network in base_url; every
    template path must land under that prefix on the wire."""
    captured: list[httpx.Request] = []
    conn = _wire_conn(monkeypatch, "https://console.example.com/proxy/network", captured)
    conn.get("/api/self/sites")
    conn.get("/api/s/default/stat/device")
    assert [str(r.url) for r in captured] == [
        "https://console.example.com/proxy/network/api/self/sites",
        "https://console.example.com/proxy/network/api/s/default/stat/device",
    ]
    assert all(r.headers["X-API-KEY"] == API_KEY for r in captured)
    conn.close()


# ── path building (central percent-encoding + default-site scope) ───────────


@pytest.mark.unit
def test_request_for_encodes_hostile_site_and_device_ids():
    p = get_platform(UNIFI)
    path, query = p.request_for("networks.alerts", {"network_id": "../admin"})
    assert path == "/api/s/..%2Fadmin/stat/alarm" and query == {}
    path, _ = p.request_for(
        "devices.get", {"org_id": "site 1", "serial": "aa:bb:cc:dd:ee:ff"}
    )
    assert path == "/api/s/site%201/stat/device/aa%3Abb%3Acc%3Add%3Aee%3Aff"


@pytest.mark.unit
def test_meraki_canonical_query_params_are_dropped_not_forwarded():
    """Meraki-specific params (timespan, serials[]) have no UniFi native
    equivalent — they must be dropped, not sent verbatim."""
    p = get_platform(UNIFI)
    _, query = p.request_for("clients.list", {"network_id": "default"}, {"timespan": 86400})
    assert query == {}
    _, query = p.request_for(
        "orgs.device_statuses", {"org_id": "default"}, {"serials[]": "X"}
    )
    assert query == {}


@pytest.mark.unit
def test_device_paths_require_a_site_scope():
    with pytest.raises(ValueError, match="org_id"):
        get_platform(UNIFI).request_for("devices.get", {"serial": "aa:bb"})


# ── response adaptation per canonical key (through the real ops layer) ──────


class _OpsConn:
    """Ops-layer double: a unifi target + canned enveloped payloads by path."""

    def __init__(self, responses, org_id="default"):
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
        "/api/self/sites": _envelope([
            {"_id": "5c1", "name": "default", "desc": "HQ"},
            {"_id": "5c2", "name": "branch1", "desc": "Branch One"},
        ])
    })
    rows = organizations.list_organizations(conn)
    # The canonical id is the short site name — the /api/s/{site}/ segment.
    assert [r["id"] for r in rows] == ["default", "branch1"]
    assert rows[0]["name"] == "HQ" and rows[0]["internalId"] == "5c1"


@pytest.mark.unit
def test_networks_list_maps_sites_to_canonical_networks():
    conn = _OpsConn({
        "/api/self/sites": _envelope([{"_id": "5c1", "name": "default", "desc": "HQ"}])
    })
    rows = networks.list_networks(conn)
    assert rows[0]["id"] == "default" and rows[0]["name"] == "HQ"
    assert rows[0]["productTypes"] == ["site"]


@pytest.mark.unit
def test_network_get_maps_stat_health_subsystems():
    conn = _OpsConn({
        "/api/s/default/stat/health": _envelope([
            {"subsystem": "wlan", "status": "ok", "num_adopted": 4, "num_user": 37},
            {"subsystem": "wan", "status": "warning"},
        ])
    })
    out = networks.get_network(conn, "default")
    assert out["overallStatus"] == "warning"
    assert out["health"][0] == {
        "subsystem": "wlan",
        "status": "ok",
        "deviceCount": 4,
        "userCount": 37,
    }


@pytest.mark.unit
def test_device_statuses_adapts_state_and_rolls_up():
    conn = _OpsConn({
        "/api/s/default/stat/device": _envelope([
            {
                "mac": "aa:aa",
                "name": "ap-1",
                "type": "uap",
                "state": 1,
                "version": "6.6.55",
                "uptime": 3600,
                "ip": "10.0.0.5",
            },
            {"mac": "bb:bb", "name": "sw-1", "type": "usw", "state": 0},
        ])
    })
    out = organizations.device_statuses(conn)
    assert out["total"] == 2
    assert out["byStatus"] == {"online": 1, "offline": 1}
    assert out["byProductType"] == {"wireless": 1, "switch": 1}
    row = out["devices"][0]
    assert row["serial"] == "aa:aa" and row["firmware"] == "6.6.55"
    assert row["uptimeSeconds"] == 3600 and row["lanIp"] == "10.0.0.5"


@pytest.mark.unit
def test_device_inventory_uses_the_target_default_site():
    conn = _OpsConn({
        "/api/s/default/stat/device": _envelope([
            {"mac": "aa:aa", "model": "U7PG2", "type": "uap", "state": 1}
        ])
    })
    out = devices.inventory(conn)
    assert out["total"] == 1
    assert out["devices"][0]["model"] == "U7PG2"
    # UniFi model strings carry no Meraki prefix: bucketed under 'other'.
    assert out["byModelFamily"] == {"other": 1}


@pytest.mark.unit
def test_device_get_fills_the_site_from_the_default_scope():
    conn = _OpsConn({
        "/api/s/default/stat/device/aa%3Abb": _envelope([
            {"mac": "aa:bb", "name": "core-sw", "type": "usw", "state": 1}
        ])
    })
    from fabric_aiops.ops._util import clean, op_get

    out = clean(op_get(conn, "devices.get", serial="aa:bb"))
    assert out["serial"] == "aa:bb" and out["status"] == "online"
    assert out["productType"] == "switch"


@pytest.mark.unit
def test_switch_ports_come_from_the_device_port_table():
    conn = _OpsConn({
        "/api/s/default/stat/device/aa%3Abb": _envelope([
            {
                "mac": "aa:bb",
                "type": "usw",
                "port_table": [
                    {
                        "port_idx": 1,
                        "name": "uplink",
                        "enable": True,
                        "up": True,
                        "speed": 1000,
                        "full_duplex": True,
                        "poe_enable": False,
                    },
                    {"port_idx": 2, "name": "cam-2", "enable": True, "up": False},
                ],
            }
        ])
    })
    rows = devices.switch_ports(conn, "aa:bb")
    assert rows[0]["portId"] == "1" and rows[0]["status"] == "connected"
    assert rows[0]["duplex"] == "full" and rows[0]["enabled"] is True
    assert rows[1]["status"] == "disconnected"


@pytest.mark.unit
def test_switch_ports_without_default_site_teach_the_org_scope():
    conn = _OpsConn({}, org_id="")
    with pytest.raises(ValueError, match="org_id"):
        devices.switch_ports(conn, "aa:bb")


@pytest.mark.unit
def test_clients_list_maps_stat_sta_rows():
    conn = _OpsConn({
        "/api/s/default/stat/sta": _envelope([
            {
                "mac": "cc:cc",
                "hostname": "laptop-1",
                "ip": "10.0.1.5",
                "vlan": 20,
                "essid": "corp",
                "is_wired": False,
                "ap_mac": "aa:aa",
                "tx_bytes": 100,
                "rx_bytes": 200,
            }
        ])
    })
    rows = clients.list_clients(conn, "default")
    row = rows[0]
    assert row["id"] == "cc:cc" and row["description"] == "laptop-1"
    assert row["ssid"] == "corp" and row["status"] == "Online"
    assert row["usage"] == {"sent": 100, "recv": 200}
    assert row["uplinkMac"] == "aa:aa"


@pytest.mark.unit
def test_client_get_maps_stat_user():
    conn = _OpsConn({
        "/api/s/default/stat/user/cc%3Acc": _envelope([
            {"mac": "cc:cc", "name": "Printer", "ip": "10.0.1.9", "is_wired": True}
        ])
    })
    out = clients.get_client(conn, "default", "cc:cc")
    assert out["mac"] == "cc:cc" and out["description"] == "Printer"
    assert out["wired"] is True


@pytest.mark.unit
def test_network_alerts_maps_alarm_severity():
    conn = _OpsConn({
        "/api/s/default/stat/alarm": _envelope([
            {
                "key": "EVT_AP_Lost_Contact",
                "msg": "AP ap-1 was disconnected",
                "subsystem": "wlan",
                "ap": "aa:aa",
                "datetime": "2026-07-13T00:00:00Z",
            },
            {"key": "EVT_SW_RestartedUnknown", "msg": "sw-1 restarted", "sw": "bb:bb"},
            {"key": "EVT_GW_WANTransition", "msg": "old news", "archived": True},
        ])
    })
    out = networks.network_alerts(conn, "default")
    assert out["total"] == 3
    assert out["bySeverity"] == {"critical": 1, "warning": 1, "info": 1}
    first = out["alerts"][0]
    assert first["type"] == "EVT_AP_Lost_Contact"
    assert first["deviceSerial"] == "aa:aa" and first["category"] == "wlan"


# ── the one mapped write: devmgr restart command envelope ────────────────────


class _WriteConn(_OpsConn):
    def __init__(self, responses, org_id="default"):
        super().__init__(responses, org_id=org_id)
        self.posts: list[tuple[str, dict | None]] = []

    def post(self, path, json=None, **_kw):
        self.posts.append((path, json))
        return _envelope([])


@pytest.mark.unit
def test_reboot_device_posts_devmgr_envelope_and_captures_prior_state():
    conn = _WriteConn({
        "/api/s/default/stat/device/aa%3Abb": _envelope([
            {"mac": "aa:bb", "name": "ap-1", "type": "uap", "state": 1}
        ])
    })
    out = rem_ops.reboot_device(conn, "aa:bb")
    # The command rides the body (never the path): site-scoped devmgr envelope.
    assert conn.posts == [
        ("/api/s/default/cmd/devmgr", {"cmd": "restart-device", "mac": "aa:bb"})
    ]
    # Prior state is fetched from the controller, not guessed.
    assert out["priorState"] == {"status": "online"}
    assert out["action"] == "reboot_device" and out["serial"] == "aa:bb"


@pytest.mark.unit
def test_reboot_body_never_carries_a_raw_hostile_path_segment():
    conn = _WriteConn({})
    rem_ops.reboot_device(conn, "../evil")
    path, body = conn.posts[0]
    assert "../" not in path and path == "/api/s/default/cmd/devmgr"
    assert body["mac"] == "../evil"  # ids in the body are data, not path


# ── unsupported ops + writes: teaching errors, never silent no-ops ──────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "call",
    [
        lambda c: organizations.get_organization(c),
        lambda c: organizations.licensing_overview(c),
        lambda c: organizations.list_admins(c),
        lambda c: organizations.api_request_usage(c),
        lambda c: networks.list_vlans(c, "default"),
        lambda c: networks.traffic_summary(c, "default"),
        lambda c: devices.uplink_status(c),
        lambda c: devices.wireless_ssids(c, "default"),
        lambda c: clients.client_usage(c, "default", "cc:cc"),
        lambda c: clients.client_connectivity(c, "default", "cc:cc"),
    ],
)
def test_unmapped_reads_raise_teaching_error(call):
    conn = _OpsConn({}, org_id="default")
    with pytest.raises(PlatformUnsupported, match="not supported on UniFi Network"):
        call(conn)


@pytest.mark.unit
@pytest.mark.parametrize(
    "call",
    [
        lambda c: rem_ops.blink_device_leds(c, "aa:bb"),
        lambda c: rem_ops.update_device(c, "aa:bb", {"name": "x"}),
        lambda c: rem_ops.update_network_vlan(c, "default", "10", {"name": "x"}),
        lambda c: rem_ops.claim_devices_into_network(c, "default", ["aa:bb"]),
        lambda c: rem_ops.remove_device_from_network(c, "default", "aa:bb"),
        lambda c: rem_ops.bind_network_to_template(c, "default", "t1"),
        lambda c: rem_ops.unbind_network_from_template(c, "default"),
    ],
)
def test_unmapped_writes_fail_fast_before_any_controller_call(call):
    """Every write except reboot is unmapped on unifi: the teaching error must
    fire BEFORE any request (never a silent no-op, never a partial write)."""

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
    with pytest.raises(PlatformUnsupported, match="issue or PR"):
        call(conn)
    assert conn.called is False


@pytest.mark.unit
def test_teaching_error_names_platforms_that_do_support_the_op():
    with pytest.raises(PlatformUnsupported, match="meraki"):
        get_platform(UNIFI).require("networks.vlan_update")


# ── doctor speaks the unifi vocabulary ──────────────────────────────────────


@pytest.mark.unit
def test_doctor_probes_unifi_sites(monkeypatch, tmp_path, capsys):
    """doctor's connectivity probe stays canonical: a unifi target is probed
    via /api/self/sites and reported in the platform's own vocabulary."""
    import fabric_aiops.connection as conn_mod
    import fabric_aiops.doctor as doctor_mod
    from fabric_aiops.config import AppConfig

    targets = (
        _target(base_url="https://console.example.com/proxy/network"),
    )

    class _FakeConn:
        def __init__(self, target):
            self.target = target

        def get_pages(self, path, params=None, **_kw):
            assert path == "/api/self/sites"
            return [_envelope([
                {"_id": "5c1", "name": "default", "desc": "HQ"},
                {"_id": "5c2", "name": "branch1", "desc": "Branch"},
            ])]

    class _FakeManager:
        def __init__(self, config):
            self._config = config

        def connect(self, name):
            return _FakeConn(self._config.get_target(name))

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("targets: []", "utf-8")
    env_file = tmp_path / ".env"  # legacy-store branch: warns, not a failure
    env_file.write_text("", "utf-8")
    monkeypatch.setenv("FABRIC_U1_APIKEY", API_KEY)
    monkeypatch.setattr(doctor_mod, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(doctor_mod, "ENV_FILE", env_file)
    monkeypatch.setattr(doctor_mod, "load_config", lambda: AppConfig(targets=targets))
    monkeypatch.setattr(doctor_mod, "has_store", lambda: False)
    monkeypatch.setattr("fabric_aiops.config.has_store", lambda: False)
    monkeypatch.setattr(conn_mod, "ConnectionManager", _FakeManager)

    assert doctor_mod.run_doctor() == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "Secret present for 'u1' (UniFi API key)" in out
    assert "Connected to 'u1' (UniFi Network API) — 2 sites visible" in out
