"""Shared helpers for fabric-aiops CLI sub-modules."""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

console = Console()

# ─── Shared Option types ───────────────────────────────────────────────────

TargetOption = Annotated[
    str | None, typer.Option("--target", "-t", help="Target name from config")
]
OrgOption = Annotated[
    str | None, typer.Option("--org-id", "-o", help="Meraki organization id (else target default)")
]
DryRunOption = Annotated[
    bool, typer.Option("--dry-run", help="Print the API call without executing")
]
LimitOption = Annotated[
    int | None, typer.Option("--limit", help="Max rows to return (result says if truncated)")
]


def _cli_error_types() -> tuple[type[BaseException], ...]:
    """Exceptions translated to a one-line teaching error instead of a traceback."""
    from fabric_aiops.connection import FabricApiError

    return (FabricApiError, KeyError, OSError, ValueError)


def cli_errors(fn: Callable) -> Callable:
    """Translate known exceptions into one red line + exit code 1."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (typer.Exit, typer.Abort):
            raise
        except _cli_error_types() as e:
            message = str(e)
            if isinstance(e, KeyError):
                message = f"Missing required key or environment variable: {message}"
            console.print(f"[red]Error: {message}[/]")
            raise typer.Exit(1) from e

    return wrapper


def get_connection(target: str | None, config_path: Path | None = None) -> tuple[Any, Any]:
    """Return a (conn, config) tuple for the given target."""
    from fabric_aiops.config import load_config
    from fabric_aiops.connection import ConnectionManager

    cfg = load_config(config_path)
    mgr = ConnectionManager(cfg)
    return mgr.connect(target), cfg


def print_result(result: Any) -> None:
    """Print a read result as JSON, and say so out loud when it was truncated.

    A capped list that does not announce the cut reads as the whole story — to a
    person skimming, and especially to a smaller local model, which will happily
    summarise a partial result as complete. So whenever the ops layer reports
    ``truncated``, the CLI prints an explicit line telling the operator to
    re-run with a higher --limit.
    """
    console.print_json(json.dumps(result))
    if isinstance(result, dict) and result.get("truncated"):
        console.print(
            f"[yellow]… truncated at {result.get('limit')} row(s) — "
            f"re-run with a higher --limit to see the rest.[/]"
        )


def limit_kwargs(limit: int | None) -> dict:
    """``{"limit": n}`` when the operator passed --limit, else ``{}`` (use the default)."""
    return {} if limit is None else {"limit": limit}


def dry_run_print(*, operation: str, api_call: str, parameters: dict | None = None) -> None:
    """Print a dry-run preview of the API call that would be made."""
    console.print("\n[bold magenta][DRY-RUN] No changes will be made.[/]")
    console.print(f"[magenta]  Operation: {operation}[/]")
    console.print(f"[magenta]  API Call:  {api_call}[/]")
    for k, v in (parameters or {}).items():
        console.print(f"[magenta]  Param:     {k} = {v}[/]")
    console.print("[magenta]  Run without --dry-run to execute.[/]\n")


def double_confirm(action: str, resource: str) -> None:
    """Require two confirmations for a destructive operation."""
    console.print(f"[bold yellow]⚠️  About to: {action} '{resource}'[/]")
    typer.confirm(f"Confirm 1/2: {action} '{resource}'?", abort=True)
    typer.confirm(
        f"Confirm 2/2: really {action} '{resource}'? This may be irreversible.",
        abort=True,
    )
