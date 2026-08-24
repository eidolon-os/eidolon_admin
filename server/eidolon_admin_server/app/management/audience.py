"""Saying which of an Owner's Eidolons a memory belongs to.

The write side of the audience axis. Every read on this surface has honoured that
axis since Phase 2 — a Companion never sees what belongs to another — and until
this module there was no way for a person to ask for it: an isolation the system
could enforce and nobody could request.

Thin, like the reads beside it, and for a sharper reason than usual. Three things
this layer must not do:

- **Decide what an audience is.** ``owner`` and ``companion:<id>`` are the memory
  contract's vocabulary and the realm validates them; a second opinion here would
  be a second answer to "who may see this".
- **Interpret the entry id.** It names a drawer in the realm's store. This layer
  quotes it into a path and lets the realm refuse anything that is not one, which
  is the only place that can tell.
- **Turn the realm's ``status`` into done.** Publishing is durable and applying is
  a projection still catching up, exactly as for a forget.

Not a two-step, unlike forgetting. A forget resolves words into a set and needs a
token binding what was shown; here the subject is one entry the person was
looking at, so there is nothing to resolve and nothing to bind — and nothing
becomes unrecallable, because the Companion it now belongs to still recalls it in
full.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from eidolon_admin_server.app.control_plane.contracts import MemoryAudience


@runtime_checkable
class MemoryAudienceKeeper(Protocol):
    """The one authority call this needs."""

    async def assign_audience(
        self,
        *,
        owner_id: str,
        entry_id: str,
        companion_id: str | None = None,
    ) -> MemoryAudience: ...


@dataclass(frozen=True, slots=True)
class MemoryAudienceResult:
    """Where a memory now belongs, and whether that has taken effect yet."""

    entry_id: str
    #: Empty means the Owner layer: every Companion may recall it again. Named
    #: rather than the raw audience token, so nothing above has to know that the
    #: token happens to be ``companion:<id>``.
    companion_id: str
    status: str


async def assign_memory_audience(
    *,
    owner_id: str,
    entry_id: str,
    companion_id: str | None,
    memory: MemoryAudienceKeeper,
) -> MemoryAudienceResult:
    answer = await memory.assign_audience(
        owner_id=owner_id, entry_id=entry_id, companion_id=companion_id
    )
    return MemoryAudienceResult(
        entry_id=answer.entry_id,
        companion_id=answer.companion_id,
        status=answer.status,
    )
