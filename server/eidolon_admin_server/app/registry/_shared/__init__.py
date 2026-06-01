"""Shared helpers for registry entity modules.

Every entity module that proxies a sub-project (Templates → agent,
Users → memory, Agents → agent in 29.F) repeats the same transport
plumbing: wrap httpx errors, check status codes, unwrap FastAPI's
``{"detail": "..."}`` envelope. Pulling those into one place keeps the
per-module repositories thin and consistent.

Public surface:
    SubProjectHTTPClient    base class for admin → sub-project HTTP
    SubProjectUnreachable   network-level failure
    SubProjectUpstreamError sub-project responded 4xx/5xx
    unwrap_detail           strip FastAPI's `{"detail": "..."}` envelope

Re-exported under the original per-module exception names (e.g.
``TemplateAgentUnreachable``) by each module's repository.py for
backwards-compatible imports.
"""
from .http_client import (
    SubProjectHTTPClient,
    SubProjectUnreachable,
    SubProjectUpstreamError,
)
from .errors import unwrap_detail

__all__ = [
    "SubProjectHTTPClient",
    "SubProjectUnreachable",
    "SubProjectUpstreamError",
    "unwrap_detail",
]
