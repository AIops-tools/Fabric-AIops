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
from fabric_aiops.governance.paths import ops_path
from fabric_aiops.platform import DEFAULT_MERAKI_BASE_URL, MERAKI
from fabric_aiops.secretstore import SecretStore, resolve_master_password

# Starter policy: keeps the secure-by-default gate (high/critical writes need a
# named approver) explicit and editable, and shows the other rule kinds.
DEFAULT_RULES_YAML = """\
# fabric-aiops policy rules — hot-reloaded on change (no restart needed).
# Kinds: deny rules, maintenance_window, risk_tiers (graduated autonomy).

risk_tiers:
  - name: high-risk-requires-approver
    tier: dual
    min_risk_level: high
    reason: >-
      High/critical writes need a named human approver — set
      FABRIC_AUDIT_APPROVED_BY (and FABRIC_AUDIT_RATIONALE) before the call.

# deny:
#   - name: no-prod-removals
#     operations: ["remove_*", "unbind_*"]
#     environments: ["production"]
#     reason: "Device removals / template unbinds in production go through change management."

# maintenance_window:
#   start: "22:00"
#   end: "06:00"
"""


def _write_default_rules() -> None:
    """Seed a starter rules.yaml (only when none exists) so the policy layer
    is explicit from day one; never overwrites an operator-authored file."""
    rules_path = ops_path("rules.yaml")
    if rules_path.exists():
        return
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(DEFAULT_RULES_YAML, "utf-8")
    console.print(f"[green]✓ Wrote default policy rules:[/] {rules_path}")


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
        console.print("[dim]Lab / self-signed controller setups can answer No here.[/]")
        verify_ssl = typer.confirm("Verify TLS certificate?", default=True)

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
        if not verify_ssl:
            entry["verify_ssl"] = False
        targets.append(entry)
        existing_names.add(name)
        _write_targets(targets)
        console.print(f"[green]✓ Saved target '{name}' (API key stored encrypted).[/]")

        if not typer.confirm("\nAdd another target?", default=False):
            break

    _write_default_rules()
    console.print(f"\n[green]✓ Setup complete.[/] Config: {CONFIG_FILE}")
    console.print(
        "[dim]Tip: export FABRIC_AIOPS_MASTER_PASSWORD=... in your shell profile "
        "so the MCP server and CLI can unlock secrets non-interactively.[/]"
    )
    if typer.confirm("Run a connectivity check now (fabric-aiops doctor)?", default=True):
        from fabric_aiops.doctor import run_doctor

        raise typer.Exit(run_doctor())
