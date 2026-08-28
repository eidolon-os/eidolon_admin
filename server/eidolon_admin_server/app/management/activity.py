"""What I asked it to do, and whether it did.

The blueprint's fifth moment — 「我交代的事，它真的做完了」 — and the one place on
this surface where a person can change something outside memory. So the boundary
matters more here than the projection does.

**The task state machine stays in the Agent.** Nothing here stores a status, and
cancel/retry return whatever the runtime says the task became. A mirror in this
process would be a second answer to "is it done", and it would be the wrong one
every time the runtime moved while nobody was looking. That is also why neither
action has a local guard: whether a finished task may be cancelled is the
runtime's rule, and asking twice for the same rule is how two copies of it drift.

**A conversation row is when it happened; a transcript is what was said.** Two
reads rather than one field, because the runtime keeps them apart for a reason
worth keeping: a page of conversation rows costs nothing and a page of turns
carries every word in them. ``read_conversations`` lists occasions;
``read_transcript`` opens one.

**A transcript carries what a person said and what their Eidolon said, and
nothing else.** Tool traffic — the calls it made, the arguments it passed, what
came back — is how an answer was reached rather than the conversation, and it can
contain anything the tools touched. Dropping it here rather than at a screen means
no client has to decide again, and none of it reaches a phone in the first place.

**Only this Owner's and Companion's, and proved by the ids the row carries.**
The Agent's list routes take both values as filters. This layer always sends
them and refuses conversation or transcript rows whose echoed scope differs.
Tasks remain Owner-authorized and Companion-filtered by the Agent's task
contract. Keeping that distinction explicit avoids pretending all three reads
have stronger authority semantics than their upstream actually provides.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from eidolon_admin_server.app.control_plane.contracts import (
    ConversationRows,
    TaskRow,
    TaskRows,
    TranscriptRows,
)
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure

#: Which roles are the conversation. Anything else on a turn — tool calls, tool
#: results, whatever a future runtime adds — is how the answer was reached, not
#: what was said, so it does not travel. An unknown role is dropped rather than
#: shown: a transcript is the wrong place to find out what a new message kind is.
SPOKEN_ROLES = frozenset({"user", "assistant"})

#: How many rows one page holds when the caller does not say. The Agent bounds
#: its own list at 200; this is the smaller number a screen actually reads.
DEFAULT_PAGE = 20
MAXIMUM_PAGE = 100


@runtime_checkable
class CompanionActivityReader(Protocol):
    """The reads and the two actions this projection needs."""

    async def list_conversations(
        self,
        *,
        owner_id: str,
        companion_id: str,
        limit: int,
        before: str | None = None,
    ) -> ConversationRows: ...

    async def list_tasks(
        self,
        *,
        owner_id: str,
        companion_id: str,
        limit: int,
        status: str | None = None,
        before: str | None = None,
    ) -> TaskRows: ...

    async def list_transcript(
        self,
        *,
        owner_id: str,
        companion_id: str,
        conversation_id: str,
        limit: int,
        before: str | None = None,
    ) -> TranscriptRows: ...

    async def get_task(self, *, owner_id: str, task_id: str) -> TaskRow: ...

    async def cancel_task(self, *, owner_id: str, task_id: str) -> TaskRow: ...

    async def retry_task(self, *, owner_id: str, task_id: str) -> TaskRow: ...


@dataclass(frozen=True, slots=True)
class ConversationView:
    conversation_id: str
    #: What it was called, when anything named it. Empty stays empty: a title
    #: composed here would be this layer summarising someone's conversation.
    title: str
    started_at: str
    updated_at: str
    #: Present only when it has ended. Absent means it has not — which is a
    #: different thing from ended at an unknown time.
    ended_at: str | None


@dataclass(frozen=True, slots=True)
class ConversationPage:
    conversations: tuple[ConversationView, ...]
    #: The runtime's cursor, forwarded as received and read by nobody in between.
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class SpokenMessage:
    #: ``user`` or ``assistant``. Kept rather than turned into a boolean: a
    #: transcript with three participants (a second Companion, a person on
    #: another device) is a thing this product may grow, and a boolean would have
    #: to be undone to say so.
    role: str
    text: str


@dataclass(frozen=True, slots=True)
class TranscriptTurn:
    turn_id: str
    started_at: str
    #: Absent while a turn is still going. A turn that never finished is a real
    #: thing to see in a transcript — it is what a dropped connection looks like.
    finished_at: str | None
    status: str
    messages: tuple[SpokenMessage, ...]


@dataclass(frozen=True, slots=True)
class Transcript:
    conversation_id: str
    #: Newest turn first, as the runtime answers. Which way a person reads it is
    #: the client's decision, and reversing a page is cheap.
    turns: tuple[TranscriptTurn, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class TaskView:
    task_id: str
    #: The runtime's word, relayed. Not narrowed to a Literal: the vocabulary is
    #: the Agent's and a state this release has not heard of must not make a
    #: person's page unopenable.
    status: str
    asked: str
    kind: str
    urgency: str
    expected_output: str
    #: What it has said about how far it has got. Absent until it says something.
    progress: str
    #: The answer, when there is one. A task can be finished and have none.
    result: str
    #: Why it stopped, when it stopped badly. Two fields because a code is for
    #: matching and a message is for reading, and collapsing them loses one.
    error_code: str
    error_message: str
    created_at: str
    updated_at: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class TaskPage:
    tasks: tuple[TaskView, ...]
    next_cursor: str | None


async def read_conversations(
    *,
    owner_id: str,
    companion_id: str,
    limit: int | None,
    cursor: str | None,
    activity: CompanionActivityReader,
) -> ConversationPage:
    page = await activity.list_conversations(
        owner_id=owner_id,
        companion_id=companion_id,
        limit=_page(limit),
        before=cursor,
    )
    for row in page.conversations:
        if row.owner_id != owner_id or row.companion_id != companion_id:
            raise AuthorityFailure(
                "agent",
                "contract_violation",
                "agent returned a conversation outside the requested scope",
                502,
            )
    return ConversationPage(
        conversations=tuple(
            ConversationView(
                conversation_id=row.conversation_id,
                title=(row.title or "").strip(),
                started_at=row.started_at or "",
                updated_at=row.updated_at or "",
                ended_at=row.ended_at,
            )
            for row in page.conversations
        ),
        next_cursor=page.next_before,
    )


async def read_transcript(
    *,
    owner_id: str,
    companion_id: str,
    conversation_id: str,
    limit: int | None,
    cursor: str | None,
    activity: CompanionActivityReader,
) -> Transcript:
    """What was said in one conversation.

    Bounded by a page rather than by shortening messages: a transcript that
    clipped someone's own words would lose exactly what they opened it for. The
    runtime bounds it too — this is the smaller number a screen reads.
    """

    page = await activity.list_transcript(
        owner_id=owner_id,
        companion_id=companion_id,
        conversation_id=conversation_id,
        limit=_page(limit),
        before=cursor,
    )
    if (
        page.owner_id != owner_id
        or page.companion_id != companion_id
        or page.conversation_id != conversation_id
    ):
        raise AuthorityFailure(
            "agent",
            "contract_violation",
            "agent returned a transcript outside the requested scope",
            502,
        )
    return Transcript(
        conversation_id=page.conversation_id,
        turns=tuple(
            TranscriptTurn(
                turn_id=turn.turn_id,
                started_at=turn.started_at or "",
                finished_at=turn.finished_at,
                status=turn.status,
                messages=tuple(
                    SpokenMessage(role=message.role, text=message.content)
                    for message in turn.messages
                    if message.role in SPOKEN_ROLES and message.tool_name is None
                ),
            )
            for turn in page.turns
        ),
        next_cursor=page.next_before,
    )


async def read_tasks(
    *,
    owner_id: str,
    companion_id: str,
    limit: int | None,
    status: str | None,
    cursor: str | None,
    activity: CompanionActivityReader,
) -> TaskPage:
    page = await activity.list_tasks(
        owner_id=owner_id,
        companion_id=companion_id,
        limit=_page(limit),
        status=status,
        before=cursor,
    )
    return TaskPage(
        tasks=tuple(_task(row) for row in page.tasks),
        next_cursor=page.next_before,
    )


async def read_task(
    *,
    owner_id: str,
    task_id: str,
    activity: CompanionActivityReader,
) -> TaskView:
    return _task(await activity.get_task(owner_id=owner_id, task_id=task_id))


async def cancel_task(
    *,
    owner_id: str,
    task_id: str,
    activity: CompanionActivityReader,
) -> TaskView:
    """Stop it, and answer with what the runtime says it became.

    No guard here about which states may be cancelled. That rule belongs to the
    thing that runs the task, and a copy of it in this process would be a second
    rule to keep in step — including the case that matters, where the task
    finished between the page being read and the button being pressed.
    """

    return _task(await activity.cancel_task(owner_id=owner_id, task_id=task_id))


async def retry_task(
    *,
    owner_id: str,
    task_id: str,
    activity: CompanionActivityReader,
) -> TaskView:
    """Ask for it again, and answer with what the runtime says it became."""

    return _task(await activity.retry_task(owner_id=owner_id, task_id=task_id))


def _task(row: TaskRow) -> TaskView:
    return TaskView(
        task_id=row.task_id,
        status=row.status,
        asked=row.task,
        kind=row.task_type,
        urgency=row.urgency,
        expected_output=row.expected_output or "",
        progress=row.progress_summary or "",
        result=row.result_text or "",
        error_code=row.error_code or "",
        error_message=row.error_message or "",
        created_at=row.created_at or "",
        updated_at=row.updated_at or "",
        completed_at=row.completed_at,
    )


def _page(limit: int | None) -> int:
    """Bounded here as well as at the runtime.

    Not defensive duplication: the number a screen wants and the number a
    runtime will serve are different decisions, and this is the first one.
    """

    if limit is None:
        return DEFAULT_PAGE
    return max(1, min(limit, MAXIMUM_PAGE))
