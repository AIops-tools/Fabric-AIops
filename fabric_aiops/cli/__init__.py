"""CLI package for fabric-aiops.

Re-exports ``app`` so the pyproject entry point
``fabric-aiops = "fabric_aiops.cli:app"`` works unchanged.
"""

from fabric_aiops.cli._root import app

__all__ = ["app"]
