"""System health module — port audit + orphan detection.

Four-file split mirroring the ``devices`` module:
- :mod:`router` — HTTP shell
- :mod:`auditor` — orchestration (services.yaml + supervisord + OS)
- :mod:`probe` — pure OS process / port introspection via psutil
- :mod:`schemas` — Pydantic wire models

Mount with ``app.include_router(router, prefix='/api')``.
"""
from .auditor import SystemHealthAuditor
from .router import router

__all__ = ["SystemHealthAuditor", "router"]
