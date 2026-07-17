"""``fabric-aiops secret`` command bodies, driven against a real encrypted store
in a throwaway directory (master password supplied via the env var so nothing
prompts). Values are never printed; these assert the command wiring only."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

import fabric_aiops.cli.secret as secret_cli
import fabric_aiops.secretstore as ss
from fabric_aiops.cli import app

runner = CliRunner()

MASTER = "master-pw-123"


@pytest.fixture
def store_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(ss, "SECRETS_FILE", tmp_path / "secrets.enc")
    monkeypatch.setattr(ss, "LEGACY_ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(ss, "_cached", None)
    monkeypatch.setenv("FABRIC_AIOPS_MASTER_PASSWORD", MASTER)
    return tmp_path


@pytest.mark.unit
def test_secret_set_then_list(store_dir):
    r = runner.invoke(app, ["secret", "set", "org1", "--value", "api-key-xyz"])
    assert r.exit_code == 0, r.output
    assert "org1" in r.output
    assert "api-key-xyz" not in r.output  # value never echoed

    r = runner.invoke(app, ["secret", "list"])
    assert r.exit_code == 0, r.output
    assert "org1" in r.output


@pytest.mark.unit
def test_secret_list_empty_hints_how_to_add(store_dir):
    r = runner.invoke(app, ["secret", "list"])
    assert r.exit_code == 0, r.output
    assert "No secrets stored yet" in r.output


@pytest.mark.unit
def test_secret_rm(store_dir):
    runner.invoke(app, ["secret", "set", "org1", "--value", "k"])
    r = runner.invoke(app, ["secret", "rm", "org1"])
    assert r.exit_code == 0, r.output
    assert "Deleted" in r.output
    assert runner.invoke(app, ["secret", "list"]).output.count("org1") == 0


@pytest.mark.unit
def test_secret_migrate_imports_legacy_env(store_dir):
    (store_dir / ".env").write_text("FABRIC_ORG1_APIKEY=legacy-key\nFOO=bar\n")
    r = runner.invoke(app, ["secret", "migrate"])
    assert r.exit_code == 0, r.output
    assert "Imported 1" in r.output
    assert "org1" in r.output


@pytest.mark.unit
def test_secret_migrate_nothing_to_do(store_dir):
    r = runner.invoke(app, ["secret", "migrate"])
    assert r.exit_code == 0, r.output
    assert "Nothing to migrate" in r.output


@pytest.mark.unit
def test_secret_rotate_password_success(store_dir, monkeypatch):
    runner.invoke(app, ["secret", "set", "org1", "--value", "k"])
    monkeypatch.setattr(secret_cli.getpass, "getpass", lambda *a, **k: "brand-new-pw")
    r = runner.invoke(app, ["secret", "rotate-password"])
    assert r.exit_code == 0, r.output
    assert "rotated" in r.output.lower()


@pytest.mark.unit
def test_secret_rotate_password_mismatch_aborts(store_dir, monkeypatch):
    runner.invoke(app, ["secret", "set", "org1", "--value", "k"])
    answers = iter(["new-pw", "different-pw"])
    monkeypatch.setattr(secret_cli.getpass, "getpass", lambda *a, **k: next(answers))
    r = runner.invoke(app, ["secret", "rotate-password"])
    assert r.exit_code == 1
    assert "did not match" in r.output.lower()
