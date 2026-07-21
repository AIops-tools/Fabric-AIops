"""``fabric-aiops init`` — a friendly, interactive onboarding wizard.

Walks a new user through connecting their first network-fabric controller —
Cisco Meraki Dashboard, Cisco Catalyst Center, Arista CloudVision Portal, or a
UniFi Network controller (the registered platforms): collects the non-secret
connection details into ``config.yaml`` and the platform secret (Meraki API key
/ Catalyst Center ``username:password`` / CVP service-account token / UniFi API
key) into the *encrypted* store (never plaintext on disk). Designed to be run
on a terminal; everything it needs is prompted with sensible defaults.
"""

from __future__ import annotations

import getpass

import typer
import yaml

from fabric_aiops.cli._common import cli_errors, console
from fabric_aiops.config import CONFIG_DIR, CONFIG_FILE
from fabric_aiops.platform import MERAKI, Platform, get_platform, platform_names
from fabric_aiops.secretstore import SecretStore, resolve_master_password


def _load_existing_targets() -> list[dict]:
    if not CONFIG_FILE.exists():
        return []
    raw = yaml.safe_load(CONFIG_FILE.read_text("utf-8")) or {}
    return list(raw.get("targets", []))


def _write_targets(targets: list[dict]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        CONFIG_DIR.chmod(0o700)
    except OSError:
        pass
    CONFIG_FILE.write_text(yaml.safe_dump({"targets": targets}, sort_keys=False), "utf-8")


def _prompt_platform() -> Platform:
    """Ask which registered controller platform this target speaks."""
    names = platform_names()
    console.print(f"[dim]Registered platforms: {', '.join(names)}[/]")
    while True:
        choice = typer.prompt("Platform", default=MERAKI).strip().lower()
        if choice in names:
            return get_platform(choice)
        console.print(f"[red]Unknown platform '{choice}'. Choose one of: {', '.join(names)}[/]")


def _prompt_base_url(platform: Platform) -> str:
    """Base URL: platform default when it has one, else required (on-prem)."""
    if platform.default_base_url:
        return typer.prompt(
            "API base URL (blank = default)", default=platform.default_base_url
        ).strip()
    console.print(
        f"[dim]{platform.label} is per-install — enter your controller's URL "
        f"(default port {platform.default_port}, HTTPS).[/]"
    )
    if platform.base_url_help:
        console.print(f"[dim]{platform.base_url_help}[/]")
    while True:
        base_url = typer.prompt(f"Controller base URL (e.g. https://<{platform.name}-host>)")
        base_url = base_url.strip().rstrip("/")
        if base_url and "://" not in base_url:
            base_url = f"https://{base_url}"
        if base_url:
            return base_url
        console.print("[red]A base URL is required for this platform.[/]")


@cli_errors
def init_cmd() -> None:
    """Interactively set up your first controller connection."""
    console.print("[bold cyan]Fabric AIops — setup wizard[/]")
    console.print(
        "This collects connection details (saved to config.yaml) and your "
        "controller secret (saved [bold]encrypted[/] to secrets.enc).\n"
    )

    console.print("[bold]Step 1 — master password[/]")
    console.print(
        "[dim]Encrypts secrets.enc. You'll set it via the "
        "FABRIC_AIOPS_MASTER_PASSWORD env var for non-interactive/MCP use.[/]"
    )
    password = resolve_master_password(confirm_if_new=True)
    store = SecretStore.unlock(password)

    targets = _load_existing_targets()
    existing_names = {t.get("name") for t in targets}

    while True:
        console.print("\n[bold]Step 2 — add a target[/]")
        name = typer.prompt("Target name (e.g. org1)").strip()
        if name in existing_names:
            if not typer.confirm(f"'{name}' already exists — overwrite?", default=False):
                continue
            targets = [t for t in targets if t.get("name") != name]

        platform = _prompt_platform()
        base_url = _prompt_base_url(platform)
        org_id = typer.prompt(
            f"Default {platform.org_id_hint} (optional)", default=""
        ).strip()
        console.print("[dim]Lab / self-signed controller setups can answer No here.[/]")
        verify_ssl = typer.confirm("Verify TLS certificate?", default=True)

        console.print(f"[dim]{platform.secret_help} Paste it below (input hidden).[/]")
        secret = getpass.getpass(f"{platform.secret_hint} for '{name}' (hidden): ")
        store = store.set(name, secret)

        entry: dict = {"name": name, "platform": platform.name}
        if base_url and base_url != platform.default_base_url:
            entry["base_url"] = base_url
        if org_id:
            entry["org_id"] = org_id
        if not verify_ssl:
            entry["verify_ssl"] = False
        targets.append(entry)
        existing_names.add(name)
        _write_targets(targets)
        console.print(f"[green]✓ Saved target '{name}' (secret stored encrypted).[/]")

        if not typer.confirm("\nAdd another target?", default=False):
            break

    console.print(f"\n[green]✓ Setup complete.[/] Config: {CONFIG_FILE}")
    console.print(
        "[dim]Tip: export FABRIC_AIOPS_MASTER_PASSWORD=... in your shell profile "
        "so the MCP server and CLI can unlock secrets non-interactively.[/]"
    )
    if typer.confirm("Run a connectivity check now (fabric-aiops doctor)?", default=True):
        from fabric_aiops.doctor import run_doctor

        raise typer.Exit(run_doctor())
