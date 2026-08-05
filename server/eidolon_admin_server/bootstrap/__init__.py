"""Early-boot host control plane for headless Eidolon OS nodes.

This package is deliberately independent from ``eidolon_admin_server.app``.
Importing it must not initialize Data, NATS, Supervisor, Hub, or Kernel.
"""

from .config import BootstrapMode, BootstrapSettings, load_bootstrap_settings

__all__ = ["BootstrapMode", "BootstrapSettings", "load_bootstrap_settings"]
