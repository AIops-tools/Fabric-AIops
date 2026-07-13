"""fabric-aiops — governed Cisco Meraki network-fabric operations for AI agents.

Standalone and self-contained: the governance harness (audit, token budget,
undo-token recording, graduated risk tiers, output sanitize) is
bundled under ``fabric_aiops.governance`` — this package has no external
skill-family dependency. Multi-platform by construction (see
``fabric_aiops.platform``); v0.1 ships Meraki. Preview: not yet full-coverage.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("fabric-aiops")
except PackageNotFoundError:  # running from an uninstalled source tree
    __version__ = "0.0.0+unknown"
