"""Tests for the ``fabric-aiops init`` onboarding wizard.

The wizard is multi-platform: ``meraki`` has a cloud default base URL while the
on-prem controllers (``catalyst``, ``cvp``) require one, and each platform asks
for its own kind of secret (API key / username:password / service-account
token). Driven end-to-end through Typer's CliRunner with every path
(config.yaml, secrets.enc) isolated under tmp_path. The master
password comes from FABRIC_AIOPS_MASTER_PASSWORD (the non-interactive path)
and the hidden secret prompt is patched at the getpass boundary, recording the
prompt text so per-platform hints can be asserted.
"""

from __future__ import annotations

import getpass as getpass_mod

import pytest
import yaml
from typer.testing import CliRunner

import fabric_aiops.cli.init as init_mod
import fabric_aiops.config as config_mod
import fabric_aiops.doctor as doctor_mod
import fabric_aiops.secretstore as ss

pytestmark = pytest.mark.unit

MASTER_PW = "init-master-pw"
SECRET = "controller-secret-material"

# Wizard answers (meraki, the default platform): name, accept platform default,
# accept the cloud base-URL default, no default org id, accept the TLS confirm
# default (True), no second target, decline the trailing doctor run.
MERAKI_INPUT = "org1\n\n\n\n\nn\nn\n"


@pytest.fixture
def init_home(tmp_path, monkeypatch):
    """Isolate config + secret store + governance home under tmp_path."""
    config_file = tmp_path / "config.yaml"
    secrets_file = tmp_path / "secrets.enc"
    monkeypatch.setenv("FABRIC_AIOPS_HOME", str(tmp_path))
    monkeypatch.setenv(ss.MASTER_PASSWORD_ENV, MASTER_PW)
    monkeypatch.setattr(init_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(init_mod, "CONFIG_FILE", config_file)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)
    monkeypatch.setattr(ss, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(ss, "SECRETS_FILE", secrets_file)
    monkeypatch.setattr(ss, "LEGACY_ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(ss, "_cached", None)
    return tmp_path


@pytest.fixture
def secret_prompts(monkeypatch):
    """Patch the hidden secret prompt; record its text for hint assertions."""
    prompts: list[str] = []
    monkeypatch.setattr(
        getpass_mod, "getpass", lambda prompt="": prompts.append(prompt) or SECRET
    )
    return prompts


def _run_init(input_text: str = MERAKI_INPUT):
    from fabric_aiops.cli import app

    return CliRunner().invoke(app, ["init"], input=input_text)


def _config(init_home) -> dict:
    return yaml.safe_load((init_home / "config.yaml").read_text("utf-8"))


def test_init_meraki_default_platform_and_cloud_base_url(init_home, secret_prompts):
    result = _run_init()
    assert result.exit_code == 0, result.output
    # Cloud default base_url, empty org_id and verify_ssl=True are all
    # defaults — the entry stays minimal.
    assert _config(init_home)["targets"] == [{"name": "org1", "platform": "meraki"}]
    assert "Meraki API key" in secret_prompts[0]


def test_init_catalyst_requires_base_url_and_asks_its_secret(init_home, secret_prompts):
    # name, platform, bare host (https:// gets prefixed), no org id, accept
    # TLS default, no second target, decline doctor.
    result = _run_init("cc1\ncatalyst\ncc.example.com\n\n\nn\nn\n")
    assert result.exit_code == 0, result.output
    assert _config(init_home)["targets"] == [
        {"name": "cc1", "platform": "catalyst", "base_url": "https://cc.example.com"}
    ]
    assert "Catalyst Center login (username:password)" in secret_prompts[0]


def test_init_cvp_requires_base_url_and_asks_its_secret(init_home, secret_prompts):
    # A trailing slash on the entered URL must be normalised away.
    result = _run_init("cvp1\ncvp\nhttps://cvp.example.com/\n\n\nn\nn\n")
    assert result.exit_code == 0, result.output
    assert _config(init_home)["targets"] == [
        {"name": "cvp1", "platform": "cvp", "base_url": "https://cvp.example.com"}
    ]
    assert "CloudVision service-account token" in secret_prompts[0]


def test_init_unknown_platform_reprompts(init_home, secret_prompts):
    result = _run_init("org1\nacme-cloud\nmeraki\n\n\n\nn\nn\n")
    assert result.exit_code == 0, result.output
    assert "Unknown platform 'acme-cloud'" in " ".join(result.output.split())
    assert _config(init_home)["targets"][0]["platform"] == "meraki"


def test_init_tls_decline_writes_verify_ssl_false(init_home, secret_prompts):
    # Explicit "n" on the TLS confirm (self-signed lab controllers).
    result = _run_init("org1\n\n\n\nn\nn\nn\n")
    assert result.exit_code == 0, result.output
    assert _config(init_home)["targets"][0]["verify_ssl"] is False


def test_init_org_id_recorded_when_given(init_home, secret_prompts):
    result = _run_init("org1\n\n\n123456\n\nn\nn\n")
    assert result.exit_code == 0, result.output
    assert _config(init_home)["targets"][0]["org_id"] == "123456"


def test_init_stores_secret_encrypted_not_in_config(init_home, secret_prompts):
    result = _run_init()
    assert result.exit_code == 0, result.output
    # Secret is readable back through the secret store API...
    assert ss.SecretStore.unlock(MASTER_PW).get("org1") == SECRET
    # ...and never lands in plaintext in config.yaml or secrets.enc.
    assert SECRET not in (init_home / "config.yaml").read_text("utf-8")
    assert SECRET not in (init_home / "secrets.enc").read_text("utf-8")


def test_init_writes_no_policy_rules(init_home, secret_prompts):
    """The skill no longer authorizes, so init seeds no rules.yaml — a fresh
    install delivers full functionality and leaves permission to the account."""
    result = _run_init()
    assert result.exit_code == 0, result.output
    assert not (init_home / "rules.yaml").exists()


def test_init_declining_doctor_confirm_skips_doctor(init_home, secret_prompts, monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(doctor_mod, "run_doctor", lambda: calls.append(True) or 0)
    result = _run_init()  # MERAKI_INPUT ends with an explicit "n"
    assert result.exit_code == 0, result.output
    assert calls == []


def test_init_accepting_doctor_confirm_runs_doctor(init_home, secret_prompts, monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(doctor_mod, "run_doctor", lambda: calls.append(True) or 0)
    # Empty last answer accepts the confirm's default=True.
    result = _run_init("org1\n\n\n\n\nn\n\n")
    assert result.exit_code == 0, result.output
    assert calls == [True]


def test_init_overwrite_existing_target(init_home, secret_prompts):
    result = _run_init()
    assert result.exit_code == 0, result.output
    # Same name again: confirm overwrite, switch it to cvp with a base URL.
    result = _run_init("org1\ny\ncvp\nhttps://cvp.example.com\n\n\nn\nn\n")
    assert result.exit_code == 0, result.output
    targets = _config(init_home)["targets"]
    assert [t["platform"] for t in targets] == ["cvp"]
