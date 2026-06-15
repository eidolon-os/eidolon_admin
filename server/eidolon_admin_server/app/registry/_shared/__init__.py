"""Shared helpers for registry entity modules.

Public surface:
    unwrap_detail           strip FastAPI's `{"detail": "..."}` envelope
"""
from .errors import unwrap_detail

__all__ = [
    "unwrap_detail",
]
