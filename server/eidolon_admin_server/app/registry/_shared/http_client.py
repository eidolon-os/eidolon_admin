"""Shared HTTP client base for admin → sub-project REST calls.

Every entity module (Templates, Users, Agents…) that proxies a
sub-project's REST surface needs the same three things:

  1. construct full URLs from a configured ``base_url``
  2. translate network-level failures (connect refused, timeout, DNS)
     into a known exception type
  3. raise on 4xx/5xx with the status code preserved, so the
     orchestrator can map to admin's domain exceptions

This base class collapses those three concerns into a single inheritable
shape. Subclasses only define the high-level methods (list / get /
create / etc.) and the URL paths they use; transport mechanics live here.

Why not just use httpx directly in every repository?
    - Three+ near-identical try/except blocks per file got tedious.
    - One file to audit if we ever need to add tracing / retries /
      auth headers across all admin proxies.
    - 29.F's Agents module would otherwise be the 3rd copy.

Subclasses can override class-level ``UNREACHABLE_EXC`` and
``UPSTREAM_EXC`` to surface module-specific exception types (the
orchestrator's status-code mapper still consumes them via
``status_code`` attribute, so all that matters is duck-typing). Default
exceptions are the shared :class:`SubProjectUnreachable` /
:class:`SubProjectUpstreamError`.
"""
from __future__ import annotations

from typing import Any, ClassVar

import httpx


class SubProjectUnreachable(Exception):
    """Network-level failure talking to a sub-project: connection
    refused, DNS, timeout. Admin orchestrators map this to 503 — the
    sub-project is presumed down."""


class SubProjectUpstreamError(Exception):
    """Sub-project responded with 4xx/5xx. ``status_code`` carries the
    original code so admin's orchestrator can preserve it (404→404,
    409→409) rather than collapsing all to 502."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class SubProjectHTTPClient:
    """Base class. Construct with a shared httpx client + base URL.

    Subclasses define high-level methods that call ``_request``. The
    base handles:

      - URL prepending
      - Exception wrapping (network errors → UNREACHABLE_EXC)
      - Status-code checking (4xx/5xx → UPSTREAM_EXC unless in
        ``ok_statuses`` for the call)
      - Returning the raw ``httpx.Response`` so subclasses can decode
        as JSON / text / SSE as appropriate

    Example::

        class MyClient(SubProjectHTTPClient):
            async def list_things(self) -> list[dict]:
                r = await self._request("GET", "/api/things")
                return r.json()

    Note: ``_request`` does NOT parse the response body — the subclass
    method does that. This lets endpoints that return text (e.g. raw
    YAML) coexist with JSON endpoints in one client.
    """

    UNREACHABLE_EXC: ClassVar[type[Exception]] = SubProjectUnreachable
    UPSTREAM_EXC: ClassVar[type[SubProjectUpstreamError]] = SubProjectUpstreamError

    # Exceptions that count as "network unreachable" rather than HTTP
    # error. Listed here (not hardcoded in _request) so a subclass with
    # different requirements can override.
    UNREACHABLE_EXCEPTIONS: ClassVar[tuple[type[BaseException], ...]] = (
        httpx.ConnectError,
        httpx.TimeoutException,
    )

    def __init__(self, http_client: httpx.AsyncClient, base_url: str) -> None:
        self._http = http_client
        self._base = base_url.rstrip("/")

    def _url(self, path: str) -> str:
        return f"{self._base}{path}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        ok_statuses: tuple[int, ...] | None = None,
        timeout: httpx.Timeout | float | None = None,
    ) -> httpx.Response:
        """Send a request; raise on network / status errors.

        Args:
            method: HTTP verb.
            path: path part (with leading ``/``). Prepended with base_url.
            json: optional JSON body.
            ok_statuses: explicit success statuses. ``None`` means
                "anything < 400 is OK", which covers the common GET/POST
                cases. Use a tuple like ``(204,)`` for endpoints that
                only return 204 to be strict.

        Returns:
            The raw httpx.Response. Subclass decides how to decode.

        Raises:
            UNREACHABLE_EXC: network-level failure (no HTTP response).
            UPSTREAM_EXC: response received but its status is not
                accepted.
        """
        try:
            r = await self._http.request(
                method,
                self._url(path),
                json=json,
                timeout=timeout,
            )
        except self.UNREACHABLE_EXCEPTIONS as exc:
            raise self.UNREACHABLE_EXC(str(exc)) from exc
        if ok_statuses is not None:
            if r.status_code not in ok_statuses:
                raise self.UPSTREAM_EXC(r.status_code, r.text)
        elif r.status_code >= 400:
            raise self.UPSTREAM_EXC(r.status_code, r.text)
        return r
