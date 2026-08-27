"""A conversation, drawn while it is still happening.

This is the seam the star map's whole "nothing moves" complaint lived at. Every
consumer downstream was built for a turn in progress — a stage whose status is
``running``, a route hop that is current, a companion that is lit — and the one
thing missing was a producer: the Agent writes its durable turn row when the
turn *ends*, so no reading ever contained a running turn.

The Agent now serves in-flight turns from the same route, in the same row shape,
with ``status: "running"``. What these tests hold is what this side must do with
such a row, and all of it is about not overclaiming:

* **a stage cannot be finished while its turn is not.** The tool stage used to
  read ``done`` for every turn, so a turn that had not called a tool yet — and
  might never — reported it had already been through that step;
* **the highlight has to move.** A turn with one running stage from beginning to
  end draws a map that never changes, which is indistinguishable from a broken
  one. ``response`` is the moment the answer starts, observed from inside the
  turn, and it is what makes the arc advance;
* **a finished turn reads exactly as it did**, because most turns on the map are
  finished ones and this change is not about them.
"""

from __future__ import annotations

from eidolon_admin_server.app.mission_control.service import (
    _turn,
    _voice_activity,
)


def _row(**overrides):
    """The shape the Agent's turns route serves. Live rows are partial by design."""

    row = {
        "turn_id": "turn-live",
        "conversation_id": "conv-1",
        "owner_id": "owner-1",
        "companion_id": "eidolon-xiaoer",
        "device_id": "dev-box3",
        "status": "running",
        "trigger": "user_utterance",
        "started_at": "2026-08-27T09:00:00Z",
        "finished_at": None,
        "latency_first_delta_ms": None,
        "total_latency_ms": None,
        # Partial on purpose: a running turn has not said whether recall
        # happened, so the block is absent rather than zeroed.
        "observability_summary": {
            "live": True,
            "tools": {"count": 0, "completed": 0, "names": [], "error_count": 0},
            "latency": {"first_delta_ms": None, "total_ms": None},
        },
    }
    row.update(overrides)
    return row


def _stages(row) -> dict[str, str]:
    return {stage["key"]: stage["status"] for stage in _turn(row).stages}


def test_a_turn_that_just_started_claims_nothing_it_has_not_done() -> None:
    stages = _stages(_row())

    assert stages["agent_turn"] == "running"
    # The two that used to lie. `tools: done` on a turn that has not called one
    # is the bug this asserts against.
    assert stages["tools"] == "pending"
    assert stages["response"] == "pending"
    assert stages["memory_recall"] == "pending"
    assert stages["memory_write"] == "pending"


def test_the_answer_starting_moves_the_highlight_forward() -> None:
    """The one moment a running turn can observe from the inside."""

    before = _voice_activity(_turn(_row()))
    after = _voice_activity(
        _turn(
            _row(
                latency_first_delta_ms=812,
                observability_summary={
                    "live": True,
                    "tools": {"count": 0, "completed": 0, "names": [], "error_count": 0},
                    "latency": {"first_delta_ms": 812, "total_ms": None},
                },
            )
        )
    )

    assert before.current_hop_id != after.current_hop_id
    assert before.current_hop_id.endswith(":agent_turn:2")
    assert after.current_hop_id.endswith(":response:4")
    # And both are live, so the companion stays lit across the move.
    assert before.status == "running"
    assert after.status == "running"


def test_an_open_tool_call_is_running_and_a_returned_one_is_done() -> None:
    """``completed`` exists only on a live row, and this is why it is needed."""

    open_call = _stages(
        _row(
            observability_summary={
                "live": True,
                "tools": {"count": 1, "completed": 0, "names": ["memory_search"], "error_count": 0},
                "latency": {"first_delta_ms": None, "total_ms": None},
            }
        )
    )
    returned = _stages(
        _row(
            observability_summary={
                "live": True,
                "tools": {"count": 1, "completed": 1, "names": ["memory_search"], "error_count": 0},
                "latency": {"first_delta_ms": None, "total_ms": None},
            }
        )
    )

    assert open_call["tools"] == "running"
    assert returned["tools"] == "done"


def test_a_finished_turn_reads_as_it_always_did() -> None:
    stages = _stages(
        _row(
            status="ok",
            finished_at="2026-08-27T09:00:04Z",
            latency_first_delta_ms=812,
            total_latency_ms=3924,
            observability_summary={
                "memory": {"attempted": True, "hit_count": 3},
                "memory_write": {"fanout_allowed": True, "disposition": "write"},
                "tools": {"count": 0, "names": [], "error_count": 0},
                "latency": {"first_delta_ms": 812, "total_ms": 3924},
            },
        )
    )

    assert stages == {
        "input": "done",
        "memory_recall": "done",
        "agent_turn": "done",
        # Settled: this turn needed no tools and is over.
        "tools": "done",
        "response": "done",
        "memory_write": "done",
    }


def test_a_running_turn_is_an_active_activity_that_back_pressure_keeps() -> None:
    """The companion is lit because the activity is active, not because of a flag."""

    activity = _voice_activity(_turn(_row()))

    assert activity.status == "running"
    assert activity.companion_id == "eidolon-xiaoer"
    assert activity.origin_device_id == "dev-box3"
    assert activity.summary == "智能体处理这一轮：进行中"
