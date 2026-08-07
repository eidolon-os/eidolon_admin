"""Admin-owned adapters and orchestration for Eidolon control authorities."""

from .router import router
from .service import ControlPlaneService

__all__ = ["ControlPlaneService", "router"]
