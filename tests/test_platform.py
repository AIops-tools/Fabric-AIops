"""Platform registry + connection wiring (Meraki), and the config dispatch."""

import pytest

from fabric_aiops.config import TargetConfig
from fabric_aiops.connection import FabricApiError, FabricConnection
from fabric_aiops.platform import (
    AUTH_MERAKI_KEY,
    CANONICAL_OPS,
    CANONICAL_WRITES,
    CATALYST,
    CVP,
    DEFAULT_MERAKI_BASE_URL,
    MERAKI,
    UNIFI,
    PlatformUnsupported,
    get_platform,
    parse_next_link,
    platform_names,
    platforms_supporting,
    seg,
)


@pytest.mark.unit
def test_meraki_is_registered_and_default():
    assert MERAKI in platform_names()
    p = get_platform(MERAKI)
    assert p.default_base_url == DEFAULT_MERAKI_BASE_URL
    assert p.label == "Cisco Meraki Dashboard API"


@pytest.mark.unit
def test_four_platforms_registered():
    assert set(platform_names()) == {MERAKI, CATALYST, CVP, UNIFI}


@pytest.mark.unit
def test_meraki_is_the_reference_platform_covering_every_canonical_op():
    p = get_platform(MERAKI)
    missing = [key for key in CANONICAL_OPS if not p.supports(key)]
    assert missing == []


@pytest.mark.unit
def test_writes_are_meraki_only_except_unifi_reboot():
    for key in CANONICAL_WRITES:
        if key == "devices.reboot":
            assert platforms_supporting(key) == (MERAKI, UNIFI)
        else:
            assert platforms_supporting(key) == (MERAKI,)


@pytest.mark.unit
def test_meraki_request_for_builds_encoded_paths_and_passes_params_through():
    p = get_platform(MERAKI)
    path, query = p.request_for("orgs.get", {"org_id": "../evil"})
    assert path == "/organizations/..%2Fevil" and query == {}
    path, query = p.request_for(
        "clients.list", {"network_id": "N 1"}, {"timespan": 7200}
    )
    assert path == "/networks/N%201/clients"
    assert query == {"timespan": 7200}  # reference platform: passthrough


@pytest.mark.unit
def test_request_for_missing_required_id_teaches():
    with pytest.raises(ValueError, match="requires: org_id"):
        get_platform(MERAKI).request_for("orgs.get", {})


@pytest.mark.unit
def test_request_for_unknown_key_raises_platform_unsupported():
    with pytest.raises(PlatformUnsupported, match="issue or PR"):
        get_platform(MERAKI).request_for("nonsense.op", {})


@pytest.mark.unit
def test_unknown_platform_raises_with_registered_names():
    with pytest.raises(ValueError, match="meraki"):
        get_platform("catalyst-center")


