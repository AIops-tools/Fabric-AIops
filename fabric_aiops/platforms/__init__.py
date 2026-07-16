"""Platform descriptor modules — importing this package registers them all.

Each module builds one :class:`fabric_aiops.platform.Platform` descriptor
(canonical-op path table + response adapters + auth metadata) and calls
``register``. ``fabric_aiops.platform`` imports this package at the bottom of
its own module body, so ``get_platform``/``platform_names`` always see the full
registry. Adding a controller platform = adding a module here.
"""

from fabric_aiops.platforms import catalyst, cvp, meraki

__all__ = ["meraki", "catalyst", "cvp"]
