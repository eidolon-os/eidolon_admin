"""What I asked it to do, and who owns the answer to "is it done".

The projection between the Agent's runtime and a person, and its whole design is
one boundary: **the task state machine stays in the Agent**. So the tests are
mostly about what this layer refuses to decide.

The one thing it *does* decide is the ownership check on a single task, and that
is not a preference: the Agent keys a task by its id alone, so its detail route
cannot prove whose it is the way Data's owner-scoped routes can. Checking here is
honest about where the boundary actually sits; pretending the producer did it
would be the kind of assumption that reads as a security property and is not one.
"""

from __future__ import annotations

import pytest

from eidolon_admin_server.app.control_plane.contracts import (
    ConversationRows,
    TaskRow,
    TaskRows,
)
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.management.activity import (
    DEFAULT_PAGE,
    MAXIMUM_PAGE,
    cancel_task,
    read_conversations,
    read_task,
    read_tasks,
    retry_task,
)

pytestmark = pytest.mark.asyncio

OWNER = "owner-1"
COMPANION = "companion-a"


def _task_row(**overrides) -> dict:
    row = {
        "task_id": "j-1",
        "owner_id": OWNER,
        "companion_id": COMPANION,
        "status": "running",
        "task": "帮我查一下周末的天气",
        "task_type": "research",
        "urgency": "normal",
        "expected_output": "一句话",
        "progress_summary": "在看了",
        "created_at": "2026-08-24T09:00:00+00:00",
        "updated_at": "2026-08-24T09:05:00+00:00",
        # Fields this projection does not carry. The Agent's rows are full of
        # them and a strict consumer would break on the next one it adds.
        "worker_id": "agent-abc",
        "lease_until": "2026-08-24T09:20:00+00:00",
        "mementos_workspace_dir": "/var/lib/whatever",
    }
    row.update(overrides)
    return row


class _Agent:
    def __init__(self, *, tasks: list[dict] | None = None, cursor: str | None = None) -> None:
        self.tasks = tasks if tasks is not None else [_task_row()]
        self.cursor = cursor
        self.asked: list[tuple] = []
        self.actions: list[tuple[str, str, str]] = []
        self.refuse: AuthorityFailure | None = None

    async def list_conversations(self, *, owner_id, companion_id, limit, before=None):
        self.asked.append(("conversations", owner_id, companion_id, limit, before))
        return ConversationRows.model_validate(
            {
                "conversations": [
                    {
                        "conversation_id": "c-1",
                        "owner_id": owner_id,
                        "companion_id": companion_id,
                        "title": "  周末计划  ",
                        "status": "open",
                        "started_at": "2026-08-24T08:00:00+00:00",
                        "updated_at": "2026-08-24T09:00:00+00:00",
                        "runtime_session_id": "rs-1",
                        "device_id": "dev-1",
                    }
                ],
                "next_before": self.cursor,
            }
        )

    async def list_tasks(
        self, *, owner_id, companion_id, limit, status=None, before=None
    ):
        self.asked.append(("tasks", owner_id, companion_id, limit, status, before))
        return TaskRows.model_validate(
            {"tasks": self.tasks, "next_before": self.cursor}
        )

    async def get_task(self, *, owner_id, task_id):
        if self.refuse is not None:
            raise self.refuse
        return TaskRow.model_validate(self.tasks[0])

    async def cancel_task(self, *, owner_id, task_id):
        if self.refuse is not None:
            raise self.refuse
        self.actions.append(("cancel", owner_id, task_id))
        return TaskRow.model_validate(_task_row(status="cancelled"))

    async def retry_task(self, *, owner_id, task_id):
        if self.refuse is not None:
            raise self.refuse
        self.actions.append(("retry", owner_id, task_id))
        return TaskRow.model_validate(_task_row(status="accepted", progress_summary=None))


async def test_a_conversation_row_is_when_it_happened_not_what_was_said() -> None:
    """The runtime keeps no words on these rows, so this list cannot pretend to."""

    agent = _Agent()

    page = await read_conversations(
        owner_id=OWNER, companion_id=COMPANION, limit=None, cursor=None, activity=agent
    )

    row = page.conversations[0]
    assert row.conversation_id == "c-1"
    assert row.title == "周末计划"
    assert row.started_at == "2026-08-24T08:00:00+00:00"
    # Still open: absent rather than an invented end.
    assert row.ended_at is None


