"""The Operator Plane: ``/api/operator/v1``.

Two routes live here, and they are here because of what their ``Authorization``
header means. On the internal plane that header is the caller proving it is the
Local API. On these two it is a **Hub management credential the operator typed
into a browser**, which this process forwards downstream and never verifies
itself. Same header name, opposite direction of trust — and until now, the same
router.

Splitting them is not tidying. A reader who assumed the control plane's meaning
would conclude these two were authenticated; a reader who assumed this one's
meaning would conclude the other twenty-one were pass-throughs. Only one of
those mistakes has to happen once.

What this plane deliberately does *not* have is a caller credential of its own.
The Admin process listens on loopback (``EIDOLON_ADMIN_API_HOST=127.0.0.1``) and
the operator reaches it from the Host or through a tunnel; the trust boundary is
the network, and stating that is better than implying a check that is not here.
It is the plan's Operator Plane (§3.1): developer, support and operations
surface, never presented as an Owner product capability.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, Response

from eidolon_admin_server.app.control_plane.contracts import (
    DeviceAdmissionRequest,
    DeviceAdmissionResult,
    OwnerInventory,
)
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure

router = APIRouter(prefix="/operator/v1", tags=["operator"])


def service_of(request: Request):
    """The same orchestration the internal plane calls.

    Shared on purpose: the workflow is one workflow, and duplicating it per
    audience is how two boundaries start admitting different devices.
    """

    return request.app.state.control_plane


def raise_failure(exc: AuthorityFailure) -> None:
    raise HTTPException(
        status_code=exc.status_code, detail=exc.to_wire().model_dump()
    ) from exc


@router.get("/owners/{owner_id}/inventory", response_model=OwnerInventory)
async def owner_inventory(
    owner_id: str,
    request: Request,
    #: The operator's Hub credential, forwarded — not this process's.
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> OwnerInventory:
    try:
        return await service_of(request).inventory(
            owner_id=owner_id,
            hub_authorization=authorization or "",
        )
    except AuthorityFailure as exc:
        raise_failure(exc)


@router.post("/workflows/device-admission", response_model=DeviceAdmissionResult)
async def admit_device(
    payload: DeviceAdmissionRequest,
    request: Request,
    response: Response,
    #: The operator's Hub credential, forwarded — not this process's.
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> DeviceAdmissionResult:
    try:
        result = await service_of(request).admit_device(
            payload,
            hub_authorization=authorization or "",
        )
    except AuthorityFailure as exc:
        raise_failure(exc)
    if result.outcome == "retry_required":
        response.status_code = 202
    elif result.outcome == "blocked":
        failed = next(
            (step.failure for step in reversed(result.steps) if step.failure), None
        )
        response.status_code = {
            "unauthorized": 401,
            "forbidden": 403,
            "not_found": 404,
            "conflict": 409,
            "invalid_request": 422,
            "configuration": 503,
            "contract_violation": 502,
        }.get(failed.kind if failed else "", 502)
    return result
