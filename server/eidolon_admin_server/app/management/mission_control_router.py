"""The Owner's runtime map, read on the internal management plane.

Its own module, and that placement is the point rather than tidiness.

``app/management/router.py`` holds this plane's mutations — creating an Eidolon,
renaming one, retiring one, forgetting a memory. Phase 6 requires that **Mission
Control being degraded cannot affect a management mutation**, and the way to
keep a requirement true is to make it checkable: the file that performs the
mutations does not import Mission Control at all, so nothing in it can wait on
the composition, and ``test_operator_separation.py`` asserts exactly that.

What lives here is one read, and only reads may ever live here.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Request

from eidolon_admin_server.app.management.mission_control import (
    owner_activity_history,
    owner_runtime_projection,
)
from eidolon_admin_server.app.mission_control.lanes import LaneLedger
from eidolon_admin_server.app.mission_control.service import (
    compose_runtime,
    owner_activity_page,
)
from eidolon_admin_server.app.service_auth import require_local_api_credential
from eidolon_admin_server.audit import IndexedAuditEvent

#: How much of the Owner's audit tail one reading carries. The events lane caps
#: at 200 in the contract; this stays under it so the projection never has to
#: drop rows it already read.
AUDIT_TAIL_LIMIT = 120

#: Same prefix and same credential as the rest of the internal plane. Declared
#: on the router so a second route here cannot be added without it.
router = APIRouter(
    prefix="/internal/v1/management",
    tags=["management-internal"],
    dependencies=[Depends(require_local_api_credential)],
)


async def _owner_audit_tail(
    request: Request,
    owner_id: str,
    ledger: LaneLedger,
) -> list[IndexedAuditEvent]:
    """The Owner's audit tail, and an honest answer when there is not one.

    Recorded on the same ledger as the rest of the reading, so a Host with no
    index answers with its events lane unreadable and the reason attached —
    rather than an empty list, which on a map looks exactly like a quiet house.
    """

    store = getattr(request.app.state, "audit_index", None)
    if store is None:
        ledger.record(
            "audit.index",
            ok=False,
            detail="这台 Host 没有审计索引：事件流没有在跑",
        )
        return []
    started = time.perf_counter()
    try:
        events = await store.tail_for_owner(owner_id, limit=AUDIT_TAIL_LIMIT)
    except Exception as exc:  # noqa: BLE001 - a broken index must not cost the map
        ledger.record("audit.index", ok=False, detail=str(exc))
        return []
    ledger.record(
        "audit.index",
        ok=True,
        detail=f"{len(events)} events",
        started=started,
    )
    return events


@router.get("/mission-control/snapshot")
async def get_mission_control_snapshot(
    request: Request,
    owner_id: str,
) -> dict[str, Any]:
    """What this Host observed of one Owner's runtime, lane by lane.

    The same composition the operator console reads, projected into the Owner's
    contract — see :mod:`eidolon_admin_server.app.management.mission_control`
    for why it is a projection and not a re-export.

    ``owner_id`` is a query parameter for the same reason it is one everywhere
    else on this plane: the only caller is Local API holding a service
    credential, passing the Owner bound to the Controller session it just
    verified. Nothing here lets a caller pick an Owner.

    Untyped on purpose. The response shape's authority is
    ``eidolon_sdk/contracts/mission_control/v1/mission-control-snapshot.schema.json``,
    validated against by test, and restating it as a pydantic tree here would
    make a second authority that can disagree with the first.
    """

    composition = await compose_runtime(request, owner_id=owner_id)
    audit_events = await _owner_audit_tail(request, owner_id, composition.ledger)
    return owner_runtime_projection(composition, audit_events=audit_events)


#: One page of history. Small enough that a phone's first screen arrives at
#: once, large enough that scrolling does not fetch on every flick.
ACTIVITY_PAGE = 30


@router.get("/mission-control/activities")
async def get_mission_control_activities(
    request: Request,
    owner_id: str,
    before: str | None = None,
    limit: int = ACTIVITY_PAGE,
) -> dict[str, Any]:
    """This Owner's interaction history, a page at a time.

    The map carries a bounded now; this carries the record. They come from the
    same projection so a turn reads the same in both, but not from the same
    read: the composition is eight upstream calls trimmed for display, and
    paging *that* for page four would be the wrong shape and eight times the
    cost. This walks the Agent's turn log, which is the durable record of every
    interaction and already has a cursor.

    ``before`` is that cursor, handed back as ``next_before``, so a caller never
    has to know it is a timestamp.
    """

    activities, cursor, failure = await owner_activity_page(
        request,
        owner_id=owner_id,
        before=before,
        limit=max(1, min(int(limit), 100)),
    )
    return owner_activity_history(activities, next_before=cursor, failure=failure)
