"""Asking an Eidolon what it remembers about something.

The other memory read on this surface, and the one a person reaches for first:
the library answers "what do you have", the timeline answers "when", and this
answers the question they actually arrive with — "do you remember X".

Text travels with known dates and the user's original words, so the same detail
presentation can explain a search result or a browsed record. Retrieval scores
and internal routing stay in the service.

Nothing here filters. The realm applies the same visibility policy its Eidolon's
recall uses, so a second filter would be a second answer to "what may this person
see" and the two would drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from eidolon_memory_contracts.owner import MemoryProvenance

from eidolon_admin_server.app.control_plane.contracts import (
    MemoryRecollection,
    MemoryRecollections,
)


@runtime_checkable
class MemoryRecollector(Protocol):
    """The one authority read this needs."""

    async def recollections(
        self,
        *,
        owner_id: str,
        query: str,
        limit: int,
        companion_id: str | None = None,
    ) -> MemoryRecollections: ...


@dataclass(frozen=True, slots=True)
class RecollectionView:
    text: str
    #: When it was laid down, when memory knows. Absent stays absent rather than
    #: being filled in with the time of asking — a person reading "记于今天"
    #: about something from March would be reading a fabrication.
    remembered_at: str | None
    provenance: MemoryProvenance


@dataclass(frozen=True, slots=True)
class Recollections:
    """What it remembers about this, and what was asked.

    The query travels back for the same reason a day's window does: an empty
    answer with no question attached cannot be told apart from an answer to a
    different one.
    """

    query: str
    recollections: tuple[RecollectionView, ...]


async def recall(
    *,
    owner_id: str,
    query: str,
    limit: int,
    companion_id: str | None,
    memory: MemoryRecollector,
) -> Recollections:
    """``companion_id`` selects an audience exactly as the browse does.

    It is a logical scope inside the Owner's physical Realm: naming an Eidolon
    reads what that one can recall, never a sibling's audience. Without it the
    Owner asks about their own memory, every audience in the realm.
    """

    found = await memory.recollections(
        owner_id=owner_id, query=query, limit=limit, companion_id=companion_id
    )
    return Recollections(
        query=query,
        recollections=tuple(_view(record) for record in found.recollections),
    )


def _view(record: MemoryRecollection) -> RecollectionView:
    """One record, reduced to what was asked for.

    Typed now: the realm serializes through the shared contract, so the loose
    dictionary this used to defend against (and its guesses at ``metadata``
    fields no realm sends) is gone.
    """

    return RecollectionView(
        text=record.text,
        remembered_at=record.remembered_at or None,
        provenance=record.provenance,
    )