async def test_the_owner_is_always_sent_because_it_is_only_a_filter() -> None:
    """The Agent's list routes answer with every Owner's rows when nobody says
    which — so this layer never leaves it out."""

    agent = _Agent()

    await read_conversations(
        owner_id=OWNER, companion_id=COMPANION, limit=None, cursor=None, activity=agent
    )
    await read_tasks(
        owner_id=OWNER,
        companion_id=COMPANION,
        limit=None,
        status=None,
        cursor=None,
        activity=agent,
    )

    assert agent.asked[0][1:3] == (OWNER, COMPANION)
    assert agent.asked[1][1:3] == (OWNER, COMPANION)


async def test_a_page_is_bounded_here_as_well() -> None:
    agent = _Agent()

    await read_tasks(
        owner_id=OWNER,
        companion_id=COMPANION,
        limit=10_000,
        status=None,
        cursor=None,
        activity=agent,
    )
    await read_tasks(
        owner_id=OWNER,
        companion_id=COMPANION,
        limit=None,
        status=None,
        cursor=None,
        activity=agent,
    )

    assert agent.asked[0][3] == MAXIMUM_PAGE
    assert agent.asked[1][3] == DEFAULT_PAGE


async def test_the_cursor_is_forwarded_and_read_by_nobody() -> None:
    """It is the runtime's own keyset position. Interpreting it here would make
    the producer's page boundary part of this contract."""

    agent = _Agent(cursor="2026-08-24T08:00:00+00:00")

    page = await read_tasks(
        owner_id=OWNER,
        companion_id=COMPANION,
        limit=None,
        status=None,
        cursor="2026-08-24T09:00:00+00:00",
        activity=agent,
    )

    assert agent.asked[0][5] == "2026-08-24T09:00:00+00:00"
    assert page.next_cursor == "2026-08-24T08:00:00+00:00"


async def test_a_status_this_release_never_heard_of_still_projects() -> None:
    """The vocabulary is the Agent's. A consumer that refused an unfamiliar state
    would turn a runtime that grew one into a page nobody can open."""

    agent = _Agent(tasks=[_task_row(status="paused_for_review")])

    page = await read_tasks(
        owner_id=OWNER,
        companion_id=COMPANION,
        limit=None,
        status=None,
        cursor=None,
        activity=agent,
    )

    assert page.tasks[0].status == "paused_for_review"


async def test_the_runtimes_own_bookkeeping_does_not_reach_a_person() -> None:
    """Worker leases and workspace directories are how it runs, not what I asked."""

    agent = _Agent()

    page = await read_tasks(
        owner_id=OWNER,
        companion_id=COMPANION,
        limit=None,
        status=None,
        cursor=None,
        activity=agent,
    )

    carried = {
        "task_id",
        "status",
        "asked",
        "kind",
        "urgency",
        "expected_output",
        "progress",
        "result",
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
        "completed_at",
    }
    from dataclasses import fields as dataclass_fields

    assert {field.name for field in dataclass_fields(page.tasks[0])} == carried


async def test_cancel_answers_with_what_the_runtime_says_it_became() -> None:
    """Not with what this layer hoped. There is no local rule about which states
    may be cancelled — that rule belongs to the thing running the task, including
    its refusal when the task finished while the page was open."""

    agent = _Agent()

    task = await cancel_task(owner_id=OWNER, task_id="j-1", activity=agent)

    assert agent.actions == [("cancel", OWNER, "j-1")]
    assert task.status == "cancelled"


async def test_retry_answers_with_what_the_runtime_says_it_became() -> None:
    agent = _Agent()

    task = await retry_task(owner_id=OWNER, task_id="j-1", activity=agent)

    assert agent.actions == [("retry", OWNER, "j-1")]
    assert task.status == "accepted"
    # The previous run's progress line is gone because the runtime cleared it,
    # not because this layer blanked it.
    assert task.progress == ""


async def test_a_refusal_from_the_runtime_is_relayed_not_smoothed() -> None:
    """A 409 from the Agent — "it already finished", "it cannot be retried from
    here" — is the answer. Turning it into a success is the one outcome nobody
    could detect."""

    agent = _Agent()
    agent.refuse = AuthorityFailure(
        "agent", "conflict", "long task already finished as succeeded", 409
    )

    for action in (cancel_task, retry_task):
        with pytest.raises(AuthorityFailure) as caught:
            await action(owner_id=OWNER, task_id="j-1", activity=agent)
        assert caught.value.status_code == 409


async def test_another_owners_task_is_not_readable() -> None:
    agent = _Agent()
    agent.refuse = AuthorityFailure("agent", "not_found", "task not found", 404)

    with pytest.raises(AuthorityFailure) as caught:
        await read_task(owner_id=OWNER, task_id="j-theirs", activity=agent)

    assert caught.value.status_code == 404
