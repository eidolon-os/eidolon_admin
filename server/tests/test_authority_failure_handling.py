"""An upstream refusal reaches a caller as a refusal, and lands in the journal.

This file is the regression gate for a failure that cost real time. Every route
on the internal planes used to convert ``AuthorityFailure`` itself, and one
authority — ``agent`` — existed in the exception the clients raise and not in the
model that serialises it. So the conversion raised *inside* the error path:
``/tasks`` and ``/conversations`` answered 500 with no body and no log line,
while the reason ("Admin Agent service credential is not configured") had been
constructed three frames down and discarded.

Two things are asserted here, and they are the two that were missing:

1. every authority in the vocabulary survives the trip to a wire, so no future
   authority can be added to half of it;
2. the conversion happens for a route that does not catch anything, and it logs
   what it converted.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import get_args, get_type_hints

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from eidolon_sdk.biz.contracts.refusal import REFUSAL_KINDS

from eidolon_admin_server.app.control_plane.contracts import Authority, FailureKind
from eidolon_admin_server.app.control_plane.errors import AuthorityFailure
from eidolon_admin_server.app.control_plane.failure_handler import (
    install_authority_failure_handler,
)


@pytest.mark.parametrize("authority", get_args(Authority))
@pytest.mark.parametrize("kind", get_args(FailureKind))
def test_every_refusal_the_clients_can_raise_can_be_put_on_a_wire(
    authority: str, kind: str
) -> None:
    """The whole cross product, because the gap was one cell of it.

    ``to_wire`` is called from an exception handler. A value it rejects does not
    become a validation error a caller can read — it becomes a 500 that says
    nothing, which is strictly worse than the refusal it replaced.
    """

    failure = AuthorityFailure(authority, kind, "because", 503, retryable=False)
    wire = failure.to_wire()
    assert wire.authority == authority
    assert wire.kind == kind
    assert wire.detail == "because"


def test_the_vocabulary_is_shared_rather_than_copied() -> None:
    """The exception and the wire model must read the same declaration.

    Asserting the annotation identity rather than the values: two lists that
    happen to match today are what this file exists because of. If someone
    re-inlines a ``Literal`` into either side, this fails while the values still
    agree — which is the only moment the mistake is cheap to fix.
    """

    from eidolon_admin_server.app.control_plane import contracts

    hints = get_type_hints(AuthorityFailure)
    assert hints["authority"] is contracts.Authority
    assert hints["kind"] is contracts.FailureKind
    assert contracts.WorkflowFailure.model_fields["authority"].annotation is contracts.Authority
    assert contracts.WorkflowFailure.model_fields["kind"].annotation is contracts.FailureKind


def _app_that_refuses(failure: AuthorityFailure) -> FastAPI:
    """A route that raises and catches nothing, like every real one now does."""

    app = FastAPI()
    install_authority_failure_handler(app)

    @app.get("/refuses")
    async def refuses() -> dict:
        raise failure

    return app


def test_a_route_that_catches_nothing_still_answers_its_chosen_status() -> None:
    client = TestClient(
        _app_that_refuses(
            AuthorityFailure(
                "agent",
                "configuration",
                "Admin Agent service credential is not configured",
                503,
                retryable=False,
            )
        ),
        raise_server_exceptions=False,
    )
    response = client.get("/refuses")
    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "authority": "agent",
            "kind": "configuration",
            "detail": "Admin Agent service credential is not configured",
            "code": None,
            "upstream_status": None,
            "retryable": False,
            # Carried from here rather than derived at the LAN boundary, which
            # imports nothing from this half and so cannot be trusted to know
            # this vocabulary is one value longer than it was.
            "refusal": {
                "kind": "not_configured",
                "reason": "Admin Agent service credential is not configured",
                "code": None,
                "retryable": False,
            },
        }
    }


def test_every_internal_kind_has_been_given_a_meaning_for_a_person() -> None:
    """No internal kind may reach a client as a guess.

    The mapping is exhaustive by test rather than by fallback on purpose: a
    default would let a new internal kind arrive on a phone as "something went
    wrong upstream" without anybody having decided that is what it means.
    """

    from eidolon_admin_server.app.control_plane.contracts import PUBLIC_REFUSAL_KIND

    assert set(PUBLIC_REFUSAL_KIND) == set(get_args(FailureKind))
    assert set(PUBLIC_REFUSAL_KIND.values()) <= set(REFUSAL_KINDS)


def test_the_two_kinds_a_person_must_not_see_folded_stay_apart() -> None:
    """"Nobody configured this" and "it is not running" lead different places.

    An operator treats both as "go look at the Host"; a person does not — one is
    worth waiting out and the other never will be. Folding them is what turned a
    missing credential into a retry button that could never work.
    """

    from eidolon_admin_server.app.control_plane.contracts import PUBLIC_REFUSAL_KIND

    assert PUBLIC_REFUSAL_KIND["configuration"] == "not_configured"
    assert PUBLIC_REFUSAL_KIND["unavailable"] == "not_running"
    assert PUBLIC_REFUSAL_KIND["runtime_missing"] == "not_running"


def test_a_conflict_keeps_the_status_a_client_must_react_to() -> None:
    """409 is the one refusal answered by re-reading rather than retrying.

    Worth its own case: a handler that flattened statuses would break the
    compare-and-set protocol every management writer depends on.
    """

    client = TestClient(
        _app_that_refuses(
            AuthorityFailure(
                "data",
                "conflict",
                "someone else changed this first",
                409,
                code="revision_stale",
            )
        ),
        raise_server_exceptions=False,
    )
    response = client.get("/refuses")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "revision_stale"


def test_a_misconfigured_host_says_so_in_its_own_journal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The line that was missing for two weeks.

    At warning, because "this Host has no credential" is not traffic — it is
    somebody's job. A stale revision stays at info so the level keeps meaning
    something.
    """

    failure = AuthorityFailure(
        "memory",
        "configuration",
        "Admin memory service credential is not configured",
        503,
    )
    client = TestClient(_app_that_refuses(failure), raise_server_exceptions=False)
    with caplog.at_level(logging.INFO, logger="eidolon_admin.authority"):
        client.get("/refuses")

    records = [r for r in caplog.records if r.name == "eidolon_admin.authority"]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    message = records[0].getMessage()
    assert "memory" in message
    assert "/refuses" in message
    assert "Admin memory service credential is not configured" in message
    assert "kind=configuration" in message


