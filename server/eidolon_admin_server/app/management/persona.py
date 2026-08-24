"""What an Eidolon has been, and going back to one of those.

The history is a record, not a settings screen. Every chapter is what this
Companion actually was for a while, and going back appends another one rather
than rewinding — so what someone reads later says "this is when it went back to
the way it was in March" instead of quietly losing the months in between.

Two things this layer drops on the way through, and one it refuses to do.

**Proposals are dropped.** A Companion considering a change has not changed, and
putting a review queue in front of someone turns living with an Eidolon into
appraising it — the one thing this product decided not to ask of them. The
authority still stores proposals; the agent still needs somewhere to stage. It is
simply not something a person is handed.

**Genome hashes, schema versions and realizers are dropped.** Those are how a
Companion is built. What a person wonders is when it changed, what changed, and
which one it is now.

**It does not compose the sentence.** ``change_summary`` travels as written by
whatever made the change, and an empty one stays empty. A sentence about who your
Eidolon became should not be written by a projection.

Ownership is proved before either call, and proved by the *authority* rather than
compared here: the persona routes are keyed on a Companion alone, so nothing in
them says whose it is. Asking the owner-scoped Companion route first is what
turns "someone else's Companion" into a 404 — and 404 rather than 403, so an
identifier cannot be probed for existence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from eidolon_admin_server.app.control_plane.contracts import (
    PersonaChapter,
    PersonaTimeline,
)
from eidolon_admin_server.app.management.roster import RosterReader

#: What is recorded as the reason when an Owner goes back. Said in their voice
#: because they did it; nothing here invents a reason on their behalf.
OWNER_RESTORE_SUMMARY = "回到了那时候的样子"


@runtime_checkable
class PersonaHistorian(Protocol):
    """The two authority calls this needs."""

    async def get_persona_timeline(self, companion_id: str) -> PersonaTimeline: ...

    async def restore_persona(
        self,
        companion_id: str,
        genome_id: str,
        change_summary: str,
    ) -> PersonaChapter: ...


@dataclass(frozen=True, slots=True)
class PersonaChapterView:
    chapter_id: str
    changed_at: str
    what_changed: str
    #: Set when this chapter exists because someone went back to an earlier one.
    restored_from: int | None
    is_current: bool


@dataclass(frozen=True, slots=True)
class PersonaHistory:
    companion_id: str
    #: Newest first, as the authority orders them.
    chapters: tuple[PersonaChapterView, ...]

    @property
    def current_chapter_id(self) -> str | None:
        for chapter in self.chapters:
            if chapter.is_current:
                return chapter.chapter_id
        return None


async def read_history(
    *,
    owner_id: str,
    companion_id: str,
    persona: PersonaHistorian,
    companions: RosterReader,
) -> PersonaHistory:
    await companions.get_owner_companion(owner_id, companion_id)
    return _history(await persona.get_persona_timeline(companion_id))


async def restore_chapter(
    *,
    owner_id: str,
    companion_id: str,
    chapter_id: str,
    persona: PersonaHistorian,
    companions: RosterReader,
) -> PersonaHistory:
    """Make this Eidolon the way it was then, and answer with where that leaves it.

    The history rather than the one new chapter: what someone wants to see after
    going back is the whole record with the new position in it, and a screen that
    had to ask again would be showing the old answer in the meantime.

    **A repeat of the same restore is a success, not a conflict.** The authority
    refuses to append a chapter for something a Companion already is — correctly,
    because there is nothing to record — but a client retrying a request whose
    answer it never saw is asking for a state that already holds. So the current
    chapter is read first and a matching request is answered with the history
    unchanged. Losing that race still surfaces the authority's conflict, which is
    the honest outcome: someone else moved this Eidolon while you were asking.
    """

    await companions.get_owner_companion(owner_id, companion_id)
    before = _history(await persona.get_persona_timeline(companion_id))
    if before.current_chapter_id == chapter_id:
        return before
    await persona.restore_persona(companion_id, chapter_id, OWNER_RESTORE_SUMMARY)
    return _history(await persona.get_persona_timeline(companion_id))


def _history(timeline: PersonaTimeline) -> PersonaHistory:
    return PersonaHistory(
        companion_id=timeline.companion_id,
        chapters=tuple(
            PersonaChapterView(
                chapter_id=chapter.genome_id,
                changed_at=chapter.created_at,
                what_changed=chapter.change_summary,
                restored_from=chapter.restored_from_version,
                is_current=chapter.is_current,
            )
            for chapter in timeline.chapters
            # Only what it has actually been. A proposal it never became is not
            # a past, and offering one to go back to would offer a life it never
            # had.
            if chapter.lifecycle_state == "committed"
        ),
    )
