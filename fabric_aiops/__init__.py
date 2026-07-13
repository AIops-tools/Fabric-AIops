"""fabric-aiops — governed Cisco Meraki network-fabric operations for AI agents.

Standalone and self-contained: the governance harness (audit, token budget,
undo-token recording, graduated risk tiers, prompt-injection sanitize) is
bundled under ``fabric_aiops.governance`` — this package has no external
skill-family dependency. Multi-platform by construction (see
``fabric_aiops.platform``); v0.1 ships Meraki. Preview: not yet full-coverage.
"""

__version__ = "0.1.0"
