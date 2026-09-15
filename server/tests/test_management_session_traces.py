"""The Owner's session traces, on the Owner's plane.

Two properties, and neither is about the payload — the payload is the Channel
Provider's document and this plane relays it rather than re-describing it:

* **the Owner comes from the session**, like everything else here. A client
  cannot name whose sessions it is reading;
* **a session belonging to somebody else is indistinguishable from one that does
  not exist.** The Provider's detail route takes no Owner — it is loopback-only
  and trusts its caller — so the check has to happen on the way past, and it has
  to answer 404 rather than 403. A distinct refusal would turn the route into a
  way to ask whether a session id is real.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from eidolon_admin_server.app.management import session_traces_router as internal

pytestmark = pytest.mark.asyncio

_OWNER = "owner_1291536"
_OTHER = "owner_someone_else"


def _document(owner_id: str) -> dict:
    return {
        "operation": "channel.session-trace",
        "session": {"session_id": "esp32-1", "owner_id": owner_id},
        "records": [{"record_kind": "session_open"}],
        "record_count": 1,
    }


async def test_a_session_belonging_to_another_owner_reads_as_absent(monkeypatch) -> None:
    async def _provider(request, path, *, params, timeout):
        return _document(_OTHER)

    monkeypatch.setattr(internal, "_provider_json", _provider)

    with pytest.raises(HTTPException) as caught:
        await internal.get_session_trace(
            SimpleNamespace(), session_id="esp32-1", owner_id=_OWNER
        )

    assert caught.value.status_code == 404
    # The same answer an unknown id gets, wording included.
    assert caught.value.detail["code"] == "NOT_FOUND"


async def test_the_owner_s_own_session_comes_back_whole(monkeypatch) -> None:
    async def _provider(request, path, *, params, timeout):
        return _document(_OWNER)

    monkeypatch.setattr(internal, "_provider_json", _provider)

    body = await internal.get_session_trace(
        SimpleNamespace(), session_id="esp32-1", owner_id=_OWNER
    )

    # Relayed, not rebuilt: the records are the Provider's own.
    assert body["records"] == [{"record_kind": "session_open"}]
    assert body["record_count"] == 1


async def test_recording_false_is_relayed_rather_than_read_as_no_sessions(
    monkeypatch,
) -> None:
    """The one field that separates two facts a screen must never merge.

    `recording: false` means this Host records nothing; an empty `sessions` with
    `recording: true` means this Owner has had none. Interpreting either here
    would take the distinction away from the only surface that can show it.
    """

    captured: dict = {}

    async def _provider(request, path, *, params, timeout):
        captured.update(params or {})
        return {"operation": "channel.session-traces", "recording": False, "sessions": []}

    monkeypatch.setattr(internal, "_provider_json", _provider)

    body = await internal.list_session_traces(SimpleNamespace(), owner_id=_OWNER)

    assert body["recording"] is False
    assert body["sessions"] == []
    # And the Owner reached the Provider, which is what scopes the listing.
    assert captured["owner_id"] == _OWNER


async def test_an_absurd_limit_is_bounded_before_it_becomes_a_request(
    monkeypatch,
) -> None:
    captured: dict = {}

    async def _provider(request, path, *, params, timeout):
        captured.update(params or {})
        return {"recording": True, "sessions": []}

    monkeypatch.setattr(internal, "_provider_json", _provider)

    await internal.list_session_traces(SimpleNamespace(), owner_id=_OWNER, limit=10_000)

    assert captured["limit"] == internal._MAX_LIMIT


async def test_a_provider_that_cannot_answer_is_unavailable_not_broken(
    monkeypatch,
) -> None:
    async def _provider(request, path, *, params, timeout):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(internal, "_provider_json", _provider)

    with pytest.raises(HTTPException) as caught:
        await internal.list_session_traces(SimpleNamespace(), owner_id=_OWNER)

    # 503: nothing about the request is wrong and retrying later is correct.
    assert caught.value.status_code == 503
    assert "connection refused" in caught.value.detail["detail"]
