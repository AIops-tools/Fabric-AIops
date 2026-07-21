"""A refused governed call must exit 1, not 0.

``@tool_errors`` flattens every refusal — a policy denial, a read-only block, a
self-lockout guard, an unreachable Dashboard API — into ``{"error": ...}``. The
CLI printed that dict and returned normally, so the process exited 0. The write
was correctly blocked; the *exit code* was the lie, and it is the half a human
never checks: a shell ``&&`` chain or a CI step reading ``$?`` recorded a
refused write as a completed one.

These tests pin the contract for EVERY governed call site in the CLI, reads
included. A repo where some commands report their outcome in the exit code and
some do not is worse than one that is uniformly wrong, because there is no rule
a caller can rely on.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

import fabric_aiops.governance.audit as audit_mod
import fabric_aiops.governance.policy as policy_mod
import fabric_aiops.governance.undo as undo_mod
from fabric_aiops.cli import app

runner = CliRunner()

pytestmark = pytest.mark.unit

_REFUSAL = "policy denied: this is what a refusal looks like"


@pytest.fixture
def gov_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIC_AIOPS_HOME", str(tmp_path))
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()
    yield tmp_path
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()


def _refuse(monkeypatch, module, name: str) -> None:
    """Make one governed twin return the error envelope a refusal produces."""
    monkeypatch.setattr(module, name, lambda **kwargs: {"error": _REFUSAL})


# ── every remediate write ───────────────────────────────────────────────────

# (CLI argv, governed twin name, stdin for the double-confirm prompts)
_WRITE_COMMANDS = [
    (["remediate", "reboot", "Q2AB-CDEF-GHIJ"], "reboot_device", "y\ny\n"),
    (["remediate", "blink-leds", "Q2AB-CDEF-GHIJ"], "blink_device_leds", ""),
    (
        ["remediate", "update-device", "Q2AB-CDEF-GHIJ", '{"name": "ap1"}'],
        "update_device",
        "y\ny\n",
    ),
    (
        ["remediate", "update-vlan", "N1", "10", '{"name": "data"}'],
        "update_network_vlan",
        "y\ny\n",
    ),
    (["remediate", "claim", "N1", "Q2AB-CDEF-GHIJ"], "claim_devices_into_network", "y\ny\n"),
    (["remediate", "remove", "N1", "Q2AB-CDEF-GHIJ"], "remove_device_from_network", "y\ny\n"),
    (["remediate", "bind", "N1", "T1"], "bind_network_to_template", "y\ny\n"),
    (["remediate", "unbind", "N1"], "unbind_network_from_template", "y\ny\n"),
]


@pytest.mark.parametrize(
    "argv,twin,stdin", _WRITE_COMMANDS, ids=[c[1] for c in _WRITE_COMMANDS]
)
def test_a_refused_write_exits_nonzero(gov_home, monkeypatch, argv, twin, stdin):
    import mcp_server.tools.remediation as gov

    _refuse(monkeypatch, gov, twin)
    result = runner.invoke(app, argv, input=stdin)
    assert result.exit_code == 1, result.output
    assert _REFUSAL in result.output


@pytest.mark.parametrize(
    "argv,twin,stdin", _WRITE_COMMANDS, ids=[c[1] for c in _WRITE_COMMANDS]
)
def test_a_successful_write_still_exits_zero(gov_home, monkeypatch, argv, twin, stdin):
    """Exactness: the new gate must fire on refusals only."""
    import mcp_server.tools.remediation as gov

    monkeypatch.setattr(gov, twin, lambda **kwargs: {"action": twin, "ok": True})
    result = runner.invoke(app, argv, input=stdin)
    assert result.exit_code == 0, result.output
    assert _REFUSAL not in result.output


# ── the undo commands (a read among them, deliberately) ─────────────────────


def test_a_refused_undo_list_exits_nonzero(gov_home, monkeypatch):
    """undo_list is a READ, and it is gated too.

    Reads are refusable — a budget ceiling or a runaway trip denies them like
    anything else. A CLI that exits 0 on a denied listing hands the caller an
    empty result that looks like "nothing recorded".
    """
    import mcp_server.tools.undo as gov

    _refuse(monkeypatch, gov, "undo_list")
    result = runner.invoke(app, ["undo", "list"])
    assert result.exit_code == 1, result.output
    assert _REFUSAL in result.output


def test_a_refused_undo_apply_exits_nonzero(gov_home, monkeypatch):
    import mcp_server.tools.undo as gov

    _refuse(monkeypatch, gov, "undo_apply")
    result = runner.invoke(app, ["undo", "apply", "u-1"], input="y\ny\n")
    assert result.exit_code == 1, result.output
    assert _REFUSAL in result.output


def test_a_refused_undo_apply_dry_run_exits_nonzero(gov_home, monkeypatch):
    """A preview that was itself refused must not print a green DRY-RUN block."""
    import mcp_server.tools.undo as gov

    _refuse(monkeypatch, gov, "undo_apply")
    result = runner.invoke(app, ["undo", "apply", "u-1", "--dry-run"])
    assert result.exit_code == 1, result.output
    assert "DRY-RUN" not in result.output


def test_undo_list_still_exits_zero_when_it_succeeds(gov_home, monkeypatch):
    import mcp_server.tools.undo as gov

    monkeypatch.setattr(
        gov, "undo_list",
        lambda **kwargs: {"undos": [], "returned": 0, "limit": 50, "truncated": False},
    )
    result = runner.invoke(app, ["undo", "list"])
    assert result.exit_code == 0, result.output


# ── no governed call site may be added without this gate ────────────────────


def test_every_governed_call_in_the_cli_is_wrapped():
    """Source-level guard: a new ``gov.x(...)`` must be routed through governed().

    A behavioural test only covers the call sites it knows about. This one fails
    the moment someone adds a ninth remediate command and forgets the wrapper,
    which is exactly how the original defect survived a repo-wide review.
    """
    import re
    from pathlib import Path

    import fabric_aiops.cli as cli_pkg

    unwrapped: list[str] = []
    for path in sorted(Path(cli_pkg.__file__).parent.glob("*.py")):
        # Collapse whitespace so a call wrapped across lines by the formatter
        # reads the same as a one-liner.
        flat = re.sub(r"\s+", "", path.read_text())
        for call in re.finditer(r"(?<![\w.])gov\.(\w+)\(", flat):
            if call.group(1).startswith("_"):
                continue  # private accessor (gov._get_connection), not a governed tool
            if not flat[: call.start()].endswith("governed("):
                unwrapped.append(f"{path.name}: gov.{call.group(1)}(")
    assert not unwrapped, (
        "governed tool calls not routed through governed(); a refusal there would "
        "exit 0:\n" + "\n".join(unwrapped)
    )
