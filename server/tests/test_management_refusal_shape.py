"""The public management surface has one refusal shape, proved from the source.

A convention would not hold, and the reason is written into this surface's own
history: the relaying path answered ``{"code", "message"}``, the same path
answered a bare sentence when no code was given, this surface's own guards
answered a third thing, and FastAPI's validation errors a fourth. Four shapes on
an API that two clients are *generated* from, so both clients were generated
against the successes and hand-written against the failures — which is why the
phone could tell exactly one refusal from the others.

Read from the source, because the failure is a line that compiles: a route
raising ``HTTPException(404, "no")`` works, ships, and is only wrong on someone's
phone.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_SURFACE = (
    Path(__file__).resolve().parents[1]
    / "eidolon_admin_server/local_api/management/router.py"
)
_ADAPTER = (
    Path(__file__).resolve().parents[1]
    / "eidolon_admin_server/local_api/management/backend.py"
)


def test_there_is_exactly_one_place_this_surface_builds_a_refusal() -> None:
    """One ``HTTPException(`` in the module, inside ``_enveloped``.

    Not "few". One: two construction sites is how this surface came to have two
    shapes, and the second one always looks locally reasonable.
    """

    source = _SURFACE.read_text(encoding="utf-8")
    sites = re.findall(r"\bHTTPException\(", source)
    assert len(sites) == 1, (
        f"{len(sites)} places build an HTTPException in the management surface; "
        "route refusals through refuse() so every client sees one shape"
    )
    body = source.split("def _enveloped(")[1]
    assert "return HTTPException(status_code, refusal.model_dump())" in body


def test_the_adapter_does_not_invent_a_refusal_of_its_own() -> None:
    """The loopback adapter relays; it does not word failures.

    It used to: it kept ``code`` from the authority's structured refusal and
    replaced everything else with "Host management backend refused this
    request", which is the sentence a person actually saw for two weeks while
    the real one — a credential this Host was never given — sat in the body it
    had just discarded.
    """

    source = _ADAPTER.read_text(encoding="utf-8")
    assert "HTTPException" not in source
    # Every raise says which refusal it is, rather than leaving a caller to
    # derive one from a status.
    raises = source.count("raise ManagementBackendError(")
    assert raises == source.count("refusal=")
    assert raises >= 5


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, "denied"),
        (403, "denied"),
        (404, "not_found"),
        (409, "conflict"),
        (412, "conflict"),
        (415, "invalid"),
        (422, "invalid"),
        (500, "upstream"),
        (503, "upstream"),
    ],
)
def test_a_status_alone_still_yields_a_kind(status_code: int, expected: str) -> None:
    """The version-skew path: an older Admin that sends no refusal.

    Forgiving on purpose. A consumer that raised on a shape it did not know
    would turn "the other process is a release behind" into an outage, and this
    wire crosses two processes that are restarted separately.
    """

    from eidolon_admin_server.local_api.management.router import refusal_for_status

    refusal = refusal_for_status(status_code, "because")
    assert refusal.kind == expected
    assert refusal.reason == "because"
    assert refusal.retryable is (status_code >= 500)


def test_the_two_refusals_that_share_a_status_do_not_share_a_meaning() -> None:
    """A Host with no Owner and a lost race are both 409.

    The phone used to separate them with two predicates that were the same
    expression — ``statusCode == 409`` twice — so a genuine conflict on the
    roster rendered "this Host has no owner yet". A domain code and an explicit
    kind are what make them different answers.
    """

    from eidolon_admin_server.local_api.management.router import refuse

    unprovisioned = refuse(
        409, "Host Workspace is not initialized",
        code="host_not_provisioned", kind="not_configured",
    )
    lost_race = refuse(409, "someone else changed this first", code="revision_stale")

    assert unprovisioned.detail["kind"] == "not_configured"
    assert unprovisioned.detail["code"] == "host_not_provisioned"
    assert lost_race.detail["kind"] == "conflict"
    assert unprovisioned.status_code == lost_race.status_code == 409
