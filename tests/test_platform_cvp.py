"""Arista CloudVision Portal platform: static Bearer auth, path building with
central encoding, response adaptation onto every mapped canonical key, and the
teaching errors for unmapped ops (all mocked — no live controller)."""

from __future__ import annotations

import pytest

from fabric_aiops.config import TargetConfig
from fabric_aiops.connection import FabricConnection
from fabric_aiops.ops import clients, devices, networks, organizations
from fabric_aiops.ops import remediation as rem_ops
from fabric_aiops.platform import (
    AUTH_FLOW_STATIC,
    CVP,
    PlatformUnsupported,
    get_platform,
    platform_names,
)


def _target(**kw) -> TargetConfig:
    kw.setdefault("name", "cvp1")
    kw.setdefault("platform", CVP)
    kw.setdefault("base_url", "https://cvp.example.com")
    return TargetConfig(**kw)


# ── registry + auth ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cvp_registered_with_static_bearer_auth():
    assert CVP in platform_names()
    p = get_platform(CVP)
    assert p.label == "Arista CloudVision Portal API"
    assert p.auth_flow == AUTH_FLOW_STATIC
    assert p.requires_base_url and p.requires_secret
    assert p.org_noun == "containers"
    headers = p.auth_headers("svc-token-123")
    assert headers["Authorization"] == "Bearer svc-token-123"


@pytest.mark.unit
def test_cvp_target_requires_base_url():
    t = TargetConfig(name="cvp-nourl", platform=CVP)
    with pytest.raises(ValueError, match="base_url"):
        _ = t.api_base


@pytest.mark.unit
def test_cvp_connection_sends_bearer_and_translates_errors(monkeypatch):
    """The service-account token rides Authorization: Bearer on the session."""
    monkeypatch.setenv("FABRIC_CVP1_APIKEY", "svc-token-123")
    captured = {}

    class _Client:
        def __init__(self, **kw):
            captured.update(kw)

        def request(self, method, path, **kw):
            class _R:
                status_code = 200
                content = b"[]"
                text = ""
                headers: dict = {}

                def json(self):
                    return []

            return _R()

        def close(self):
            pass

    monkeypatch.setattr("fabric_aiops.connection.httpx.Client", _Client)
    conn = FabricConnection(_target())
    conn.get("/cvpservice/inventory/devices")
    assert captured["headers"]["Authorization"] == "Bearer svc-token-123"
    assert captured["base_url"] == "https://cvp.example.com"


# ── path building (central percent-encoding + native query mapping) ─────────


@pytest.mark.unit
def test_request_for_maps_container_ids_to_query_params():
    p = get_platform(CVP)
    path, query = p.request_for("orgs.get", {"org_id": "root ?x"})
    assert path == "/cvpservice/provisioning/getContainerInfoById.do"
    assert query == {"containerId": "root ?x"}  # httpx encodes query values
    path, query = p.request_for("networks.get", {"network_id": "c-9"})
    assert query == {"containerId": "c-9"}


@pytest.mark.unit
def test_events_and_users_carry_required_paging_defaults():
    p = get_platform(CVP)
    _, query = p.request_for("networks.alerts", {"network_id": "ignored"})
    assert query["startIndex"] == "0" and query["endIndex"] == "200"
    _, query = p.request_for("orgs.admins", {"org_id": "ignored"})
    assert query["startIndex"] == "0" and "queryparam" in query


@pytest.mark.unit
def test_meraki_params_are_dropped_on_cvp():
    p = get_platform(CVP)
    _, query = p.request_for("devices.list", {"org_id": "c1"}, {"serials[]": "X"})
    assert "serials[]" not in query


# ── response adaptation per canonical key (through the real ops layer) ──────


class _OpsConn:
    def __init__(self, responses, org_id="container-1"):
        self._responses = responses
        self.target = _target(org_id=org_id)

    def get(self, path, **_kw):
        return self._responses[path]

    def get_pages(self, path, params=None, **_kw):
        data = self._responses[path]
        return data if isinstance(data, list) else [data]


_INVENTORY = [
    {
        "serialNumber": "JPE1",
        "hostname": "leaf-1",
        "fqdn": "leaf-1.example.com",
        "modelName": "DCS-7050SX3",
        "systemMacAddress": "00:1c:73:aa:bb:01",
        "ipAddress": "10.0.0.11",
        "version": "4.30.4M",
        "streamingStatus": "active",
        "parentContainerKey": "container-1",
        "complianceCode": "0000",
        "complianceIndication": "",
    },
    {
        "serialNumber": "JPE2",
        "hostname": "leaf-2",
        "modelName": "DCS-7050SX3",
        "systemMacAddress": "00:1c:73:aa:bb:02",
        "ipAddress": "10.0.0.12",
        "version": "4.30.4M",
        "streamingStatus": "inactive",
        "parentContainerKey": "container-1",
        "complianceCode": "0001",
        "complianceIndication": "WARNING",
    },
]


