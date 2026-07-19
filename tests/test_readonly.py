"""Read-only mode: the hard switch that removes every write capability.

Two layers are under test:
  1. ``@governed_tool`` refuses non-low-risk calls (covers CLI + in-process).
  2. ``apply_read_only()`` unregisters write tools from the MCP registry, so a
     weak model never sees a tool it could hallucinate a call to.

Plus the guard that keeps the two write-markers (``risk_level`` and the
``[READ]``/``[WRITE]`` docstring tag) from drifting apart.
"""

import pytest

from fabric_aiops.governance import READ_ONLY_ENV, is_read_only
from fabric_aiops.governance.decorators import PolicyDenied, governed_tool


@pytest.fixture
def read_only(monkeypatch):
    """Turn read-only mode on for the duration of a test."""
    monkeypatch.setenv(READ_ONLY_ENV, "1")


@pytest.mark.unit
@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
def test_truthy_values_enable_read_only(monkeypatch, value):
    monkeypatch.setenv(READ_ONLY_ENV, value)
    assert is_read_only() is True


@pytest.mark.unit
@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_other_values_leave_writes_enabled(monkeypatch, value):
    monkeypatch.setenv(READ_ONLY_ENV, value)
    assert is_read_only() is False


@pytest.mark.unit
def test_unset_env_leaves_writes_enabled(monkeypatch):
    monkeypatch.delenv(READ_ONLY_ENV, raising=False)
    assert is_read_only() is False


@pytest.mark.unit
def test_governed_write_is_refused_in_read_only(read_only):
    """A write is refused before it can reach the controller — from any caller."""
    calls = []

    @governed_tool(risk_level="high")
    def remove_device_from_network(serial: str = "Q2XX-AAAA-BBBB") -> dict:
        calls.append(serial)
        return {"removed": True}

    with pytest.raises(PolicyDenied) as excinfo:
        remove_device_from_network()

    assert calls == [], "the tool body must never run in read-only mode"
    assert excinfo.value.result.rule == "read_only"
    assert READ_ONLY_ENV in excinfo.value.result.reason


@pytest.mark.unit
def test_governed_read_still_works_in_read_only(read_only):
    @governed_tool(risk_level="low")
    def network_list(org_id: str = "123456") -> list:
        return [{"id": "N_1", "name": "branch-1"}]

    assert network_list() == [{"id": "N_1", "name": "branch-1"}]


@pytest.mark.unit
def test_medium_risk_write_is_also_refused(read_only):
    """Only 'low' is a read — medium (e.g. update_device) must be refused too."""

    @governed_tool(risk_level="medium")
    def update_device(serial: str = "Q2XX-AAAA-BBBB") -> dict:
        return {"updated": True}

    with pytest.raises(PolicyDenied):
        update_device()


@pytest.mark.unit
def test_apply_read_only_unregisters_write_tools(monkeypatch):
    """Write tools disappear from the registry; reads stay."""
    from mcp_server import server

    registry = server.mcp._tool_manager._tools
    original = dict(registry)
    try:
        monkeypatch.setenv(READ_ONLY_ENV, "1")
        dropped = server.apply_read_only()

        assert dropped, "expected at least one write tool to be removed"
        assert "reboot_device" in dropped and "claim_devices_into_network" in dropped
        assert "bind_network_to_template" in dropped and "update_device" in dropped
        assert "network_list" not in dropped and "device_inventory" not in dropped

        remaining = server.mcp._tool_manager._tools
        assert all(
            getattr(t.fn, "_risk_level", "low") == "low" for t in remaining.values()
        ), "a non-read tool survived read-only mode"
        assert "network_list" in remaining, "reads must still be exposed"
        assert "org_device_statuses" in remaining
    finally:
        registry.clear()
        registry.update(original)


@pytest.mark.unit
def test_blink_leds_is_treated_as_a_write(monkeypatch):
    """A controller POST is a write even when it changes no configuration.

    ``blink_device_leds`` shipped as risk_level='low', which would have left it
    exposed and callable with FABRIC_READ_ONLY set despite its [WRITE] tag.
    """
    from mcp_server import server

    registry = server.mcp._tool_manager._tools
    original = dict(registry)
    try:
        monkeypatch.setenv(READ_ONLY_ENV, "1")
        assert "blink_device_leds" in server.apply_read_only()
    finally:
        registry.clear()
        registry.update(original)


@pytest.mark.unit
def test_apply_read_only_is_a_noop_when_disabled(monkeypatch):
    from mcp_server import server

    monkeypatch.delenv(READ_ONLY_ENV, raising=False)
    before = len(server.mcp._tool_manager._tools)
    assert server.apply_read_only() == []
    assert len(server.mcp._tool_manager._tools) == before


@pytest.mark.unit
def test_risk_level_agrees_with_read_write_docstring_tag():
    """The two write-markers must never drift apart.

    ``apply_read_only`` keys off ``risk_level``; the docs and capability tables
    are derived from the ``[READ]``/``[WRITE]`` docstring tag. If they disagree,
    read-only mode would expose something the docs call a write.
    """
    from mcp_server import server

    untagged, mismatched = [], []
    for name, tool in server.mcp._tool_manager._tools.items():
        doc = (tool.fn.__doc__ or "").lstrip()
        if doc.startswith("[READ]"):
            tagged_as_read = True
        elif doc.startswith("[WRITE]"):
            tagged_as_read = False
        else:
            untagged.append(name)
            continue
        if tagged_as_read != (getattr(tool.fn, "_risk_level", "low") == "low"):
            mismatched.append(name)

    assert not untagged, f"tools missing a [READ]/[WRITE] docstring tag: {untagged}"
    assert not mismatched, f"risk_level disagrees with the docstring tag: {mismatched}"
