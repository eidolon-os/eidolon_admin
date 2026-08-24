"""Asking an Eidolon what it remembers about something.

The other memory read on this surface, and the one a person reaches for first:
the library answers "what do you have", 今日 answers "what happened", and this
answers the question they actually arrive with — "do you remember X".

What it hands back is a sentence and a time, and that is the whole design. The
records memory returns carry wings, rooms, scores and provenance; those are *how*
it found something, not what it remembers. A person asked the second question,
and passing the first through would make every client decide again which half to
show — with the machinery winning, because it is there.

Nothing here filters. The realm applies the same visibility policy its Eidolon's
recall uses, so a second filter would be a second answer to "what may this person
see" and the two would drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


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
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class RecollectionView:
    text: str
    #: When it was laid down, when memory knows. Absent stays absent rather than
    #: being filled in with the time of asking — a person reading "记于今天"
    #: about something from March would be reading a fabrication.
    remembered_at: str | None


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

    Not a scope: the space is the Owner's either way, and naming an Eidolon adds
    what that one was told in particular. It cannot widen what the space can see.
    """

    found = await memory.recollections(
        owner_id=owner_id, query=query, limit=limit, companion_id=companion_id
    )
    return Recollections(
        query=query,
        recollections=tuple(_view(record) for record in found),
    )


def _view(record: dict[str, Any]) -> RecollectionView:
    """One record, reduced to what was asked for.

    Defensive about shape rather than strict, and deliberately: this is the one
    read whose rows come from the realm as loose dictionaries. A row that cannot
    be read yields an empty sentence instead of failing the whole answer, because
    "it remembers nothing about you" is a much worse thing to say wrongly than
    one blank line is to show.
    """

    text = record.get("text")
    metadata = record.get("metadata")
    remembered_at = None
    if isinstance(metadata, dict):
        raw = metadata.get("created_at") or metadata.get("occurred_at")
        remembered_at = raw if isinstance(raw, str) and raw else None
    return RecollectionView(
        text=text if isinstance(text, str) else "",
        remembered_at=remembered_at,
    )
