"""Configuration management for Fabric AIops.

Loads network-fabric controller connection targets from a YAML config file. Each
target names its ``platform`` — ``meraki`` (Cisco Meraki Dashboard API, the
reference platform), ``catalyst`` (Cisco Catalyst Center), ``cvp`` (Arista
CloudVision Portal), or ``unifi`` (UniFi Network controller / UniFi OS console).
See :mod:`fabric_aiops.platform` for what each supports.

The secret (the controller **API key**) is NEVER stored in the config file and
never on disk in plaintext: it lives in the encrypted store
``~/.fabric-aiops/secrets.enc`` (see :mod:`fabric_aiops.secretstore`). For
backward compatibility a legacy plaintext env var (``FABRIC_<TARGET>_APIKEY``)
is still honoured as a fallback, with a warning nudging migration to the
encrypted store.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from fabric_aiops.governance.paths import ops_home
from fabric_aiops.platform import MERAKI, get_platform
from fabric_aiops.secretstore import (
    MasterPasswordError,
    SecretStoreError,
    get_secret,
    has_store,
)

if TYPE_CHECKING:
    from fabric_aiops.platform import Platform

CONFIG_DIR = ops_home()
CONFIG_FILE = CONFIG_DIR / "config.yaml"
ENV_FILE = CONFIG_DIR / ".env"

# Legacy env-var prefix/suffix; also used by the migration helper.
SECRET_ENV_PREFIX = "FABRIC_"  # nosec B105 — env-var name, not a secret
SECRET_ENV_SUFFIX = "_APIKEY"  # nosec B105 — env-var name, not a secret

_log = logging.getLogger("fabric-aiops.config")


def _secret_env_key(name: str) -> str:
    """Legacy per-target API-key env var name, e.g. FABRIC_ORG1_APIKEY."""
    return f"{SECRET_ENV_PREFIX}{name.upper().replace('-', '_')}{SECRET_ENV_SUFFIX}"


def _resolve_secret(name: str) -> str:
    """Return a target's API key: encrypted store first, then legacy env var."""
    if has_store():
        try:
            return get_secret(name)
        except MasterPasswordError:
            # A wrong or missing master password is NOT "this target has no
            # secret". Falling through resurfaced it as "No API key for target
            # X", sending the operator to add a credential that is already
            # there. MasterPasswordError subclasses SecretStoreError, so the
            # broad catch below would swallow it — re-raise first.
            raise
        except SecretStoreError:
            pass  # no secret stored for this target — try the legacy env var
    legacy = os.environ.get(_secret_env_key(name))
    if legacy:
        _log.warning(
            "Using plaintext env var %s. Migrate to the encrypted store with "
            "'fabric-aiops secret migrate'.",
            _secret_env_key(name),
        )
        return legacy
    raise OSError(
        f"No API key for target '{name}'. Add one with "
        f"'fabric-aiops secret set {name}' (stored encrypted), or run "
        f"'fabric-aiops init'."
    )


@dataclass(frozen=True)
class TargetConfig:
    """A connection target for one network-fabric controller.

    ``platform`` selects the controller family (``meraki``, ``catalyst``,
    ``cvp``, ``unifi``). ``base_url`` overrides the platform default (a Meraki
    region/self-hosted proxy) — and is *required* for on-prem controllers
    (Catalyst Center, CVP, UniFi — where a UniFi OS console carries the
    ``/proxy/network`` prefix in the base URL) that have no cloud default.
    ``org_id`` is a convenience default scope id (Meraki organization /
    Catalyst Center site / CVP container key / UniFi site name) so most
    commands need only ``--target``. The secret comes from the encrypted
    store, never the config file.
    """

    name: str
    platform: str = MERAKI
    base_url: str = ""
    org_id: str = ""
    verify_ssl: bool = True
    auth_style: str = "bearer"

    def __post_init__(self) -> None:
        # Fail fast on an unknown platform (validated at the trust boundary).
        get_platform(self.platform)

    @property
    def platform_obj(self) -> Platform:
        return get_platform(self.platform)

    @property
    def api_key(self) -> str:
        return _resolve_secret(self.name)

    @property
    def api_base(self) -> str:
        """Effective API base URL: the override, else the platform default.

        Raises a teaching error for platforms with no cloud default (on-prem
        controllers) when the target does not set ``base_url``.
        """
        platform = self.platform_obj
        base = self.base_url or platform.default_base_url
        if not base:
            raise ValueError(
                f"Target '{self.name}' ({platform.label}) has no API base URL — "
                f"{platform.label} is per-install. Set 'base_url' (e.g. "
                f"https://<controller-host>, default port {platform.default_port}) "
                f"on the target in config.yaml, or re-run 'fabric-aiops init'."
            )
        return base


@dataclass(frozen=True)
class AppConfig:
    """Top-level application config."""

    targets: tuple[TargetConfig, ...] = ()

    def get_target(self, name: str) -> TargetConfig:
        for t in self.targets:
            if t.name == name:
                return t
        available = ", ".join(t.name for t in self.targets) or "(none)"
        raise KeyError(f"Target '{name}' not found. Available: {available}")

    @property
    def default_target(self) -> TargetConfig:
        if not self.targets:
            raise ValueError("No targets configured. Check config.yaml")
        return self.targets[0]


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load config from YAML; the API key comes from the encrypted store."""
    path = config_path or CONFIG_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Run 'fabric-aiops init' to set up a target and store its API key "
            f"encrypted, or create {CONFIG_FILE} with a 'targets' list."
        )

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    targets = tuple(
        TargetConfig(
            name=t["name"],
            platform=t.get("platform", MERAKI),
            base_url=t.get("base_url", ""),
            org_id=str(t.get("org_id", "")),
            verify_ssl=t.get("verify_ssl", True),
            auth_style=t.get("auth_style", "bearer"),
        )
        for t in raw.get("targets", [])
    )

    return AppConfig(targets=targets)
