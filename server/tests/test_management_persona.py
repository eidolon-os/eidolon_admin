"""What an Eidolon has been, and what going back does to that record.

The projection between the Companion authority and a person, and three decisions
that live nowhere else:

**Proposals are dropped.** A Companion considering a change has not changed.
Handing a review queue to someone turns living with an Eidolon into appraising
it, and offering a proposal as somewhere to *go back to* would offer a life it
never had.

**Ownership is proved by the authority.** These routes are keyed on a Companion
alone, so nothing in them says whose it is; the owner-scoped Companion route is
asked first, and someone else's Companion is a 404 from that call rather than a
comparison made here.

**Restoring is idempotent at the top.** The authority refuses to append a chapter
for something a Companion already is, which is correct — there is nothing to
record. But a client retrying a request whose answer it never saw is asking for a
state that already holds, and answering that with a conflict would make the retry
look like a failure. Losing the race still surfaces the authority's conflict,
which is honest: someone else moved this Eidolon while you were asking.
"""

from __future__ import annotations

import pytest

from eidolon_admin_server.app.control_plane.contracts import (
    CompanionIdentity,
    PersonaChapter,
    PersonaTimeline,
)
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.management.persona import (
    OWNER_RESTORE_SUMMARY,
    read_history,
    restore_chapter,
)

pytestmark = pytest.mark.asyncio

OWNER = "owner-1"
COMPANION = "companion-a"


def _chapter(
    genome_id: str,
    *,
    version: int,
    state: str = "committed",
    current: bool = False,
    summary: str = "",
    restored_from: int | None = None,
) -> PersonaChapter:
    return PersonaChapter(
        genome_id=genome_id,
        version=version,
        lifecycle_state=state,  # type: ignore[arg-type]
        change_summary=summary,
        restored_from_version=restored_from,
        is_current=current,
        created_at="2026-08-20T09:00:00+00:00",
    )


class _Authority:
    """The two calls the projection makes, plus the ownership proof."""

    def __init__(
        self,
        chapters: list[PersonaChapter],
        *,
        owns: bool = True,
        conflict: str | None = None,
    ) -> None:
        self.chapters = chapters
        self.owns = owns
        self.conflict = conflict
        self.proofs: list[tuple[str, str]] = []
        self.restores: list[tuple[str, str, str]] = []

    async def get_owner_companion(self, owner_id: str, companion_id: str):
        self.proofs.append((owner_id, companion_id))
        if not self.owns:
            raise AuthorityFailure(
                "data", "not_found", "companion not found", 404, retryable=False
            )
        return CompanionIdentity(
            operation="companion.identity",
            companion_id=companion_id,
            owner_id=owner_id,
            display_name="小忆",
            lifecycle_state="active",
            kind="standard",
            revision=2,
        )

    async def get_persona_timeline(self, companion_id: str) -> PersonaTimeline:
        return PersonaTimeline(
            operation="companion.persona-timeline",
            companion_id=companion_id,
            chapters=tuple(self.chapters),
        )

    async def restore_persona(
        self, companion_id: str, genome_id: str, change_summary: str
    ) -> PersonaChapter:
        self.restores.append((companion_id, genome_id, change_summary))
        if self.conflict is not None:
            raise AuthorityFailure(
                "data", "conflict", self.conflict, 409, retryable=False
            )
        self.chapters = [
            _chapter(chapter.genome_id, version=chapter.version, current=False)
            for chapter in self.chapters
        ]
        appended = _chapter(
            "g_new", version=99, current=True, summary=change_summary, restored_from=1
        )
        self.chapters.insert(0, appended)
        return appended


async def test_only_what_it_has_been_is_offered() -> None:
    """A proposal is not a past, so it is not somewhere to go back to."""

    authority = _Authority(
        [
            _chapter("g_proposed", version=3, state="proposed"),
            _chapter("g_2", version=2, current=True),
            _chapter("g_1", version=1),
        ]
    )

    history = await read_history(
        owner_id=OWNER,
        companion_id=COMPANION,
        persona=authority,
        companions=authority,
    )

    assert [chapter.chapter_id for chapter in history.chapters] == ["g_2", "g_1"]
    assert history.current_chapter_id == "g_2"


async def test_a_read_proves_ownership_before_it_reads() -> None:
    """The persona routes name no Owner, so the proof cannot come from them."""

    authority = _Authority([_chapter("g_1", version=1, current=True)], owns=False)

    with pytest.raises(AuthorityFailure) as caught:
        await read_history(
            owner_id=OWNER,
            companion_id=COMPANION,
            persona=authority,
            companions=authority,
        )

    assert caught.value.status_code == 404
    assert authority.proofs == [(OWNER, COMPANION)]


async def test_a_restore_proves_ownership_before_it_writes() -> None:
    authority = _Authority([_chapter("g_1", version=1, current=True)], owns=False)

    with pytest.raises(AuthorityFailure):
        await restore_chapter(
            owner_id=OWNER,
            companion_id=COMPANION,
            chapter_id="g_1",
            persona=authority,
            companions=authority,
        )

    assert authority.restores == []


async def test_going_back_appends_and_says_where_that_leaves_it() -> None:
    """Appends rather than rewinds: the months in between stay in the record."""

    authority = _Authority(
        [_chapter("g_2", version=2, current=True), _chapter("g_1", version=1)]
    )

    history = await restore_chapter(
        owner_id=OWNER,
        companion_id=COMPANION,
        chapter_id="g_1",
        persona=authority,
        companions=authority,
    )

    assert authority.restores == [(COMPANION, "g_1", OWNER_RESTORE_SUMMARY)]
    # The old chapters are still there, and the new one is current.
    assert [chapter.chapter_id for chapter in history.chapters] == ["g_new", "g_2", "g_1"]
    assert history.current_chapter_id == "g_new"


async def test_the_reason_recorded_is_the_owners_and_is_not_invented() -> None:
    """Said in their voice because they did it; nothing composes one for them."""

    authority = _Authority(
        [_chapter("g_2", version=2, current=True), _chapter("g_1", version=1)]
    )

    await restore_chapter(
        owner_id=OWNER,
        companion_id=COMPANION,
        chapter_id="g_1",
        persona=authority,
        companions=authority,
    )

    _companion, _genome, summary = authority.restores[0]
    assert summary == OWNER_RESTORE_SUMMARY


async def test_asking_for_the_chapter_it_already_is_writes_nothing() -> None:
    """The retry case. The desired end state holds, so this is a success — and
    the authority is not asked to append a chapter it would refuse."""

    authority = _Authority(
        [_chapter("g_2", version=2, current=True), _chapter("g_1", version=1)]
    )

    history = await restore_chapter(
        owner_id=OWNER,
        companion_id=COMPANION,
        chapter_id="g_2",
        persona=authority,
        companions=authority,
    )

    assert authority.restores == []
    assert history.current_chapter_id == "g_2"


async def test_a_conflict_from_the_authority_is_not_smoothed_over() -> None:
    """Two restores racing, or a chapter it never was: the refusal is the answer.

    Only the case that is already true gets turned into a success. Anything else
    the authority refuses stays refused — a projection that reported "went back"
    for a restore that did not happen is the one outcome nobody could detect.
    """

    authority = _Authority(
        [_chapter("g_2", version=2, current=True), _chapter("g_1", version=1)],
        conflict="only a committed persona genome can be restored",
    )

    with pytest.raises(AuthorityFailure) as caught:
        await restore_chapter(
            owner_id=OWNER,
            companion_id=COMPANION,
            chapter_id="g_1",
            persona=authority,
            companions=authority,
        )

    assert caught.value.status_code == 409
