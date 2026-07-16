"""Tests for ``fabric_aiops.doctor.run_doctor``.

fabric-aiops is multi-platform (meraki / catalyst / cvp); the doctor probes the
canonical ``orgs.list`` on each target and reports in the platform's own
vocabulary (organizations / sites / containers). All filesystem paths are
redirected to a tmp dir and the connection layer is mocked at the
ConnectionManager + ``op_get_pages`` boundary — no test ever touches a real
controller or the real ``~/.fabric-aiops``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import yaml

import fabric_aiops.config as config_mod
import fabric_aiops.doctor as doctor_mod
import fabric_aiops.secretstore as ss
from fabric_aiops.doctor import run_doctor

pytestmark = pytest.mark.unit

MASTER_PW = "test-master-pw"


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Redirect every config/secret path constant at a throwaway directory."""
    config_file = tmp_path / "config.yaml"
    env_file = tmp_path / ".env"
    secrets_file = tmp_path / "secrets.enc"

    monkeypatch.setenv("FABRIC_AIOPS_HOME", str(tmp_path))
    monkeypatch.setenv(ss.MASTER_PASSWORD_ENV, MASTER_PW)

    # config module reads its globals at call time.
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)
    monkeypatch.setattr(config_mod, "ENV_FILE", env_file)
    # doctor imported the names directly; patch its namespace too.
    monkeypatch.setattr(doctor_mod, "CONFIG_FILE", config_file)
    monkeypatch.setattr(doctor_mod, "ENV_FILE", env_file)
    monkeypatch.setattr(doctor_mod, "SECRETS_FILE", secrets_file)
    # secret store paths + cache.
    monkeypatch.setattr(ss, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(ss, "SECRETS_FILE", secrets_file)
    monkeypatch.setattr(ss, "LEGACY_ENV_FILE", env_file)
    monkeypatch.setattr(ss, "_cached", None)
    return tmp_path


def _write_config(home, targets: list[dict]) -> None:
    (home / "config.yaml").write_text(yaml.safe_dump({"targets": targets}), "utf-8")


def _store_secret(name: str, value: str = "api-key") -> None:
    ss.SecretStore.unlock(MASTER_PW).set(name, value)


@pytest.fixture
def ok_connection(monkeypatch):
    """Mock the connectivity boundary: ConnectionManager + the canonical
    ``orgs.list`` probe (two rows visible on every platform)."""
    mgr = MagicMock(name="ConnectionManager")
    monkeypatch.setattr("fabric_aiops.connection.ConnectionManager", mgr)
    monkeypatch.setattr(
        "fabric_aiops.ops._util.op_get_pages",
        lambda conn, key, **kw: [{"id": "1"}, {"id": "2"}],
    )
    return mgr


def test_missing_config_file(isolated_home, capsys):
    assert run_doctor() == 1
    out = capsys.readouterr().out
    assert "Config file missing" in out
    assert "fabric-aiops init" in out


def test_config_load_failure_reported_not_raised(isolated_home, capsys):
    # An unregistered platform makes TargetConfig fail fast; doctor must
    # report the failure as a check, never a traceback.
    _write_config(isolated_home, [{"name": "org1", "platform": "acme-cloud"}])
    assert run_doctor() == 1
    assert "Config load failed" in capsys.readouterr().out


def test_no_targets_configured(isolated_home, capsys):
    _write_config(isolated_home, [])
    assert run_doctor() == 1
    assert "No targets configured" in capsys.readouterr().out


def test_meraki_healthy_reports_organizations(isolated_home, ok_connection, capsys):
    _write_config(isolated_home, [{"name": "org1", "platform": "meraki"}])
    _store_secret("org1")
    assert run_doctor() == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "Config file present" in out
    assert "1 target(s) configured" in out
    assert "Encrypted secret store present" in out
    assert "Secret present for 'org1' (Meraki API key)" in out
    assert "Connected to 'org1' (Cisco Meraki Dashboard API) — 2 organizations visible" in out
    ok_connection.return_value.connect.assert_called_once_with("org1")


def test_catalyst_healthy_reports_sites(isolated_home, ok_connection, capsys):
    _write_config(
        isolated_home,
        [{"name": "cc1", "platform": "catalyst", "base_url": "https://cc.example.com"}],
    )
    _store_secret("cc1", "admin:pa55")
    assert run_doctor() == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "Secret present for 'cc1' (Catalyst Center login (username:password))" in out
    assert "Connected to 'cc1' (Cisco Catalyst Center API) — 2 sites visible" in out


def test_cvp_healthy_reports_containers(isolated_home, ok_connection, capsys):
    _write_config(
        isolated_home,
        [{"name": "cvp1", "platform": "cvp", "base_url": "https://cvp.example.com"}],
    )
    _store_secret("cvp1", "svc-token")
    assert run_doctor() == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "Secret present for 'cvp1' (CloudVision service-account token)" in out
    assert "Connected to 'cvp1' (Arista CloudVision Portal API) — 2 containers visible" in out


def test_onprem_platform_missing_base_url_teaching_error(isolated_home, capsys):
    # Catalyst Center is per-install: without base_url the doctor must teach
    # how to fix it (config key + init), not stack-trace.
    _write_config(isolated_home, [{"name": "cc1", "platform": "catalyst"}])
    _store_secret("cc1", "admin:pa55")
    assert run_doctor(skip_auth=True) == 1
    out = " ".join(capsys.readouterr().out.split())
    assert "has no API base URL" in out
    assert "base_url" in out


def test_missing_secret_is_a_problem(isolated_home, capsys):
    _write_config(isolated_home, [{"name": "org1", "platform": "meraki"}])
    _store_secret("other-target")  # store exists, but not for this target
    assert run_doctor(skip_auth=True) == 1
    out = " ".join(capsys.readouterr().out.split())
    assert "No API key for target 'org1'" in out


def test_no_secret_store_yet_warns_and_fails(isolated_home, capsys):
    _write_config(isolated_home, [{"name": "org1", "platform": "meraki"}])
    assert run_doctor(skip_auth=True) == 1
    assert "No secret store yet" in capsys.readouterr().out


def test_skip_auth_never_touches_connection_layer(isolated_home, monkeypatch, capsys):
    _write_config(isolated_home, [{"name": "org1", "platform": "meraki"}])
    _store_secret("org1")

    def _boom(*a, **k):  # pragma: no cover — must not be reached
        raise AssertionError("ConnectionManager must not be constructed with --skip-auth")

    monkeypatch.setattr("fabric_aiops.connection.ConnectionManager", _boom)
    assert run_doctor(skip_auth=True) == 0
    assert "Skipping connectivity check" in capsys.readouterr().out


def test_connect_failure_reported_per_target(isolated_home, ok_connection, capsys):
    _write_config(
        isolated_home,
        [{"name": "org-a", "platform": "meraki"}, {"name": "org-b", "platform": "meraki"}],
    )
    _store_secret("org-a")
    _store_secret("org-b")

    def _connect(name):
        if name == "org-b":
            raise ConnectionError("401 Unauthorized from controller")
        return MagicMock(name="conn")

    ok_connection.return_value.connect.side_effect = _connect
    assert run_doctor() == 1
    out = " ".join(capsys.readouterr().out.split())
    assert "Connected to 'org-a'" in out
    assert "Connect to 'org-b' failed: 401 Unauthorized" in out


def test_legacy_env_file_warns_but_env_secret_passes(isolated_home, monkeypatch, capsys):
    _write_config(isolated_home, [{"name": "org1", "platform": "meraki"}])
    (isolated_home / ".env").write_text("FABRIC_ORG1_APIKEY=legacy\n")
    monkeypatch.setenv("FABRIC_ORG1_APIKEY", "legacy")
    assert run_doctor(skip_auth=True) == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "legacy plaintext .env" in out
    assert "Secret present for 'org1'" in out


def test_permission_warning_surfaced(isolated_home, capsys):
    _write_config(isolated_home, [{"name": "org1", "platform": "meraki"}])
    _store_secret("org1")
    (isolated_home / "secrets.enc").chmod(0o644)
    assert run_doctor(skip_auth=True) == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "should be 600" in out


def test_cli_doctor_command_exits_with_doctor_code(isolated_home):
    from typer.testing import CliRunner

    from fabric_aiops.cli import app

    _write_config(isolated_home, [{"name": "org1", "platform": "meraki"}])
    _store_secret("org1")
    result = CliRunner().invoke(app, ["doctor", "--skip-auth"])
    assert result.exit_code == 0
    assert "Skipping connectivity check" in result.output
