"""``fabric-aiops init`` — a friendly, interactive onboarding wizard.

Walks a new user through connecting their first network-fabric controller (Cisco
Meraki today): collects the non-secret connection details into ``config.yaml``
and the API key into the *encrypted* store (never plaintext on disk). Designed to
be run on a terminal; everything it needs is prompted with sensible defaults.
"""

from __future__ import annotations

import getpass

import typer
import yaml

from fabric_aiops.cli._common import cli_errors, console
from fabric_aiops.config import CONFIG_DIR, CONFIG_FILE
from fabric_aiops.platform import DEFAULT_MERAKI_BASE_URL, MERAKI
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


@cli_errors
def init_cmd() -> None:
    """Interactively set up your first Meraki connection."""
    console.print("[bold cyan]Fabric AIops — setup wizard[/]")
    console.print(
        "This collects connection details (saved to config.yaml) and your "
        "Meraki API key (saved [bold]encrypted[/] to secrets.enc).\n"
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

        base_url = typer.prompt(
            "API base URL (blank = default)", default=DEFAULT_MERAKI_BASE_URL
        ).strip()
        org_id = typer.prompt("Default organization id (optional)", default="").strip()

        console.print(
            "[dim]Create an API key in the Meraki Dashboard: Organization → "
            "Settings → API access → Generate. Paste it below (input hidden).[/]"
        )
        secret = getpass.getpass(f"API key for '{name}' (hidden): ")
        store = store.set(name, secret)

        entry: dict = {"name": name, "platform": MERAKI}
        if base_url and base_url != DEFAULT_MERAKI_BASE_URL:
            entry["base_url"] = base_url
        if org_id:
            entry["org_id"] = org_id
        targets.append(entry)
        existing_names.add(name)
        _write_targets(targets)
        console.print(f"[green]✓ Saved target '{name}' (API key stored encrypted).[/]")

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
