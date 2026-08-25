"""What has been done to this Owner's things lately.

The authority records facts — an action, what it was done to, when. This layer
adds the one thing the facts cannot carry: **what the subject is called**. An
event that says ``companion.archived c_9f3a`` is not something a person can
read; the same event beside 小忆 is.

**It does not compose the sentence.** The words belong in the client, next to
the rest of its language, so a Host does not have to know Chinese to be able to
say that an Eidolon was put away. What travels is a stable action word, the
subject, its name when there is one, and the couple of fields a sentence needs.

**An action this layer has never heard of still travels.** A Host newer than its
client records acts the client has no word for, and the honest answer is "this
happened, on this day, to this one" rather than dropping the row — a history
with holes in it is worse than a history with an unfamiliar line in it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from eidolon_admin_server.app.control_plane.contracts import OwnerGovernanceEvents
from eidolon_admin_server.app.management.roster import RosterReader

#: Payload fields worth carrying to a screen, by the action that produces them.
#:
#: An allow-list rather than "pass the payload through": a governance payload is
#: written for an audit reader and may grow fields nobody meant to put in front
#: of a person. What a sentence needs is small and known.
_DETAIL_FIELDS: dict[str, tuple[str, ...]] = {
    "companion.retirement_begun": ("replacement_companion_id",),
    "owner.default_companion_changed": (
        "companion_id",
        "previous_companion_id",
        "reason",
    ),
}


@runtime_checkable
class GovernanceHistorian(Protocol):
    async def list_governance_events(
        self,
        owner_id: str,
        *,
        limit: int | None = None,
        before: int | None = None,
    ) -> OwnerGovernanceEvents: ...


@dataclass(frozen=True, slots=True)
class ActivityMoment:
    """One thing that happened, as a screen receives it."""

    event_id: str
    action: str
    subject_type: str
    subject_id: str
    #: What the subject is called now. Empty when the Owner never named it, or
    #: when it no longer exists — a Companion that was deleted still happened,
    #: and the row says so with its identifier rather than disappearing.
    subject_name: str
    occurred_at: str
    #: Whether it worked. Failures are part of a history, not noise to hide.
    outcome: str
    detail: dict[str, str]


@dataclass(frozen=True, slots=True)
class ActivityFeed:
    moments: tuple[ActivityMoment, ...]
    #: Send back for the page before this one. ``None`` means this is as far
    #: back as the Host still holds, which is not "nothing happened before".
    next_cursor: int | None


async def read_activity(
    *,
    owner_id: str,
    limit: int | None = None,
    before: int | None = None,
    history: GovernanceHistorian,
    companions: RosterReader,
) -> ActivityFeed:
    """One page of this Owner's history, with names filled in.

    The roster is read once for the page rather than per row — a page of twenty
    events about three Companions must not be twenty lookups — and only when the
    page actually names one, so a history of Owner-level events costs nothing
    extra.
    """

    page = await history.list_governance_events(owner_id, limit=limit, before=before)
    names = (
        await _companion_names(owner_id, companions)
        if any(_names_a_companion(event) for event in page.events)
        else {}
    )
    return ActivityFeed(
        moments=tuple(
            ActivityMoment(
                event_id=event.event_id,
                action=event.action,
                subject_type=event.subject_type,
                subject_id=event.subject_id,
                subject_name=names.get(event.subject_id, ""),
                occurred_at=event.occurred_at,
                outcome=event.outcome,
                detail=_detail(event.action, event.payload, names),
            )
            for event in page.events
        ),
        next_cursor=page.next_cursor,
    )


def _names_a_companion(event) -> bool:
    """Whether this event mentions a Companion anywhere a person will read.

    The subject is the obvious place and not the only one: "who answers now
    changed" is an event about the *Owner* whose whole meaning is two Companions
    named in its payload. Checking only the subject left those lines showing
    identifiers, which is the one thing this layer exists to prevent.
    """

    if event.subject_type == "companion":
        return True
    return any(
        field.endswith("companion_id") for field in _DETAIL_FIELDS.get(event.action, ())
    )


async def _companion_names(owner_id: str, companions: RosterReader) -> dict[str, str]:
    """Every Companion this Owner has, by id.

    One page. A person with more Companions than fit in one is a person this
    read will name partly — and a row that falls back to an identifier is a
    smaller failure than a second round trip on every history read.
    """

    page = await companions.list_owner_companions(owner_id)
    return {row.companion_id: row.display_name for row in page.companions}


def _detail(
    action: str, payload: dict, names: dict[str, str]
) -> dict[str, str]:
    fields = _DETAIL_FIELDS.get(action, ())
    detail: dict[str, str] = {}
    for field in fields:
        value = payload.get(field)
        if value is None:
            continue
        detail[field] = str(value)
        # A companion id in a detail is as unreadable as one in a subject, so it
        # gets the same treatment.
        if field.endswith("companion_id"):
            name = names.get(str(value))
            if name:
                detail[f"{field}_name"] = name
    return detail
