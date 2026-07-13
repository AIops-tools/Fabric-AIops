"""Read-path ops tests (organizations / networks / devices / clients / overview).

Uses a lightweight fake connection that returns canned JSON for each path, so
the normalisation, model-bucketing, rollups, and org-id resolution are exercised
without a live Meraki organization.
"""

import pytest

from fabric_aiops.config import TargetConfig
from fabric_aiops.ops import clients, devices, networks, organizations, overview


class _Conn:
    """Fake connection: get/get_pages look up canned responses by path."""

    def __init__(self, responses, org_id="O1"):
        self._responses = responses
        self.target = TargetConfig(name="t", org_id=org_id)

    def get(self, path, **_kw):
        return self._responses[path]

    def get_pages(self, path, params=None, **_kw):
        data = self._responses[path]
        return data if isinstance(data, list) else [data]


@pytest.mark.unit
def test_require_org_uses_target_default():
    conn = _Conn({"/organizations/O1": {"id": "O1", "name": "Acme"}})
    assert organizations.get_organization(conn)["name"] == "Acme"


@pytest.mark.unit
def test_require_org_raises_without_default():
    conn = _Conn({}, org_id="")
    with pytest.raises(ValueError, match="organization id"):
        organizations.device_statuses(conn)


@pytest.mark.unit
def test_org_device_statuses_rollup():
    conn = _Conn({
        "/organizations/O1/devices/statuses": [
            {"serial": "Q1", "status": "online", "productType": "switch"},
            {"serial": "Q2", "status": "offline", "productType": "switch"},
            {"serial": "Q3", "status": "online", "productType": "wireless"},
        ]
    })
    out = organizations.device_statuses(conn)
    assert out["total"] == 3
    assert out["byStatus"]["online"] == 2
    assert out["byProductType"]["switch"] == 2


@pytest.mark.unit
def test_api_request_usage_totals_and_429():
    conn = _Conn({
        "/organizations/O1/apiRequests/overview": {
            "responseCodeCounts": {"200": 90, "429": 10}
        }
    })
    out = organizations.api_request_usage(conn)
    assert out["totalRequests"] == 100
    assert out["rateLimited429"] == 10


@pytest.mark.unit
def test_device_inventory_buckets_by_model_and_filters():
    conn = _Conn({
        "/organizations/O1/devices": [
            {"serial": "Q1", "model": "MX67"},
            {"serial": "Q2", "model": "MS220-8P"},
            {"serial": "Q3", "model": "MR46"},
            {"serial": "Q4", "model": "MS250"},
        ]
    })
    allout = devices.inventory(conn)
    assert allout["total"] == 4
    assert allout["byModelFamily"]["MS"] == 2
    filtered = devices.inventory(conn, model="MS")
    assert filtered["matched"] == 2
    assert all(d["model"].startswith("MS") for d in filtered["devices"])


@pytest.mark.unit
def test_device_status_finds_serial():
    conn = _Conn({
        "/organizations/O1/devices/statuses": [
            {"serial": "Q1", "status": "online"},
            {"serial": "Q2", "status": "dormant"},
        ]
    })
    assert devices.device_status(conn, "Q2")["status"] == "dormant"
    with pytest.raises(KeyError):
        devices.device_status(conn, "NOPE")


@pytest.mark.unit
def test_network_alerts_by_severity():
    conn = _Conn({
        "/networks/N1/health/alerts": [
            {"type": "a", "severity": "critical"},
            {"type": "b", "severity": "warning"},
            {"type": "c", "severity": "warning"},
        ]
    })
    out = networks.network_alerts(conn, "N1")
    assert out["total"] == 3
    assert out["bySeverity"]["warning"] == 2


@pytest.mark.unit
def test_network_traffic_ranks_by_bytes():
    conn = _Conn({
        "/networks/N1/traffic": [
            {"application": "web", "sent": 10, "recv": 5},
            {"application": "video", "sent": 100, "recv": 200},
        ]
    })
    out = networks.traffic_summary(conn, "N1")
    assert out["topApplications"][0]["application"] == "video"
    assert out["topApplications"][0]["totalKb"] == 300


@pytest.mark.unit
def test_client_usage_sums_series():
    conn = _Conn({
        "/networks/N1/clients/C1/usageHistory": [
            {"sent": 10, "received": 20},
            {"sent": 5, "received": 5},
        ]
    })
    out = clients.client_usage(conn, "N1", "C1")
    assert out["totalSentKb"] == 15 and out["totalReceivedKb"] == 25
    assert out["totalKb"] == 40


@pytest.mark.unit
def test_client_connectivity_shape():
    conn = _Conn({
        "/networks/N1/clients/C1/connectionStats": {
            "assoc": 1, "auth": 0, "dhcp": 2, "dns": 0, "success": 100
        }
    })
    out = clients.client_connectivity(conn, "N1", "C1")
    assert out["dhcp"] == 2 and out["success"] == 100


@pytest.mark.unit
def test_fleet_overview_resilient_and_shapes():
    conn = _Conn({
        "/organizations/O1/networks": [{"id": "N1"}, {"id": "N2"}],
        "/organizations/O1/devices/statuses": [
            {"serial": "Q1", "status": "online", "productType": "switch"},
        ],
    })
    out = overview.fleet_overview(conn)
    assert out["networks"] == 2
    assert out["devicesTotal"] == 1
    assert out["organizationId"] == "O1"


@pytest.mark.unit
def test_fleet_overview_missing_org_returns_error():
    conn = _Conn({}, org_id="")
    out = overview.fleet_overview(conn)
    assert "error" in out