@pytest.mark.unit
def test_doctor_connectivity_uses_canonical_org_probe_per_platform(monkeypatch, tmp_path, capsys):
    """doctor's connectivity probe is canonical: a catalyst target reports
    sites, a cvp target reports containers (no Meraki path hardcoded)."""
    import fabric_aiops.connection as conn_mod
    import fabric_aiops.doctor as doctor_mod
    from fabric_aiops.config import AppConfig

    targets = (
        TargetConfig(name="cc", platform=CATALYST, base_url="https://cc.example.com"),
        TargetConfig(name="cv", platform=CVP, base_url="https://cvp.example.com"),
    )

    class _FakeConn:
        def __init__(self, target):
            self.target = target

        def get_pages(self, path, params=None, **_kw):
            if self.target.platform == CATALYST:
                assert path == "/dna/intent/api/v1/site"
                return [{"response": [{"id": "s1", "name": "HQ"}]}]
            assert path == "/cvpservice/inventory/containers"
            return [{"key": "root", "name": "Tenant"}]

    class _FakeManager:
        def __init__(self, config):
            self._config = config

        def connect(self, name):
            return _FakeConn(self._config.get_target(name))

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("targets: []", "utf-8")
    monkeypatch.setenv("FABRIC_CC_APIKEY", "admin:pw")
    monkeypatch.setenv("FABRIC_CV_APIKEY", "svc-token")
    env_file = tmp_path / ".env"  # legacy-store branch: warns, not a failure
    env_file.write_text("", "utf-8")
    monkeypatch.setattr(doctor_mod, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(doctor_mod, "ENV_FILE", env_file)
    monkeypatch.setattr(doctor_mod, "load_config", lambda: AppConfig(targets=targets))
    monkeypatch.setattr(doctor_mod, "has_store", lambda: False)
    monkeypatch.setattr("fabric_aiops.config.has_store", lambda: False)
    monkeypatch.setattr(conn_mod, "ConnectionManager", _FakeManager)

    assert doctor_mod.run_doctor() == 0
    out = capsys.readouterr().out
    assert "sites" in out and "containers" in out


@pytest.mark.unit
def test_bearer_auth_header_is_default():
    headers = get_platform(MERAKI).auth_headers("KEY123")
    assert headers["Authorization"] == "Bearer KEY123"
    assert headers["Accept"] == "application/json"


@pytest.mark.unit
def test_meraki_key_auth_style():
    headers = get_platform(MERAKI).auth_headers("KEY123", AUTH_MERAKI_KEY)
    assert headers["X-Cisco-Meraki-API-Key"] == "KEY123"
    assert "Authorization" not in headers


@pytest.mark.unit
def test_normalise_sanitizes_strings():
    out = get_platform(MERAKI).normalise({"name": "ok", "n": 5, "nested": {"x": "y"}})
    assert out["name"] == "ok" and out["n"] == 5 and out["nested"]["x"] == "y"


@pytest.mark.unit
def test_parse_next_link():
    header = '<https://api.meraki.com/api/v1/organizations/1/devices?startingAfter=Q2>; rel=next'
    assert parse_next_link(header).endswith("startingAfter=Q2")
    assert parse_next_link('<https://x>; rel=first') is None
    assert parse_next_link(None) is None


@pytest.mark.unit
def test_target_config_defaults_and_api_base():
    t = TargetConfig(name="org1")
    assert t.platform == "meraki"
    assert t.api_base == DEFAULT_MERAKI_BASE_URL
    override = TargetConfig(name="o2", base_url="https://api.meraki.eu/api/v1")
    assert override.api_base == "https://api.meraki.eu/api/v1"


@pytest.mark.unit
def test_target_config_rejects_unknown_platform():
    with pytest.raises(ValueError):
        TargetConfig(name="x", platform="nope")


@pytest.mark.unit
def test_connection_bearer_auth_and_error_translation(monkeypatch):
    """FabricConnection sends Bearer auth and translates non-2xx to FabricApiError."""
    monkeypatch.setenv("FABRIC_ORG1_APIKEY", "secret-key")
    target = TargetConfig(name="org1", verify_ssl=False)

    class _Resp:
        def __init__(self, status, payload=None, content=b"{}"):
            self.status_code = status
            self._payload = payload or {}
            self.content = content
            self.text = "body"
            self.headers = {}

        def json(self):
            return self._payload

    class _Client:
        def request(self, method, path, **k):
            if path == "/notfound":
                return _Resp(404, content=b"x")
            return _Resp(200, [{"id": "1", "name": "Acme"}], content=b"[]")

        def close(self):
            pass

    conn = FabricConnection(target, client=_Client())
    assert conn.get("/organizations")[0]["name"] == "Acme"
    with pytest.raises(FabricApiError) as ei:
        conn.get("/notfound")
    assert ei.value.status_code == 404
    assert "not found" in str(ei.value).lower()


@pytest.mark.unit
def test_connection_get_pages_follows_link_header():
    """get_pages aggregates across pages via the Link rel=next header."""
    target = TargetConfig(name="org1")

    class _Resp:
        def __init__(self, payload, link=None):
            self.status_code = 200
            self._payload = payload
            self.content = b"[]"
            self.text = ""
            self.headers = {"Link": link} if link else {}

        def json(self):
            return self._payload

    class _Client:
        def __init__(self):
            self.calls = 0

        def request(self, method, path, **k):
            self.calls += 1
            if self.calls == 1:
                return _Resp([{"id": "a"}], link="<https://x/next?startingAfter=a>; rel=next")
            return _Resp([{"id": "b"}])

        def close(self):
            pass

    # api_key not needed: client is injected, so no auth header is built.
    conn = FabricConnection.__new__(FabricConnection)
    conn._target = target
    conn._client = _Client()
    rows = conn.get_pages("/organizations/1/devices")
    assert [r["id"] for r in rows] == ["a", "b"]


@pytest.mark.unit
def test_seg_encodes_hostile_path_segments():
    """Agent-supplied ids are URL-encoded: no traversal, no smuggled query."""
    assert seg("N_123") == "N_123"
    assert "/" not in seg("../admin")
    assert "../" not in seg("../admin")
    assert seg("a b?x=1#f") == "a%20b%3Fx%3D1%23f"
    assert seg(42) == "42"


@pytest.mark.unit
def test_ops_path_interpolation_never_emits_raw_traversal():
    """An id containing ``../`` must never reach the client as a raw ``../``
    path segment — the ops layer routes every interpolated value through seg()."""
    from unittest.mock import MagicMock

    from fabric_aiops.ops import networks as net_ops
    from fabric_aiops.ops import remediation as rem_ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {"id": "N1"}
    conn.post.return_value = {}

    net_ops.get_network(conn, "../../admin")
    path = conn.get.call_args[0][0]
    assert "../" not in path
    assert path == "/networks/..%2F..%2Fadmin"

    conn.reset_mock()
    conn.get.return_value = {"serial": "S1", "status": "online"}
    rem_ops.reboot_device(conn, "../evil")
    posted = conn.post.call_args[0][0]
    assert "../" not in posted
    assert posted == "/devices/..%2Fevil/reboot"


@pytest.mark.unit
def test_atexit_hook_closes_cached_clients_and_never_raises():
    """The atexit closer shuts down every cached client, is idempotent, and
    swallows close errors (never raises at interpreter exit)."""
    from unittest.mock import MagicMock

    from fabric_aiops import connection as conn_mod
    from fabric_aiops.config import AppConfig

    mgr = conn_mod.ConnectionManager(AppConfig(targets=[TargetConfig(name="org1")]))
    fake = MagicMock(name="fabric-conn")
    mgr._connections["org1"] = fake

    conn_mod._close_all_managers()
    fake.close.assert_called_once()
    assert mgr.list_connected() == []

    # Idempotent + error-safe: a second run and a failing close never raise.
    conn_mod._close_all_managers()
    bad = MagicMock(name="bad-conn")
    bad.close.side_effect = RuntimeError("boom")
    mgr._connections["org1"] = bad
    conn_mod._close_all_managers()  # must not raise
