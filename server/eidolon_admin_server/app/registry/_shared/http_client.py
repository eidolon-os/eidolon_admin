"""Admin compatibility names for SDK service-to-service HTTP primitives."""

from __future__ import annotations

from eidolon_sdk.http import (
    ServiceHTTPClient,
    ServiceUnavailable,
    ServiceUpstreamError,
)

SubProjectUnreachable = ServiceUnavailable
SubProjectUpstreamError = ServiceUpstreamError


class SubProjectHTTPClient(ServiceHTTPClient):
    """Backwards-compatible admin name for ``ServiceHTTPClient``."""