@pytest.mark.unit
def test_orgs_list_maps_containers_tolerant_of_key_casing():
    conn = _OpsConn({
        "/cvpservice/inventory/containers": [
            {"Key": "root", "Name": "Tenant"},
            {"key": "c-2", "name": "DC-2"},
        ]
    })
    rows = organizations.list_organizations(conn)
    assert [r["id"] for r in rows] == ["root", "c-2"]
    assert rows[0]["name"] == "Tenant"


@pytest.mark.unit
def test_networks_list_maps_containers_with_product_type():
    conn = _OpsConn({"/cvpservice/inventory/containers": [{"key": "c-2", "name": "DC-2"}]})
    rows = networks.list_networks(conn)
    assert rows[0] == {"id": "c-2", "name": "DC-2", "productTypes": ["container"]}


@pytest.mark.unit
def test_orgs_get_maps_container_info():
    conn = _OpsConn({
        "/cvpservice/provisioning/getContainerInfoById.do": {
            "key": "container-1",
            "name": "DC-1",
            "childContainerCount": 2,
            "netElementCount": 14,
        }
    })
    out = organizations.get_organization(conn)
    assert out["id"] == "container-1" and out["netElementCount"] == 14


@pytest.mark.unit
def test_device_inventory_maps_cvp_fields_and_compliance_drift_signal():
    conn = _OpsConn({"/cvpservice/inventory/devices": _INVENTORY})
    out = devices.inventory(conn)
    assert out["total"] == 2
    row = out["devices"][0]
    assert row["serial"] == "JPE1" and row["name"] == "leaf-1"
    assert row["model"] == "DCS-7050SX3" and row["firmware"] == "4.30.4M"
    assert row["networkId"] == "container-1"
    # complianceCode is the CVP config-drift signal — must survive adaptation.
    assert out["devices"][1]["complianceCode"] == "0001"
    assert out["devices"][1]["complianceIndication"] == "WARNING"


@pytest.mark.unit
def test_device_statuses_rollup_from_streaming_status():
    conn = _OpsConn({"/cvpservice/inventory/devices": _INVENTORY})
    out = organizations.device_statuses(conn)
    assert out["total"] == 2
    assert out["byStatus"] == {"online": 1, "offline": 1}
    assert out["byProductType"] == {"switch": 2}


@pytest.mark.unit
def test_network_alerts_maps_events_by_severity():
    conn = _OpsConn({
        "/cvpservice/event/getAllEvents.do": {
            "total": 3,
            "data": [
                {"severity": "CRITICAL", "title": "Device unreachable", "objectId": "c-1"},
                {"severity": "WARNING", "title": "Config out of sync", "objectId": "c-1"},
                {"severity": "INFO", "title": "Task completed", "objectId": "c-1"},
            ],
        }
    })
    out = networks.network_alerts(conn, "c-1")
    assert out["total"] == 3
    assert out["bySeverity"] == {"critical": 1, "warning": 1, "info": 1}
    assert out["alerts"][1]["type"] == "Config out of sync"


@pytest.mark.unit
def test_org_admins_maps_users():
    conn = _OpsConn({
        "/cvpservice/user/getUsers.do": {
            "total": 1,
            "users": [
                {
                    "userId": "ops",
                    "firstName": "Net",
                    "lastName": "Ops",
                    "email": "ops@example.com",
                    "userStatus": "Enabled",
                }
            ],
        }
    })
    rows = organizations.list_admins(conn)
    assert rows[0]["name"] == "Net Ops" and rows[0]["email"] == "ops@example.com"


# ── unsupported ops + writes: teaching errors, never silent no-ops ──────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "call",
    [
        lambda c: organizations.licensing_overview(c),
        lambda c: organizations.api_request_usage(c),
        lambda c: networks.list_vlans(c, "c-1"),
        lambda c: networks.traffic_summary(c, "c-1"),
        lambda c: devices.uplink_status(c),
        lambda c: devices.switch_ports(c, "JPE1"),
        lambda c: devices.wireless_ssids(c, "c-1"),
        lambda c: clients.list_clients(c, "c-1"),
        lambda c: clients.get_client(c, "c-1", "aa:bb"),
    ],
)
def test_unmapped_reads_raise_teaching_error(call):
    conn = _OpsConn({}, org_id="container-1")
    with pytest.raises(PlatformUnsupported, match="not supported on Arista CloudVision"):
        call(conn)


@pytest.mark.unit
@pytest.mark.parametrize(
    "call",
    [
        lambda c: rem_ops.reboot_device(c, "JPE1"),
        lambda c: rem_ops.update_device(c, "JPE1", {"name": "x"}),
        lambda c: rem_ops.claim_devices_into_network(c, "c-1", ["JPE1"]),
        lambda c: rem_ops.bind_network_to_template(c, "c-1", "t1"),
    ],
)
def test_writes_fail_fast_with_issue_invitation(call):
    class _NoCallConn(_OpsConn):
        def __init__(self):
            super().__init__({})
            self.called = False

        def get(self, path, **kw):
            self.called = True
            raise AssertionError("controller must not be called")

        post = put = get

    conn = _NoCallConn()
    with pytest.raises(PlatformUnsupported, match="issue or PR"):
        call(conn)
    assert conn.called is False