def test_an_ordinary_domain_refusal_is_not_logged_as_an_operator_problem(
    caplog: pytest.LogCaptureFixture,
) -> None:
    failure = AuthorityFailure("data", "not_found", "no such Companion", 404)
    client = TestClient(_app_that_refuses(failure), raise_server_exceptions=False)
    with caplog.at_level(logging.INFO, logger="eidolon_admin.authority"):
        client.get("/refuses")

    records = [r for r in caplog.records if r.name == "eidolon_admin.authority"]
    assert [r.levelno for r in records] == [logging.INFO]


def test_no_route_on_the_internal_planes_converts_a_refusal_itself() -> None:
    """Read from the source, because the old pattern still compiles.

    Written after watching it happen: a route added while this change was in
    flight arrived carrying its own ``except AuthorityFailure`` and its own
    silent conversion, because that is what every route around it looked like
    when it was copied. A docstring saying "do not" does not survive
    copy-and-paste; a failing test does.
    """

    root = Path(__file__).resolve().parents[1] / "eidolon_admin_server/app"
    offenders = []
    for path in sorted(root.rglob("router.py")):
        if "except AuthorityFailure" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(root)))
    assert not offenders, (
        f"{offenders} convert an AuthorityFailure themselves; let it out and the "
        "app-level handler will answer it and log it once"
    )
