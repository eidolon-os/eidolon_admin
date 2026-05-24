"""Devices module — admin's cross-service device-binding workflows.

Four-layer split (top-down):
- :mod:`router` — HTTP shell, no logic
- :mod:`orchestrator` — use-case compositions (hub HTTP + NATS KV)
- :mod:`repository` — NATS bucket adapter (only place that knows key naming)
- :mod:`schemas` — Pydantic wire models

Nothing else in the codebase reaches past :mod:`router` (which is exported as
``router``). Mount with ``app.include_router(router, prefix='/api')``.
"""
from .orchestrator import DeviceOrchestrator
from .repository import ALL_BUCKETS, DeviceBindingRepository
from .router import router

__all__ = [
    "ALL_BUCKETS",
    "DeviceBindingRepository",
    "DeviceOrchestrator",
    "router",
]
