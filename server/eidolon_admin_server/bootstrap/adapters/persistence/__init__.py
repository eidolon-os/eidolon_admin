"""Bootstrap-owned persistence adapter."""

from .sqlite import BootstrapStore, BootstrapStoreError

__all__ = ["BootstrapStore", "BootstrapStoreError"]
