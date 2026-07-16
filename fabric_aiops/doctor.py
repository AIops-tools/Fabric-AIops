"""Environment and connectivity diagnostics for Fabric AIops."""

from __future__ import annotations

from rich.console import Console

from fabric_aiops.config import CONFIG_FILE, ENV_FILE, load_config
from fabric_aiops.secretstore import SECRETS_FILE, check_permissions, has_store

_console = Console()


def run_doctor(skip_auth: bool = False) -> int:
    """Check config, secrets, and (optionally) connectivity.

    Returns a process exit code: 0 healthy, 1 problems found. Connectivity
    failures are reported as status, never raised as tracebacks (a doctor must
    survive the thing it diagnoses being unhealthy).
    """
    problems = 0

    if not CONFIG_FILE.exists():
        _console.print(f"[red]✗ Config file missing: {CONFIG_FILE}[/]")
        _console.print("[yellow]  Run 'fabric-aiops init' to set up your first target.[/]")
        return 1
    _console.print(f"[green]✓ Config file present: {CONFIG_FILE}[/]")

    try:
        config = load_config()
    except Exception as exc:  # noqa: BLE001 — report, do not crash
        _console.print(f"[red]✗ Config load failed: {exc}[/]")
        return 1

    if not config.targets:
        _console.print("[red]✗ No targets configured[/]")
        return 1
    _console.print(f"[green]✓ {len(config.targets)} target(s) configured[/]")

    if has_store():
        _console.print(f"[green]✓ Encrypted secret store present: {SECRETS_FILE}[/]")
        perm_warning = check_permissions()
        if perm_warning:
            _console.print(f"[yellow]! {perm_warning}[/]")
    elif ENV_FILE.exists():
        _console.print(
            f"[yellow]! Using legacy plaintext .env ({ENV_FILE}). Migrate with "
            f"'fabric-aiops secret migrate'.[/]"
        )
    else:
        _console.print(
            "[yellow]! No secret store yet. Run 'fabric-aiops init' to set up "
            "credentials (stored encrypted).[/]"
        )
        problems += 1

    for target in config.targets:
        platform = target.platform_obj
        if not platform.requires_secret:
            continue
        try:
            _ = target.api_key
            _console.print(
                f"[green]✓ Secret present for '{target.name}' ({platform.secret_hint})[/]"
            )
        except OSError as exc:
            _console.print(f"[red]✗ {exc}[/]")
            problems += 1
        try:
            _ = target.api_base
        except ValueError as exc:  # on-prem platform without a base_url
            _console.print(f"[red]✗ {exc}[/]")
            problems += 1

    if skip_auth:
        _console.print("[dim]Skipping connectivity check (--skip-auth).[/]")
        return 1 if problems else 0

    from fabric_aiops.connection import ConnectionManager
    from fabric_aiops.ops._util import op_get_pages

    mgr = ConnectionManager(config)
    for target in config.targets:
        try:
            conn = mgr.connect(target.name)
            # 'orgs.list' is the canonical top-of-hierarchy probe on every
            # platform (Meraki organizations / Catalyst Center sites / CVP
            # containers) — it exercises auth (incl. the Catalyst Center
            # session-token exchange) plus one real read.
            rows = op_get_pages(conn, "orgs.list")
            count = len(rows) if isinstance(rows, list) else 0
            _console.print(
                f"[green]✓ Connected to '{target.name}' ({target.platform_obj.label}) "
                f"— {count} {target.platform_obj.org_noun} visible[/]"
            )
        except Exception as exc:  # noqa: BLE001 — connectivity is a status, not a crash
            _console.print(f"[red]✗ Connect to '{target.name}' failed: {exc}[/]")
            problems += 1

    return 1 if problems else 0
