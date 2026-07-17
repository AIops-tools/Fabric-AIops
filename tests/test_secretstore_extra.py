"""Secret store edge cases: master-password resolution (env / TTY / non-TTY),
membership + validation guards, corrupt/version-mismatched files, and the
module-level convenience API — all against a throwaway store directory."""

from __future__ import annotations

import pytest

import fabric_aiops.secretstore as ss


@pytest.fixture
def store_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(ss, "SECRETS_FILE", tmp_path / "secrets.enc")
    monkeypatch.setattr(ss, "LEGACY_ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(ss, "_cached", None)
    return tmp_path


# ── resolve_master_password ──────────────────────────────────────────────────


@pytest.mark.unit
def test_resolve_prefers_env_var(store_dir, monkeypatch):
    monkeypatch.setenv(ss.MASTER_PASSWORD_ENV, "from-env")
    assert ss.resolve_master_password() == "from-env"


@pytest.mark.unit
def test_resolve_non_tty_without_env_raises_teaching_error(store_dir, monkeypatch):
    monkeypatch.delenv(ss.MASTER_PASSWORD_ENV, raising=False)
    monkeypatch.setattr(ss.sys, "stdin", type("S", (), {"isatty": staticmethod(lambda: False)})())
    with pytest.raises(ss.MasterPasswordError, match="non-interactively"):
        ss.resolve_master_password()


@pytest.mark.unit
def test_resolve_tty_prompts_and_rejects_empty(store_dir, monkeypatch):
    monkeypatch.delenv(ss.MASTER_PASSWORD_ENV, raising=False)
    monkeypatch.setattr(ss.sys, "stdin", type("S", (), {"isatty": staticmethod(lambda: True)})())
    monkeypatch.setattr(ss.getpass, "getpass", lambda *a, **k: "")
    with pytest.raises(ss.MasterPasswordError, match="Empty master password"):
        ss.resolve_master_password()


@pytest.mark.unit
def test_resolve_tty_confirm_mismatch_on_new_store(store_dir, monkeypatch):
    monkeypatch.delenv(ss.MASTER_PASSWORD_ENV, raising=False)
    monkeypatch.setattr(ss.sys, "stdin", type("S", (), {"isatty": staticmethod(lambda: True)})())
    answers = iter(["pw1", "pw2"])
    monkeypatch.setattr(ss.getpass, "getpass", lambda *a, **k: next(answers))
    with pytest.raises(ss.MasterPasswordError, match="did not match"):
        ss.resolve_master_password(confirm_if_new=True)


@pytest.mark.unit
def test_resolve_tty_confirm_matches_on_new_store(store_dir, monkeypatch):
    monkeypatch.delenv(ss.MASTER_PASSWORD_ENV, raising=False)
    monkeypatch.setattr(ss.sys, "stdin", type("S", (), {"isatty": staticmethod(lambda: True)})())
    monkeypatch.setattr(ss.getpass, "getpass", lambda *a, **k: "same-pw")
    assert ss.resolve_master_password(confirm_if_new=True) == "same-pw"


# ── membership + validation guards ───────────────────────────────────────────


@pytest.mark.unit
def test_contains_membership(store_dir):
    store = ss.SecretStore.unlock("pw").set("known", "1")
    assert "known" in store
    assert "unknown" not in store


@pytest.mark.unit
def test_set_empty_name_rejected(store_dir):
    with pytest.raises(ss.SecretStoreError, match="name must not be empty"):
        ss.SecretStore.unlock("pw").set("", "value")


@pytest.mark.unit
def test_delete_missing_name_rejected(store_dir):
    with pytest.raises(ss.SecretStoreError, match="No secret named"):
        ss.SecretStore.unlock("pw").delete("ghost")


@pytest.mark.unit
def test_with_password_empty_rejected(store_dir):
    with pytest.raises(ss.SecretStoreError, match="must not be empty"):
        ss.SecretStore.unlock("pw").with_password("")


# ── corrupt / version-mismatched files ───────────────────────────────────────


@pytest.mark.unit
def test_unlock_unreadable_json_raises_store_error(store_dir):
    (store_dir / "secrets.enc").write_text("{ not json", "utf-8")
    with pytest.raises(ss.SecretStoreError, match="Could not read"):
        ss.SecretStore.unlock("pw")


@pytest.mark.unit
def test_unlock_wrong_version_raises_store_error(store_dir):
    blob = '{"version": 99, "salt": "x", "ciphertext": "y"}'
    (store_dir / "secrets.enc").write_text(blob, "utf-8")
    with pytest.raises(ss.SecretStoreError, match="Unsupported secret store version"):
        ss.SecretStore.unlock("pw")


# ── module-level convenience API ─────────────────────────────────────────────


@pytest.mark.unit
def test_open_store_caches_and_get_secret_reads(store_dir, monkeypatch):
    ss.SecretStore.unlock("pw").set("org1", "the-key")
    monkeypatch.setenv(ss.MASTER_PASSWORD_ENV, "pw")
    first = ss.open_store()
    second = ss.open_store()  # cached: same instance
    assert first is second
    assert ss.get_secret("org1") == "the-key"


@pytest.mark.unit
def test_has_store_reflects_disk(store_dir):
    assert ss.has_store() is False
    ss.SecretStore.unlock("pw").set("a", "1")
    assert ss.has_store() is True


@pytest.mark.unit
def test_check_permissions_none_when_no_file(store_dir):
    assert ss.check_permissions() is None


@pytest.mark.unit
def test_migrate_returns_empty_when_no_legacy_file(store_dir):
    assert ss.migrate_legacy_env("FABRIC_", "_APIKEY", "pw") == []
