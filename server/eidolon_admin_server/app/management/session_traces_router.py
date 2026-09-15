"""One voice session's engineering trace, read on the internal management plane.

A pass-through, and that is the whole design. Channel writes these files and its
Provider already serves them over a bearer GET; the Owner's constraint on this
work was that a second reader must not exist — "读取接口应该是统一的一份，不应该
写重复读取实现". So nothing here parses a trace, aggregates marks, or computes a
duration. It forwards, and adds the one thing the Provider cannot know: which
Owner is asking.

That addition is not decoration. ``GET /v1/session-traces/{session_id}`` on the
Provider takes no Owner — it is loopback-only and its caller is trusted — so
relaying it unguarded would let one Owner read another's session by naming its
id. The detail route below therefore checks the Owner on the summary the
Provider returns and answers 404 when it is somebody else's, which is the same
answer an id that does not exist gets: a probe must not be able to tell the
difference between "not yours" and "not there".

Reads only, like its neighbour. See ``mission_control_router`` for why that
separation is enforced rather than merely intended.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from eidolon_admin_server.app.gateway.proxy import upstream_auth_headers
from eidolon_admin_server.app.service_auth import require_local_api_credential

#: Same prefix and same credential as the rest of the internal plane.
router = APIRouter(
    prefix="/internal/v1/management",
    tags=["management-internal"],
    dependencies=[Depends(require_local_api_credential)],
)

#: The Provider bounds this itself (50 default, 200 max). Named here so the
#: Owner plane refuses an absurd number before it becomes a request.
_MAX_LIMIT = 200


async def _provider_json(
    request: Request, path: str, *, params: dict[str, Any] | None, timeout: float
) -> Any:
    """GET one path on the Channel Provider, as the registry describes it.

    A local copy of a shape Mission Control also uses, and deliberately not an
    import of it. Session traces have nothing to do with the runtime
    projection, and ``test_operator_separation`` holds a short list of
    management modules allowed to depend on Mission Control precisely so that
    dependency stays meaningful — joining that list to borrow fifteen lines of
    registry lookup would have spent the guard on a convenience.
    """

    registry = request.app.state.registry
    service = registry.get("channel-provider")
    if service is None or not service.base_url:
        raise RuntimeError("service 'channel-provider' is not proxied")
    prefix = (service.upstream_prefix or "").rstrip("/")
    suffix = path if path.startswith("/") else f"/{path}"
    url = f"{service.base_url.rstrip('/')}{prefix}{suffix}"
    http_client: httpx.AsyncClient = request.app.state.http_client
    headers = {"Connection": "close", **upstream_auth_headers(service)}
    response = await http_client.get(
        url, params=params, timeout=timeout, headers=headers
    )
    response.raise_for_status()
    return response.json()


def _unavailable(exc: Exception) -> HTTPException:
    """The Channel Provider could not answer.

    503 rather than 500: nothing about the request is wrong, and a client that
    retries later is behaving correctly.
    """

    return HTTPException(
        status_code=503,
        detail={"code": "CHANNEL_PROVIDER_UNAVAILABLE", "detail": str(exc)},
    )


@router.get("/session-traces")
async def list_session_traces(
    request: Request,
    owner_id: str,
    companion_id: str | None = None,
    since: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """The Owner's recorded voice sessions, newest first.

    ``owner_id`` is required and forwarded: the Provider filters on it, so a
    reading is already this Owner's before it reaches here. The caller is Local
    API holding a service credential and passing the Owner bound to the
    Controller session it verified — nothing on this plane lets a client choose.

    The answer is the Provider's own document, untouched. ``recording`` in it is
    the field that separates "this Host records nothing" from "this Owner has no
    sessions", and collapsing those two is the failure this whole work exists to
    stop — so it is relayed rather than interpreted.
    """

    params: dict[str, Any] = {
        "owner_id": owner_id,
        "limit": max(1, min(int(limit), _MAX_LIMIT)),
    }
    if companion_id:
        params["companion_id"] = companion_id
    if since:
        params["since"] = since
    try:
        body = await _provider_json(
            request, "/v1/session-traces", params=params, timeout=5.0
        )
    except Exception as exc:  # noqa: BLE001 - relayed, not reinterpreted
        raise _unavailable(exc) from exc
    return body if isinstance(body, dict) else {"sessions": []}


@router.get("/session-traces/{session_id}")
async def get_session_trace(
    request: Request,
    session_id: str,
    owner_id: str,
) -> dict[str, Any]:
    """One session's records, in the order they were written.

    The Provider's ``kinds`` filter is deliberately not exposed. The Owner
    plane keeps a curated query surface — a contract test holds the list — and
    a waterfall wants every record anyway. CLI and benchmark callers reach the
    Provider directly and still have it.

    The Owner check is the reason this route exists at all rather than the
    generic service proxy being pointed at the Provider.
    """

    try:
        body = await _provider_json(
            request,
            f"/v1/session-traces/{session_id}",
            params=None,
            timeout=10.0,
        )
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(exc) from exc

    session = body.get("session") if isinstance(body, dict) else None
    recorded_owner = str((session or {}).get("owner_id") or "")
    if recorded_owner != owner_id:
        # Deliberately the same answer as an unknown id. A distinct "not yours"
        # would turn this route into a way to test whether a session exists.
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "detail": f"no session trace for {session_id!r}"},
        )
    return body


__all__ = ["router"]
