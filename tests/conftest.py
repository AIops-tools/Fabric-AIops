"""Shared fixtures for the fabric-aiops test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _default_approver(monkeypatch):
    """Record a synthetic approver on every audit row so the trail looks
    realistic. The approver is an optional annotation now — it gates nothing —
    but the governance-persistence tests clear it to prove a high-risk write
    still runs without one."""
    monkeypatch.setenv("FABRIC_AUDIT_APPROVED_BY", "pytest")
